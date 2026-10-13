"""``runcoach profile show`` and ``runcoach profile set`` -- read and enter the athlete profile.

``show`` calls ``GET /me`` and prints each profile field's effective value with its source, and the
entered value a FIT value shadows, then the four HR anchors, each with its version or the reason it is
unavailable (``missing`` or ``order_conflict``). ``set`` sends the given fields to ``PATCH /me``
(``--clear FIELD`` sends null for that field) and prints the returned profile the same way.

``history add|list|remove`` enter, list and remove the dated HR settings of ``/me/hr-history``
(see the section at the end of this module).

The CLI does no profile validation of its own beyond option types (an HR is an integer) and refusing a
non-finite body mass or height (``nan`` and ``inf`` cannot be sent as JSON): the API owns the rules and
answers 422 for a value it refuses, which is reported like any other non-2xx.
"""

from __future__ import annotations

import enum
import json
import math
from typing import Any

import httpx
import typer

from runcoach_cli import config
from runcoach_cli.api_client import (
    ApiUnreachableError,
    add_hr_history_entry,
    delete_hr_history_entry,
    get_me,
    list_hr_history,
    patch_me,
)

# The profile fields, in the order ``show`` prints them (the API's entered fields).
PROFILE_FIELDS: tuple[str, ...] = (
    "sex",
    "birth_date",
    "body_mass_kg",
    "height_cm",
    "resting_hr_bpm",
    "max_hr_bpm",
    "threshold_hr_bpm",
)
ANCHOR_FIELDS: tuple[str, ...] = ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm", "sex")


class Sex(str, enum.Enum):
    female = "female"
    male = "male"
    unspecified = "unspecified"


def _fail(message: str) -> typer.Exit:
    typer.echo(f"Error: {message}", err=True)
    return typer.Exit(code=1)


def _send(
    api_url: str,
    request,
    expected: tuple[int, ...] = (200,),
    describe=lambda resp: resp.text,
) -> httpx.Response:
    """Run ``request`` and return its response when its status is one of ``expected``; otherwise
    report the status and ``describe(resp)`` to stderr and exit 1 (unreachable: exit 1 too)."""
    try:
        resp: httpx.Response = request()
    except ApiUnreachableError:
        raise _fail(
            f"could not reach API at {api_url}. Is it running? Start it with `runcoach-api`."
        ) from None
    if resp.status_code not in expected:
        raise _fail(f"API returned {resp.status_code}: {describe(resp)}")
    return resp


def _call(
    api_url: str,
    request,
    expected: tuple[int, ...] = (200,),
    describe=lambda resp: resp.text,
) -> dict[str, Any]:
    """Run ``request`` and return the parsed JSON object body, or report to stderr and exit 1."""
    resp = _send(api_url, request, expected, describe)
    try:
        body = resp.json()
        if not isinstance(body, dict):
            raise TypeError("the profile is not a JSON object")
        return body
    except (json.JSONDecodeError, TypeError) as exc:
        raise _fail(f"the API's response could not be understood: {exc}") from None


def _source(entry: dict[str, Any]) -> str:
    parts = [f"source {entry['source']}"]
    if entry.get("session_id"):
        parts.append(f"session {entry['session_id']}")
    if entry.get("recorded_at"):
        parts.append(str(entry["recorded_at"]))
    return ", ".join(parts)


def _field_line(name: str, entry: dict[str, Any]) -> str:
    if entry["unavailable"] is not None or entry["value"] is None:
        line = f"  {name}: unavailable ({entry['unavailable']})"
    else:
        line = f"  {name}: {entry['value']} ({_source(entry)})"
    if entry["source"] == "fit" and entry.get("entered_value") is not None:
        line += f"; shadows entered {entry['entered_value']}"
    return line


def _anchor_line(name: str, anchor: dict[str, Any]) -> str:
    if anchor["unavailable"] is not None or anchor["value"] is None:
        return f"  {name}: unavailable ({anchor['unavailable']})"
    return f"  {name}: {anchor['value']} (version {anchor['version']}, {_source(anchor)})"


def _render(body: dict[str, Any]) -> str:
    """The profile as text, or raise ``KeyError``/``TypeError`` when the body lacks the shape."""
    lines = ["Profile:"]
    lines += [_field_line(name, body[name]) for name in PROFILE_FIELDS]
    lines.append("Anchors:")
    lines += [_anchor_line(name, body["anchors"][name]) for name in ANCHOR_FIELDS]
    return "\n".join(lines)


def _print_profile(body: dict[str, Any]) -> None:
    try:
        text = _render(body)
    except (KeyError, TypeError) as exc:
        raise _fail(f"the API's response could not be understood: missing or malformed {exc}") from None
    typer.echo(text)


def show() -> None:
    """Print the athlete profile and the four HR anchors from GET /me."""
    cfg = config.load_config()
    _print_profile(_call(cfg.api_url, lambda: get_me(cfg.api_url)))


def set_profile(
    sex: Sex | None = typer.Option(None, "--sex", help="female, male or unspecified."),  # noqa: B008 -- Typer's parameter idiom
    birth_date: str | None = typer.Option(None, "--birth-date", help="YYYY-MM-DD."),
    body_mass_kg: float | None = typer.Option(None, "--body-mass-kg", help="Body mass in kg."),
    height_cm: float | None = typer.Option(None, "--height-cm", help="Height in cm."),
    resting_hr: int | None = typer.Option(None, "--resting-hr", help="Resting HR in bpm (integer)."),
    max_hr: int | None = typer.Option(None, "--max-hr", help="Max HR in bpm (integer)."),
    threshold_hr: int | None = typer.Option(None, "--threshold-hr", help="Threshold HR in bpm (integer)."),
    clear: list[str] | None = typer.Option(  # noqa: B008 -- Typer's parameter idiom
        None,
        "--clear",
        metavar="FIELD",
        help=f"Clear an entered field (sends null); repeatable. One of: {', '.join(PROFILE_FIELDS)}.",  # noqa: B008 -- Typer's parameter idiom
    ),
) -> None:
    """Enter or clear profile fields via PATCH /me, then print the returned profile."""
    # A non-finite float is not valid JSON, so it cannot reach the API's own validation: refuse it here.
    for option, value in (("--body-mass-kg", body_mass_kg), ("--height-cm", height_cm)):
        if value is not None and not math.isfinite(value):
            raise typer.BadParameter(f"{value!r} is not a finite number", param_hint=option)
    given = {
        "sex": sex.value if sex is not None else None,
        "birth_date": birth_date,
        "body_mass_kg": body_mass_kg,
        "height_cm": height_cm,
        "resting_hr_bpm": resting_hr,
        "max_hr_bpm": max_hr,
        "threshold_hr_bpm": threshold_hr,
    }
    changes: dict[str, Any] = {name: value for name, value in given.items() if value is not None}
    for name in clear or []:
        if name not in PROFILE_FIELDS:
            raise typer.BadParameter(f"{name!r} is not a profile field", param_hint="--clear")
        if name in changes:
            raise typer.BadParameter(f"{name!r} is both set and cleared", param_hint="--clear")
        changes[name] = None
    if not changes:
        raise typer.BadParameter("give at least one field to set or --clear", param_hint="options")

    cfg = config.load_config()
    _print_profile(_call(cfg.api_url, lambda: patch_me(cfg.api_url, changes)))


# ---------------------------------------------------------------------------
# ``runcoach profile history add|list|remove``: the dated HR entries of /me/hr-history (F021).
#
# An entry is one HR setting and the date it took effect; a run lacking that value in its own file
# borrows the setting in effect on its own date. ``add`` posts one (201 added, 200 when the entry for
# that field and date was replaced, keeping its entry_id), ``list`` prints them in the API's order
# (by field, then date), ``remove`` deletes one (204). Any other status, a 422 or a 404 included,
# prints the API's ``detail`` and exits 1. The API owns validation: the value and date go as given.
# ---------------------------------------------------------------------------


class HrField(str, enum.Enum):
    resting_hr_bpm = "resting_hr_bpm"
    max_hr_bpm = "max_hr_bpm"
    threshold_hr_bpm = "threshold_hr_bpm"


def _detail(resp: httpx.Response) -> str:
    """The API's ``detail``: a string as given, a validation list as ``loc: msg`` items joined by
    ``; ``, and the raw text when the body has no ``detail``."""
    try:
        body = resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return resp.text
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list) and detail:
        items = []
        for item in detail:
            if isinstance(item, dict) and "msg" in item:
                loc = item.get("loc")
                where = ".".join(str(part) for part in loc) if isinstance(loc, list) else ""
                items.append(f"{where}: {item['msg']}" if where else str(item["msg"]))
            else:
                items.append(str(item))
        return "; ".join(items)
    return resp.text


def _entry_line(entry: Any) -> str:
    """One entry as ``<id>  <field>  <value>  from <date>  set <set_at>``, or raise ``KeyError``/``TypeError``."""
    if not isinstance(entry, dict):
        raise TypeError("an entry is not a JSON object")
    return (
        f"{entry['entry_id']}  {entry['field']}  {entry['value']}  "
        f"from {entry['effective_from']}  set {entry['set_at']}"
    )


def _unparseable_entry(exc: Exception) -> typer.Exit:
    return _fail(f"the API's response could not be understood: missing or malformed {exc}")


def history_add(
    field: HrField = typer.Argument(..., help="resting_hr_bpm, max_hr_bpm or threshold_hr_bpm."),  # noqa: B008 -- Typer's parameter idiom
    value: int = typer.Argument(..., help="The setting in bpm (a whole number)."),
    effective_from: str = typer.Option(..., "--from", help="The date it took effect, YYYY-MM-DD."),
) -> None:
    """Add a dated HR setting via POST /me/hr-history (replacing the entry for that field and date)."""
    cfg = config.load_config()
    resp = _send(
        cfg.api_url,
        lambda: add_hr_history_entry(cfg.api_url, field.value, value, effective_from),
        expected=(200, 201),
        describe=_detail,
    )
    try:
        entry = resp.json()
        if not isinstance(entry, dict):
            raise TypeError("the entry is not a JSON object")
        verb = "Added" if resp.status_code == 201 else "Replaced"
        line = f"{verb} entry {entry['entry_id']}: {entry['field']} {entry['value']} from {entry['effective_from']}"
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise _unparseable_entry(exc) from None
    typer.echo(line)


def history_list() -> None:
    """List the dated HR settings from GET /me/hr-history, by field, then date."""
    cfg = config.load_config()
    resp = _send(cfg.api_url, lambda: list_hr_history(cfg.api_url), describe=_detail)
    try:
        entries = resp.json()
        if not isinstance(entries, list):
            raise TypeError("the entries are not a JSON list")
        lines = [_entry_line(entry) for entry in entries]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise _unparseable_entry(exc) from None
    typer.echo("\n".join(lines) if lines else "No dated HR entries.")


def history_remove(
    entry_id: int = typer.Argument(..., help="The entry_id `runcoach profile history list` shows."),
) -> None:
    """Remove a dated HR setting via DELETE /me/hr-history/{entry_id}."""
    cfg = config.load_config()
    _send(
        cfg.api_url,
        lambda: delete_hr_history_entry(cfg.api_url, entry_id),
        expected=(204,),
        describe=_detail,
    )
    typer.echo(f"Removed entry {entry_id}.")
