"""``runcoach import`` and ``runcoach import-status`` -- run and report a whole-archive import.

``runcoach import PATH`` uploads a zip (Garmin's "Export your data" archive, or any zip of ``.fit``
files) to ``POST /imports``. When PATH is a directory, every file under it is written, recursively
and with names relative to it, into a zip in the system temp directory (outside PATH); that zip is
uploaded under the directory's name, which the API keeps as ``archive_name``, and deleted whatever
the outcome. The API screens the contents as it does for any archive.

The command prints the import id, then polls ``GET /imports/{id}`` every ``POLL_SECONDS``. It prints
a line when the status changes and a line each time ``activities_processed`` passes a multiple of
100, and at the end the report: stored runs, stored resting captures and stored nights (told apart
by the stored rows' ``activity_tag``; the nights only when there are any), replaced night summaries
and files with no night (each only when non-zero), already stored, skipped by sport, failed (then
one line per failed row: entry, status, detail), the non-activity total, not-FIT by extension,
unreadable inner archives. A Strava archive's import (``source`` ``strava``) reports instead the
``counts.strava`` figures on one line, in the F030 reference section 3 order, then one line per
``failed``, ``ambiguous``, ``multisport_not_stored`` and ``deleted_in_strava`` row: its outcome,
its ``time_created`` as a local date (``sync._local_date``), its Strava name (read from
``GET /strava-activities/{id}``; ``-`` when that answers 404 or the name is null), and for a failed
row its status and detail, for another its detail when it has one. A row that did not parse (entry
``activities.csv line <n>``) prints its entry and the raw cells its ``detail`` holds. Then, unless
``excluded_from_load`` and ``restored_to_load`` are both 0 and ``load_changes`` is empty, a line
with those two counts and one line per ``load_changes`` entry in the served order: ``excluded`` or
``restored``, the served ``local_date`` (already the athlete's date), the Strava type (``-`` when
the run is no longer linked) and the old load to 1 decimal (``no load`` when null). An API that
stores nights counts them in its progress total, so the
progress lines then say "activities and nights"; an older API's lines read "activities".

Exit codes: 0 when the import ends ``completed`` (failed rows included); 1 when it ends ``failed`` or
``interrupted``, when a file under a directory cannot be read, when the upload is refused (non-2xx),
the API is unreachable or a body cannot be understood, and after ``MAX_FAILED_POLLS`` consecutive
polls that each failed in one of those ways (the job may still be running, so the status hint is
printed). Ctrl-C stops only the CLI: it prints the hint naming ``runcoach import-status <id>`` and
exits 130; the job keeps running in the API.

``runcoach import-status [ID]`` prints the status and the same report once, for ID or else the
newest import ``GET /imports`` lists.

``sleep`` and ``POLL_SECONDS`` are module attributes so tests can patch them.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import typer

from runcoach_cli import config
from runcoach_cli.api_client import (
    ApiUnreachableError,
    get_import,
    get_strava_activity,
    list_imports,
    start_import,
)
from runcoach_cli.commands import sync
from runcoach_cli.commands.profile import _fail
from runcoach_cli.commands.sessions import _printable, _unparseable, _write_utf8

POLL_SECONDS = 2
MAX_FAILED_POLLS = 3
PROGRESS_STEP = 100
TERMINAL = {"completed", "failed", "interrupted"}
# The activity tags the HRV classifier gives a resting capture (F020 reference section 3).
RESTING_TAGS = {"resting_hrv_check", "health_snapshot"}
# The activity tag of a night stored from an HRV_STATUS wellness file.
NIGHT_TAG = "overnight_hrv"
# The count keys an API that stores nights always serves (0 when none); an older API serves neither.
NIGHT_COUNT_KEYS = ("replaced", "no_night")
NULL = "-"
# A Strava archive's import (``Import.source``).
STRAVA_SOURCE = "strava"
# The ``counts.strava`` figures the report prints, in the F030 reference section 3 order: each row
# outcome, then the warned rows.
STRAVA_COUNT_KEYS = (
    "attached",
    "attached_by_file_start",
    "stored",
    "stored_retyped",
    "already_stored",
    "ambiguous",
    "multisport_not_stored",
    "not_fit",
    "no_file",
    "not_running",
    "failed",
    "unchanged",
    "updated",
    "deleted_in_strava",
    "distance_differs",
)
# The Strava row outcomes the report lists one line each for.
STRAVA_LISTED = frozenset({"failed", "ambiguous", "multisport_not_stored", "deleted_in_strava"})
# The ``entry`` of a Strava row that did not parse: ``activities.csv line <n>``.
UNPARSED_ENTRY = "activities.csv line "
# A load change's old load when the saved body had none (a ``no_hr`` run; every restored entry).
NO_LOAD = "no load"

sleep = time.sleep


class _PollFailed(Exception):
    """One request whose answer could not be used: unreachable, non-2xx, or an unparseable body."""


def _unreachable_message(api_url: str) -> str:
    return f"could not reach API at {api_url}. Is it running? Start it with `runcoach-api`."


def _hint(import_id: str) -> str:
    return f"Import {import_id} keeps running in the API; `runcoach import-status {import_id}` shows it."


def _body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _PollFailed(f"the API's response could not be understood: {exc}") from None


def _import_body(resp: httpx.Response) -> dict[str, Any]:
    """An ``Import`` body from a 2xx response, or raise ``_PollFailed`` naming what went wrong."""
    if not 200 <= resp.status_code < 300:
        raise _PollFailed(f"API returned {resp.status_code}: {resp.text}")
    body = _body(resp)
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("import_id"), str)
        or not isinstance(body.get("status"), str)
        or not isinstance(body.get("progress") or {}, dict)
    ):
        raise _PollFailed("the API's response could not be understood: not an import")
    return body


def _fetch(api_url: str, import_id: str) -> dict[str, Any]:
    try:
        resp = get_import(api_url, import_id)
    except ApiUnreachableError:
        raise _PollFailed(_unreachable_message(api_url)) from None
    return _import_body(resp)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _counts_nights(body: dict[str, Any]) -> bool:
    """Whether the API stores nights, so its progress counts nights among the activities."""
    counts = body.get("counts")
    return isinstance(counts, dict) and any(key in counts for key in NIGHT_COUNT_KEYS)


def _total_noun(body: dict[str, Any]) -> str:
    return "activities and nights" if _counts_nights(body) else "activities"


def _status_line(body: dict[str, Any]) -> str:
    status = body["status"]
    total = _int((body.get("progress") or {}).get("activities_total"))
    if status == "running" and total is not None:
        return f"Status: {status} ({total} {_total_noun(body)})"
    return f"Status: {status}"


def _processed(body: dict[str, Any]) -> tuple[int | None, int | None]:
    progress = body.get("progress") or {}
    return _int(progress.get("activities_processed")), _int(progress.get("activities_total"))


def _processed_line(body: dict[str, Any], processed: int, total: int | None) -> str:
    """``Processed X of N``, naming nights when the API counts them in N; an older API's N is bare."""
    if total is None:
        return f"Processed {processed}"
    noun = f" {_total_noun(body)}" if _counts_nights(body) else ""
    return f"Processed {processed} of {total}{noun}"


def _by_key(counts: dict[str, Any], key: str) -> tuple[int, str]:
    """The total of a ``{name: n}`` count and its ``" (a 1, b 2)"`` breakdown, sorted by name."""
    mapping = counts.get(key) or {}
    if not isinstance(mapping, dict) or any(_int(n) is None for n in mapping.values()):
        raise TypeError(f"counts.{key}")
    total = sum(mapping.values())
    detail = ", ".join(f"{_printable(str(name))} {n}" for name, n in sorted(mapping.items()))
    return total, f" ({detail})" if detail else ""


def _count(counts: dict[str, Any], key: str) -> int:
    value = counts.get(key, 0)
    if value is None:
        return 0
    if _int(value) is None:
        raise TypeError(f"counts.{key}")
    return value


def _is_strava(body: dict[str, Any]) -> bool:
    return body.get("source") == STRAVA_SOURCE


def _report_rows(body: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """The body's ``counts`` (``{}`` when null), its ``files[]`` rows and whether ``files`` was served."""
    counts = body.get("counts")
    if counts is None:
        counts = {}
    if not isinstance(counts, dict):
        raise TypeError("counts")
    files = body.get("files")
    if files is not None and not isinstance(files, list):
        raise TypeError("files")
    return counts, [row for row in (files or []) if isinstance(row, dict)], files is not None


def _unparsed(row: dict[str, Any]) -> bool:
    """A Strava row that did not parse: its ``entry`` names the CSV line, its ``detail`` the cells."""
    return str(row.get("entry")).startswith(UNPARSED_ENTRY)


def _listed_strava_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("outcome") in STRAVA_LISTED]


def _name_ids(body: dict[str, Any]) -> list[int]:
    """The Activity IDs whose names a Strava report prints, once each, in row order."""
    _, rows, _ = _report_rows(body)
    ids: list[int] = []
    for row in _listed_strava_rows(rows):
        aid = _int(row.get("strava_activity_id"))
        if aid is not None and not _unparsed(row) and aid not in ids:
            ids.append(aid)
    return ids


def _strava_lines(
    counts: dict[str, Any],
    rows: list[dict[str, Any]],
    names: dict[int, str | None],
    body_changes: Any = None,
) -> list[str]:
    """A Strava import's report: the ``counts.strava`` figures on one line, then one line per
    failed, ambiguous, multisport and deleted row (its local date and name; a row that did not
    parse, its entry and the raw cells its ``detail`` holds), then ``_load_lines`` for
    ``body_changes`` (the body's ``load_changes``)."""
    strava = counts.get("strava")
    if strava is None:
        strava = {}
    if not isinstance(strava, dict):
        raise TypeError("counts.strava")
    lines = ["Strava: " + ", ".join(f"{key} {_count(strava, key)}" for key in STRAVA_COUNT_KEYS)]
    for row in _listed_strava_rows(rows):
        outcome = str(row["outcome"])
        detail = row.get("detail")
        if _unparsed(row):
            cells = [
                outcome,
                _printable(str(row.get("entry"))),
                NULL if detail is None else _printable(str(detail)),
            ]
            lines.append("  " + "  ".join(cells))
            continue
        when = row.get("time_created")
        aid = _int(row.get("strava_activity_id"))
        name = names.get(aid) if aid is not None else None
        cells = [
            outcome,
            NULL if when is None else sync._local_date(when).isoformat(),
            _printable(name) if name else NULL,
        ]
        if outcome == "failed":
            status = row.get("status")
            cells.append(NULL if status is None else str(status))
            cells.append(NULL if detail is None else _printable(str(detail)))
        elif detail is not None:
            cells.append(_printable(str(detail)))
        lines.append("  " + "  ".join(cells))
    return lines + _load_lines(strava, body_changes)


def _load_change_line(change: Any) -> str:
    """``  <change>  <local_date>  <strava_type>  <old_load>`` for one ``load_changes`` entry."""
    if not isinstance(change, dict):
        raise TypeError("load_changes")
    kind, local_date = change.get("change"), change.get("local_date")
    strava_type, old_load = change.get("strava_type"), change.get("old_load")
    if (
        not isinstance(kind, str)
        or not isinstance(local_date, str)
        or (strava_type is not None and not isinstance(strava_type, str))
        or (old_load is not None and (isinstance(old_load, bool) or not isinstance(old_load, int | float)))
    ):
        raise TypeError("load_changes")
    cells = [
        _printable(kind),
        _printable(local_date),
        NULL if strava_type is None else _printable(strava_type),
        NO_LOAD if old_load is None else f"{old_load:.1f}",
    ]
    return "  " + "  ".join(cells)


def _load_lines(strava: dict[str, Any], changes: Any) -> list[str]:
    """The ``excluded_from_load`` and ``restored_to_load`` counts and one line per ``load_changes``
    entry, in the served order; nothing when both counts are 0 and there is no entry."""
    if changes is None:
        changes = []
    if not isinstance(changes, list):
        raise TypeError("load_changes")
    excluded, restored = _count(strava, "excluded_from_load"), _count(strava, "restored_to_load")
    if not (excluded or restored or changes):
        return []
    lines = [f"Load: excluded_from_load {excluded}, restored_to_load {restored}"]
    return lines + [_load_change_line(change) for change in changes]


def _report_lines(body: dict[str, Any], names: dict[int, str | None] | None = None) -> list[str]:
    """The summary of an ``Import`` body, or raise ``ValueError``/``KeyError``/``TypeError``.

    A Strava import's summary is ``_strava_lines``, naming each listed row from ``names`` (its
    Activity ID to the row's ``name``); any other import's is the Garmin report.
    """
    counts, rows, served = _report_rows(body)
    if _is_strava(body):
        return _strava_lines(counts, rows, names or {}, body.get("load_changes"))

    stored = _count(counts, "stored")
    if not served:
        stored_line = f"Stored: {stored}"
    else:
        stored_tags = [row.get("activity_tag") for row in rows if row.get("outcome") == "stored"]
        captures = sum(1 for tag in stored_tags if tag in RESTING_TAGS)
        nights = sum(1 for tag in stored_tags if tag == NIGHT_TAG)
        stored_line = f"Stored: {stored - captures - nights} runs, {captures} resting captures"
        if nights:
            stored_line += f", {nights} nights"
    replaced = _count(counts, "replaced")
    no_night = _count(counts, "no_night")
    skipped, skipped_detail = _by_key(counts, "skipped_sport")
    non_activity, _ = _by_key(counts, "non_activity")
    not_fit, not_fit_detail = _by_key(counts, "not_fit")
    unreadable = counts.get("unreadable_archives") or []
    if not isinstance(unreadable, list):
        raise TypeError("counts.unreadable_archives")

    lines = [stored_line]
    if replaced:
        # When both summaries of a night are in one archive the earlier one counts as stored, so the
        # line says a replaced night is not another night.
        lines.append(
            f"Replaced: {replaced} (night summaries replaced by a later summary of the same night, "
            "not extra nights)"
        )
    if no_night:
        lines.append(f"No night: {no_night}")
    lines += [
        f"Already stored: {_count(counts, 'already_stored')}",
        f"Skipped by sport: {skipped}{skipped_detail}",
        f"Failed: {_count(counts, 'failed')}",
    ]
    for row in rows:
        if row.get("outcome") != "failed":
            continue
        status = row.get("status")
        detail = row.get("detail")
        lines.append(
            "  "
            + "  ".join(
                [
                    _printable(str(row.get("entry"))),
                    NULL if status is None else str(status),
                    NULL if detail is None else _printable(str(detail)),
                ]
            )
        )
    lines.append(f"Non-activity files: {non_activity}")
    lines.append(f"Not FIT: {not_fit}{not_fit_detail}")
    lines.append(f"Unreadable archives: {len(unreadable)}")
    for item in unreadable:
        if isinstance(item, dict):
            lines.append(f"  {_printable(str(item.get('label')))}: {_printable(str(item.get('reason')))}")
        else:
            lines.append(f"  {_printable(str(item))}")
    return lines


def _strava_name(api_url: str, activity_id: int) -> str | None:
    """The ``name`` ``GET /strava-activities/{id}`` serves; ``None`` on a 404 or a null name."""
    try:
        resp = get_strava_activity(api_url, activity_id)
    except ApiUnreachableError:
        raise _fail(_unreachable_message(api_url)) from None
    if resp.status_code == 404:
        return None
    try:
        body = _ok_body(resp)
    except _PollFailed as exc:
        raise _fail(str(exc)) from None
    name = body.get("name") if isinstance(body, dict) else None
    if name is not None and not isinstance(name, str):
        raise _unparseable(f"strava activity {activity_id} name")
    return name


def _ok_body(resp: httpx.Response) -> Any:
    """A 2xx response's JSON body, or raise ``_PollFailed`` naming what went wrong."""
    if not 200 <= resp.status_code < 300:
        raise _PollFailed(f"API returned {resp.status_code}: {resp.text}")
    return _body(resp)


def _print_report(body: dict[str, Any], api_url: str) -> None:
    try:
        ids = _name_ids(body) if _is_strava(body) else []
    except (KeyError, TypeError, ValueError) as exc:
        raise _unparseable(f"missing or malformed {exc}") from None
    names = {aid: _strava_name(api_url, aid) for aid in ids}
    try:
        lines = _report_lines(body, names)
    except (KeyError, TypeError, ValueError) as exc:
        raise _unparseable(f"missing or malformed {exc}") from None
    for line in lines:
        _write_utf8(line)


def _end(body: dict[str, Any], api_url: str) -> None:
    """Print the final report of a terminal import and exit with its code."""
    _print_report(body, api_url)
    import_id = body["import_id"]
    if body["status"] == "failed":
        raise _fail(f"import {import_id} failed: {_printable(str(body.get('error')))}")
    if body["status"] == "interrupted":
        raise _fail(
            f"import {import_id} was interrupted (the API stopped while it ran); "
            "its report covers the files done. Run `runcoach import` again to finish it."
        )


def _raise(exc: OSError) -> None:
    """``os.walk``'s ``onerror``: a folder that cannot be listed stops the walk."""
    raise exc


def _zip_directory(directory: Path) -> Path:
    """Write every file under ``directory`` (relative names) to a new temp zip outside it.

    A file dated before 1980, which a zip cannot hold, is stored with 1980-01-01 00:00. A file
    that cannot be read, or a folder that cannot be listed, raises ``OSError``, and the temp zip
    is removed (``Path.rglob`` would skip such a folder silently). The walk does not follow a
    symlinked directory.
    """
    walked = (Path(root) / name for root, _dirs, files in os.walk(directory, onerror=_raise) for name in files)
    entries = sorted(p for p in walked if p.is_file())
    fd, name = tempfile.mkstemp(prefix="runcoach-import-", suffix=".zip")
    os.close(fd)
    target = Path(name)
    try:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
            for entry in entries:
                zf.write(entry, entry.relative_to(directory).as_posix())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def _upload(api_url: str, path: Path) -> dict[str, Any]:
    """Upload PATH (a zip, or a directory zipped on the fly) and return the queued ``Import``."""
    zipped: Path | None = None
    try:
        if path.is_dir():
            try:
                zipped = _zip_directory(path)
            except OSError as exc:
                raise _fail(f"could not zip {_printable(str(path))}: {_printable(str(exc))}") from None
            resp = start_import(api_url, zipped, upload_name=path.resolve().name)
        else:
            resp = start_import(api_url, path)
    except ApiUnreachableError:
        raise _fail(_unreachable_message(api_url)) from None
    finally:
        if zipped is not None:
            zipped.unlink(missing_ok=True)
    try:
        return _import_body(resp)
    except _PollFailed as exc:
        raise _fail(str(exc)) from None


def import_archive(
    path: Path = typer.Argument(..., exists=True, help="A zip of FIT files, or a directory to zip."),  # noqa: B008 -- Typer's parameter idiom
) -> None:
    """Import every activity in a Garmin export zip (or a zip or directory of FIT files).

    Runs, resting captures and overnight HRV nights (from HRV_STATUS files) are stored; other
    sports are skipped and listed, and an HRV_STATUS file with no night is counted, not stored.
    Resting-HRV captures become HRV readings only when their activity profile is named in
    resting_hrv_profile_names in the API's config: set it before importing, then restart the
    API, which reads its config once per process.
    """
    cfg = config.load_config()
    started = _upload(cfg.api_url, path)
    import_id = started["import_id"]
    _write_utf8(f"Import {import_id} started ({_printable(str(started.get('archive_name')))}).")

    last_status: str | None = None
    last_step = 0
    failures = 0
    body = started
    try:
        while True:
            if body["status"] != last_status:
                last_status = body["status"]
                _write_utf8(_status_line(body))
            processed, total = _processed(body)
            if processed is not None and processed // PROGRESS_STEP > last_step:
                last_step = processed // PROGRESS_STEP
                _write_utf8(_processed_line(body, processed, total))
            if body["status"] in TERMINAL:
                break
            sleep(POLL_SECONDS)
            try:
                body = _fetch(cfg.api_url, import_id)
                failures = 0
            except _PollFailed as exc:
                failures += 1
                if failures >= MAX_FAILED_POLLS:
                    typer.echo(f"Error: {exc}", err=True)
                    raise _fail(f"{MAX_FAILED_POLLS} polls in a row failed. {_hint(import_id)}") from None
    except KeyboardInterrupt:
        _write_utf8(_hint(import_id))
        raise typer.Exit(code=130) from None
    _end(body, cfg.api_url)


def _newest_id(api_url: str) -> str | None:
    try:
        resp = list_imports(api_url)
    except ApiUnreachableError:
        raise _fail(_unreachable_message(api_url)) from None
    if resp.status_code != 200:
        raise _fail(f"API returned {resp.status_code}: {resp.text}")
    try:
        body = _body(resp)
    except _PollFailed as exc:
        raise _fail(str(exc)) from None
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise _unparseable("the listing has no items list")
    if not body["items"]:
        return None
    newest = body["items"][0]
    if not isinstance(newest, dict) or not isinstance(newest.get("import_id"), str):
        raise _unparseable("the newest import has no import_id")
    return newest["import_id"]


def import_status(
    import_id: str | None = typer.Argument(None, help="The import id; the newest import when omitted."),
) -> None:
    """Show an import's status and report, once (the newest import when no ID is given).

    Resting-HRV captures become HRV readings only when their activity profile is named in
    resting_hrv_profile_names in the API's config: set it before importing, then restart the
    API, which reads its config once per process.
    """
    cfg = config.load_config()
    if import_id is None:
        import_id = _newest_id(cfg.api_url)
        if import_id is None:
            typer.echo("No imports.")
            return
    try:
        body = _fetch(cfg.api_url, import_id)
    except _PollFailed as exc:
        raise _fail(str(exc)) from None
    _write_utf8(f"Import {body['import_id']} ({_printable(str(body.get('archive_name')))})")
    _write_utf8(_status_line(body))
    processed, total = _processed(body)
    if body["status"] == "running" and processed is not None:
        _write_utf8(_processed_line(body, processed, total))
    if body["status"] in TERMINAL:
        _end(body, cfg.api_url)
    else:
        _print_report(body, cfg.api_url)
