"""Each athlete profile field's effective value: stored FIT files first, then entries.

F016's rule (``spec/references/F016-athlete-profile.md``, "Effective value,
per field"; research/00 PRIN-28 admits the FIT settings as profile inputs),
applied to each field on its own:

1. the value from the stored session with the latest ``start_time`` whose
   file carries the field. **For ``max_hr_bpm`` and ``threshold_hr_bpm`` only
   sessions whose stored ``sport`` is ``running`` count**: Garmin keeps HR
   settings per activity profile, and snapshot files carry another
   profile's threshold. Stored sport decides, so a walk or capture recorded
   on the Run profile and stored ``running`` counts. On a tie in start time
   the higher ``upload_order`` (the later upload) wins;
2. else the latest entered value;
3. else unavailable, reason ``missing``.

The latest entered value is the last entry written for the field (the
highest ``entry_id``), not the one with the latest ``set_at``. An entry whose
value is ``None`` is a clear: the field then has no entered value, so it
falls to unavailable and never back to an earlier entry. The latest entered
value is reported as ``entered_value`` whatever the source, so a caller can
show an entry a file shadows.

Pure: ``resolve`` reads plain mappings (``db.read_profile_inputs`` returns
them) and does no I/O. Nothing is cached; the profile is computed on read,
so deleting a source session recomputes it. ``garmin_activity_class`` is
stored per session and resolved by nothing here: it is not an entered field.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# The fields an athlete can enter, in response order.
ENTERED_FIELDS: tuple[str, ...] = (
    "sex",
    "birth_date",
    "body_mass_kg",
    "height_cm",
    "resting_hr_bpm",
    "max_hr_bpm",
    "threshold_hr_bpm",
)

# The entered fields a FIT file can also carry (``birth_date`` has no FIT source).
FIT_FIELDS: tuple[str, ...] = tuple(f for f in ENTERED_FIELDS if f != "birth_date")

# Fields whose FIT value counts only from a session stored as running.
RUNNING_ONLY_FIELDS: frozenset[str] = frozenset({"max_hr_bpm", "threshold_hr_bpm"})

RUNNING_SPORT = "running"


@dataclass(frozen=True)
class FieldValue:
    """One field's effective value.

    ``source`` is ``"fit"`` (``session_id`` and ``recorded_at``, the session's
    start time, name the file), ``"entered"`` (``recorded_at`` is the entry's
    ``set_at``; ``session_id`` is ``None``) or ``None`` when unavailable, in
    which case ``value``, ``session_id`` and ``recorded_at`` are ``None`` and
    ``reason`` is ``"missing"``. ``entered_value`` is the latest entered value
    whatever the source, ``None`` when there is none or it was cleared.
    """

    value: Any
    source: str | None
    session_id: str | None
    recorded_at: str | None
    entered_value: Any
    reason: str | None


def _counts_for(field: str, session: Mapping[str, Any]) -> bool:
    if session.get(field) is None:
        return False
    return field not in RUNNING_ONLY_FIELDS or session.get("sport") == RUNNING_SPORT


def _latest_session(field: str, sessions: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    carrying = [s for s in sessions if _counts_for(field, s)]
    if not carrying:
        return None
    # start_time is stored as isoformat() of an aware UTC datetime, so the
    # strings order chronologically. A pre-F016 row has no upload_order, and
    # no profile values either, so it never reaches this comparison carrying one.
    return max(carrying, key=lambda s: (s["start_time"], s.get("upload_order") or 0))


def _latest_entries(entries: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    latest: dict[str, Mapping[str, Any]] = {}
    for entry in sorted(entries, key=lambda e: e["entry_id"]):
        latest[entry["field"]] = entry
    return latest


def resolve(
    sessions: Iterable[Mapping[str, Any]], entries: Iterable[Mapping[str, Any]]
) -> dict[str, FieldValue]:
    """Every field of ``ENTERED_FIELDS`` resolved to a ``FieldValue``.

    ``sessions``: mappings with ``session_id``, ``start_time``,
    ``upload_order``, ``sport`` and the ``FIT_FIELDS`` (``None`` where the
    file had no value). ``entries``: mappings with ``entry_id``, ``field``,
    ``value`` (``None`` for a clear) and ``set_at``, in any order.
    """
    session_rows = list(sessions)
    latest_entry = _latest_entries(entries)

    resolved: dict[str, FieldValue] = {}
    for field in ENTERED_FIELDS:
        entry = latest_entry.get(field)
        entered_value = None if entry is None else entry["value"]

        session = _latest_session(field, session_rows) if field in FIT_FIELDS else None
        if session is not None:
            resolved[field] = FieldValue(
                value=session[field],
                source="fit",
                session_id=session["session_id"],
                recorded_at=session["start_time"],
                entered_value=entered_value,
                reason=None,
            )
        elif entered_value is not None:
            resolved[field] = FieldValue(
                value=entered_value,
                source="entered",
                session_id=None,
                recorded_at=entry["set_at"],
                entered_value=entered_value,
                reason=None,
            )
        else:
            resolved[field] = FieldValue(
                value=None,
                source=None,
                session_id=None,
                recorded_at=None,
                entered_value=None,
                reason="missing",
            )
    return resolved
