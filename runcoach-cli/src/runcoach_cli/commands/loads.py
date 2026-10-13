"""``runcoach loads recompute`` -- recompute every running session's saved load (F021).

A saved load is computed once, at upload; entering dated HR settings changes no saved body until it
is recomputed. This command walks ``GET /sessions`` in pages of ``PAGE_SIZE`` (200) until
``next_cursor`` is null, keeps the rows whose ``sport`` is ``running``, and calls
``POST /sessions/{id}/load/recompute`` for each, newest first. It prints ``Progress: N of TOTAL``
each time the count of sessions tried passes a multiple of ``PROGRESS_STEP`` (100), then a summary:

    Recomputed: <sessions whose recompute answered 200 with a load body>
    Changed: <of those, the ones whose body says ``changed: true``>
    Out of period: <of those, the ones where any of the three HR input blocks
                    (resting, max, threshold) says ``out_of_period: true``>
    Skipped (not running): <rows not stored as running>
    Failed: <count>
      <session_id>: <why>          (one line per failed session)

A session fails when its recompute answers other than 200 (a 404 included, with the API's ``detail``) or
with a body that is not a load (no boolean ``changed``, no ``inputs`` object); the walk continues
past it, and the command exits 1 at the end if any failed. An unreachable API (a timeout included)
exits 1 at once, whether on a page or on a recompute. Each recompute has its own timeout,
``api_client.RECOMPUTE_TIMEOUT``.
"""

from __future__ import annotations

import json

import httpx

from runcoach_cli import config
from runcoach_cli.api_client import ApiUnreachableError, recompute_session_load
from runcoach_cli.commands.profile import _detail, _fail
from runcoach_cli.commands.sessions import PAGE_SIZE, _fetch_page, _printable, _unparseable, _write_utf8

PROGRESS_STEP = 100
HR_INPUTS: tuple[str, ...] = ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm")


class _Failed(Exception):
    """One recompute whose answer could not be used: non-200, or not a load body."""


def _running_ids(api_url: str) -> tuple[list[str], int]:
    """Every running session's id, newest first, and the count of rows skipped as not running."""
    ids: list[str] = []
    skipped = 0
    since: str | None = None
    while True:
        body = _fetch_page(api_url, since, PAGE_SIZE)
        for row in body["items"]:
            if not isinstance(row, dict) or not isinstance(row.get("session_id"), str):
                raise _unparseable("a row has no session_id")
            if row.get("sport") == "running":
                ids.append(row["session_id"])
            else:
                skipped += 1
        next_cursor = body.get("next_cursor")
        if next_cursor is None:
            return ids, skipped
        if not isinstance(next_cursor, str) or next_cursor == since:
            raise _unparseable("next_cursor repeats the page just read")
        since = next_cursor


def _recompute(api_url: str, session_id: str) -> tuple[bool, bool]:
    """Recompute one session: ``(changed, out_of_period)``, or raise ``_Failed``.

    An unreachable API is not caught here: it ends the command.
    """
    resp: httpx.Response = recompute_session_load(api_url, session_id)
    if resp.status_code != 200:
        raise _Failed(f"API returned {resp.status_code}: {_detail(resp)}")
    try:
        body = resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _Failed(f"the API's response could not be understood: {exc}") from None
    inputs = body.get("inputs") if isinstance(body, dict) else None
    if not isinstance(body, dict) or not isinstance(body.get("changed"), bool) or not isinstance(inputs, dict):
        raise _Failed("the API's response could not be understood: not a load with `changed` and `inputs`")
    out_of_period = any(
        isinstance(inputs.get(name), dict) and inputs[name].get("out_of_period") is True for name in HR_INPUTS
    )
    return body["changed"], out_of_period


def recompute() -> None:
    """Recompute the saved load of every running session with the current HR files, entries and anchors."""
    cfg = config.load_config()
    ids, skipped = _running_ids(cfg.api_url)
    total = len(ids)
    recomputed = changed = out_of_period = 0
    failed: list[tuple[str, str]] = []
    for tried, session_id in enumerate(ids, start=1):
        try:
            was_changed, was_out = _recompute(cfg.api_url, session_id)
        except ApiUnreachableError:
            raise _fail(
                f"could not reach API at {cfg.api_url} while recomputing session {_printable(session_id)} "
                f"({recomputed} recomputed, {len(failed)} failed before it). Is it running? "
                "Start it with `runcoach-api`."
            ) from None
        except _Failed as exc:
            failed.append((session_id, str(exc)))
        else:
            recomputed += 1
            changed += was_changed
            out_of_period += was_out
        if tried % PROGRESS_STEP == 0:
            _write_utf8(f"Progress: {tried} of {total}")

    _write_utf8(f"Recomputed: {recomputed}")
    _write_utf8(f"Changed: {changed}")
    _write_utf8(f"Out of period: {out_of_period}")
    _write_utf8(f"Skipped (not running): {skipped}")
    _write_utf8(f"Failed: {len(failed)}")
    for session_id, why in failed:
        _write_utf8(f"  {_printable(session_id)}: {_printable(why)}")
    if failed:
        raise _fail(f"{len(failed)} of {total} sessions failed to recompute")
