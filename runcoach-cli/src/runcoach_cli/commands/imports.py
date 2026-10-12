"""``runcoach import`` and ``runcoach import-status`` -- run and report a whole-archive import.

``runcoach import PATH`` uploads a zip (Garmin's "Export your data" archive, or any zip of ``.fit``
files) to ``POST /imports``. When PATH is a directory, every file under it is written, recursively
and with names relative to it, into a zip in the system temp directory (outside PATH); that zip is
uploaded under the directory's name, which the API keeps as ``archive_name``, and deleted whatever
the outcome. The API screens the contents as it does for any archive.

The command prints the import id, then polls ``GET /imports/{id}`` every ``POLL_SECONDS``. It prints
a line when the status changes and a line each time ``activities_processed`` passes a multiple of
100, and at the end the report: stored runs and stored resting captures (told apart by the stored
rows' ``activity_tag``), already stored, skipped by sport, failed (then one line per failed row:
entry, status, detail), the non-activity total, not-FIT by extension, unreadable inner archives.

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
from runcoach_cli.api_client import ApiUnreachableError, get_import, list_imports, start_import
from runcoach_cli.commands.profile import _fail
from runcoach_cli.commands.sessions import _printable, _unparseable, _write_utf8

POLL_SECONDS = 2
MAX_FAILED_POLLS = 3
PROGRESS_STEP = 100
TERMINAL = {"completed", "failed", "interrupted"}
# The activity tags the HRV classifier gives a resting capture (F020 reference section 3).
RESTING_TAGS = {"resting_hrv_check", "health_snapshot"}
NULL = "-"

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


def _status_line(body: dict[str, Any]) -> str:
    status = body["status"]
    total = _int((body.get("progress") or {}).get("activities_total"))
    if status == "running" and total is not None:
        return f"Status: {status} ({total} activities)"
    return f"Status: {status}"


def _processed(body: dict[str, Any]) -> tuple[int | None, int | None]:
    progress = body.get("progress") or {}
    return _int(progress.get("activities_processed")), _int(progress.get("activities_total"))


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


def _report_lines(body: dict[str, Any]) -> list[str]:
    """The summary of an ``Import`` body, or raise ``ValueError``/``KeyError``/``TypeError``."""
    counts = body.get("counts")
    if counts is None:
        counts = {}
    if not isinstance(counts, dict):
        raise TypeError("counts")
    files = body.get("files")
    if files is not None and not isinstance(files, list):
        raise TypeError("files")
    rows = [row for row in (files or []) if isinstance(row, dict)]

    stored = _count(counts, "stored")
    if files is None:
        stored_line = f"Stored: {stored}"
    else:
        captures = sum(
            1 for row in rows if row.get("outcome") == "stored" and row.get("activity_tag") in RESTING_TAGS
        )
        stored_line = f"Stored: {stored - captures} runs, {captures} resting captures"
    skipped, skipped_detail = _by_key(counts, "skipped_sport")
    non_activity, _ = _by_key(counts, "non_activity")
    not_fit, not_fit_detail = _by_key(counts, "not_fit")
    unreadable = counts.get("unreadable_archives") or []
    if not isinstance(unreadable, list):
        raise TypeError("counts.unreadable_archives")

    lines = [
        stored_line,
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


def _print_report(body: dict[str, Any]) -> None:
    try:
        lines = _report_lines(body)
    except (KeyError, TypeError, ValueError) as exc:
        raise _unparseable(f"missing or malformed {exc}") from None
    for line in lines:
        _write_utf8(line)


def _end(body: dict[str, Any]) -> None:
    """Print the final report of a terminal import and exit with its code."""
    _print_report(body)
    import_id = body["import_id"]
    if body["status"] == "failed":
        raise _fail(f"import {import_id} failed: {_printable(str(body.get('error')))}")
    if body["status"] == "interrupted":
        raise _fail(
            f"import {import_id} was interrupted (the API stopped while it ran); "
            "its report covers the files done. Run `runcoach import` again to finish it."
        )


def _zip_directory(directory: Path) -> Path:
    """Write every file under ``directory`` (relative names) to a new temp zip outside it.

    A file dated before 1980, which a zip cannot hold, is stored with 1980-01-01 00:00. A file
    that cannot be read raises ``OSError``, and the temp zip is removed.
    """
    entries = sorted(p for p in directory.rglob("*") if p.is_file())
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
    path: Path = typer.Argument(..., exists=True, help="A zip of FIT files, or a directory to zip."),
) -> None:
    """Import every activity in a Garmin export zip (or a zip or directory of FIT files).

    Runs and resting captures are stored; other sports are skipped and listed.
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
                of_total = f" of {total}" if total is not None else ""
                _write_utf8(f"Processed {processed}{of_total}")
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
    _end(body)


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
        of_total = f" of {total}" if total is not None else ""
        _write_utf8(f"Processed {processed}{of_total}")
    if body["status"] in TERMINAL:
        _end(body)
    else:
        _print_report(body)
