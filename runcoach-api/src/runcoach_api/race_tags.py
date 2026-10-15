"""The race-tag matching rule (F023 AC2 and AC3): one decision per Connect item.

Pure: no store, configuration or web import. ``db.apply_race_tags`` reads the candidate rows and
writes the decided tags in one transaction; this module only decides.

An *item* is a mapping with ``connect_activity_id``, ``start_time`` (UTC, ISO 8601, whole seconds,
``Z`` or ``+00:00``), ``distance_m``, ``duration_s`` (numbers or ``None``) and ``race`` (bool). A
*candidate row* is a mapping with ``session_id``, ``start_time`` (as stored, ``+00:00``),
``activity_tag`` and ``summary`` (the JSON-decoded stored summary, or ``None``). The caller hands
over only candidate rows: stored sessions with sport ``running`` whose ``activity_tag`` is not one
of ``RESTING_CAPTURE_TAGS``. A resting capture recorded on the Run profile is stored as sport
``running``, so the tag exclusion is what keeps it out.

The rule, as the feature states it:

- An item's candidates are the rows whose ``start_time`` equals the item's, ``Z`` normalised to
  ``+00:00``.
- A candidate *agrees* when the item's and its summary's ``distance_m`` and ``duration_s`` are all
  present and finite and each stored value ``s`` is within 1 % of the item's ``d``:
  ``|s - d| <= 0.01 d``. A missing, null, non-numeric, NaN or infinite value on either side never
  agrees.
- 0 candidates: ``not_stored``. 1 candidate: agreeing, it is the target; not agreeing,
  ``mismatch`` naming it. 2 or more: exactly one agreeing is the target; otherwise ``ambiguous``.
- Two or more items whose target is one session are all ``ambiguous`` and none is applied (an
  item that is a ``mismatch`` on that session has no target and does not count).
- On a target: a stored tag other than null or ``race`` is left as is (``other_tag``); ``race``
  true turns null into ``race`` (``tagged``) and leaves ``race`` (``unchanged``); ``race`` false
  turns ``race`` into null (``cleared``) and leaves null (``unchanged``).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from runcoach_api.metrics.session_load import DECLARED_CAPTURE_TAGS

RACE_TAG = "race"

# The resting-capture tags: a session carrying one is never a candidate. The same constant the
# load rule reads, so the two cannot list different tags.
RESTING_CAPTURE_TAGS = DECLARED_CAPTURE_TAGS

# The agreement tolerance, as a fraction of the item's (Connect's) value.
AGREEMENT_TOLERANCE = 0.01

OUTCOMES = ("tagged", "cleared", "unchanged", "not_stored", "mismatch", "ambiguous", "other_tag")


@dataclass(frozen=True)
class Decision:
    """One item's outcome. ``write`` says whether ``new_tag`` is to be stored on ``session_id``;
    it is true only for ``tagged`` and ``cleared``."""

    connect_activity_id: int
    outcome: str
    session_id: str | None
    candidates: tuple[str, ...]
    write: bool = False
    new_tag: str | None = None

    def as_dict(self) -> dict:
        """The response shape: ``connect_activity_id``, ``outcome``, ``session_id``, ``candidates``."""
        return {
            "connect_activity_id": self.connect_activity_id,
            "outcome": self.outcome,
            "session_id": self.session_id,
            "candidates": list(self.candidates),
        }


def normalise_start_time(value: str) -> str:
    """``...Z`` -> ``...+00:00``, the stored form; any other string is returned unchanged."""
    return value[:-1] + "+00:00" if value.endswith("Z") else value


def _finite(value: object) -> float | None:
    """``value`` as a float when it is a real, finite number (a bool is not one), else ``None``.
    An int beyond float range (``json.loads`` keeps one as an int) is not one either."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _within(stored: float, item: float) -> bool:
    return abs(stored - item) <= AGREEMENT_TOLERANCE * item


def agrees(item: Mapping, summary: Mapping | None) -> bool:
    """Whether a candidate's stored ``summary`` agrees with ``item`` on distance and duration."""
    if not isinstance(summary, Mapping):
        return False
    pairs = [
        (_finite(summary.get("distance_m")), _finite(item.get("distance_m"))),
        (_finite(summary.get("duration_s")), _finite(item.get("duration_s"))),
    ]
    if any(stored is None or wanted is None for stored, wanted in pairs):
        return False
    return all(_within(stored, wanted) for stored, wanted in pairs)


def _transition(
    activity_id: int, session_id: str, candidates: tuple[str, ...], stored_tag, race: bool
) -> Decision:
    if stored_tag is not None and stored_tag != RACE_TAG:
        return Decision(activity_id, "other_tag", session_id, candidates)
    if race:
        if stored_tag is None:
            return Decision(activity_id, "tagged", session_id, candidates, write=True, new_tag=RACE_TAG)
        return Decision(activity_id, "unchanged", session_id, candidates)
    if stored_tag == RACE_TAG:
        return Decision(activity_id, "cleared", session_id, candidates, write=True, new_tag=None)
    return Decision(activity_id, "unchanged", session_id, candidates)


def decide(items: Sequence[Mapping], candidate_rows: Iterable[Mapping]) -> list[Decision]:
    """One ``Decision`` per item, in request order (see the module docstring for the rule)."""
    by_start: dict[str, list[Mapping]] = {}
    for row in candidate_rows:
        by_start.setdefault(row["start_time"], []).append(row)
    for rows in by_start.values():
        rows.sort(key=lambda r: r["session_id"])

    # First pass: each item's candidates and its target, if it has one.
    staged: list[tuple[Mapping, tuple[str, ...], Mapping | None, Decision | None]] = []
    for item in items:
        activity_id = item["connect_activity_id"]
        rows = by_start.get(normalise_start_time(item["start_time"]), [])
        ids = tuple(r["session_id"] for r in rows)
        agreeing = [r for r in rows if agrees(item, r.get("summary"))]
        if not rows:
            staged.append((item, ids, None, Decision(activity_id, "not_stored", None, ids)))
        elif len(agreeing) == 1:
            staged.append((item, ids, agreeing[0], None))
        elif len(rows) == 1:
            staged.append((item, ids, None, Decision(activity_id, "mismatch", rows[0]["session_id"], ids)))
        else:
            staged.append((item, ids, None, Decision(activity_id, "ambiguous", None, ids)))

    # Second pass: a session two or more items would apply to is applied by none of them.
    appliers: dict[str, int] = {}
    for _item, _ids, target, _decision in staged:
        if target is not None:
            appliers[target["session_id"]] = appliers.get(target["session_id"], 0) + 1

    decisions: list[Decision] = []
    for item, ids, target, decision in staged:
        if decision is not None:
            decisions.append(decision)
        elif appliers[target["session_id"]] > 1:
            decisions.append(Decision(item["connect_activity_id"], "ambiguous", None, ids))
        else:
            decisions.append(
                _transition(
                    item["connect_activity_id"],
                    target["session_id"],
                    ids,
                    target["activity_tag"],
                    bool(item["race"]),
                )
            )
    return decisions
