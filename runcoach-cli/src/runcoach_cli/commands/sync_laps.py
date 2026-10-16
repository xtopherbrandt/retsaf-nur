"""``runcoach sync laps --since YYYY-MM-DD [--until YYYY-MM-DD]``: attach laps and workout steps to
stored runs from their Connect originals (F025 reference section 5).

The command is registered on ``sync.sync_app`` and reads every seam through ``sync`` at call time
(``sync._adapter``, ``sync.today``, ``sync._library_missing``), so tests replace them there.

1. ``GET /openapi.json`` must list ``attachSessionLaps`` and ``matchLapCandidates``; a reply other
   than 200 names its status, an API without both operations is too old. Nothing else is asked
   of the API or Garmin before this check passes.
2. One Connect listing for ``[since, until]`` (``until`` defaults to today), read with F022's rules
   (``sync._read_summary``, ``sync._is_fetched_type``): fetched-type activities whose local start
   is in the range, once per ``activityId``, oldest first by local start then id. A summary that
   cannot be read, or whose ``startTimeGMT`` cannot (``sync._read_gmt``), is ``failed`` and never
   sent; a manual activity is ``no FIT original`` and never sent or downloaded.
3. ``POST /laps/candidates`` in batches of ``CANDIDATES_BATCH``, each item
   ``{connect_activity_id, start_time}`` (UTC, to the second, ``Z``). A reply that is not a 200
   answering every item of its batch, in order, with ``matched`` (and a session id),
   ``not_stored`` or ``ambiguous`` stops the run.
4. For each ``matched`` item whose ``laps_read_at`` is null (one already read is ``already read``,
   with no download): the ORIGINAL is downloaded with F022's download rules (``sync._download``:
   a 404 is ``no FIT original``) and read with the shared entry reader (``sync.read_zip_entries``;
   this module never opens a zip itself). It must hold exactly one non-directory entry whose name
   ends ``.fit`` (any case), else the item is ``failed`` (``zip_entries``); a failure of the
   download or of that entry is ``failed`` with the reader's code and detail. The entry is sent to
   ``PUT /sessions/{session_id}/laps``, whose reply is classified here, never by F022's
   ``_classify`` (which reads a 409 as already stored): 200 attached, 409 identity mismatch, 400 and
   413 ``file_refused`` (a permanent failure), anything else a temporary failure.

The report is the ``From`` line, the counts line (``OUTCOMES`` in order) and one line per identity
mismatch or failed item, in the order handled. A stop (a login needed, a Garmin rate limit or no
reply, any error on the listing, a web page for a download, the API unreachable or answering the
candidates other than as above) prints the report so far and its message with the one
``runcoach sync laps --since`` command, and exits 1; so does a run with any failed or mismatched
item. The command never reads or writes the sync state and never writes a race tag.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx
import typer

from runcoach_cli import api_client, config, garmin
from runcoach_cli.commands import sessions, sync
from runcoach_cli.commands.profile import _fail
from runcoach_cli.commands.sessions import _write_utf8

# The operations the API must list (F025 reference section 5).
OPERATIONS = ("attachSessionLaps", "matchLapCandidates")
OLD_API_LAPS = "the API does not support sync laps: update it and restart the API"
# The most items one ``POST /laps/candidates`` takes; batches are sent oldest first.
CANDIDATES_BATCH = 500

ATTACHED = "attached"
ALREADY_READ = "already_read"
NOT_STORED = "not_stored"
AMBIGUOUS = "ambiguous"
IDENTITY_MISMATCH = "identity_mismatch"
NO_FIT_ORIGINAL = "no_fit_original"
FAILED = "failed"
# An item the candidates call matched to an unread session: it is downloaded and attached next.
_MATCHED = "matched"
# The outcomes the counts line prints, in its order, with the words it prints them as.
OUTCOMES = {
    ATTACHED: "attached",
    ALREADY_READ: "already read",
    NOT_STORED: "not stored",
    AMBIGUOUS: "ambiguous",
    IDENTITY_MISMATCH: "identity mismatch",
    NO_FIT_ORIGINAL: "no FIT original",
    FAILED: "failed",
}
_CANDIDATE_OUTCOMES = frozenset({_MATCHED, NOT_STORED, AMBIGUOUS})
# What a permanent failure's line says instead of a recovery command.
SAME_AGAIN = "it would fail the same way again"
ZIP_ENTRIES = "zip_entries"


class LapsStop(Exception):
    """The run cannot go on; ``str(exc)`` is the message to print."""


class _GarminStopped(Exception):
    """A Garmin error that stops the run, carried out of ``sync._download`` (which catches every
    ``GarminError``) so that its message names this command."""

    def __init__(self, error: garmin.GarminError) -> None:
        super().__init__(str(error))
        self.error = error


@dataclass
class LapItem:
    """One listed activity the command handled.

    ``local`` is its local start (``None`` only for a summary that could not be read).
    ``start_time`` is what the candidates call is sent; ``outcome`` is one of ``OUTCOMES`` once
    decided (``_MATCHED`` while it waits for its download, ``None`` before it is answered);
    ``detail`` says why an item failed or mismatched, and ``retry`` is False for a failure that
    would fail the same way again.
    """

    label: str
    local: datetime | None
    activity_id: int | None = None
    start_time: str | None = None
    outcome: str | None = None
    session_id: str | None = None
    detail: str = ""
    retry: bool = True

    @property
    def day(self) -> date | None:
        return self.local.date() if self.local is not None else None

    def fail(self, detail: str, *, retry: bool, outcome: str = FAILED) -> None:
        self.outcome, self.detail, self.retry = outcome, detail, retry


def _command(day: date) -> str:
    return f"runcoach sync laps --since {day.isoformat()}"


def _unreachable(api_url: str, day: date) -> str:
    return f"could not reach API at {api_url}: start it with `runcoach-api`, then run `{_command(day)}`"


def _garmin_stop(exc: garmin.GarminError, day: date) -> LapsStop:
    """The stop for a Garmin error on ``day``, worded as ``runcoach sync`` words it."""
    if isinstance(exc, garmin.LoginNeeded):
        return LapsStop(sync.LOGIN_NEEDED)
    reason = sync._garmin_reason(exc)
    if isinstance(exc, garmin.RateLimited):
        return LapsStop(f"{reason}: wait, then run `{_command(day)}`")
    return LapsStop(f"{reason}: run `{_command(day)}`")


def check_api(api_url: str, start: date) -> None:
    """Raise ``LapsStop`` unless the served ``/openapi.json`` lists both ``OPERATIONS``."""
    try:
        doc = sync.read_openapi(api_url)
    except api_client.ApiUnreachableError:
        raise LapsStop(_unreachable(api_url, start)) from None
    if isinstance(doc, sync.OpenApiUnread):
        raise LapsStop(f"{doc.message()}: run `{_command(start)}`")
    if any(sync._find_operation(doc, op_id) is None for op_id in OPERATIONS):
        raise LapsStop(OLD_API_LAPS)


def plan(listed: Any, start: date, end: date) -> list[LapItem]:
    """The items for a Connect listing covering ``start`` to ``end``: unreadable summaries first
    (``failed``, day unknown), then the fetched-type activities in the range, once per id, by local
    start then id; a manual one is ``no FIT original`` and one whose ``startTimeGMT`` cannot be
    read is ``failed``, neither sent. The rest have ``start_time`` set and no outcome yet."""
    if not isinstance(listed, list):
        raise LapsStop(f"Garmin's activity list could not be understood: run `{_command(start)}`")
    unreadable: list[LapItem] = []
    listed_items: list[tuple[dict[str, Any], LapItem]] = []
    seen: set[int] = set()
    for summary in listed:
        label = f"activity {sync._describe(summary)}"
        try:
            activity_id, local, activity_type = sync._read_summary(summary)
        except (TypeError, ValueError) as exc:
            item = LapItem(label=label, local=None)
            item.fail(f"unreadable summary: {exc}", retry=False)
            unreadable.append(item)
            continue
        if not sync._is_fetched_type(activity_type) or not start <= local.date() <= end:
            continue
        if activity_id in seen:
            continue
        seen.add(activity_id)
        listed_items.append((summary, LapItem(label=label, local=local, activity_id=activity_id)))
    listed_items.sort(key=lambda pair: (pair[1].local, pair[1].activity_id))
    for summary, item in listed_items:
        if summary.get("manualActivity") is True:
            item.outcome = NO_FIT_ORIGINAL
            continue
        try:
            start_utc = sync._read_gmt(summary.get("startTimeGMT"))
        except ValueError as exc:
            item.fail(str(exc), retry=False)
            continue
        item.start_time = start_utc.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
    return unreadable + [item for _, item in listed_items]


def _apply_candidates(batch: list[LapItem], resp: httpx.Response) -> bool:
    """Record a 200 reply's answers on ``batch``; False when it does not answer every item, in
    order, with a known outcome (a ``matched`` one naming its session)."""
    body = sync._body(resp)
    answers = body.get("items") if isinstance(body, dict) else None
    if not isinstance(answers, list) or len(answers) != len(batch):
        return False
    parsed: list[tuple[str, str | None]] = []
    for item, answer in zip(batch, answers, strict=True):
        if not isinstance(answer, dict) or answer.get("connect_activity_id") != item.activity_id:
            return False
        outcome, session_id, read_at = (
            answer.get("outcome"),
            answer.get("session_id"),
            answer.get("laps_read_at"),
        )
        if outcome not in _CANDIDATE_OUTCOMES:
            return False
        if outcome == _MATCHED:
            if (
                not isinstance(session_id, str)
                or not session_id
                or not (read_at is None or isinstance(read_at, str))
            ):
                return False
            if read_at is not None:
                outcome = ALREADY_READ
        parsed.append((outcome, session_id if isinstance(session_id, str) else None))
    for item, (outcome, session_id) in zip(batch, parsed, strict=True):
        item.outcome, item.session_id = outcome, session_id
    return True


def match(api_url: str, items: list[LapItem]) -> None:
    """Send every item with a ``start_time`` and no outcome to ``POST /laps/candidates``, in
    batches of ``CANDIDATES_BATCH`` oldest first, recording each answer. No items, no request."""
    to_send = [item for item in items if item.outcome is None and item.start_time is not None]
    for first in range(0, len(to_send), CANDIDATES_BATCH):
        batch = to_send[first : first + CANDIDATES_BATCH]
        day = batch[0].day or date.min
        body = [{"connect_activity_id": item.activity_id, "start_time": item.start_time} for item in batch]
        try:
            resp = api_client.post_laps_candidates(api_url, body)
        except api_client.ApiUnreachableError as exc:
            if isinstance(exc.__cause__, httpx.ReadTimeout):
                raise LapsStop(
                    f"the API at {api_url} did not answer within {api_client.LIST_TIMEOUT:g} s while "
                    f"matching runs: run `{_command(day)}`"
                ) from None
            raise LapsStop(_unreachable(api_url, day)) from None
        if resp.status_code != 200:
            raise LapsStop(f"{sessions._printable(sync._reply_detail(resp))}: run `{_command(day)}`")
        if not _apply_candidates(batch, resp):
            text = sessions._printable(resp.text[: sync._DETAIL_CHARS])
            raise LapsStop(
                f"the API's answer to the laps candidates could not be understood: {text}: run `{_command(day)}`"
            )


def _is_fit(read: sync.ZipEntry | sync.ZipFailure) -> bool:
    info = read.info
    return info is not None and not info.is_dir() and info.filename.lower().endswith(".fit")


def _one_fit_entry(data: bytes) -> sync.ZipEntry | sync.ZipFailure:
    """The download's one FIT entry, or the failure that stands for it: the reader's failure of
    the whole download or of that entry, or ``zip_entries`` when the FIT entries are not exactly
    one. A web page stops the run."""
    fits: list[sync.ZipEntry | sync.ZipFailure] = []
    with closing(sync.read_zip_entries(data, activity=True)) as reads:
        for read in reads:
            if isinstance(read, sync.ZipFailure) and read.info is None:
                if read.code == "web_page":
                    raise LapsStop(sync.WEB_PAGE)
                return read
            if _is_fit(read):
                fits.append(read)
    if len(fits) != 1:
        return sync.ZipFailure(
            ZIP_ENTRIES, f"the download holds {len(fits)} FIT entries, not one", retry=False
        )
    return fits[0]


def _original(client: garmin.GarminClient) -> Any:
    """``client.download_original``, with the errors that stop the run carried past ``sync._download``."""

    def fetch_bytes(activity_id: int) -> Any:
        try:
            return client.download_original(activity_id)
        except sync._STOPPING as exc:
            raise _GarminStopped(exc) from None

    return fetch_bytes


def _classify(item: LapItem, resp: httpx.Response) -> None:
    """Record the attach reply on ``item``: 200 attached, 409 identity mismatch, 400 and 413 the file
    refused (permanent), anything else a temporary failure."""
    status = resp.status_code
    if status == 200:
        item.outcome = ATTACHED
    elif status == 409:
        item.fail(sync._reply_detail(resp), retry=False, outcome=IDENTITY_MISMATCH)
    elif status in sync._API_PERMANENT_STATUSES:
        item.fail(f"the API refused the file: {sync._reply_detail(resp)}", retry=False)
    else:
        item.fail(sync._reply_detail(resp), retry=True)


def attach(client: garmin.GarminClient, api_url: str, item: LapItem) -> None:
    """Download ``item``'s original, read its one FIT entry and send it to the attach operation."""
    day = item.day or date.min
    try:
        data = sync._download(_original(client), item.activity_id, day)
    except _GarminStopped as exc:
        raise _garmin_stop(exc.error, day) from None
    if isinstance(data, sync._DownloadFailed):
        item.fail(data.detail, retry=data.retry)
        return
    if not data:
        item.outcome = NO_FIT_ORIGINAL
        return
    entry = _one_fit_entry(data)
    if isinstance(entry, sync.ZipFailure):
        item.fail(f"{entry.code}: {entry.detail}", retry=entry.retry)
        return
    try:
        resp = api_client.put_session_laps(api_url, item.session_id or "", entry.data)
    except api_client.ApiUnreachableError as exc:
        if isinstance(exc.__cause__, httpx.ReadTimeout):
            raise LapsStop(
                f"the API at {api_url} did not answer within {api_client.UPLOAD_TIMEOUT:g} s while "
                f"attaching laps to {sessions._printable(item.label)}; if it attached them, the next "
                f"run reports them as already read: run `{_command(day)}`"
            ) from None
        raise LapsStop(_unreachable(api_url, day)) from None
    _classify(item, resp)


def report_lines(items: list[LapItem]) -> list[str]:
    """The counts line, then one line per identity mismatch or failed item, in the order handled.
    Items not yet decided (after a stop) are not counted."""
    printable = sessions._printable
    counts = dict.fromkeys(OUTCOMES, 0)
    for item in items:
        if item.outcome in counts:
            counts[item.outcome] += 1
    lines = ["Laps: " + ", ".join(f"{OUTCOMES[key]} {n}" for key, n in counts.items())]
    for item in items:
        day = item.day.isoformat() if item.day is not None else "day unknown"
        where = f"{day}, {printable(item.label)}, {printable(item.detail)}"
        if item.outcome == IDENTITY_MISMATCH:
            lines.append(f"Identity mismatch: {where}")
        elif item.outcome == FAILED:
            tail = f"run {_command(item.day)}" if item.retry and item.day is not None else SAME_AGAIN
            lines.append(f"Failed: {where}, {tail}")
    return lines


def _parse_until(value: str, today: date, since: date) -> date:
    try:
        until = sync._parse_since(value, today)
    except typer.BadParameter as exc:
        exc.param_hint = "'--until'"
        raise
    if until < since:
        raise typer.BadParameter(f"{value} is before --since ({since.isoformat()})", param_hint="'--until'")
    return until


@sync.sync_app.command(name="laps")
def laps(
    since: str = typer.Option(
        ...,
        "--since",
        metavar="YYYY-MM-DD",
        help="The first day whose runs get their laps (required; 2016-01-01 reads them all).",
    ),
    until: str | None = typer.Option(
        None, "--until", metavar="YYYY-MM-DD", help="The last day (default today)."
    ),
) -> None:
    """Attach laps and workout steps to stored runs from their Garmin Connect originals.

    Lists Connect activities in the range, asks the API which stored run each one is, and for each
    run whose laps were never read downloads the original and sends it to the API. Runs already
    read are not downloaded. The sync state is not read or written.
    """
    sync._require_library()
    today = sync.today()
    start = sync._parse_since(since, today)
    end = _parse_until(until, today, start) if until is not None else today
    cfg = config.load_config()
    _write_utf8(f"From {start.isoformat()} to {end.isoformat()}")
    items: list[LapItem] | None = None
    try:
        check_api(cfg.api_url, start)
        try:
            client = sync._adapter().open_client()
            listed = client.list_activities(start, end)
        except garmin.GarminError as exc:
            raise _garmin_stop(exc, start) from None
        items = plan(listed, start, end)
        match(cfg.api_url, items)
        for item in items:
            if item.outcome == _MATCHED:
                attach(client, cfg.api_url, item)
    except LapsStop as exc:
        if items is not None:
            _write_utf8("\n".join(report_lines(items)))
        raise _fail(str(exc)) from None
    _write_utf8("\n".join(report_lines(items)))
    if any(item.outcome in (FAILED, IDENTITY_MISMATCH) for item in items):
        raise typer.Exit(code=1)
