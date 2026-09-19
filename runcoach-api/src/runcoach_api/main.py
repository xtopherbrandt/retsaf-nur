import datetime
from contextlib import asynccontextmanager
from dataclasses import replace
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from runcoach_api import __version__, db
from runcoach_api.ingestion.exceptions import (
    DuplicateSessionError,
    FitParseFailure,
    MissingCanonicalFieldError,
    NotAFitFileError,
    TooManyRecordsError,
)
from runcoach_api.ingestion.pipeline import ingest_fit_bytes
from runcoach_api.metrics import hrv_trend
from runcoach_api.schemas import (
    Band,
    Baseline,
    ExcludedReading,
    HealthResponse,
    HrvPoint,
    HrvTrendResponse,
    IncludedReading,
    IngestResponse,
    Thresholds,
)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

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
    this feature's local-first single-athlete deployment model.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.precheck_ceiling = max_bytes + _MULTIPART_FRAMING_ALLOWANCE_BYTES

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
            if declared_bytes is not None and declared_bytes > self.precheck_ceiling:
                response = PlainTextResponse(
                    f"upload exceeds {self.max_bytes} byte limit", status_code=413
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.get_connection()
    try:
        db.init_schema(conn)
    finally:
        conn.close()
    yield


app = FastAPI(title="Run Coaching API", lifespan=lifespan)
app.add_middleware(ContentLengthLimitMiddleware, max_bytes=MAX_UPLOAD_BYTES)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@app.post("/sessions", response_model=IngestResponse, status_code=201)
def create_session(
    file: UploadFile = File(...),
    resting_capture: bool = Form(False),
) -> IngestResponse:
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
    """
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"upload exceeds {MAX_UPLOAD_BYTES} byte limit")
    try:
        result = ingest_fit_bytes(raw, resting_capture_override=resting_capture)
    except NotAFitFileError as exc:
        raise HTTPException(400, f"not a valid FIT file: {exc}") from exc
    except FitParseFailure as exc:
        raise HTTPException(400, f"file could not be parsed: {exc}") from exc
    except TooManyRecordsError as exc:
        # 413, not 400 and not a quality flag: "this upload is too
        # large" stays one semantic bucket whichever dimension trips it
        # -- the byte cap or any of the parser's three decode ceilings.
        # One bucket, one status, but ``exc.unit`` names the quantity
        # that actually overflowed, so a beat overrun is not reported as
        # a record overrun in a file that carries no records at all.
        raise HTTPException(
            413,
            f"upload exceeds the limit of {exc.limit} {exc.unit} "
            f"(reached {exc.count})",
        ) from exc
    except MissingCanonicalFieldError as exc:
        raise HTTPException(400, f"{exc.field} could not be determined: {exc}") from exc
    except DuplicateSessionError as exc:
        raise HTTPException(
            409, f"already ingested as session {exc.existing_session_id}"
        ) from exc
    return IngestResponse(session_id=result.session_id, quality_flags=result.quality_flags)


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
#: sustained-tier-change rule (T092) resolves the tier over the *previous*
#: baseline window ``[D-126, D-67]`` and compares, and a read that stops at
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
    verdict is unavailable for an unrelated reason). The verdict and
    ``below_by`` are withheld; everything that *produced* them -- the band,
    the baseline, ``readings_in_window``, the week's mean -- is left as
    computed, because the band is a property of the baseline ``[d-66, d-7]``
    (which lies wholly in the past) and the contract's ``points[]`` draws it
    on days with no reading (T091), and because the response must still be
    reproducible by hand (``research/00`` §1.6).

    **``unavailable_reason`` (T137).** Overridden to ``REASON_DAY_NOT_HAPPENED``
    whenever this withholds, regardless of whichever of ``judge``'s own four
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

    **T151's bridge, owned by T155.** ``build_series`` now returns one
    dataset per source tier (F006), and ``judge`` still takes one dataset.
    Until T155 lands F006's selection (highest-fidelity judgeable dataset,
    skipped past on baseline-window staleness), the dataset handed to
    ``judge`` and rendered is the one F005's retired resolver would have
    picked, flattened onto the F005 series shape by
    ``hrv_trend.select_by_retired_resolver``. T155 replaces that call with
    the selector and removes the view; nothing else here is a function of
    which dataset was chosen.

    Raises ``OverflowError`` where a day's windows reach past the calendar's
    origin; the route names that as the parameters' problem."""
    judged = []
    for offset in range((to - from_).days + 1):
        day = from_ + datetime.timedelta(days=offset)
        datasets = hrv_trend.build_series(rows, zone, day, earliest_start_time)
        series = hrv_trend.select_by_retired_resolver(datasets)  # T151 bridge; T155 removes
        judged.append((series, _withhold_future(hrv_trend.judge(series), day, today)))
    return judged


def _point(series: hrv_trend.SingleDatasetView, verdict: hrv_trend.HrvVerdict) -> HrvPoint:
    """One day of the contract's ``points[]``: the reading the day's own
    series holds for it (null when none) and the band the day's own baseline
    asserts (all three null together when it cannot build one). The band
    comes from ``judge`` -- the same call that decides ``to``'s verdict -- so
    the last point and ``band`` are the same floats, not two computations."""
    reading = next((r for r in series.series if r.date == series.target_date), None)
    band = verdict.band
    return HrvPoint(
        date=series.target_date,
        ln_rmssd=None if reading is None else hrv_trend.ln_rmssd(reading),
        baseline=None if band is None else band.mean,
        swc_low=None if band is None else band.lo,
        swc_high=None if band is None else band.hi,
    )


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
    """
    span_first = hrv_trend.baseline_window(series.target_date)[0]
    band = verdict.band
    return HrvTrendResponse(
        date=series.target_date,
        from_=from_,
        timezone=series.timezone,
        points=points,
        verdict=verdict.verdict,
        unavailable_reason=verdict.unavailable_reason,
        ln_rmssd_7d_mean=verdict.ln_rmssd_7d_mean,
        below_by=verdict.below_by,
        band=None
        if band is None
        else Band(mean=band.mean, half_width=band.half_width, lo=band.lo, hi=band.hi, floored=band.floored),
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
        ),
    )


@app.get("/metrics/hrv", response_model=HrvTrendResponse, operation_id="getHrvTrend")
def get_hrv_trend(
    from_: datetime.date | None = Query(None, alias="from"),  # noqa: B008 -- FastAPI's parameter idiom
    to: datetime.date | None = Query(None),  # noqa: B008
) -> HrvTrendResponse:
    """The resting-HRV trend verdict for local day ``to`` (F005, spec §3.7)
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
