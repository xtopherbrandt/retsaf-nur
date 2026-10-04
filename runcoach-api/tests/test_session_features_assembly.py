"""``metrics/session_features.py``: the assembly of the session features with their gates (F013).

Pure unit tests at the module seam, over hand-built row dicts and session
mappings. The module computes no grade, cost, NGP or descriptor itself; these
tests pin what it adds on top of the wave modules:

- the flat invariant (constant altitude, and no altitude at all);
- the clamp seam, both signs, with exactly 0.45 in range and 0.5 clamped;
- the session-wide overrides and their precedence, and ``no_distance``;
- the GAP clause of the recorded-time rule (pause and duplicate inserted);
- the pass-through and derived fields, the flag order and uniqueness, and the
  14-key response shape;
- the identity ``avg_pace / gap_avg_pace == distance-weighted mean g``;
- the per-segment stream and its block numbering against NGP's.

Expected values are derived in the test body from the reference's rules: the
cost polynomial's coefficients are restated here, never read from the module.
"""

from __future__ import annotations

import math
import random

import pytest
from runcoach_api.metrics import ngp as N
from runcoach_api.metrics import segments as S
from runcoach_api.metrics import session_features as SF

FEATURE_KEYS = {
    "duration_s",
    "distance_m",
    "avg_pace_s_per_km",
    "gap_avg_pace_s_per_km",
    "ngp_speed_m_s",
    "ngp_pace_s_per_km",
    "avg_hr_bpm",
    "avg_cadence_spm",
    "avg_power_w",
    "total_ascent_m",
    "total_descent_m",
    "env_temperature_c",
    "env_humidity_pct",
    "env_wind_ms",
}


def _g_oracle(i: float) -> float:
    """Minetti's C(i) / 3.6 from the published coefficients, independent of the module."""
    return (155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6) / 3.6


def _session(sport: str = "running", flags: list[str] | None = None, context: dict | None = None) -> dict:
    return {
        "session_id": "s1",
        "sport": sport,
        "quality_flags": [] if flags is None else flags,
        "context": {} if context is None else context,
    }


def _ramp(n: int, speed: float, grade: float | None, alt0: float = 0.0, **extra) -> list[dict]:
    """``n`` records on a 1 s grid at ``speed`` m/s, altitude rising ``grade`` per metre (None: absent)."""
    rows = []
    for k in range(n):
        row = {"t": float(k), "distance": speed * k, "speed": speed}
        if grade is not None:
            row["altitude"] = alt0 + grade * speed * k
        row.update({name: value[k] if isinstance(value, list) else value for name, value in extra.items()})
        rows.append(row)
    return rows


def _value(response: dict, name: str) -> float:
    feature = response["features"][name]
    assert feature["unavailable"] is None, (name, feature)
    return feature["value"]


def _reason(response: dict, name: str) -> str:
    feature = response["features"][name]
    assert feature["value"] is None, (name, feature)
    return feature["unavailable"]


# -- AC4: flat or ungraded ground --------------------------------------------------------------------


def test_constant_altitude_leaves_gap_pace_equal_to_raw_pace():
    rows = _ramp(120, 3.0, grade=None, altitude=10.0)
    features = SF.compute_session_features(_session(), rows)["features"]
    assert features["gap_avg_pace_s_per_km"]["value"] == pytest.approx(
        features["avg_pace_s_per_km"]["value"], rel=1e-9
    )
    assert features["avg_pace_s_per_km"]["value"] == pytest.approx(1000.0 / 3.0, rel=1e-12)


def test_no_altitude_gives_equal_paces_zero_coverage_and_the_gap_unavailable_flag():
    out = SF.compute_session_features(_session(), _ramp(120, 3.0, grade=None))
    assert _value(out, "gap_avg_pace_s_per_km") == pytest.approx(_value(out, "avg_pace_s_per_km"), rel=1e-9)
    assert out["gap_coverage"] == 0.0
    assert out["flags"] == ["gap_unavailable"]
    assert out["grade_clamped_fraction"] == 0.0


def test_a_fully_graded_flat_run_has_coverage_one_and_no_gap_unavailable_flag():
    out = SF.compute_session_features(_session(), _ramp(120, 3.0, grade=0.0))
    assert out["gap_coverage"] == 1.0
    assert out["flags"] == []


# -- AC2: the clamp --------------------------------------------------------------------------------


def test_a_ramp_at_grade_0_5_is_clamped_to_0_45_counted_and_flagged():
    # 1 m/s, altitude 0.5 m per metre: every window reads i = 0.5, above the 22.5 m over 50 m boundary.
    out = SF.compute_session_features(_session(), _ramp(120, 1.0, grade=0.5))
    assert "grade_clamped" in out["flags"]
    assert out["grade_clamped_fraction"] == pytest.approx(1.0)
    assert out["gap_coverage"] == pytest.approx(1.0)
    # Every segment carries g(0.45), so avg / gap is exactly that factor.
    ratio = _value(out, "avg_pace_s_per_km") / _value(out, "gap_avg_pace_s_per_km")
    assert ratio == pytest.approx(_g_oracle(0.45), rel=1e-9)
    assert all(row["clamped"] and row["i"] == 0.45 for row in SF.segment_rows(_session(), _ramp(120, 1.0, grade=0.5)))


def test_a_ramp_at_exactly_0_45_is_in_range_and_not_clamped():
    # 20 m and 9 m per record: (9n) / (20n) is the float nearest 0.45, on the inclusive boundary.
    rows = [{"t": float(k), "distance": 20.0 * k, "speed": 20.0, "altitude": 9.0 * k} for k in range(60)]
    out = SF.compute_session_features(_session(), rows)
    assert "grade_clamped" not in out["flags"]
    assert out["grade_clamped_fraction"] == 0.0
    assert out["gap_coverage"] == 1.0
    ratio = _value(out, "avg_pace_s_per_km") / _value(out, "gap_avg_pace_s_per_km")
    assert ratio == pytest.approx(_g_oracle(0.45), rel=1e-9)
    assert not any(row["clamped"] for row in SF.segment_rows(_session(), rows))


def test_steep_downhill_clamps_to_minus_0_45_and_no_gap_speed_is_negative_or_non_finite():
    out = SF.compute_session_features(_session(), _ramp(120, 1.0, grade=-0.6, alt0=100.0))
    assert "grade_clamped" in out["flags"]
    assert out["grade_clamped_fraction"] == pytest.approx(1.0)
    gap_pace = _value(out, "gap_avg_pace_s_per_km")
    assert math.isfinite(gap_pace) and gap_pace > 0.0
    ratio = _value(out, "avg_pace_s_per_km") / gap_pace
    assert ratio == pytest.approx(_g_oracle(-0.45), rel=1e-9)
    assert ratio == pytest.approx(1.12, abs=0.01)
    for row in SF.segment_rows(_session(), _ramp(120, 1.0, grade=-0.6, alt0=100.0)):
        assert row["i"] == -0.45 and row["clamped"]
        assert math.isfinite(row["v_actual"] * row["g"]) and row["v_actual"] * row["g"] > 0.0


# -- AC8: the overrides and no_distance ------------------------------------------------------------


def test_a_non_running_sport_makes_every_feature_sport_not_running():
    out = SF.compute_session_features(_session("cycling", flags=["smart_recording"]), _ramp(120, 3.0, 0.0))
    assert {v["unavailable"] for v in out["features"].values()} == {"sport_not_running"}
    assert {v["value"] for v in out["features"].values()} == {None}
    assert out["gap_coverage"] is None
    assert out["grade_clamped_fraction"] is None
    assert out["gps_degraded_fraction"] is None
    assert out["flags"] == ["smart_recording"]
    assert out["session_id"] == "s1" and out["sport"] == "cycling"
    assert out["features"]["avg_power_w"]["power_model"] is None


def test_sport_not_running_takes_precedence_over_no_records():
    out = SF.compute_session_features(_session("other"), [])
    assert {v["unavailable"] for v in out["features"].values()} == {"sport_not_running"}


def test_a_running_session_with_no_rows_makes_every_feature_no_records():
    out = SF.compute_session_features(_session(flags=["rr_artefact_burst"]), [])
    assert {v["unavailable"] for v in out["features"].values()} == {"no_records"}
    assert out["flags"] == ["rr_artefact_burst"]
    assert out["gap_coverage"] is None and out["grade_clamped_fraction"] is None
    assert out["gps_degraded_fraction"] is None


def test_a_constant_distance_is_no_distance_never_zero_and_the_fractions_are_null():
    rows = [{"t": float(k), "distance": 500.0, "speed": 3.0, "altitude": 10.0} for k in range(120)]
    out = SF.compute_session_features(_session(), rows)
    for name in ("distance_m", "avg_pace_s_per_km", "gap_avg_pace_s_per_km"):
        assert _reason(out, name) == "no_distance", name
    assert out["gap_coverage"] is None
    assert out["grade_clamped_fraction"] is None
    assert out["gps_degraded_fraction"] is None
    assert out["flags"] == []
    assert _value(out, "duration_s") == 119.0
    # NGP reads device speed, so it may still hold a value at D = 0.
    assert _value(out, "ngp_speed_m_s") == pytest.approx(3.0)


def test_a_single_record_has_no_counted_segments():
    out = SF.compute_session_features(_session(), [{"t": 0.0, "distance": 0.0, "speed": 3.0}])
    assert _reason(out, "duration_s") == "no_counted_segments"
    assert _reason(out, "distance_m") == "no_distance"
    assert _reason(out, "ngp_speed_m_s") == "no_30s_block"
    assert _reason(out, "ngp_pace_s_per_km") == "no_30s_block"


# -- AC5 on graded ground: the GAP clause ----------------------------------------------------------


def _graded_run() -> list[dict]:
    """A 5 % ramp for 100 records, then flat, at 2 m/s."""
    rows = []
    for k in range(240):
        altitude = 0.1 * k if k <= 100 else 10.0
        rows.append({"t": float(k), "distance": 2.0 * k, "speed": 2.0, "altitude": altitude})
    return rows


def test_a_pause_and_a_duplicate_on_the_flat_leave_gap_pace_identical():
    plain = _graded_run()
    # A 600 s standstill after record 159: dt = 600, dd = 0, and every stride of ``plain`` is kept.
    paused = plain[:160] + [{**plain[159], "t": plain[159]["t"] + 600.0}]
    paused += [{**row, "t": row["t"] + 600.0} for row in plain[160:]]
    duplicated = plain[:160] + [dict(plain[159])] + plain[160:]
    gap = [_value(SF.compute_session_features(_session(), rows), "gap_avg_pace_s_per_km") for rows in (plain, paused, duplicated)]
    assert gap[0] == gap[1] == gap[2]
    # The run is graded, so this pins more than the flat invariant.
    out = SF.compute_session_features(_session(), plain)
    assert _value(out, "gap_avg_pace_s_per_km") < _value(out, "avg_pace_s_per_km")


# -- Pass-through and derived fields ---------------------------------------------------------------


def test_gps_degraded_records_give_their_share_of_distance_as_a_fraction():
    tags = [["gps_degraded"] if k < 40 else [] for k in range(120)]
    out = SF.compute_session_features(_session(), _ramp(120, 3.0, 0.0, sample_quality=tags))
    # 119 counted segments of 3 m; the first 40 start on a tagged record.
    assert out["gps_degraded_fraction"] == pytest.approx(40 / 119)
    assert out["flags"] == []


def test_the_environment_context_reaches_features_verbatim():
    context = {"env_temperature_c": 12.5, "env_humidity_pct": 80, "env_wind_ms": 3.2}
    out = SF.compute_session_features(_session(context=context), _ramp(120, 3.0, 0.0))
    assert out["features"]["env_temperature_c"] == {"value": 12.5, "unavailable": None}
    assert out["features"]["env_humidity_pct"] == {"value": 80, "unavailable": None}
    assert out["features"]["env_wind_ms"] == {"value": 3.2, "unavailable": None}
    bare = SF.compute_session_features(_session(), _ramp(120, 3.0, 0.0))
    assert bare["features"]["env_wind_ms"] == {"value": None, "unavailable": "not_recorded"}


def test_ngp_pace_is_1000_over_ngp_speed():
    out = SF.compute_session_features(_session(), _ramp(120, 2.5, 0.0))
    speed = _value(out, "ngp_speed_m_s")
    assert speed == pytest.approx(2.5, rel=1e-12)
    assert _value(out, "ngp_pace_s_per_km") == pytest.approx(1000.0 / speed, rel=1e-12)


def test_the_descriptors_and_power_model_are_carried_through():
    rows = _ramp(120, 3.0, 0.0, heart_rate=150, cadence=170, power=250, power_model="stryd")
    out = SF.compute_session_features(_session(), rows)
    assert _value(out, "avg_hr_bpm") == pytest.approx(150.0)
    assert _value(out, "avg_cadence_spm") == pytest.approx(170.0)
    assert out["features"]["avg_power_w"] == {"value": pytest.approx(250.0), "unavailable": None, "power_model": "stryd"}
    assert _value(out, "total_ascent_m") == 0.0 and _value(out, "total_descent_m") == 0.0


# -- Flags -----------------------------------------------------------------------------------------


def _regressed_half_graded_steep() -> list[dict]:
    """A 0.5 ramp on the first half, no altitude on the second, and one distance regression."""
    rows = _ramp(120, 1.0, grade=0.5)
    for row in rows[60:]:
        del row["altitude"]
    rows[90]["distance"] = rows[89]["distance"] - 0.5
    return rows


def test_flags_follow_the_session_flags_in_the_reference_order_each_at_most_once():
    out = SF.compute_session_features(_session(flags=["rr_artefact_burst", "distance_regressed"]), _regressed_half_graded_steep())
    assert out["flags"] == ["rr_artefact_burst", "distance_regressed", "gap_unavailable", "grade_clamped"]
    assert 0.0 < out["gap_coverage"] < 1.0
    assert out["grade_clamped_fraction"] == pytest.approx(out["gap_coverage"])


def test_the_derived_flags_alone_come_in_the_reference_order():
    # No session flag names any of the three, so the order below is the module's own.
    out = SF.compute_session_features(_session(), _regressed_half_graded_steep())
    assert out["flags"] == ["distance_regressed", "gap_unavailable", "grade_clamped"]


def test_smart_recording_is_listed_first_when_present():
    out = SF.compute_session_features(_session(flags=["rr_artefact_burst", "smart_recording"]), _ramp(120, 3.0, 0.0))
    assert out["flags"] == ["smart_recording", "rr_artefact_burst"]


def test_the_session_quality_flags_are_never_mutated():
    flags = ["rr_artefact_burst"]
    session = _session(flags=flags)
    out = SF.compute_session_features(session, _regressed_half_graded_steep())
    assert flags == ["rr_artefact_burst"] and session["quality_flags"] is flags
    assert out["flags"][0] == "rr_artefact_burst" and len(out["flags"]) == 4


# -- Response shape --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "session, rows",
    [
        (_session(), _ramp(120, 3.0, 0.05, heart_rate=150, cadence=170, power=250, power_model="stryd")),
        (_session("other"), _ramp(120, 3.0, 0.0)),
        (_session(), []),
        (_session(), [{"t": 0.0, "distance": 0.0}]),
    ],
)
def test_the_response_has_the_seven_keys_and_fourteen_features_with_exactly_one_side_set(session, rows):
    out = SF.compute_session_features(session, rows)
    assert set(out) == {
        "session_id",
        "sport",
        "flags",
        "gap_coverage",
        "grade_clamped_fraction",
        "gps_degraded_fraction",
        "features",
    }
    assert set(out["features"]) == FEATURE_KEYS
    for name, feature in out["features"].items():
        expected_keys = {"value", "unavailable", "power_model"} if name == "avg_power_w" else {"value", "unavailable"}
        assert set(feature) == expected_keys, name
        assert (feature["value"] is None) != (feature["unavailable"] is None), name


# -- The mean-g identity ---------------------------------------------------------------------------


def _random_graded_session(seed: int = 13) -> list[dict]:
    """Irregular strides and a wandering altitude, so time and distance weights differ."""
    rng = random.Random(seed)
    rows = []
    t = d = 0.0
    alt = 50.0
    for _ in range(300):
        rows.append({"t": t, "distance": d, "speed": 3.0, "altitude": alt})
        t += 1.0
        d += rng.uniform(0.5, 5.0)
        alt += rng.uniform(-0.3, 0.3)
    return rows


def test_avg_over_gap_pace_is_the_distance_weighted_mean_g():
    session = _session()
    rows = _random_graded_session()
    out = SF.compute_session_features(session, rows)
    stream = SF.segment_rows(session, rows)
    distance = sum(row["contributed_m"] for row in stream)
    assert distance == pytest.approx(_value(out, "distance_m"), rel=1e-12)
    distance_weighted = sum(row["contributed_m"] * row["g"] for row in stream) / distance
    time_weighted = sum(row["dt"] * row["g"] for row in stream) / sum(row["dt"] for row in stream)
    assert distance_weighted != pytest.approx(time_weighted, rel=1e-6)  # the two weightings are distinguishable
    ratio = _value(out, "avg_pace_s_per_km") / _value(out, "gap_avg_pace_s_per_km")
    assert ratio == pytest.approx(distance_weighted, rel=1e-9)
    assert any(row["g"] != 1.0 for row in stream)


# -- The per-segment stream ------------------------------------------------------------------------


def test_segment_rows_carry_the_reference_fields_for_every_counted_segment():
    rows = _ramp(60, 2.0, 0.05, heart_rate=[0] * 10 + [140] * 50, sample_quality=[["cadence_lock"]] * 20 + [[]] * 40)
    rows[30]["t"] += 600.0  # a break before record 30
    for row in rows[31:]:
        row["t"] += 600.0
    stream = SF.segment_rows(_session(), rows)
    tb = S.build_time_base(S.screen(rows))
    counted = [seg for seg in tb.segments if seg.counted]
    assert len(stream) == len(counted) == 58
    fields = ["t_start", "dt", "s_start", "contributed_m", "v_actual", "speed", "i", "graded", "clamped", "g", "block_id", "heart_rate", "hr_excluded"]
    for row, seg in zip(stream, counted):
        assert list(row) == fields
        assert row["t_start"] == tb.records[seg.k].t and row["dt"] == seg.dt
        assert row["s_start"] == tb.s[seg.k] and row["contributed_m"] == seg.contributed_m
        assert row["v_actual"] == seg.v_actual and row["speed"] == 2.0
        assert row["graded"] and not row["clamped"] and row["g"] == pytest.approx(_g_oracle(row["i"]))
    assert stream[0]["heart_rate"] is None and stream[0]["hr_excluded"]  # HR 0 is absent
    assert stream[15]["heart_rate"] == 140.0 and stream[15]["hr_excluded"]  # locked
    assert stream[25]["heart_rate"] == 140.0 and not stream[25]["hr_excluded"]
    assert sum(row["dt"] for row in stream) == _value(SF.compute_session_features(_session(), rows), "duration_s")


def test_segment_rows_block_ids_equal_ngp_block_numbering():
    rows = _ramp(90, 2.0, 0.0)
    rows[40]["speed"] = None  # closes block 0 at record 39; record 41 starts block 1
    for row in rows[70:]:
        row["t"] += 100.0  # a break before record 70 starts block 2
    stream = SF.segment_rows(_session(), rows)
    tb = S.build_time_base(S.screen(rows))
    ids = N.record_block_ids(tb)
    assert [row["block_id"] for row in stream] == [ids[seg.k + 1] for seg in tb.segments if seg.counted]
    assert sorted({row["block_id"] for row in stream} - {None}) == [0, 1, 2]
    assert stream[39]["block_id"] is None  # the segment ending at the absent-speed record
    assert stream[38]["block_id"] == 0 and stream[40]["block_id"] == 1
    assert stream[-1]["block_id"] == 2


def test_segment_rows_are_empty_for_a_non_running_sport_or_no_rows():
    assert SF.segment_rows(_session("other"), _ramp(60, 2.0, 0.0)) == []
    assert SF.segment_rows(_session(), []) == []
