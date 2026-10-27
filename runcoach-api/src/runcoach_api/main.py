import datetime
from contextlib import asynccontextmanager
from dataclasses import replace
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from runcoach_api import __version__, db
from runcoach_api.imports.archive import screen_file_id
from runcoach_api.ingestion import overnight
from runcoach_api.ingestion.exceptions import (
    DuplicateSessionError,
    FitParseFailure,
    MissingCanonicalFieldError,
    NotAFitFileError,
    OvernightSeriesError,
    TooManyRecordsError,
    http_status_detail,
)
from runcoach_api.ingestion.pipeline import ingest_fit_bytes
from runcoach_api.metrics import hrv_trend
from runcoach_api.schemas import (
    Band,
    Baseline,
    DatasetSummary,
    Disagreement,
    ExcludedReading,
    HealthResponse,
    HrvPoint,
    HrvTrendResponse,
    IncludedReading,
    IngestResponse,
    IngestSkipped,
    Thresholds,
)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# POST /imports carries a whole export archive, so it gets its own ceiling.
IMPORT_MAX_BYTES = 1024 * 1024 * 1024
_IMPORT_PATHS = frozenset({"/imports", "/imports/"})

# The precheck compares the request's declared Content-Length -- which
# includes multipart boundary markers and part headers on top of the
# file's own bytes -- against MAX_UPLOAD_BYTES, which caps the *file's*
# byte count exactly (as create_session's own read(MAX_UPLOAD_BYTES + 1)
# check enforces). A single-field, single-file /sessions upload needs at
# most a few hundred bytes of multipart framing, so this allowance is
# generous headroom for that framing, not a second size cap: a file
# right at MAX_UPLOAD_BYTES must still pass the precheck and reach
# create_session's exact byte-for-byte check, rather than being
# falsely rejected a few bytes early by request-level framing overhead.
_MULTIPART_FRAMING_ALLOWANCE_BYTES = 64 * 1024


class ContentLengthLimitMiddleware:
    """Rejects an oversized upload by its declared ``Content-Length``
    header before Starlette's multipart parser ever runs.

    M1 (sprint-002 review): ``create_session``'s own
    ``file.file.read(MAX_UPLOAD_BYTES + 1)`` guard only stops FIT
    parsing/DB writes on an oversized file -- it runs too late to stop
    the buffering itself. By the time that line executes, FastAPI has
    already resolved the ``UploadFile = File(...)`` dependency, which
    means Starlette's ``MultiPartParser`` has already consumed the
    entire request body and spooled it to a ``SpooledTemporaryFile``
    (spilling to disk past its 1MB in-memory threshold -- confirmed by
    reading the vendored source, ``.venv/.../starlette/formparsers.py``:
    ``MultiPartParser.max_part_size`` caps the size of an individual
    multipart *part*, never the total size of a file part, so nothing
    in Starlette itself stops an arbitrarily large file body from being
    received and buffered before a route ever runs).

    This middleware runs ahead of routing/parsing and rejects a request
    whose ``Content-Length`` already exceeds the cap (plus a small
    multipart-framing allowance -- see
    ``_MULTIPART_FRAMING_ALLOWANCE_BYTES``) -- for that request,
    ``receive()`` (and therefore multipart buffering) never happens. It
    does not close the full gap: a client using chunked
    transfer-encoding sends no ``Content-Length`` header at all and is
    not caught here. That residual gap is accepted rather than closed
    with a full ASGI-level streaming body-size limit -- see F003's
    Decision Log, 2026-09-03 "M1 upload buffering" entry for why, given
    this feature's local-first single-athlete deployment model. The
    same gap applies to ``/imports``.

    The ceiling is chosen per request: ``/imports`` and ``/imports/``
    get ``IMPORT_MAX_BYTES`` and every other path gets
    ``MAX_UPLOAD_BYTES``; the middleware takes no ceiling of its own.
    Both constants are read when a request arrives, not when the
    middleware stack is built, and the 413 text names the limit that
    applied.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    def _limit_for(self, path: str) -> int:
        if path in _IMPORT_PATHS:
            return IMPORT_MAX_BYTES
        return MAX_UPLOAD_BYTES

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                declared_bytes = int(content_length)
            except ValueError:
                declared_bytes = None
            limit = self._limit_for(scope.get("path", ""))
            precheck_ceiling = limit + _MULTIPART_FRAMING_ALLOWANCE_BYTES
            if declared_bytes is not None and declared_bytes > precheck_ceiling:
                response = PlainTextResponse(
                    f"upload exceeds {limit} byte limit", status_code=413
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        # F020: a job the previous process left active is marked interrupted, here only (never in
        # init_schema, which every ingest calls). import_job is imported with the /imports routes.
        import_job.recover_at_startup(conn, db._load_config_cached().data_dir)
    finally:
        conn.close()
    yield


app = FastAPI(title="Run Coaching API", lifespan=lifespan)
app.add_middleware(ContentLengthLimitMiddleware)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@app.post(
    "/sessions",
    response_model=IngestResponse,
    status_code=201,
    response_model_exclude_none=True,
    responses={200: {"model": IngestSkipped, "description": "Skipped: nothing stored."}},
)
def create_session(
    file: UploadFile = File(...),  # noqa: B008 -- FastAPI's parameter idiom
    resting_capture: bool = Form(False),
    runs_and_captures_only: bool = Form(False),
) -> IngestResponse | Response:
    """Ingest one uploaded FIT file.

    ``resting_capture`` is F004's upload-time Tier-1 declaration: the athlete
    saying *this* file was a resting-HRV capture. It exists because the config
    path cannot rescue a file already recorded on the wrong activity profile,
    and adding a name to ``resting_hrv_profile_names`` reclassifies nothing
    already stored.

    Optional and defaulting to ``False``, so an upload that sends only the file
    part -- every client that predates the amendment -- keeps working
    unchanged. It is a real ``bool`` rather than a string the route
    reinterprets, so ``"true"``/``"1"``/``"on"``/``"yes"`` and their negatives
    are coerced by pydantic and anything else is a 422: the one input on this
    path that carries nothing but the athlete's intent must never be guessed
    at.

    ``runs_and_captures_only`` (F022) applies the archive import's rule to this
    one file: ``imports.archive.screen_file_id`` before decoding, then
    ``imports.job.keep_session`` on the classified session. A file the rule does
    not keep is a 200 ``IngestSkipped`` and nothing is stored; a ``file_id`` the
    screen fails is a 400 with the screen's detail; otherwise the replies are the
    usual ones, and the 201 carries ``filter_applied: true``. The switch with
    ``resting_capture`` is a 422: a declaration that bypasses the rule and a
    request to apply it contradict each other. Without the switch an activity's
    reply is as before it existed (no ``filter_applied`` key); an HRV_STATUS file's is not.

    Every upload is screened (one ``file_id`` read). An HRV_STATUS file (F028)
    takes the night path, with or without the switch: ``overnight.map_night``,
    then ``db.persist_night``. A night is a 201 (``filter_applied: true`` only
    with the switch, ``replaced_session_id`` only when it replaced a stored
    re-summary of the same night); a file without a night is a 200
    ``IngestSkipped`` with reason ``no_night``, ``file_type: "hrv_status"`` and
    ``filter_applied`` saying whether the switch was sent; the same night
    again, or an earlier re-summary of a stored one, is a 409 naming the stored
    id; a decode fault or a faulty series is a 400; ``resting_capture`` with an
    HRV_STATUS file is a 422. Any other screen outcome takes the activity path
    above; without the switch a file the screen fails still reaches
    ``ingest_fit_bytes``, so its reply is the pipeline's own.

    Every 200 is a ``JSONResponse`` of ``IngestSkipped`` with ``None`` keys
    dropped; the route's ``response_model`` is ``IngestResponse`` alone, so the
    served 201 names that component and nothing else.
    """
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"upload exceeds {MAX_UPLOAD_BYTES} byte limit")
    if runs_and_captures_only and resting_capture:
        # RequestValidationError is imported with the /me routes below, as update_me raises it.
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("body", "runs_and_captures_only"),
                    "msg": "Value error, runs_and_captures_only cannot be combined with resting_capture",
                    "input": runs_and_captures_only,
                }
            ]
        )
    screen = screen_file_id(raw)
    if screen.outcome == "hrv_status":
        if resting_capture:
            raise RequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "resting_capture"),
                        "msg": "Value error, resting_capture cannot be declared for an HRV_STATUS file",
                        "input": resting_capture,
                    }
                ]
            )
        return _create_night(raw, filter_applied=runs_and_captures_only)
    kept_sessions: list = []
    try:
        if not runs_and_captures_only:
            result = ingest_fit_bytes(raw, resting_capture_override=resting_capture)
        else:
            if screen.outcome == "failed":
                raise HTTPException(400, screen.detail)
            if screen.outcome == "non_activity":
                return _skipped(reason="non_activity", file_type=screen.file_type, filter_applied=True)

            def keep(session) -> bool:
                kept_sessions.append(session)
                # import_job is imported with the /imports routes below, as lifespan uses it.
                return import_job.keep_session(session)

            result = ingest_fit_bytes(raw, resting_capture_override=False, keep=keep)
    except (
        NotAFitFileError,
        FitParseFailure,
        TooManyRecordsError,
        MissingCanonicalFieldError,
        DuplicateSessionError,
    ) as exc:
        # One mapping, shared with the archive import's per-file rows, so the
        # two cannot drift: 400 for a file that is not FIT, does not parse or
        # lacks a canonical field; 413 for a decode ceiling, naming the
        # quantity that overflowed (the byte cap's bucket, one status); 409
        # naming the session a duplicate already is.
        status, detail = http_status_detail(exc)
        raise HTTPException(status, detail) from exc
    if not runs_and_captures_only:
        return IngestResponse(session_id=result.session_id, quality_flags=result.quality_flags)
    if result.skipped:
        return _skipped(reason="skipped_sport", sport=import_job._skip_label(kept_sessions[-1]), filter_applied=True)
    return IngestResponse(
        session_id=result.session_id, quality_flags=result.quality_flags, filter_applied=True
    )


def _skipped(**fields) -> Response:
    """``POST /sessions``'s 200: an ``IngestSkipped`` body, its ``None`` keys dropped.

    A ``JSONResponse`` of its own, so the route's ``response_model`` stays
    ``IngestResponse`` alone. JSONResponse is imported with the /me routes
    below, as their validation handler uses it.
    """
    body = IngestSkipped(skipped=True, **fields)
    return JSONResponse(status_code=200, content=body.model_dump(mode="json", exclude_none=True))


def _create_night(raw: bytes, *, filter_applied: bool) -> IngestResponse | Response:
    """The night path of ``POST /sessions`` for an HRV_STATUS file (the replies are in its docstring)."""
    try:
        mapped = overnight.map_night(raw)
        if isinstance(mapped, overnight.NoNight):
            return _skipped(reason=mapped.reason, file_type="hrv_status", filter_applied=filter_applied)
        conn = db.get_connection()
        try:
            # As ingest_fit_bytes does: a store the lifespan never initialised gains its tables here.
            db.init_schema(conn)
            write = db.persist_night(conn, mapped)
        finally:
            conn.close()
    except (
        NotAFitFileError,
        FitParseFailure,
        TooManyRecordsError,
        MissingCanonicalFieldError,
        OvernightSeriesError,
        DuplicateSessionError,
    ) as exc:
        status, detail = http_status_detail(exc)
        raise HTTPException(status, detail) from exc
    return IngestResponse(
        session_id=write.session_id,
        quality_flags=list(mapped.session.quality_flags),
        filter_applied=True if filter_applied else None,
        replaced_session_id=write.replaced_session_id,
    )


@app.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    conn = db.get_connection()
    try:
        detail = db.get_session_detail(conn, session_id)
    finally:
        conn.close()

    if detail is None:
        raise HTTPException(404, f"session {session_id} not found")

    return detail


@app.delete("/sessions/{session_id}", status_code=204, response_class=Response)
def delete_session(session_id: str) -> Response:
    """Remove one session and every row that hangs off it.

    The recovery path out of F004's 2026-09-06 upgrade window: a capture
    ingested before the athlete added their activity-profile name to
    ``api.toml`` carries no reading, adding the name reclassifies
    nothing already stored, and ``UNIQUE (source_device, start_time)``
    answered a re-upload with a 409. Deleting the session frees that
    slot, so *configure, delete, upload again* actually works. The 409
    itself is unchanged -- this route is what makes it survivable, not a
    relaxation of it.

    404 on an unknown id, matching ``get_session``'s message shape; the
    id is otherwise unvalidated, since it reaches nothing but a bound
    parameter in a ``DELETE ... WHERE session_id = ?``.
    """
    conn = db.get_connection()
    try:
        deleted = db.delete_session(conn, session_id)
    finally:
        conn.close()

    if not deleted:
        raise HTTPException(404, f"session {session_id} not found")

    # An explicit empty 204 rather than a serialized ``None``: a 204 must
    # carry no body at all, and returning None through the default JSON
    # response class would emit a four-byte ``null``.
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# GET /metrics/hrv (F005, T085)
# ---------------------------------------------------------------------------

#: How far past a local day's midnight-UTC anchor the row read is padded, so
#: the whole local day is covered in any zone (UTC+14 through UTC-12) before
#: ``hrv_trend`` buckets each row with ``astimezone(zone)``. ``read_hrv_rows``
#: deliberately does no day arithmetic; the padding is the caller's.
_HRV_READ_PADDING = datetime.timedelta(hours=26)
#: The route reads back to ``from - 126`` local days, not ``from - 66``: the
#: sustained-tier-change rule (T092) reads the highest-fidelity tier with
#: >= 14 days in the *previous* baseline window ``[D-126, D-67]`` (HRV-38,
#: clause (b)) and compares, and a read that stops at
#: ``D-66`` leaves that window empty -- which reads as "thin", never as a
#: change, so ``reset_reason: tier_change`` would be silently unreachable
#: through the endpoint (IDEA-045). The extra rows come back from
#: ``build_series`` as ``outside_windows`` and are trimmed from ``excluded[]``.
#: It is not widened further for the coverage-gap rule: a layoff longer than
#: this is told from a new athlete by ``db.earliest_hrv_reading``, one scalar
#: read beside the rows (T096, review cycle 3 G13).
_HRV_READ_BACK_DAYS = 2 * hrv_trend.BASELINE_DAYS + hrv_trend.WINDOW_DAYS - 1
#: The longest ``[from, to]`` the per-day series will be built over (T091):
#: ``to - from`` greater than this is a 422. Nothing else bounds the loop, and
#: a year plus a day is every chart the UI draws; 367 evaluations of a pure
#: function over a single athlete's rows is still cheap, but unbounded is not.
_HRV_MAX_RANGE_DAYS = 366


def _utcnow() -> datetime.datetime:
    """The clock seam: the current aware UTC instant. Patched by the tests
    that freeze time; nothing else reads the clock."""
    return datetime.datetime.now(datetime.UTC)


def _today_in(zone: ZoneInfo) -> datetime.date:
    """The athlete's local today -- the default ``to``. Resolved from the
    configured zone, not the machine's: at 13:00Z it is already tomorrow in
    Auckland, and a verdict about the wrong day is the defect
    ``athlete_timezone`` exists to prevent."""
    return _utcnow().astimezone(zone).date()


def _midnight_utc(day: datetime.date) -> datetime.datetime:
    """The aware UTC midnight that opens ``day``: the anchor both read bounds
    are padded from."""
    return datetime.datetime.combine(day, datetime.time.min, tzinfo=datetime.UTC)


def _shifted(instant: datetime.datetime, delta: datetime.timedelta) -> datetime.datetime:
    """``instant + delta``, clamped to the calendar's edge it would cross."""
    try:
        return instant + delta
    except OverflowError:
        if delta < datetime.timedelta(0):
            return datetime.datetime.min.replace(tzinfo=datetime.UTC)
        return datetime.datetime.max.replace(tzinfo=datetime.UTC)


def hrv_read_range(from_: datetime.date, to: datetime.date) -> tuple[str, str]:
    """The inclusive UTC ``start_time`` bounds handed to ``db.read_hrv_rows``:
    ``[from - 126d - 26h, (to + 1d) + 26h]``, spelled ``+00:00`` exactly as
    the rows are stored (never ``Z``: it does not compare against ``+00:00``).

    ``from`` anchors the lower bound so T091's per-day loop over ``[from,
    to]`` reads once; for this task's single verdict ``from == to`` by
    default and the bound is ``to - 126d - 26h``.

    Both bounds are clamped to the calendar: ``to=9999-12-31`` is a valid
    date whose padded end does not exist, and a far-future or far-past
    request is ``hrv_unavailable``, not a 500 (the adversarial table's
    far-future row found the overflow).
    """
    back = datetime.timedelta(days=_HRV_READ_BACK_DAYS) + _HRV_READ_PADDING
    forward = datetime.timedelta(days=1) + _HRV_READ_PADDING
    start = _shifted(_midnight_utc(from_), -back)
    end = _shifted(_midnight_utc(to), forward)
    return start.isoformat(), end.isoformat()


def _withhold_future(
    verdict: hrv_trend.HrvVerdict, day: datetime.date, today: datetime.date
) -> hrv_trend.HrvVerdict:
    """A day strictly after the athlete's local ``today`` asserts no verdict
    (F005: "no suppression is asserted about a day that has not happened").

    The clock is the route's, not the module's: ``hrv_trend.judge`` knows
    only the rows, and for any ``to`` up to four days after the last capture
    the judged window ``[to-6, to]`` still holds three or more readings on an
    intact baseline, so left alone it says ``hrv_suppressed`` about a day
    that has not happened (sprint-005 review, M1 -- the only future-date
    test used ``D + 400``, where every row is ``outside_windows`` and the
    verdict is unavailable for an unrelated reason). The verdict is replaced
    with ``hrv_unavailable`` (``day_not_happened``) and ``below_by`` with
    ``None``; everything that *produced* them -- the band,
    the baseline, ``readings_in_window``, the week's mean -- is left as
    computed, because the band is a property of the baseline ``[d-66, d-7]``
    (which lies wholly in the past) and the contract's ``points[]`` draws it
    on days with no reading (T091), and because the verdict must remain
    reproducible by hand from the response (``research/00`` PRIN-12; the
    inputs it does not serve are PRIN-12's OPEN exceptions, PRIN-24 and PRIN-27).

    **``unavailable_reason`` (T137).** Overridden to ``REASON_DAY_NOT_HAPPENED``
    whenever this replaces the verdict, regardless of whichever of ``judge``'s own four
    causes the pure rule reported (including ``None``, on a day that would
    otherwise have read ``hrv_normal`` or ``hrv_suppressed``): the fields that
    would explain those causes are still reported as computed, so the only
    claim actually true of *this* response is that the day has not happened
    yet -- not, say, that the baseline is unestablished, which for a
    near-future day it usually is not."""
    if day <= today:
        return verdict
    return replace(
        verdict,
        verdict=hrv_trend.VERDICT_UNAVAILABLE,
        below_by=None,
        unavailable_reason=hrv_trend.REASON_DAY_NOT_HAPPENED,
    )


def _judge_days(
    rows: list[dict],
    zone: ZoneInfo,
    from_: datetime.date,
    to: datetime.date,
    today: datetime.date,
    earliest_start_time: str | None = None,
) -> list[tuple[hrv_trend.SingleDatasetView, hrv_trend.HrvVerdict]]:
    """Every local day in ``[from, to]``, in order, judged against its own
    baseline ``[d-66, d-7]`` by the pure computation over the one set of
    ``rows`` -- ~30 evaluations of a function of ``target_date`` for a month's
    chart, done plainly (no caching, no incremental trick) -- and then held
    against ``today``, the athlete's local date resolved once per request:
    a day after it carries no verdict (``_withhold_future``). The last pair
    is ``to``'s, and it is the one the verdict blocks are rendered from, so
    the last point and ``band`` are the same objects rather than two
    computations. ``earliest_start_time`` is the store's earliest reading
    (``db.earliest_hrv_reading``), handed to every day alike: it is a
    property of the store, not of the day.

    **Selection runs per judged day** (F006, T155; AC14). ``build_series``
    returns one dataset per source tier, and ``judge`` takes one: the
    dataset handed to it and rendered is the one ``hrv_trend.select_dataset``
    selects for that day -- the highest-fidelity judgeable dataset the recency
    gate did not skip -- or the presentation fallback when
    none was selected (AC9), flattened onto the F005 series shape by
    ``hrv_trend.selected_view``. Nothing else here is a function of which
    dataset was chosen, and **that is why every point names its own**
    (T159, ``_point``): two adjacent days of one chart can be drawn against
    two different instruments, and the view alone cannot say so.

    Raises ``OverflowError`` where a day's windows reach past the calendar's
    origin; the route names that as the parameters' problem."""
    judged = []
    for offset in range((to - from_).days + 1):
        day = from_ + datetime.timedelta(days=offset)
        datasets = hrv_trend.build_series(rows, zone, day, earliest_start_time)
        series = hrv_trend.selected_view(datasets)  # F006 selection (T155); T159 renders datasets[]
        judged.append((series, _withhold_future(hrv_trend.judge(series), day, today)))
    return judged


def _band(band: hrv_trend.Band | None) -> Band | None:
    """The contract's ``Band`` from the module's, or null. One renderer, so
    the response's ``band`` and every ``datasets[].band`` are built the same
    way rather than twice."""
    if band is None:
        return None
    return Band(mean=band.mean, half_width=band.half_width, lo=band.lo, hi=band.hi, floored=band.floored)


def _point(series: hrv_trend.SingleDatasetView, verdict: hrv_trend.HrvVerdict) -> HrvPoint:
    """One day of the contract's ``points[]``: the reading the day's own
    series holds for it (null when none), the band the day's own baseline
    asserts (all three null together when it cannot build one), and **the
    dataset that band came from** (F006 AC14, T159). The band comes from
    ``judge`` -- the same call that decides ``to``'s verdict -- so the last
    point and ``band`` are the same floats, not two computations.

    ``dataset`` is ``series.tier``: the tier of the dataset ``selected_view``
    presented for **this** day, which is the selection's when there is one
    and the presentation fallback's when there is not. Selection is run per
    judged day and reads nothing from yesterday, so the name can change
    between adjacent points -- and when it does the band steps by the
    systematic bias between the two tiers, which is exactly the reading a
    chart would otherwise draw as a change in the athlete (``CRITIC-F005``
    priority 3). It is null only on the empty view, where no reading of any
    tier exists in ``[d-66, d]``; a dataset with one baseline reading has a
    tier and no band, so ``dataset`` is non-null there while the other three
    are null."""
    reading = next((r for r in series.series if r.date == series.target_date), None)
    band = verdict.band
    return HrvPoint(
        date=series.target_date,
        ln_rmssd=None if reading is None else hrv_trend.ln_rmssd(reading),
        baseline=None if band is None else band.mean,
        swc_low=None if band is None else band.lo,
        swc_high=None if band is None else band.hi,
        dataset=series.tier,
    )


def _band_readings(selection: hrv_trend.Selection | None) -> dict[str, hrv_trend.BandReading]:
    """``Selection.band_readings`` keyed by tier, empty when there is no
    selection. One builder, so ``_datasets`` and ``_disagreed_with`` read the
    same mapping instead of each constructing it -- a dataset's ``week_days``
    is one number wherever it is rendered, not two derivations that agree."""
    if selection is None:
        return {}
    return {r.tier: r for r in selection.band_readings}


def _datasets(series: hrv_trend.SingleDatasetView) -> list[DatasetSummary]:
    """Every dataset of the day's series, in fidelity order (F006 AC1/AC2/
    AC10, T159) -- the block that makes the retained losers legible.

    Nothing is recomputed here. ``tier``, ``n``, ``band``, ``established``
    and the per-dataset ``reset_on``/``reset_reason`` are the dataset's own
    fields (``build_series``); ``week_days``, ``week_mean`` and ``below``
    are ``Selection.band_readings`` (``read_against_band``, T157), which
    asks ``judge`` for each dataset's band and week mean so there is **one**
    arithmetic rather than a second derivation of it; ``last_read`` is the
    selection's own ``_last_read`` over the baseline-window slice, the same
    mapping the recency gate was applied to. ``fidelity_rank`` is the tier's
    index in ``hrv_trend.TIER_FIDELITY`` -- the ordinal that arbitrates
    selection, not a confidence weight, which spec 03 3.7.4 computes none of
    in this section (see ``DatasetSummary.fidelity_rank``).

    A dataset with no baseline-window reading has no ``last_read`` entry and
    renders null; one with no judged-week reading has ``week_days`` 0 and a
    null ``week_mean``. ``selection`` is set by ``selected_view`` on every
    view it builds, the empty one included, so the band readings are always
    present for the datasets that exist.
    """
    selection = series.selection
    readings = _band_readings(selection)
    last_read = selection.last_read if selection is not None else {}
    summaries = []
    for dataset in series.datasets:
        tier = dataset.tier or ""
        reading = readings.get(tier)
        summaries.append(
            DatasetSummary(
                tier=tier,
                n=dataset.n,
                established=dataset.established,
                band=_band(dataset.band),
                fidelity_rank=hrv_trend.TIER_FIDELITY.index(tier),
                last_read=last_read.get(tier),
                week_days=0 if reading is None else reading.week_days,
                week_mean=None if reading is None else reading.week_mean,
                below=None if reading is None else reading.below,
                reset_on=dataset.reset_on,
                reset_reason=dataset.reset_reason,
            )
        )
    return summaries


def _disagreed_with(
    series: hrv_trend.SingleDatasetView, verdict: hrv_trend.HrvVerdict
) -> list[Disagreement]:
    """The datasets on the other side of their own band from the selected
    one, each with the judged-week count that weighs it (F006 AC10/AC11,
    T159; the count is T157's own finding -- a one-reading judged week can
    name a dissenter, and the consumer needs to weigh it).

    Order and candidate membership are the selection's, rendered rather than
    re-derived from ``Selection.disagreed_with``. Emptiness is not the
    selection's alone: it **also depends on the served verdict**
    (``research/00`` HRV-22), so the served list is empty on a withheld
    selected dataset and on ``day_not_happened`` even where
    ``Selection.disagreed_with`` still names a dataset.

    **Empty wherever no verdict was conferred** -- ``research/00`` HRV-22
    as amended 2026-09-21 (T167, ``B-CR-002``), restated in
    ``spec/03`` §3.7.4. The predicate is one condition, ``verdict == VERDICT_UNAVAILABLE``,
    and it subsumes all **three** states in which no claim is made:

    * nothing selected -- the AC9 presentation fallback, already decided in
      ``hrv_trend.disagreed_with``'s own words: "a disagreement is with a
      verdict, and the presentation fallback confers none; naming a dissenter
      against ``hrv_unavailable`` would report a contradiction of a claim
      never made";
    * a dataset that **is** selected whose verdict is **withheld** under HRV-31
      because a returning dataset's judged week is entirely later than
      its own -- served as ``week_not_representative``. This is T125's
      returning athlete, the population F006 exists for, and it is the state
      ``B-CR-002`` found uncovered: ``withheld`` is computed per dataset
      independent of judgeability, so a selected dataset can be withheld and
      the response carried a named dissenter beside its ``hrv_unavailable``
      (``week_not_representative``);
    * a day that has not happened -- ``day_not_happened``. ``_withhold_future``
      is the one place a verdict is replaced *after* ``judge`` has spoken, and
      the selection never sees the clock, so left alone this named a dissenter
      there too (sprint-006 review iteration 1, M2, which guarded this state
      alone and is what T167 generalises).

    Only the *claim* is replaced, by ``hrv_unavailable`` with its reason.
    ``selected_dataset`` and ``selected_reason``
    are kept as computed, for ``_withhold_future``'s own stated reason:
    everything that **produced** the verdict is left alone, so the response
    keeps what ``research/00`` PRIN-12 asks a verdict to be reproduced from by
    hand (the inputs it does not serve are PRIN-12's OPEN exceptions, PRIN-24 and
    PRIN-27, owned by IDEA-102), and those two identify
    which dataset the retained ``baseline``/``band`` came from. They are
    producers, not claims. ``datasets[]`` is kept for the same reason and
    keeps the day's state legible: every dataset's own ``below`` is still
    reported, so a consumer can still see that the snapshot read the other
    side of its band -- what is not reported is that this *contradicts*
    anything, because nothing was asserted to contradict.
    """
    selection = series.selection
    if selection is None or verdict.verdict == hrv_trend.VERDICT_UNAVAILABLE:
        return []
    readings = _band_readings(selection)
    return [
        Disagreement(
            dataset=tier,
            week_days=0 if readings.get(tier) is None else readings[tier].week_days,
        )
        for tier in selection.disagreed_with
    ]


def _trend_response(
    from_: datetime.date,
    points: list[HrvPoint],
    series: hrv_trend.SingleDatasetView,
    verdict: hrv_trend.HrvVerdict,
) -> HrvTrendResponse:
    """Render the pure module's result on the contract's shape.

    ``excluded[]`` is documented as every non-contributing row inside
    ``[to-66, to]``; the module lists the rows the padded read brought in
    from before ``to-66`` (and any clock-skewed row after ``to``) as
    ``outside_windows``, and they are trimmed here so the list is exactly
    the population it claims to be. The *unclipped* window is the span, so a
    reset does not shrink it.

    **F006 (T159).** ``baseline``, ``band``, ``verdict`` and ``below_by``
    keep their names and now mean *the selected dataset's*; ``datasets[]``,
    ``selected_dataset``, ``selected_reason`` and ``disagreed_with`` are
    added beside them. ``selected_dataset`` and ``selected_reason`` are null
    together and only together -- ``Selection.selected_reason`` is derived
    from the selection rather than stored beside it, so the pair cannot come
    apart here. ``verdict`` is the one of the four that ``_withhold_future``
    may already have replaced, and the split that follows is
    ``_disagreed_with``'s: the dissent list is a claim *about* a verdict and
    is emptied wherever that verdict is ``hrv_unavailable``, while
    ``selected_dataset``, ``selected_reason`` and
    ``datasets[]`` produced the retained ``baseline``/``band`` and are kept as
    computed. On that null the presentation fallback still populates
    ``baseline``/``band``, which is what keeps this addition additive:
    nothing non-nullable before F006 became nullable (AC12), and the one
    breaking change of this sprint was T152's removal of ``off_baseline_tier``.
    """
    span_first = hrv_trend.baseline_window(series.target_date)[0]
    selection = series.selection
    selected = None if selection is None else selection.selected
    return HrvTrendResponse(
        date=series.target_date,
        from_=from_,
        timezone=series.timezone,
        points=points,
        verdict=verdict.verdict,
        unavailable_reason=verdict.unavailable_reason,
        ln_rmssd_7d_mean=verdict.ln_rmssd_7d_mean,
        below_by=verdict.below_by,
        band=_band(verdict.band),
        baseline=Baseline(
            window=series.baseline_window,
            n=verdict.baseline_n,
            tier=series.tier,
            established=verdict.established,
            reset_on=series.reset_on,
            reset_reason=series.reset_reason,
        ),
        window=series.judged_window,
        readings_in_window=verdict.readings_in_window,
        included=[
            IncludedReading(date=r.date, session_id=r.session_id, tier=r.tier, rmssd_ms=r.rmssd_ms)
            for r in series.window
        ],
        excluded=[
            ExcludedReading(date=e.date, session_id=e.session_id, reason=e.reason)
            for e in series.excluded
            if span_first <= e.date <= series.target_date
        ],
        thresholds=Thresholds(
            baseline_days=hrv_trend.BASELINE_DAYS,
            min_baseline_readings=hrv_trend.MIN_BASELINE_READINGS,
            min_window_readings=hrv_trend.MIN_WINDOW_READINGS,
            gap_reset_days=hrv_trend.GAP_RESET_DAYS,
            band_floor=hrv_trend.BAND_FLOOR,
            swc_factor=hrv_trend.SWC_FACTOR,
            recency_tolerance_days=hrv_trend.RECENCY_TOLERANCE_DAYS,
        ),
        datasets=_datasets(series),
        selected_dataset=None if selected is None else selected.tier,
        selected_reason=None if selection is None else selection.selected_reason,
        disagreed_with=_disagreed_with(series, verdict),
    )


@app.get("/metrics/hrv", response_model=HrvTrendResponse, operation_id="getHrvTrend")
def get_hrv_trend(
    from_: datetime.date | None = Query(None, alias="from"),  # noqa: B008 -- FastAPI's parameter idiom
    to: datetime.date | None = Query(None),  # noqa: B008
) -> HrvTrendResponse:
    """The resting-HRV trend verdict for local day ``to`` (F005, spec/03 §3.7)
    and the contract's per-day ``points[]`` over ``[from, to]``, on the
    UI<->engine contract's path (``operationId: getHrvTrend``).

    ``to`` defaults to the athlete's local today in the configured zone and
    ``from`` to ``to``; ``from`` after ``to`` is a 422 naming both, and so is
    a range longer than ``_HRV_MAX_RANGE_DAYS`` (T091's cap). Both are
    coerced by pydantic from ``YYYY-MM-DD`` -- never hand-parsed, for the
    reason ``create_session`` gives about ``resting_capture``: an input that
    carries the athlete's intent must never be guessed at, and a malformed
    date is a free 422. ``from`` is a Python keyword, hence the alias, which
    is what ``/openapi.json`` serialises.

    There is essentially no other error path. An empty database, a day the
    athlete has not reached and a day before any capture are all
    ``hrv_unavailable`` with a 200 -- the absence of a verdict is itself the
    answer, not a missing resource. The athlete's local today is resolved
    **once per request** and every judged day is held against it
    (``_withhold_future``): the pure module is clock-free, so this is where
    "a future date asserts no verdict" is enforced, for ``to`` and for every
    ``points[]`` day alike.

    The zone is read from the config **per request** and handed down, so the
    pure module never imports ``config`` and a changed ``athlete_timezone``
    simply re-buckets the history on the next request; nothing is stored to
    detect the change and nothing is asserted about it. The connection is
    opened and closed inline like the other routes -- there is no dependency
    injection seam for the DB here and this feature does not add one.
    """
    zone = ZoneInfo(db._load_config_cached().athlete_timezone)
    today = _today_in(zone)
    if to is None:
        to = today
    if from_ is None:
        from_ = to
    if from_ > to:
        raise HTTPException(
            422,
            f"'from' ({from_.isoformat()}) is after 'to' ({to.isoformat()}); "
            "'from' must be on or before 'to'",
        )
    if (to - from_).days > _HRV_MAX_RANGE_DAYS:
        raise HTTPException(
            422,
            f"'from' ({from_.isoformat()}) to 'to' ({to.isoformat()}) spans {(to - from_).days} days; "
            f"the range may be at most {_HRV_MAX_RANGE_DAYS} days",
        )

    start_iso, end_iso = hrv_read_range(from_, to)
    conn = db.get_connection()
    try:
        rows = db.read_hrv_rows(conn, start_iso, end_iso)
        # One scalar beside the row read, not a wider read (T096, G13): the
        # store's earliest reading tells a layoff longer than the 126 days
        # read (a reading precedes the window, none was read) from a new
        # athlete (none precedes it). The row read's bounds are unchanged.
        earliest = db.earliest_hrv_reading(conn, hrv_trend.TIER_FIDELITY)
    finally:
        conn.close()

    # The contract's series, and ``to``'s verdict from the same last pair.
    try:
        judged = _judge_days(rows, zone, from_, to, today, earliest)
    except OverflowError as exc:
        # The windows are ``date`` arithmetic back to ``d - 126``; a day
        # inside the calendar's first 126 days has no such history to look
        # into. Named as the parameters' problem, not served as a 500.
        raise HTTPException(
            422,
            f"'from' ({from_.isoformat()}) to 'to' ({to.isoformat()}) is too close to the calendar's "
            f"origin to have a {_HRV_READ_BACK_DAYS}-day history window",
        ) from exc
    points = [_point(series, verdict) for series, verdict in judged]
    series, verdict = judged[-1]
    return _trend_response(from_, points, series, verdict)


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/features (F013)
#
# Appended below the earlier routes with its own imports: this module's
# existing lines are cited by line number elsewhere and are left in place.
# ---------------------------------------------------------------------------
from runcoach_api.metrics import session_features
from runcoach_api.schemas import SessionFeaturesResponse


@app.get(
    "/sessions/{session_id}/features",
    response_model=SessionFeaturesResponse,
    operation_id="getSessionFeatures",
    tags=["Sessions"],
)
def get_session_features(session_id: str) -> SessionFeaturesResponse:
    """The session's grade-adjusted pace, NGP and descriptors, computed on read (F013).

    ``db.read_session_feature_inputs`` reads the session's ``sport``,
    ``quality_flags``, ``context`` and the record columns the transform
    consumes, and ``metrics.session_features.compute_session_features`` builds
    the response of the F013 reference, section 8. Nothing is stored and no
    schema is touched: the connection is opened and closed inline, as
    ``get_hrv_trend`` does, and ``init_schema`` is never called here, so the
    schema and every row count are identical before and after the request.

    An unknown or deleted ``session_id`` is the 404 ``get_session`` raises,
    with the same message, so the two routes share one envelope. Every other
    case is a 200: a non-running session, or one with no records, serves every
    feature unavailable with its reason, because an all-unavailable session is
    an answer, not an error.
    """
    conn = db.get_connection()
    try:
        inputs = db.read_session_feature_inputs(conn, session_id)
    finally:
        conn.close()

    if inputs is None:
        raise HTTPException(404, f"session {session_id} not found")

    session, rows = inputs
    return SessionFeaturesResponse(**session_features.compute_session_features(session, rows))


# ---------------------------------------------------------------------------
# GET /me (F016)
#
# Appended below the earlier routes with its own imports, as the features
# route is, so the lines cited above stay in place.
# ---------------------------------------------------------------------------
from runcoach_api import profile
from runcoach_api.schemas import Athlete


def _entry(field: profile.FieldValue) -> dict:
    return {
        "value": field.value,
        "unavailable": field.reason,
        "source": field.source,
        "session_id": field.session_id,
        "recorded_at": field.recorded_at,
        "entered_value": field.entered_value,
    }


def _anchor(anchor: profile.Anchor) -> dict:
    return {
        "value": anchor.value,
        "unavailable": anchor.reason,
        "source": anchor.source,
        "session_id": anchor.session_id,
        "recorded_at": anchor.recorded_at,
        "version": anchor.version,
        "entered_value": anchor.entered_value,
    }


@app.get("/me", response_model=Athlete, operation_id="getMe", tags=["Auth & Athlete"])
def get_me() -> Athlete:
    """The athlete's profile (F016): the settings row, each field's effective value with its
    source and any entry a file shadows, and the four HR anchors.

    ``db.read_athlete`` reads all three in one snapshot. Nothing is stored and no schema is
    touched: the schema and the settings row come from ``db.init_schema``, which the app
    lifespan runs at startup, never from this read. An empty store is a 200 with every field
    and anchor unavailable, reason ``missing``.

    The one named error is a store out of step, in a store that has been through startup: no
    settings row, or an anchor version log that does not hold a served value
    (``db.read_hr_anchors``). Only a write outside the app's write paths causes either, and
    ``init_schema`` repairs both, so this is a 500 that names the cause and the repair (restart
    the API) rather than serving a version that names another value. The read does not repair
    it, because a GET does not write. A store deleted or replaced while the API runs (as
    deleting ``runcoach.db`` to rebuild it does) has no tables, and fails as on every other
    route, with a bare 500 (``sqlite3.OperationalError``) that names neither.
    """
    conn = db.get_connection()
    try:
        return _athlete(conn)
    finally:
        conn.close()


def _athlete(conn) -> Athlete:
    """``GET /me``'s body, read by ``db.read_athlete``; a store out of step is the named 500.

    Only ``db.read_athlete``'s ``ValueError`` is named; a store with no tables raises
    ``sqlite3.OperationalError``, which passes through as the bare 500 every route gives.
    """
    try:
        settings, resolved, served = db.read_athlete(conn)
    except ValueError as exc:
        raise HTTPException(
            500, f"the profile store is out of step ({exc}); restart the API, whose startup repairs it"
        ) from exc
    return Athlete(
        **settings,
        **{field: _entry(resolved[field]) for field in profile.ENTERED_FIELDS},
        anchors={field: _anchor(served[field]) for field in profile.ANCHOR_FIELDS},
    )


# ---------------------------------------------------------------------------
# PATCH /me (F016)
# ---------------------------------------------------------------------------
import math

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from runcoach_api.schemas import AthleteProfileUpdate

_NON_FINITE_TOKENS = {math.inf: "Infinity", -math.inf: "-Infinity"}


def _json_safe(value):
    """``value`` with every non-finite float written as the JSON token that carried it."""
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else _NON_FINITE_TOKENS[value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


@app.exception_handler(RequestValidationError)
async def _request_validation_error(request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's own 422 body (``fastapi.exception_handlers.request_validation_exception_handler``),
    except that a non-finite input is echoed as text. The request parser reads the ``NaN`` and
    ``Infinity`` tokens, so a refused one sits in the error's ``input``, and the response encoder
    refuses non-finite floats: without this a refused ``NaN`` body value was a 500, not a 422."""
    return JSONResponse(status_code=422, content={"detail": _json_safe(jsonable_encoder(exc.errors()))})


@app.patch("/me", response_model=Athlete, operation_id="updateMe", tags=["Auth & Athlete"])
def update_me(update: AthleteProfileUpdate) -> Athlete:
    """Store the athlete's entries and settings (F016), then return ``GET /me``'s shape.

    ``AthleteProfileUpdate`` screens each value; a rejected body is a 422 that writes nothing.
    The one screen it cannot apply is the clock: a ``birth_date`` after today in
    ``athlete_timezone`` (read as ``get_hrv_trend`` reads it, through the ``_utcnow`` seam) is a
    422 here, before any write. Each profile field present writes one entered-value row through
    ``db.write_profile_entries``, null a clear, and ``display_name`` and ``units`` change the
    settings row, all in one transaction with the anchor version log. ``{}`` writes nothing.
    Ordering across fields is not checked: an entry that conflicts with another field's
    effective value is stored, and ``GET /me`` serves the anchors as ``order_conflict``.
    """
    present = update.model_fields_set
    birth_date = update.birth_date if "birth_date" in present else None
    if birth_date is not None:
        today = _today_in(ZoneInfo(db._load_config_cached().athlete_timezone))
        if datetime.date.fromisoformat(birth_date) > today:
            raise RequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "birth_date"),
                        "msg": f"Value error, birth_date {birth_date} is after today ({today}) in the athlete's timezone",
                        "input": birth_date,
                    }
                ]
            )
    settings = {field: getattr(update, field) for field in db.SETTINGS_FIELDS if field in present}
    if "units" in settings:
        settings["units"] = update.units.model_dump()
    values = {field: getattr(update, field) for field in profile.ENTERED_FIELDS if field in present}

    conn = db.get_connection()
    try:
        if values or settings:
            db.write_profile_entries(conn, values, settings=settings)
        return _athlete(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/load (F017)
#
# Appended below the earlier routes with its own imports, as the features and
# profile routes are, so the lines cited above stay in place.
# ---------------------------------------------------------------------------
from runcoach_api.schemas import SessionLoad


@app.get(
    "/sessions/{session_id}/load",
    response_model=SessionLoad,
    operation_id="getSessionLoad",
    tags=["Sessions"],
)
def get_session_load(session_id: str) -> SessionLoad:
    """The session's training load, HR-TRIMP first, as its upload or last recompute saved it (F017).

    The body is the one ``db._save_session_load`` wrote in the upload's
    transaction (or a recompute last replaced), with every value used; it is
    served unchanged through ``db.read_session_load_body``, reading no anchor
    (user rulings C1-C3, 2026-10-08), so a later ``PATCH /me``, dated HR
    entry or upload that moves an anchor changes nothing here until
    ``POST /sessions/{id}/load/recompute`` replaces it, and a corrupted
    anchor version log that makes ``GET /me`` a 500 leaves this route a 200. Nothing is computed and nothing is
    written: the connection is opened and closed inline, as the features
    route does.

    An unknown ``session_id`` is the 404 ``get_session`` raises, with the same
    message, and so is a read after ``DELETE /sessions/{id}``, which removes
    the saved load with the session. A stored session with no saved load (one
    a pre-F017 server wrote to an upgraded store, until the next startup's
    fill has run) is also a 404, whose detail says so instead:
    ``session {id} has no saved load``.
    """
    conn = db.get_connection()
    try:
        body = db.read_session_load_body(conn, session_id)
        stored = body is not None or db.session_exists(conn, session_id)
    finally:
        conn.close()

    if body is None:
        missing = "has no saved load" if stored else "not found"
        raise HTTPException(404, f"session {session_id} {missing}")
    return SessionLoad(**body)


# ---------------------------------------------------------------------------
# GET /metrics/load (F018)
#
# Appended below the earlier routes with its own imports, as the routes
# above are, so the lines cited by number stay in place.
# ---------------------------------------------------------------------------
import dataclasses

from runcoach_api.metrics import load_chart
from runcoach_api.schemas import LoadChart, LoadChartDay, LoadChartGap, LoadChartSeed


@app.get(
    "/metrics/load",
    response_model=LoadChart,
    operation_id="getLoadChart",
    tags=["State & Metrics"],
)
def get_load_chart(
    from_: datetime.date | None = Query(None, alias="from"),  # noqa: B008 -- FastAPI's parameter idiom
    to: datetime.date | None = Query(None),  # noqa: B008
) -> LoadChart:
    """The fitness, fatigue and form chart (F018, spec/03 section 3.5) over ``[from, to]``, on
    the contract's path (``operationId: getLoadChart``).

    The zone is read from the config per request and the athlete's local
    today resolved once, as ``get_hrv_trend`` does; both are handed to the
    pure ``metrics.load_chart``, which buckets every stored session's saved
    load (``db.read_session_load_rows``, one statement, one snapshot) into
    local days and runs the curves from the first day of history. ``from``
    and ``to`` only choose which days are returned.

    ``to`` defaults to today and ``from`` to the earlier of ``first_day`` and
    ``to``, so a ``to`` before history, or a store whose every session is
    dated after today, answers ``days`` empty rather than a 422. ``from``
    after ``to`` is a 422 naming both; ``to`` after today is a 422
    (projection belongs to the taper, spec Section 7), unlike ``/metrics/hrv``,
    which withholds future days with a 200. Both are coerced by pydantic from
    ``YYYY-MM-DD`` and never hand-parsed. There is no range cap: one athlete,
    about 365 rows a year. A store with no running session is a 200 with
    ``first_day`` and ``seed`` null and ``days`` empty. A session whose local
    date is after today is not listed until that date arrives (no projection).

    A session whose saved load row is missing (both load columns null in the
    LEFT JOIN) is the module's ``ValueError`` naming the session, served as
    a 500 with that message: a run without a load is never a 0, and a chart
    that silently omitted it would be the worse answer. Nothing is written.
    """
    zone = ZoneInfo(db._load_config_cached().athlete_timezone)
    today = _today_in(zone)
    if to is None:
        to = today
    elif to > today:
        raise HTTPException(
            422,
            f"'to' ({to.isoformat()}) is after today ({today.isoformat()}) in the athlete's timezone; "
            "the chart is not projected",
        )
    if from_ is not None and from_ > to:
        raise HTTPException(
            422,
            f"'from' ({from_.isoformat()}) is after 'to' ({to.isoformat()}); "
            "'from' must be on or before 'to'",
        )

    conn = db.get_connection()
    try:
        rows = db.read_session_load_rows(conn)
    finally:
        conn.close()

    try:
        chart = load_chart.build_chart(rows, zone, today)
    except ValueError as exc:
        raise HTTPException(500, str(exc)) from exc

    if from_ is None:
        from_ = min(chart.first_day, to) if chart.first_day is not None else to
    days = [
        LoadChartDay.model_validate(dataclasses.asdict(day)) for day in chart.days if from_ <= day.date <= to
    ]
    seed = None if chart.seed is None else LoadChartSeed.model_validate(dataclasses.asdict(chart.seed))
    # The gaps list the whole history up to today, unfiltered by from/to (IDEA-143).
    gaps = [LoadChartGap.model_validate(dataclasses.asdict(gap)) for gap in chart.gaps]
    return LoadChart(timezone=zone.key, today=today, first_day=chart.first_day, seed=seed, gaps=gaps, days=days)


# ---------------------------------------------------------------------------
# GET /sessions (F019)
#
# Appended below the earlier routes with its own imports, as the routes
# above are, so the lines cited by number stay in place.
# ---------------------------------------------------------------------------
from runcoach_api.metrics import session_list
from runcoach_api.schemas import SessionPage, SessionSummary


@app.get("/sessions", response_model=SessionPage, operation_id="listSessions", tags=["Sessions"])
def list_sessions(
    since: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    include_nights: bool = Query(False),
) -> SessionPage:
    """The stored sessions, newest first, keyset-paged (F019), on the contract's path
    (``operationId: listSessions``).

    Nights (``activity_tag`` ``overnight_hrv``) are left out unless
    ``include_nights`` is true (F028 AC6); a value that is not a boolean is
    pydantic's 422. The filter sits in the query, so the order and the paging
    are those of the store without the nights.

    ``db.read_session_page`` reads one page of ``limit`` rows plus one, ordered
    by ``start_time`` descending then ``session_id`` descending, after the row
    ``since`` names; ``metrics.session_list.page`` drops the extra row and
    builds ``next_cursor`` from the last served one, null on the last page.
    ``limit`` outside 1..200, or not an integer, is pydantic's 422. A ``since``
    that ``session_list.decode_cursor`` refuses is a ``RequestValidationError``
    with ``loc ("query", "since")``, as ``update_me`` raises its clock check,
    so the body is FastAPI's default ``{detail: [...]}``.

    Each running row's three numbers are ``segments.recorded_totals`` over
    the ``(t, distance)`` records ``db.read_session_totals_rows`` reads
    through the ``records (session_id, t)`` index; a row of any other sport
    reads no records. Only that per-row computation is wrapped: any exception
    in it serves the row with its three numbers null (user ruling 2026-10-09),
    and never the page as an error. The session query itself is not wrapped:
    a store whose ``sessions`` table lacks the ``name`` column (startup not
    run since it was added) is the named 500 ``get_me`` serves; a store with
    no tables, or a row whose stored ``context`` or ``quality_flags`` does
    not decode, is the bare 500 every route gives, since a restart repairs
    neither. Nothing is written.
    """
    before = None
    if since is not None:
        try:
            before = session_list.decode_cursor(since)
        except ValueError as exc:
            raise RequestValidationError(
                [{"type": "value_error", "loc": ("query", "since"), "msg": f"Value error, {exc}", "input": since}]
            ) from exc

    conn = db.get_connection()
    try:
        try:
            rows = db.read_session_page(conn, limit=limit, before=before, include_nights=include_nights)
        except db.SessionStoreOutOfStepError as exc:
            raise HTTPException(
                500, f"the session store is out of step ({exc}); restart the API, whose startup repairs it"
            ) from exc
        served, next_cursor = session_list.page(rows, limit)
        items = [SessionSummary(**session_list.summary_row(row, _summary_numbers(conn, row))) for row in served]
    finally:
        conn.close()
    return SessionPage(items=items, next_cursor=next_cursor)


def _summary_numbers(conn, row: dict) -> dict[str, float | None]:
    """The three numbers of one listed session, or three nulls.

    A session not stored as running reads no records. For a running one the
    records read and the totals are the AC5 seam: whatever they raise, the row
    lists with blanks rather than failing the page.
    """
    if row["sport"] != session_features.RUNNING:
        return dict(session_list.NO_NUMBERS)
    try:
        T, D = session_list.recorded_totals(db.read_session_totals_rows(conn, row["session_id"]))
    except Exception:  # noqa: BLE001 -- the ruling: a row that cannot be computed lists blank
        return dict(session_list.NO_NUMBERS)
    return session_list.summary_numbers(T, D)


# ---------------------------------------------------------------------------
# /me/hr-history (F021)
#
# Appended below the earlier routes with its own imports, as the routes
# above are, so the lines cited by number stay in place.
# ---------------------------------------------------------------------------
from runcoach_api.schemas import HrHistoryEntry, HrHistoryEntryCreate


@app.post(
    "/me/hr-history",
    response_model=HrHistoryEntry,
    status_code=201,
    operation_id="addHrHistoryEntry",
    tags=["Auth & Athlete"],
    responses={200: {"model": HrHistoryEntry, "description": "The entry for that field and date, replaced"}},
)
def add_hr_history_entry(entry: HrHistoryEntryCreate, response: Response) -> HrHistoryEntry:
    """Store one dated HR setting (F021 AC1): 201 with the new entry, or 200 when an entry for the same
    field and date existed, whose value is replaced and whose ``entry_id`` is kept.

    ``HrHistoryEntryCreate`` screens the body; the one screen it cannot apply is the clock: an
    ``effective_from`` after today in ``athlete_timezone`` (read through the ``_utcnow`` seam, as
    ``update_me`` reads it) is a 422 here, before any write. Nothing here touches the anchor version
    log: an entry changes neither ``GET /me`` nor an anchor.
    """
    today = _today_in(ZoneInfo(db._load_config_cached().athlete_timezone))
    if datetime.date.fromisoformat(entry.effective_from) > today:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("body", "effective_from"),
                    "msg": f"Value error, effective_from {entry.effective_from} is after today ({today}) in the athlete's timezone",
                    "input": entry.effective_from,
                }
            ]
        )
    conn = db.get_connection()
    try:
        stored, created = db.write_hr_history_entry(conn, entry.field, entry.value, entry.effective_from)
    finally:
        conn.close()
    if not created:
        response.status_code = 200
    return HrHistoryEntry(**stored)


@app.get(
    "/me/hr-history",
    response_model=list[HrHistoryEntry],
    operation_id="listHrHistory",
    tags=["Auth & Athlete"],
)
def list_hr_history() -> list[HrHistoryEntry]:
    """Every dated HR entry, by field, then effective date (``db.read_hr_history``). Nothing is written."""
    conn = db.get_connection()
    try:
        return [HrHistoryEntry(**entry) for entry in db.read_hr_history(conn)]
    finally:
        conn.close()


@app.delete(
    "/me/hr-history/{entry_id}",
    status_code=204,
    response_class=Response,
    operation_id="deleteHrHistoryEntry",
    tags=["Auth & Athlete"],
    responses={404: {"description": "No dated HR entry has that entry_id"}},
)
def delete_hr_history_entry(entry_id: int) -> Response:
    """Remove one dated HR entry (204); an unknown ``entry_id`` is a 404. A saved load that cited
    the entry keeps the value it recorded."""
    conn = db.get_connection()
    try:
        removed = -(2**63) <= entry_id < 2**63 and db.delete_hr_history_entry(conn, entry_id)  # no row has an id past int64
    finally:
        conn.close()
    if not removed:
        raise HTTPException(404, f"hr history entry {entry_id} not found")
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# /imports (F020)
#
# Appended below the earlier routes with its own imports, as the routes
# above are, so the lines cited by number stay in place. Every SQL statement
# lives in imports.job; the routes check the request, place the archive and
# start the worker.
# ---------------------------------------------------------------------------
import zipfile

from runcoach_api.imports import job as import_job
from runcoach_api.schemas import Import, ImportPage

#: The archive part is copied into the job's temp dir in reads of this size,
#: counting bytes, so a body that passes the middleware without a declared
#: length is still refused at IMPORT_MAX_BYTES.
IMPORT_COPY_CHUNK_BYTES = 1024 * 1024


def _copy_archive(source, target) -> int:
    """Copy the uploaded part to ``target``, counting bytes; 413 past ``IMPORT_MAX_BYTES``.

    The cap is read when the request arrives (tests lower it). A read that
    would take the count past the cap is refused before it is written, so the
    copy never holds more than the cap; the caller removes what was written.
    This bounds the copy only: the request body the server spooled before the
    route ran is not bounded here.
    """
    limit = IMPORT_MAX_BYTES
    written = 0
    with target.open("wb") as out:
        while chunk := source.read(IMPORT_COPY_CHUNK_BYTES):
            written += len(chunk)
            if written > limit:
                raise HTTPException(413, f"upload exceeds {limit} byte limit")
            out.write(chunk)
    return written


def _check_zip(path) -> None:
    """400 unless ``path`` opens as a zip. Decided by content, never by the file name: a directory
    upload is named after the directory and carries no ``.zip``."""
    try:
        with zipfile.ZipFile(path):
            pass
    except (zipfile.BadZipFile, EOFError, ValueError, NotImplementedError) as exc:
        # ValueError covers a UTF-8-flagged entry name that does not decode (UnicodeDecodeError).
        # OSError is not caught: zipfile reports malformed input as BadZipFile, so an OSError is
        # a fault reading the server's own copy, and the caller answers it 500.
        raise HTTPException(400, f"not a zip archive: {type(exc).__name__}: {exc}") from exc


@app.post(
    "/imports",
    response_model=Import,
    status_code=202,
    operation_id="startImport",
    tags=["Sessions"],
    responses={
        400: {"description": "The upload does not open as a zip archive (a zero-byte upload included)"},
        409: {"description": "An import is queued, scanning or running; the detail names its import_id"},
        413: {"description": "The archive exceeds the 1 GiB limit"},
        500: {
            "description": "The archive could not be stored (disk full, unwritable data dir, the "
            "stored copy unreadable), or the worker could not start; the detail names the cause"
        },
    },
)
def start_import(file: UploadFile = File(...)) -> Import:  # noqa: B008 -- FastAPI's parameter idiom
    """Start an import of a Garmin export archive, or any zip of FIT files (F020 reference section 2.1).

    The checks run in the reference's order: the middleware refuses a declared
    length over ``IMPORT_MAX_BYTES``; an active job is a 409 naming it (the
    check and the new row are one ``BEGIN IMMEDIATE`` transaction in
    ``import_job.create_job``); the part is copied to the job's temp dir,
    counting bytes, 413 past the cap; a copy that does not open as a zip is a
    400. Any refusal, and a failure to write the copy or read it back (a 500
    naming the cause), removes the row and the temp dir. Then the worker starts with the
    data dir, ``resting_hrv_profile_names`` and ``athlete_timezone`` read here,
    once, and the response is the job as it was before the worker ran:
    ``queued``, with ``files`` empty. A worker that cannot start is a 500
    naming the cause, and the row and the temp dir are removed, so the next
    import is not refused as active.
    """
    config = db._load_config_cached()
    data_dir = config.data_dir
    archive_name = file.filename or import_job.ARCHIVE_FILENAME
    conn = db.get_connection(data_dir)
    try:
        try:
            import_id = import_job.create_job(conn, archive_name, None)
        except import_job.ImportActive as exc:
            raise HTTPException(
                409,
                f"import {exc.import_id} is active; one import runs at a time (if it never finishes, "
                "restart the API, whose startup marks it interrupted)",
            ) from exc
        try:
            target = import_job.archive_path(data_dir, import_id)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                archive_bytes = _copy_archive(file.file, target)
                _check_zip(target)
            except OSError as exc:
                raise HTTPException(
                    500, f"the archive could not be stored: {type(exc).__name__}: {exc}"
                ) from exc
            import_job.set_archive_bytes(conn, import_id, archive_bytes)
            body = import_job.read_import(conn, import_id)
        except BaseException:
            import_job.discard_job(conn, data_dir, import_id)
            raise
    finally:
        conn.close()
    try:
        import_job.start_worker(
            import_id, data_dir, list(config.resting_hrv_profile_names), ZoneInfo(config.athlete_timezone)
        )
    except Exception as exc:
        # A job no worker runs would hold the one active slot until the next startup.
        conn = db.get_connection(data_dir)
        try:
            import_job.discard_job(conn, data_dir, import_id)
        finally:
            conn.close()
        raise HTTPException(500, f"the import could not be started: {type(exc).__name__}: {exc}") from exc
    return Import.model_validate(body)


@app.get(
    "/imports",
    response_model=ImportPage,
    operation_id="listImports",
    tags=["Sessions"],
    response_model_exclude={"items": {"__all__": {"files"}}},
)
def list_imports() -> ImportPage:
    """Every import job, newest first, without ``files``. Nothing is written."""
    conn = db.get_connection()
    try:
        rows = import_job.list_imports(conn)
    finally:
        conn.close()
    return ImportPage(items=[Import.model_validate(row) for row in rows])


@app.get(
    "/imports/{import_id}",
    response_model=Import,
    operation_id="getImport",
    tags=["Sessions"],
    responses={404: {"description": "No import has that import_id"}},
)
def get_import(import_id: str) -> Import:
    """One import job with its ``files`` rows in processing order; 404 for an unknown id."""
    conn = db.get_connection()
    try:
        body = import_job.read_import(conn, import_id)
    finally:
        conn.close()
    if body is None:
        raise HTTPException(404, f"import {import_id} not found")
    return Import.model_validate(body)


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/load/recompute (F021)
#
# Appended below the earlier routes with its own imports, as the routes
# above are, so the lines cited by number stay in place.
# ---------------------------------------------------------------------------
import json

from pydantic import ValidationError

from runcoach_api.schemas import SessionLoadRecompute


def _load_changed(old_body: dict | None, new_body: SessionLoad) -> bool:
    """Whether the new body differs from the old in any field, the old read through ``SessionLoad``
    so the dated-entry input keys it lacks take their defaults. No old body, or one that does not
    read as a load, is a change."""
    if old_body is None:
        return True
    try:
        old = SessionLoad(**old_body)
    except (ValidationError, TypeError):
        return True
    return old.model_dump(mode="json") != new_body.model_dump(mode="json")


@app.post(
    "/sessions/{session_id}/load/recompute",
    response_model=SessionLoadRecompute,
    operation_id="recomputeSessionLoad",
    tags=["Sessions"],
    responses={
        404: {"description": "No stored session has that session_id"},
        500: {"description": "The anchor version log is out of step; the detail names the cause and the repair"},
    },
)
def recompute_session_load(session_id: str) -> SessionLoadRecompute:
    """Recompute the session's saved load with the current files, dated entries and anchors (F021
    AC5), replace it, and return it with ``changed``.

    ``db.recompute_session_load`` computes and writes in one transaction, under the run-date lookup
    a save uses; a non-running session is computed as an upload computes it, and a session with no
    saved load gets one. 200 in every case, with the ``SessionLoad`` fields at the top level.
    ``changed`` compares the new body with the old read through ``SessionLoad``, so a body saved
    before dated entries existed differs only where a value does. An unknown ``session_id`` is the
    404 ``get_session`` raises, writing nothing. An anchor version log out of step is the named 500
    ``GET /me`` serves, writing nothing. This route is the only path that replaces a saved load.
    The response is built inside the transaction, before the write, so a computed body that does
    not read as a ``SessionLoad`` is a 500 that writes nothing.
    """
    zone = ZoneInfo(db._load_config_cached().athlete_timezone)
    built: list[SessionLoadRecompute] = []

    def build(old_body: dict | None, new_body: dict) -> None:
        body = SessionLoad(**json.loads(json.dumps(new_body)))
        built.append(SessionLoadRecompute(**body.model_dump(), changed=_load_changed(old_body, body)))

    conn = db.get_connection()
    try:
        result = db.recompute_session_load(conn, session_id, zone=zone, build=build)
    except db.AnchorLogOutOfStepError as exc:
        raise HTTPException(
            500, f"the profile store is out of step ({exc}); restart the API, whose startup repairs it"
        ) from exc
    finally:
        conn.close()
    if result is None:
        raise HTTPException(404, f"session {session_id} not found")
    return built[0]


# ---------------------------------------------------------------------------
# PUT /race-tags (F023)
#
# Appended below the earlier routes with its own imports, as the routes
# above are, so the lines cited by number stay in place. The matching rule
# lives in race_tags; db.apply_race_tags reads and writes in one transaction.
# ---------------------------------------------------------------------------
from runcoach_api import race_tags
from runcoach_api.schemas import RaceTagCounts, RaceTagsResult, RaceTagsUpdate


@app.put("/race-tags", response_model=RaceTagsResult, operation_id="setRaceTags", tags=["Sessions"])
def set_race_tags(update: RaceTagsUpdate) -> RaceTagsResult:
    """Carry Connect's race marks onto stored runs (F023 AC1): one outcome per item, in request
    order, and every outcome's count.

    ``RaceTagsUpdate`` screens the whole body before anything is read, so a 422 writes nothing.
    ``db.apply_race_tag_decisions`` matches each item to the stored running sessions at its start
    second, confirms by distance and duration within 1 %, and writes only ``activity_tag``, in one
    transaction, by the F026 reference section 2 table. Re-sending the same items is idempotent:
    what was tagged, cleared or retagged is then ``unchanged``.

    Each item is handed over with ``tag`` set to the tag it asks for (``RaceTagItem.wanted``),
    never as ``model_dump()``: a sparse item's dump carries both keys, and a race-only item would
    read as ``tag: None``, a clear.
    """
    items = [
        {
            "connect_activity_id": item.connect_activity_id,
            "start_time": item.start_time,
            "distance_m": item.distance_m,
            "duration_s": item.duration_s,
            "tag": item.wanted(),
        }
        for item in update.items
    ]
    conn = db.get_connection()
    try:
        decisions = db.apply_race_tag_decisions(conn, items)
    finally:
        conn.close()
    return RaceTagsResult(
        items=[decision.as_dict() for decision in decisions],
        counts=RaceTagCounts(**race_tags.counts(decisions)),
    )


# ---------------------------------------------------------------------------
# GET /state (F024)
#
# Appended below the earlier routes with its own imports, as the routes
# above are, so the lines cited by number stay in place. The estimator lives
# in metrics.threshold_pace; db.read_threshold_effort_rows reads its rows.
# ---------------------------------------------------------------------------
import functools
import re
from collections.abc import Callable

from runcoach_api.metrics import riegel_b, threshold_pace
from runcoach_api.schemas import RiegelDeterminant, StateModel, ThresholdDeterminant

_AS_OF_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_AS_OF_DATE_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T.+")
ESTIMATE_NOT_FINITE = "estimate_not_finite"


def parse_as_of(value: str | None, zone: ZoneInfo, now: datetime.datetime) -> datetime.datetime:
    """The instant ``GET /state`` estimates at, in UTC (F024 reference section 5); pure.

    ``None`` is ``now``. A ``YYYY-MM-DD`` date is compared with today in ``zone`` first, before
    any day arithmetic, so a date after today (``9999-12-31`` included) raises; otherwise it
    means the last instant (one microsecond) before the next local midnight, clamped to
    ``now``, so today's date is accepted. Any other value must be an extended-form ISO 8601
    date-time (``YYYY-MM-DDT...``) with a zone, at or before ``now``. A basic or week-form date
    (``20261014``, ``2026-W42-3``), a naive or future date-time, an instant outside the
    calendar in UTC and anything unparseable raise ``ValueError``.
    """
    if value is None:
        return now
    try:
        if _AS_OF_DATE.fullmatch(value):
            day = datetime.date.fromisoformat(value)
            today = now.astimezone(zone).date()
            if day > today:
                raise ValueError(f"the date {value} is after today ({today.isoformat()}) in {zone.key}")
            midnight = datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time.min, tzinfo=zone)
            return min(midnight - datetime.timedelta(microseconds=1), now).astimezone(datetime.UTC)
        if not _AS_OF_DATE_TIME.fullmatch(value):
            raise ValueError(f"{value!r} is neither a YYYY-MM-DD date nor an ISO 8601 date-time with a zone")
        instant = datetime.datetime.fromisoformat(value)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError(f"{value!r} has no zone; an instant needs one")
        instant = instant.astimezone(datetime.UTC)
    except OverflowError as exc:
        raise ValueError(f"{value!r} is outside the calendar in UTC") from exc
    if instant > now:
        raise ValueError(f"{value!r} is after now ({now.isoformat()}); the estimate is not projected")
    return instant


def _threshold_determinant(
    result: threshold_pace.ThresholdPace, unreadable: list[dict]
) -> ThresholdDeterminant:
    """The ``functional_threshold_pace`` entry: the estimator's result, with the races and workouts
    the reader could not read merged into ``excluded`` (those at or before ``as_of``, in start-time
    order), each exclusion with its ``kind``, ``admitted_by`` and ``workout_name`` as the reader
    decided them (``dataclasses.asdict`` carries the same fields on the efforts).

    An estimate with a non-finite published number (a finite but absurd GAP pace overflows the
    interval) is served as ``estimate_not_finite`` with ``estimate`` and ``error`` null, never as a value JSON
    cannot carry; ``efforts`` (with GAP pace and gain) and ``parameters`` stay, so the race can be found."""
    excluded = [dataclasses.asdict(e) for e in result.excluded]
    for row in unreadable:
        start = datetime.datetime.fromisoformat(row["start_time"])
        if start <= result.as_of:
            excluded.append({**row, "start_time": start})
    excluded.sort(key=lambda e: (e["start_time"], e["session_id"]))

    numbers = (result.value, result.interval_low, result.interval_high, result.error)
    unavailable = result.unavailable
    if unavailable is None and not all(math.isfinite(n) for n in numbers):
        unavailable = ESTIMATE_NOT_FINITE
    estimate = None
    if unavailable is None:
        estimate = {
            "value": result.value,
            "unit": "s/km",
            "interval": {"low": result.interval_low, "high": result.interval_high},
            "confidence": result.confidence,
            "provisional": False,
        }
    return ThresholdDeterminant(
        name="functional_threshold_pace",
        estimate=estimate,
        unavailable=unavailable,
        trend={"direction": "unknown", "rate": None},
        error=None if unavailable else result.error,
        short_races_only=False if unavailable else result.short_races_only,
        efforts=[dataclasses.asdict(effort) for effort in result.efforts],
        excluded=excluded,
        parameters=dict(result.parameters),
    )


def _riegel_fitness(chart: load_chart.LoadChart) -> Callable[[datetime.date], tuple]:
    """The fitness lookup ``riegel_b`` reads (F029 reference section 2 row 5): for a race's local
    date, the chart's CTL on the day before and on the day ``FITNESS_LOOKBACK_DAYS + 1`` days before
    it, with the day-before day's ``provisional`` and ``after_gap``. A day the chart does not hold
    (before its ``first_day``) is null. The race day itself is never read: its CTL includes the
    race's own load."""
    days = {day.date: day for day in chart.days}
    earlier_offset = datetime.timedelta(days=riegel_b.FITNESS_LOOKBACK_DAYS + 1)

    def lookup(race_date: datetime.date) -> tuple:
        before = days.get(race_date - datetime.timedelta(days=1))
        earlier = days.get(race_date - earlier_offset)
        return (
            None if before is None else before.ctl,
            None if earlier is None else earlier.ctl,
            None if before is None else before.provisional,
            None if before is None else before.after_gap,
        )

    return lookup


def _finite_or_none(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _riegel_determinant(result: riegel_b.RiegelEstimate) -> RiegelDeterminant:
    """The ``riegel_exponent`` entry: the estimator's result as served. A GAP-equivalent distance
    that is not finite (the estimate is then ``estimate_not_finite``) is served null, never as a
    value JSON cannot carry; each exclusion carries its session, start, reason and the three
    fitness figures."""
    estimate = None
    if result.unavailable is None:
        estimate = {
            "value": result.value,
            "unit": "1",
            "interval": {"low": result.interval_low, "high": result.interval_high},
            "confidence": result.confidence,
            "provisional": result.provisional,
        }
    efforts = [
        {**dataclasses.asdict(effort), "gap_equivalent_m": _finite_or_none(effort.gap_equivalent_m)}
        for effort in result.efforts
    ]
    excluded = [
        {
            "session_id": e.session_id,
            "start_time": e.start_time,
            "reason": e.reason,
            "fitness_before": e.fitness_before,
            "fitness_earlier": e.fitness_earlier,
            "fitness_drop": e.fitness_drop,
        }
        for e in result.excluded
    ]
    return RiegelDeterminant(
        name="riegel_exponent",
        estimate=estimate,
        unavailable=result.unavailable,
        trend={"direction": "unknown", "rate": None},
        error=result.error,
        t_multiplier=result.t_multiplier,
        population_default=result.population_default,
        below_one=result.below_one,
        efforts=efforts,
        excluded=excluded,
        parameters=dict(result.parameters),
    )


@app.get(
    "/state",
    response_model=StateModel,
    operation_id="getState",
    tags=["State & Metrics"],
    responses={500: {"description": "The load chart could not be built; the detail is the chart's message"}},
)
def get_state(as_of: str | None = Query(None)) -> StateModel:
    """The determinant profile at ``as_of`` (F024, F026, F027, F029): threshold pace from the tagged
    races and the threshold workouts' counted work reps, with its 80 % interval and confidence,
    then the endurance exponent b from the tagged races, on the contract's path
    (``operationId: getState``).

    The zone and the workout names are read per request from the config cached at startup, so a
    changed list needs a restart. ``parse_as_of`` reads
    ``as_of`` against the ``_utcnow`` seam; a value it refuses is a ``RequestValidationError`` on
    ``("query", "as_of")``, as ``list_sessions`` raises its cursor check.
    ``db.read_threshold_effort_rows`` reads the tagged races (records only for those in the
    duration band) and the workouts admitted by their ``threshold_workout`` tag or by a step's
    workout name listed in the config's ``threshold_workout_names`` (F027 reference section 2;
    records only for those past orders 1 to 5 of the F027 reference section 4), each row with
    its ``kind``, ``admitted_by`` and ``workout_name``, and ``metrics.threshold_pace.estimate``
    replays them to the instant, recomputed on every request. A race or workout whose summary,
    quality flags, feature inputs or laps do not read is excluded with its reason
    (``summary_unreadable``, ``quality_flags_unreadable``, ``features_unavailable``,
    ``laps_unreadable``) rather than failing the read. A stored ``start_time`` is trusted, as the
    ingest writes it with its offset; a naive or unparseable one, which only a write from outside
    the API could leave, fails the read. ``readiness`` and ``gap_to_goal`` are null. Nothing is
    written.

    The clock is read once: ``instant`` and the athlete's local today both come from that one
    value. ``db.read_state_inputs`` reads the loads, the threshold rows and the Riegel rows as one
    snapshot; its factory builds the load chart (``load_chart.build_chart``, as ``GET
    /metrics/load`` does) on every request, tagged races or none, and a chart that cannot be built
    (a session with no saved load row, a naive or unparseable stored ``start_time`` on any session)
    is a 500 with the chart's message, which withholds threshold pace too. The Riegel predicate,
    bound to the instant, the chart's fitness lookup and the zone, decides which races' F013
    features are read (each at most once per request, shared with the threshold read).
    ``metrics.riegel_b.estimate`` then screens and fits the races (F029 reference sections 2 and
    3), and the two entries are served threshold first.
    """
    config = db._load_config_cached()
    zone = ZoneInfo(config.athlete_timezone)
    now = _utcnow()
    try:
        instant = parse_as_of(as_of, zone, now)
    except ValueError as exc:
        raise RequestValidationError(
            [{"type": "value_error", "loc": ("query", "as_of"), "msg": f"Value error, {exc}", "input": as_of}]
        ) from exc
    today = now.astimezone(zone).date()
    fitness: list[Callable[[datetime.date], tuple]] = []

    def make_needs_features(load_rows: list) -> Callable[[dict], bool]:
        try:
            chart = load_chart.build_chart(load_rows, zone, today)
        except ValueError as exc:
            raise HTTPException(500, str(exc)) from exc
        fitness.append(_riegel_fitness(chart))
        return functools.partial(riegel_b.needs_features, instant=instant, fitness=fitness[0], zone=zone)

    conn = db.get_connection()
    try:
        inputs = db.read_state_inputs(conn, config.threshold_workout_names, make_needs_features)
    finally:
        conn.close()

    result = threshold_pace.estimate(inputs.threshold_rows, instant)
    riegel = riegel_b.estimate(inputs.riegel_rows, instant, fitness[0], zone)
    return StateModel(
        as_of=result.as_of,
        timezone=zone.key,
        determinants=[_threshold_determinant(result, inputs.threshold_unreadable), _riegel_determinant(riegel)],
        readiness=None,
        gap_to_goal=None,
    )


# ---------------------------------------------------------------------------
# PUT /sessions/{session_id}/laps (F025)
#
# Appended below the earlier routes with its own imports, as the routes
# above are, so the lines cited by number stay in place. The decode is
# ingest's (pipeline.read_laps); db.attach_session_laps writes in one
# BEGIN IMMEDIATE transaction.
# ---------------------------------------------------------------------------
from runcoach_api.ingestion import pipeline as ingest_pipeline
from runcoach_api.schemas import SessionLapsAttached


@app.put(
    "/sessions/{session_id}/laps",
    response_model=SessionLapsAttached,
    operation_id="attachSessionLaps",
    tags=["Sessions"],
    responses={
        400: {"description": "A file POST /sessions refuses with 400; the detail is the one it gives"},
        404: {"description": "No stored session has that session_id; the file is not decoded"},
        409: {"description": "The file is another session's; the detail names both ids and both start instants"},
        413: {"description": "The file exceeds the upload limit or a decode ceiling, as POST /sessions answers"},
    },
)
def attach_session_laps(
    session_id: str,
    file: UploadFile = File(...),  # noqa: B008 -- FastAPI's parameter idiom
) -> SessionLapsAttached:
    """Read a stored run's laps and workout steps from its original file (F025 reference section 4).

    In order: an unknown ``session_id`` is a 404, before the file is read or decoded; a file over
    ``MAX_UPLOAD_BYTES`` is the 413 ``POST /sessions`` gives; the file is decoded as ingest decodes
    it (``pipeline.read_laps``), and a file ingest refuses gets ``POST /sessions``'s status and
    detail through ``http_status_detail`` (400, 413); a file whose derived session id
    (``mapping.derive_session_id`` of its device and start) is not ``session_id`` is a 409 naming
    both ids and both start instants. Otherwise ``db.attach_session_laps`` replaces the session's
    ``laps`` and ``workout_steps`` rows and sets ``laps_read_at``, writing no other table or
    column, and the 200 carries the counts. Repeating the call gives the same rows.
    """
    conn = db.get_connection()
    try:
        stored_start = db.session_start_time(conn, session_id)
    finally:
        conn.close()
    if stored_start is None:
        raise HTTPException(404, f"session {session_id} not found")

    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"upload exceeds {MAX_UPLOAD_BYTES} byte limit")
    try:
        read = ingest_pipeline.read_laps(raw)
    except (NotAFitFileError, FitParseFailure, TooManyRecordsError, MissingCanonicalFieldError) as exc:
        status, detail = http_status_detail(exc)
        raise HTTPException(status, detail) from exc
    if read.session_id != session_id:
        raise HTTPException(
            409,
            f"the file is session {read.session_id} (start {read.start_time}), not session {session_id} "
            f"(start {stored_start}); nothing was written",
        )

    conn = db.get_connection()
    try:
        attached = db.attach_session_laps(conn, session_id, read.laps, read.steps)
    finally:
        conn.close()
    if not attached:
        raise HTTPException(404, f"session {session_id} not found")
    return SessionLapsAttached(session_id=session_id, laps=len(read.laps), workout_steps=len(read.steps))


# ---------------------------------------------------------------------------
# POST /laps/candidates (F025)
#
# Appended below the earlier routes with its own imports, as the routes
# above are, so the lines cited by number stay in place. The decision lives
# in lap_candidates, with race_tags' start-second lookup; the rows are read
# by db.read_lap_candidate_rows in a transaction it rolls back.
# ---------------------------------------------------------------------------
from runcoach_api import lap_candidates
from runcoach_api.schemas import LapCandidatesRequest, LapCandidatesResult


@app.post(
    "/laps/candidates",
    response_model=LapCandidatesResult,
    operation_id="matchLapCandidates",
    tags=["Sessions"],
)
def match_lap_candidates(request: LapCandidatesRequest) -> LapCandidatesResult:
    """Match listed Connect runs to stored runs by start second (F025 reference section 5 step 2).

    ``LapCandidatesRequest`` screens the whole body first (a 422 reads nothing). Each item is
    answered ``matched`` (exactly one stored running session at its start second, resting captures
    excluded, with that session's ``laps_read_at``), ``not_stored`` or ``ambiguous``, in request
    order. Distance and duration are never compared: the attach endpoint's identity check on the
    file proves the match. Read-only: nothing is written.
    """
    items = [item.model_dump() for item in request.items]
    conn = db.get_connection()
    try:
        rows = db.read_lap_candidate_rows(conn, [item["start_time"] for item in items])
    finally:
        conn.close()
    return LapCandidatesResult(items=lap_candidates.decide(items, rows))
