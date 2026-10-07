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

**The HR anchors** (the reference's "The anchor lookup"). ``order_rule``
applies the ordering rule to the effective values of ``ANCHOR_FIELDS``:

- ``resting_hr_bpm < max_hr_bpm`` is required for both; failing it makes both
  ``order_conflict``;
- ``threshold_hr_bpm`` is checked against whichever of resting and max are
  served (available and not in conflict): strictly above resting and
  strictly below max. Failing either makes threshold alone
  ``order_conflict``; when resting and max conflict it is checked against
  neither;
- a field with no value is ``missing``, never ``order_conflict``;
- ``sex`` is never ``order_conflict``; absent or ``unspecified`` is
  ``missing``;
- no plausibility range: the spec has no range constants.

So when resting and max are both served, ``max_hr_bpm - resting_hr_bpm > 0``,
and the HR-TRIMP heart-rate-reserve ratio (spec/03 section 3.4.1) may divide by
it unguarded.

**The version** changes when, and only when, the anchor's served value
changes: the source does not move it, and an unavailable period does not
either (188, unavailable, 188 keeps one version). It cannot be recomputed
from current rows, since deletes are hard and entries can be cleared, so
``db`` keeps a version log (``db.ANCHOR_VERSIONS_TABLE``) holding, per anchor,
the last served value and its version. ``next_versions`` says which log rows
a write changes; ``anchors`` builds the served anchors from the resolver
result and that log.
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


# --- the HR anchors -------------------------------------------------------------------------

# The anchors the lookup serves, in response order.
ANCHOR_FIELDS: tuple[str, ...] = ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm", "sex")

MISSING = "missing"
ORDER_CONFLICT = "order_conflict"
UNSPECIFIED_SEX = "unspecified"


@dataclass(frozen=True)
class Anchor:
    """One served anchor, or why it is unavailable.

    Available: ``value``, ``source``, ``session_id`` and ``recorded_at`` are the
    resolver's (``FieldValue``), ``version`` is the version log's, and
    ``reason`` is ``None``. Unavailable: ``value``, ``source``, ``session_id``,
    ``recorded_at`` and ``version`` are ``None`` and ``reason`` is ``"missing"``
    or ``"order_conflict"``. ``entered_value`` is the resolver's either way.
    """

    value: Any
    source: str | None
    session_id: str | None
    recorded_at: str | None
    version: int | None
    entered_value: Any
    reason: str | None


@dataclass(frozen=True)
class AnchorLogChange:
    """One version log row a write sets: the newly served value, its version, and the
    source that served it when the version began (``source_session_id`` is ``None`` for
    an entry)."""

    value: Any
    version: int
    source: str
    source_session_id: str | None


def _effective(field: FieldValue) -> Any:
    return None if field.source is None else field.value


def order_rule(resolved: Mapping[str, FieldValue]) -> dict[str, str | None]:
    """Each of ``ANCHOR_FIELDS`` mapped to ``None`` (served), ``"missing"`` or
    ``"order_conflict"``, from ``resolve``'s result. Pure."""
    resting = _effective(resolved["resting_hr_bpm"])
    max_hr = _effective(resolved["max_hr_bpm"])
    threshold = _effective(resolved["threshold_hr_bpm"])
    sex = _effective(resolved["sex"])

    reasons: dict[str, str | None] = {
        "resting_hr_bpm": MISSING if resting is None else None,
        "max_hr_bpm": MISSING if max_hr is None else None,
        "threshold_hr_bpm": MISSING if threshold is None else None,
        "sex": MISSING if sex is None or sex == UNSPECIFIED_SEX else None,
    }
    if resting is not None and max_hr is not None and not resting < max_hr:
        reasons["resting_hr_bpm"] = reasons["max_hr_bpm"] = ORDER_CONFLICT
    if threshold is not None:
        # Only a served bound checks threshold: a missing or conflicting one does not.
        lower = resting if reasons["resting_hr_bpm"] is None else None
        upper = max_hr if reasons["max_hr_bpm"] is None else None
        if (lower is not None and not threshold > lower) or (
            upper is not None and not threshold < upper
        ):
            reasons["threshold_hr_bpm"] = ORDER_CONFLICT
    return reasons


def next_versions(
    resolved: Mapping[str, FieldValue], logged: Mapping[str, tuple[Any, int]]
) -> dict[str, AnchorLogChange]:
    """The version log rows a write changes, given the log as it stands.

    ``logged`` maps an anchor to its last served ``(value, version)``. A served
    anchor with no log row starts at version 1; one whose value differs from
    the logged value takes the next version; one with the logged value, or an
    unavailable one, changes nothing and is left out. Pure.
    """
    changes: dict[str, AnchorLogChange] = {}
    for field, reason in order_rule(resolved).items():
        if reason is not None:
            continue
        value = resolved[field]
        previous = logged.get(field)
        if previous is not None and previous[0] == value.value:
            continue
        changes[field] = AnchorLogChange(
            value=value.value,
            version=1 if previous is None else previous[1] + 1,
            source=value.source,
            source_session_id=value.session_id,
        )
    return changes


def anchors(
    resolved: Mapping[str, FieldValue], logged: Mapping[str, tuple[Any, int]]
) -> dict[str, Anchor]:
    """The four anchors, from ``resolve``'s result and the version log.

    Raises ``ValueError`` when a served anchor's value is not the value the log
    holds for it: every write that can change an effective value updates the
    log in its own transaction, so a mismatch means a write that skipped it,
    and serving the logged version would name another value. Pure.
    """
    served: dict[str, Anchor] = {}
    for field, reason in order_rule(resolved).items():
        value = resolved[field]
        if reason is not None:
            served[field] = Anchor(
                value=None,
                source=None,
                session_id=None,
                recorded_at=None,
                version=None,
                entered_value=value.entered_value,
                reason=reason,
            )
            continue
        previous = logged.get(field)
        if previous is None or previous[0] != value.value:
            raise ValueError(
                f"anchor version log holds {previous!r} for {field}, which serves {value.value!r}"
            )
        served[field] = Anchor(
            value=value.value,
            source=value.source,
            session_id=value.session_id,
            recorded_at=value.recorded_at,
            version=previous[1],
            entered_value=value.entered_value,
            reason=None,
        )
    return served
