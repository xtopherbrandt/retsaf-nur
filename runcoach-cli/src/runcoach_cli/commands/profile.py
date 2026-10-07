"""``runcoach profile show`` and ``runcoach profile set`` -- read and enter the athlete profile.

``show`` calls ``GET /me`` and prints each profile field's effective value with its source, and the
entered value a FIT value shadows, then the four HR anchors, each with its version or the reason it is
unavailable (``missing`` or ``order_conflict``). ``set`` sends the given fields to ``PATCH /me``
(``--clear FIELD`` sends null for that field) and prints the returned profile the same way.

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
from runcoach_cli.api_client import ApiUnreachableError, get_me, patch_me

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


def _call(api_url: str, request) -> dict[str, Any]:
    """Run ``request`` and return the parsed profile body, or report to stderr and exit 1."""
    try:
        resp: httpx.Response = request()
    except ApiUnreachableError:
        raise _fail(
            f"could not reach API at {api_url}. Is it running? Start it with `runcoach-api`."
        ) from None
    if resp.status_code != 200:
        raise _fail(f"API returned {resp.status_code}: {resp.text}")
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
    sex: Sex | None = typer.Option(None, "--sex", help="female, male or unspecified."),
    birth_date: str | None = typer.Option(None, "--birth-date", help="YYYY-MM-DD."),
    body_mass_kg: float | None = typer.Option(None, "--body-mass-kg", help="Body mass in kg."),
    height_cm: float | None = typer.Option(None, "--height-cm", help="Height in cm."),
    resting_hr: int | None = typer.Option(None, "--resting-hr", help="Resting HR in bpm (integer)."),
    max_hr: int | None = typer.Option(None, "--max-hr", help="Max HR in bpm (integer)."),
    threshold_hr: int | None = typer.Option(None, "--threshold-hr", help="Threshold HR in bpm (integer)."),
    clear: list[str] | None = typer.Option(
        None,
        "--clear",
        metavar="FIELD",
        help=f"Clear an entered field (sends null); repeatable. One of: {', '.join(PROFILE_FIELDS)}.",
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
