"""The gate table of the F017 reference on the pure function (AC6 gates 4-6 and 9, AC7), with the adversarial probes.

``metrics.session_load.compute_session_load`` is driven on hand-built inputs:
a 1 Hz run of constant HR with the four settings given as the session's own
file values, so every row isolates one rule and the gate comparisons see the
exact integers the table names.

**The table was written from the reference's gate table and AC6/AC7 before
the gates were implemented**, one row per gate in the reference's order, a row
for each pair of gates that can both fire (the earlier one wins), the two
reference reasons that leave ``hr_trimp.value`` served, and the equality rows
(``avg == resting`` and ``avg == max`` are computed; ``resting == max`` and
``threshold == resting`` or ``== max`` are refused). The rows pin the two
mutants that survived the first build: gate 5 at equality and the threshold
order check at either bound.

**The adversarial table** attacks the bounds of the values used: 0 (not
refused by the pure function; the ingest and the entry screens keep it out),
the smallest ordered pair, ``2**63`` (a finite float, so computed), ``10**400``
(``float()`` raises ``OverflowError``, so gate 9 unless an earlier exact-int
gate fires first) and a threshold served while resting and max conflict (gate
5 wins, the threshold is never read).

Every reason a row expects is a member of ``session_load.SERVED_REASONS``,
which the route test holds inside the contract's closed enum.
"""

from __future__ import annotations

import math

import pytest
from runcoach_api.metrics import session_features, session_load
from runcoach_api.metrics.session_load import compute_session_load

HR_FIELDS = ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm", "sex")
HUGE = 10**400
BIG = 2**63


def _from_file(value) -> dict:
    if value is None:
        return {"value": None, "source": None, "session_id": None, "anchor_version": None, "anchor_unavailable": None}
    return {"value": value, "source": "session_file", "session_id": "synthetic", "anchor_version": None, "anchor_unavailable": None}


def _synthetic(
    *,
    seconds: int = 600,
    heart_rate: float | None = 150.0,
    resting=50,
    max_hr=190,
    threshold=170,
    sex="male",
    sport: str = "running",
    activity_tag: str | None = None,
) -> dict:
    """A 1 Hz run of ``seconds`` records at a constant HR, the settings as the file's own values."""
    rows = [{"t": float(t), "distance": 3.0 * t, "heart_rate": heart_rate, "sample_quality": []} for t in range(seconds + 1)]
    session = {
        "session_id": "synthetic",
        "sport": sport,
        "activity_tag": activity_tag,
        "hr_source": "chest_strap",
        "quality_flags": [],
        "context": {},
        "timer_time_s": float(seconds),
    }
    inputs = {
        **session,
        "segment_rows": session_features.segment_rows(session, rows),
        "features": session_features.compute_session_features(session, rows),
    }
    for field_name, value in zip(HR_FIELDS, (resting, max_hr, threshold, sex)):
        inputs[field_name] = _from_file(value)
    return inputs


def _outcome(body: dict) -> tuple[str | None, str | None, bool]:
    """``(hr_trimp.unavailable, session_load.unavailable, hr_trimp.value is served)``."""
    return (
        body["metrics"]["hr_trimp"]["unavailable"],
        body["session_load"]["unavailable"],
        body["metrics"]["hr_trimp"]["value"] is not None,
    )


# (id, inputs, expected hr_trimp reason, expected session_load reason, trimp served)
# Written from the reference's gate table (rows 4, 5, 6, 9 and the reference step) before the build.
GATE_ROWS = [
    # gate 4: resting or max without a value
    ("4_no_resting_is_missing_anchor", dict(resting=None), "missing_anchor", "missing_anchor", False),
    ("4_no_max_is_missing_anchor", dict(max_hr=None), "missing_anchor", "missing_anchor", False),
    ("4_neither_is_missing_anchor", dict(resting=None, max_hr=None), "missing_anchor", "missing_anchor", False),
    # gate 5: resting >= max among the values used (equality refused: the surviving mutant)
    ("5_resting_equal_max_is_order_conflict", dict(resting=190, max_hr=190), "order_conflict", "order_conflict", False),
    ("5_resting_above_max_is_order_conflict", dict(resting=191, max_hr=190), "order_conflict", "order_conflict", False),
    # gate 6: average HR outside the bounds (R3); equality is computed, see EQUALITY_ROWS
    ("6_avg_below_resting", dict(heart_rate=40.0), "avg_hr_below_resting", "avg_hr_below_resting", False),
    ("6_avg_above_max", dict(heart_rate=200.0), "avg_hr_above_max", "avg_hr_above_max", False),
    # gate 9: a value used does not convert to a finite float
    ("9_max_ten_to_the_400_is_not_representable", dict(max_hr=HUGE), "not_representable", "not_representable", False),
    ("9_threshold_ten_to_the_400_is_not_representable", dict(threshold=HUGE), "not_representable", "not_representable", False),
    # two gates met: the earlier one wins
    ("4_before_6_no_resting_and_avg_above_max", dict(resting=None, heart_rate=200.0), "missing_anchor", "missing_anchor", False),
    ("5_before_6_resting_equal_max_and_avg_below", dict(resting=190, max_hr=190, heart_rate=40.0), "order_conflict", "order_conflict", False),
    ("5_before_9_resting_above_a_huge_max", dict(resting=HUGE + 1, max_hr=HUGE), "order_conflict", "order_conflict", False),
    ("6_before_9_avg_above_max_with_a_huge_threshold", dict(heart_rate=200.0, threshold=HUGE), "avg_hr_above_max", "avg_hr_above_max", False),
    ("3_before_4_no_hr_and_no_resting", dict(heart_rate=None, resting=None), "no_hr", "no_hr", False),
    # AC7: TRIMP computed, the reference withheld; hr_trimp.value still served
    ("ref_no_threshold_is_no_threshold_hr", dict(threshold=None), None, "no_threshold_hr", True),
    ("ref_threshold_equal_resting_is_threshold_order_conflict", dict(threshold=50), None, "threshold_order_conflict", True),
    ("ref_threshold_equal_max_is_threshold_order_conflict", dict(threshold=190), None, "threshold_order_conflict", True),
    ("ref_threshold_below_resting_is_threshold_order_conflict", dict(threshold=40), None, "threshold_order_conflict", True),
    ("ref_threshold_above_max_is_threshold_order_conflict", dict(threshold=200), None, "threshold_order_conflict", True),
    # otherwise: computed and served
    ("computed_in_order", dict(), None, None, True),
]


@pytest.mark.parametrize(
    ("overrides", "trimp_reason", "load_reason", "trimp_served"),
    [pytest.param(*row[1:], id=row[0]) for row in GATE_ROWS],
)
def test_gate_table(overrides: dict, trimp_reason, load_reason, trimp_served: bool) -> None:
    body = compute_session_load(_synthetic(**overrides))
    got = _outcome(body)
    print(f"  {overrides} -> {got}")
    assert got == (trimp_reason, load_reason, trimp_served)
    assert (body["session_load"]["driver"] == "hr_trimp") == (body["session_load"]["value"] is not None)
    if trimp_reason is not None:
        assert body["metrics"]["hr_trimp"]["threshold_hour_reference"] is None
        assert body["metrics"]["hr_trimp"]["coefficients"] is None
    for reason in (trimp_reason, load_reason):
        assert reason is None or reason in session_load.SERVED_REASONS, reason


def test_gate_4_names_the_fields_without_a_value() -> None:
    assert compute_session_load(_synthetic(resting=None))["metrics"]["hr_trimp"]["unavailable_fields"] == ["resting_hr_bpm"]
    assert compute_session_load(_synthetic(max_hr=None))["metrics"]["hr_trimp"]["unavailable_fields"] == ["max_hr_bpm"]
    both = compute_session_load(_synthetic(resting=None, max_hr=None))["metrics"]["hr_trimp"]
    assert both["unavailable_fields"] == ["resting_hr_bpm", "max_hr_bpm"]
    # Only gate 4 fills the list.
    assert compute_session_load(_synthetic(resting=190, max_hr=190))["metrics"]["hr_trimp"]["unavailable_fields"] == []


# --- the equality rows are computed, never refused (r = 0 and r = 1) -----------------------------


def test_average_equal_to_resting_is_computed_with_r_zero() -> None:
    body = compute_session_load(_synthetic(heart_rate=50.0))
    assert _outcome(body) == (None, None, True)
    assert body["metrics"]["hr_trimp"]["value"] == 0.0
    assert body["session_load"]["value"] == 0.0
    assert body["session_load"]["driver"] == "hr_trimp"


def test_average_equal_to_max_is_computed_with_r_one() -> None:
    body = compute_session_load(_synthetic(heart_rate=190.0))
    assert _outcome(body) == (None, None, True)
    assert body["metrics"]["hr_trimp"]["value"] == pytest.approx(10.0 * 0.64 * math.exp(1.92))


def test_sex_defaulted_is_flagged_only_when_trimp_is_computed() -> None:
    assert compute_session_load(_synthetic(sex=None))["flags"] == ["sex_defaulted"]
    for refused in (dict(heart_rate=None), dict(resting=None), dict(resting=190, max_hr=190), dict(heart_rate=40.0), dict(max_hr=HUGE)):
        assert compute_session_load(_synthetic(sex=None, **refused))["flags"] == [], refused
    # The reference step comes after TRIMP, so the flag is still set under no_threshold_hr.
    assert compute_session_load(_synthetic(sex=None, threshold=None))["flags"] == ["sex_defaulted"]


# --- the adversarial table: the bounds of the values used -----------------------------------------

# (id, inputs, expected hr_trimp reason, expected session_load reason)
ADVERSARIAL_ROWS = [
    ("resting_0_is_computed", dict(resting=0, threshold=100), None, None),
    ("resting_0_max_0_is_order_conflict", dict(resting=0, max_hr=0), "order_conflict", "order_conflict"),
    ("max_0_below_resting_is_order_conflict", dict(max_hr=0), "order_conflict", "order_conflict"),
    ("threshold_0_is_threshold_order_conflict", dict(threshold=0), None, "threshold_order_conflict"),
    ("smallest_ordered_pair_at_r_one", dict(resting=149, max_hr=150, threshold=None), None, "no_threshold_hr"),
    ("smallest_ordered_pair_below_resting", dict(resting=151, max_hr=152), "avg_hr_below_resting", "avg_hr_below_resting"),
    ("max_two_to_the_63_is_computed", dict(max_hr=BIG), None, None),
    ("resting_two_to_the_63_is_order_conflict", dict(resting=BIG), "order_conflict", "order_conflict"),
    ("resting_two_to_the_63_under_a_larger_max_is_avg_below_resting", dict(resting=BIG, max_hr=BIG + 1), "avg_hr_below_resting", "avg_hr_below_resting"),
    ("max_ten_to_the_400_is_not_representable", dict(max_hr=HUGE), "not_representable", "not_representable"),
    ("resting_ten_to_the_400_is_order_conflict", dict(resting=HUGE), "order_conflict", "order_conflict"),
    ("resting_ten_to_the_400_under_a_larger_max_is_avg_below_resting", dict(resting=HUGE, max_hr=HUGE + 1), "avg_hr_below_resting", "avg_hr_below_resting"),
    ("threshold_ten_to_the_400_is_not_representable", dict(threshold=HUGE), "not_representable", "not_representable"),
    ("threshold_served_while_resting_and_max_conflict", dict(resting=190, max_hr=190, threshold=170), "order_conflict", "order_conflict"),
    ("huge_threshold_while_resting_and_max_conflict", dict(resting=190, max_hr=190, threshold=HUGE), "order_conflict", "order_conflict"),
    ("huge_threshold_under_no_hr", dict(heart_rate=None, threshold=HUGE), "no_hr", "no_hr"),
]


@pytest.mark.parametrize(
    ("overrides", "trimp_reason", "load_reason"),
    [pytest.param(*row[1:], id=row[0]) for row in ADVERSARIAL_ROWS],
)
def test_adversarial_bounds(overrides: dict, trimp_reason, load_reason) -> None:
    body = compute_session_load(_synthetic(**overrides))
    got = _outcome(body)
    print(f"  {overrides} -> {got} trimp={body['metrics']['hr_trimp']['value']}")
    assert got[:2] == (trimp_reason, load_reason)
    assert got[2] == (trimp_reason is None)
    value = body["metrics"]["hr_trimp"]["value"]
    assert value is None or math.isfinite(value)
    load = body["session_load"]["value"]
    assert load is None or math.isfinite(load)
    # The exact integer is echoed, never a float.
    for field_name in ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm"):
        echoed = body["inputs"][field_name]["value"]
        assert echoed is None or isinstance(echoed, int), field_name
    for reason in (trimp_reason, load_reason):
        assert reason is None or reason in session_load.SERVED_REASONS, reason


def test_max_two_to_the_63_gives_a_tiny_finite_trimp() -> None:
    body = compute_session_load(_synthetic(max_hr=BIG))
    trimp = body["metrics"]["hr_trimp"]["value"]
    assert 0.0 < trimp < 1e-12
    assert body["inputs"]["max_hr_bpm"]["value"] == BIG
    # The max-independent limit: hr_time_h * (avg - rest) / (thr - rest) * 100.
    assert body["session_load"]["value"] == pytest.approx(10.0 / 60.0 * (150 - 50) / (170 - 50) * 100.0, abs=0.01)


def test_every_served_reason_is_in_the_reference_order() -> None:
    """The module's own list of the reasons it can serve: gates 1-9 and the two reference
    reasons, in the reference's order."""
    assert session_load.SERVED_REASONS == (
        "sport_not_running",
        "declared_capture",
        "no_hr",
        "missing_anchor",
        "order_conflict",
        "avg_hr_below_resting",
        "avg_hr_above_max",
        "wrist_hr_threshold_unknown",
        "wrist_hr_at_threshold",
        "not_representable",
        "no_threshold_hr",
        "threshold_order_conflict",
    )
