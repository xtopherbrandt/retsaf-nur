"""The race-tag matching rule (F023 AC2 and AC3): one decision per Connect item.

Pure: no store, configuration or web import. ``db.apply_race_tags`` reads the candidate rows and
writes the decided tags in one transaction; this module only decides.

An *item* is a mapping with ``connect_activity_id``, ``start_time`` (UTC, ISO 8601, whole seconds,
``Z`` or ``+00:00``), ``distance_m``, ``duration_s`` (numbers or ``None``) and either ``tag``
(``race``, ``threshold_workout`` or ``None``) or ``race`` (bool); ``wanted_tag`` reads which. A
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
- On a target, the stored tag and the wanted one decide (F026 reference section 2): a stored tag
  other than null, ``race`` and ``threshold_workout`` is left as is (``other_tag``); a stored tag
  equal to the wanted one is ``unchanged``; null to a tag is ``tagged``; a tag to null is
  ``cleared``; ``race`` to ``threshold_workout`` and back is ``retagged``. ``tagged``, ``cleared``
  and ``retagged`` are written.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from runcoach_api.metrics import finite as _finite_screen
from runcoach_api.metrics.session_load import DECLARED_CAPTURE_TAGS

RACE_TAG = "race"
THRESHOLD_WORKOUT_TAG = "threshold_workout"

# The tags an item may ask for (null aside), in the order ``counts``' ``by_tag`` lists them.
ITEM_TAGS = (RACE_TAG, THRESHOLD_WORKOUT_TAG)

# The resting-capture tags: a session carrying one is never a candidate. The same constant the
# load rule reads, so the two cannot list different tags.
RESTING_CAPTURE_TAGS = DECLARED_CAPTURE_TAGS

# The agreement tolerance, as a fraction of the item's (Connect's) value.
AGREEMENT_TOLERANCE = 0.01

OUTCOMES = ("tagged", "cleared", "retagged", "unchanged", "not_stored", "mismatch", "ambiguous", "other_tag")

# The outcomes that write, which ``counts``' ``by_tag`` breaks down per tag.
WRITTEN_OUTCOMES = ("tagged", "cleared", "retagged")


@dataclass(frozen=True)
class Decision:
    """One item's outcome. ``write`` says whether ``new_tag`` is to be stored on ``session_id``;
    it is true only for ``tagged``, ``cleared`` and ``retagged``. ``stored_tag`` is the target's
    tag before the apply (``None`` when there is no target) and ``wanted_tag`` the item's; neither
    is served per item, ``counts`` reads them."""

    connect_activity_id: int
    outcome: str
    session_id: str | None
    candidates: tuple[str, ...]
    write: bool = False
    new_tag: str | None = None
    stored_tag: str | None = None
    wanted_tag: str | None = None

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
    """``metrics.finite.finite``: a real, finite number as a float, else ``None``."""
    return _finite_screen.finite(value)


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


def wanted_tag(item: Mapping) -> str | None:
    """The tag ``item`` asks for: its ``tag`` when the key is present (``None`` meaning clear),
    else ``race`` for ``race`` true and ``None`` for false. The caller has checked that exactly
    one of the two keys is present."""
    if "tag" in item:
        return item["tag"]
    return RACE_TAG if item["race"] else None


def _transition(
    activity_id: int, session_id: str, candidates: tuple[str, ...], stored: str | None, wanted: str | None
) -> Decision:
    """The F026 reference section 2 table: the stored tag (row) and the wanted one (column)."""
    if stored is not None and stored not in ITEM_TAGS:
        outcome = "other_tag"
    elif stored == wanted:
        outcome = "unchanged"
    elif stored is None:
        outcome = "tagged"
    elif wanted is None:
        outcome = "cleared"
    else:
        outcome = "retagged"
    write = outcome in WRITTEN_OUTCOMES
    return Decision(
        activity_id,
        outcome,
        session_id,
        candidates,
        write=write,
        new_tag=wanted if write else None,
        stored_tag=stored,
        wanted_tag=wanted,
    )


def counts(decisions: Iterable[Decision]) -> dict:
    """Every outcome's count (``OUTCOMES`` order, zeros included), then ``by_tag``: per tag of
    ``ITEM_TAGS``, its ``tagged``, ``cleared`` and ``retagged`` counts. ``tagged`` and
    ``retagged`` are keyed by the wanted tag, ``cleared`` by the stored one."""
    out: dict = dict.fromkeys(OUTCOMES, 0)
    by_tag = {tag: dict.fromkeys(WRITTEN_OUTCOMES, 0) for tag in ITEM_TAGS}
    for decision in decisions:
        out[decision.outcome] += 1
        if decision.outcome in WRITTEN_OUTCOMES:
            key = decision.stored_tag if decision.outcome == "cleared" else decision.wanted_tag
            by_tag[key][decision.outcome] += 1
    out["by_tag"] = by_tag
    return out


def index_by_start_second(candidate_rows: Iterable[Mapping]) -> dict[str, list[Mapping]]:
    """The candidate rows by their stored ``start_time``, each second's rows ordered by
    ``session_id``. The start-second lookup F023's ``decide`` and F025's laps candidates share."""
    by_start: dict[str, list[Mapping]] = {}
    for row in candidate_rows:
        by_start.setdefault(row["start_time"], []).append(row)
    for rows in by_start.values():
        rows.sort(key=lambda r: r["session_id"])
    return by_start


def at_start_second(index: Mapping[str, list[Mapping]], start_time: str) -> list[Mapping]:
    """The rows of ``index`` stored at ``start_time``'s second (``Z`` normalised to ``+00:00``);
    an empty list when none is."""
    return list(index.get(normalise_start_time(start_time), []))


def decide(items: Sequence[Mapping], candidate_rows: Iterable[Mapping]) -> list[Decision]:
    """One ``Decision`` per item, in request order (see the module docstring for the rule)."""
    by_start = index_by_start_second(candidate_rows)

    # First pass: each item's candidates and its target, if it has one.
    staged: list[tuple[Mapping, tuple[str, ...], Mapping | None, Decision | None]] = []
    for item in items:
        activity_id = item["connect_activity_id"]
        wanted = wanted_tag(item)
        rows = at_start_second(by_start, item["start_time"])
        ids = tuple(r["session_id"] for r in rows)
        agreeing = [r for r in rows if agrees(item, r.get("summary"))]
        if not rows:
            staged.append((item, ids, None, Decision(activity_id, "not_stored", None, ids, wanted_tag=wanted)))
        elif len(agreeing) == 1:
            staged.append((item, ids, agreeing[0], None))
        elif len(rows) == 1:
            mismatch = Decision(activity_id, "mismatch", rows[0]["session_id"], ids, wanted_tag=wanted)
            staged.append((item, ids, None, mismatch))
        else:
            staged.append((item, ids, None, Decision(activity_id, "ambiguous", None, ids, wanted_tag=wanted)))

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
            decisions.append(
                Decision(item["connect_activity_id"], "ambiguous", None, ids, wanted_tag=wanted_tag(item))
            )
        else:
            decisions.append(
                _transition(
                    item["connect_activity_id"],
                    target["session_id"],
                    ids,
                    target["activity_tag"],
                    wanted_tag(item),
                )
            )
    return decisions
