"""``metrics/descriptors.py``: the grade-free session descriptors over the time base (F013 AC7).

Pure unit tests over hand-built row dicts pushed through ``segments.screen``
and ``segments.build_time_base``, at the module seam. Each descriptor is a
time-weighted mean of a value read at the **start record** of each counted
segment, or an ``unavailable`` reason when nothing qualifies; the rules under
test are the reference's gate-aware screens:

- HR of 0 or absent is excluded; HR on a ``cadence_lock`` record is excluded;
  no present HR gives ``no_heart_rate`` and all-locked HR gives ``cadence_lock``
  (that precedence is pinned too).
- Cadence of 0 (a stopped sample) is excluded; nothing qualifying gives
  ``no_cadence``.
- Power with a null ``power_model`` still counts; only distinct **non-null**
  models make ``mixed_power_models``.
- Ascent and descent come from a 1 m hysteresis over the present altitude
  samples, pinned below, at and above the threshold; fewer than two samples
  give ``no_altitude``.
- The three ``env_*`` features are verbatim or ``not_recorded``.
- ``gps_degraded_fraction`` is a contributed-distance fraction, None at D = 0,
  and gps-degraded samples are excluded from nothing.

The expected values were derived by hand from the reference's rules before the
module existed, never read back from it.
"""

from __future__ import annotations

import pytest
from runcoach_api.metrics import descriptors as D
from runcoach_api.metrics import segments as S

TOL = 1e-9


def _rows(n: int = 5, **fields) -> list[dict]:
    """``n`` records 1 s and 1 m apart, so every segment is counted with dt = 1.

    Each keyword is either a scalar applied to every row or a per-row list, so a
    tag list that should apply to every row is passed as ``[[tag]] * n``.
    """
    out = []
    for k in range(n):
        row: dict = {"t": float(k), "distance": float(k)}
        for name, value in fields.items():
            row[name] = value[k] if isinstance(value, list) else value
        out.append(row)
    return out


def _tb(rows: list[dict]) -> S.TimeBase:
    return S.build_time_base(S.screen(rows))


# -- avg_hr ----------------------------------------------------------------------------------------


def test_hr_of_zero_is_excluded_from_avg_hr():
    tb = _tb(_rows(heart_rate=[150, 0, 150, 150, 150]))
    assert D.avg_hr(tb) == S.Feature(150.0, None)


def test_hr_absent_on_one_sample_is_excluded_from_avg_hr():
    tb = _tb(_rows(heart_rate=[140, None, 160, 140, 160]))
    # Counted segments start at records 0..3; record 1 is excluded, so 140, 160, 140 at dt = 1.
    assert D.avg_hr(tb).value == pytest.approx((140 + 160 + 140) / 3, abs=TOL)


def test_avg_hr_is_time_weighted_by_segment_dt():
    rows = [
        {"t": 0.0, "distance": 0.0, "heart_rate": 100},
        {"t": 1.0, "distance": 1.0, "heart_rate": 200},
        {"t": 4.0, "distance": 4.0, "heart_rate": 150},
    ]
    # Segment 0: dt 1 at 100; segment 1: dt 3 at 200. (100*1 + 200*3) / 4 = 175.
    assert D.avg_hr(_tb(rows)) == S.Feature(175.0, None)


def test_hr_only_on_non_counted_segments_is_no_heart_rate():
    # The one record carrying HR starts a break (dt = 600), so it is never counted.
    rows = [
        {"t": 0.0, "distance": 0.0, "heart_rate": 150},
        {"t": 600.0, "distance": 0.0},
        {"t": 601.0, "distance": 1.0},
    ]
    assert D.avg_hr(_tb(rows)) == S.Feature(None, "no_heart_rate")


def test_hr_absent_throughout_is_no_heart_rate():
    assert D.avg_hr(_tb(_rows())) == S.Feature(None, "no_heart_rate")


def test_hr_on_cadence_lock_samples_is_excluded_from_avg_hr():
    tags = [["cadence_lock"], [], [], ["cadence_lock"], []]
    tb = _tb(_rows(heart_rate=[170, 150, 150, 170, 150], sample_quality=tags))
    assert D.avg_hr(tb) == S.Feature(150.0, None)


def test_every_hr_sample_cadence_locked_gives_cadence_lock():
    tb = _tb(_rows(heart_rate=170, sample_quality=[["cadence_lock"]] * 5))
    assert D.avg_hr(tb) == S.Feature(None, "cadence_lock")


def test_no_present_hr_takes_precedence_over_cadence_lock():
    # Locked records with no HR at all: there is no HR to be locked out of.
    tb = _tb(_rows(sample_quality=[["cadence_lock"]] * 5))
    assert D.avg_hr(tb) == S.Feature(None, "no_heart_rate")


def test_cadence_lock_excludes_by_the_segment_start_record_only():
    # Only the last record is locked, and it starts no counted segment.
    tags = [[], [], [], [], ["cadence_lock"]]
    tb = _tb(_rows(heart_rate=150, sample_quality=tags))
    assert D.avg_hr(tb) == S.Feature(150.0, None)


def test_hr_excluded_names_the_rule_once():
    present, absent, locked = S.screen(
        [
            {"t": 0.0, "heart_rate": 150},
            {"t": 1.0, "heart_rate": 0},
            {"t": 2.0, "heart_rate": 150, "sample_quality": ["cadence_lock"]},
        ]
    )
    assert D.hr_excluded(present) is False
    assert D.hr_excluded(absent) is True
    assert D.hr_excluded(locked) is True


# -- avg_cadence -----------------------------------------------------------------------------------


def test_stopped_samples_with_cadence_zero_are_excluded_from_avg_cadence():
    tb = _tb(_rows(cadence=[170, 0, 0, 170, 170]))
    assert D.avg_cadence(tb) == S.Feature(170.0, None)


def test_cadence_zero_throughout_is_no_cadence():
    assert D.avg_cadence(_tb(_rows(cadence=0))) == S.Feature(None, "no_cadence")


def test_cadence_absent_throughout_is_no_cadence():
    assert D.avg_cadence(_tb(_rows())) == S.Feature(None, "no_cadence")


def test_gps_degraded_samples_are_not_excluded_from_any_mean():
    tags = [["gps_degraded"], ["gps_degraded"], [], [], []]
    tb = _tb(_rows(heart_rate=[200, 200, 100, 100, 100], cadence=[180, 180, 160, 160, 160], sample_quality=tags))
    assert D.avg_hr(tb) == S.Feature(150.0, None)
    assert D.avg_cadence(tb) == S.Feature(170.0, None)


# -- avg_power -------------------------------------------------------------------------------------


def test_avg_power_returns_the_mean_and_the_one_model():
    tb = _tb(_rows(power=[200, 300, 200, 300, 999], power_model="stryd"))
    feature, model = D.avg_power(tb)
    assert feature == S.Feature(250.0, None)
    assert model == "stryd"


def test_no_power_is_no_power_with_no_model():
    assert D.avg_power(_tb(_rows())) == (S.Feature(None, "no_power"), None)


def test_two_distinct_power_models_give_mixed_power_models():
    models = ["stryd", "stryd", "garmin_rp", "stryd", "stryd"]
    tb = _tb(_rows(power=250, power_model=models))
    assert D.avg_power(tb) == (S.Feature(None, "mixed_power_models"), None)


def test_interpolated_power_with_a_null_model_still_counts():
    # The gate interpolates power across a gap but never its model, so the
    # interpolated record carries power with a None model. It counts, and
    # None is not a second model.
    models = ["stryd", None, "stryd", None, "stryd"]
    tb = _tb(_rows(power=[200, 300, 200, 300, 200], power_model=models))
    assert D.avg_power(tb) == (S.Feature(250.0, None), "stryd")


def test_power_with_every_model_null_reports_the_value_and_no_model():
    tb = _tb(_rows(power=250))
    assert D.avg_power(tb) == (S.Feature(250.0, None), None)


# -- ascent_descent --------------------------------------------------------------------------------


def test_hysteresis_sums_only_moves_of_at_least_one_metre():
    # 0.6 up: below threshold, ref stays 0. 1.2: >= 1 from 0, ascent 1.2, ref 1.2.
    # 0.1: ref - a = 1.1, descent 1.1, ref 0.1. 2.0: ascent 1.9. Totals 3.1 and 1.1.
    asc, desc = D.ascent_descent(_tb(_rows(altitude=[0.0, 0.6, 1.2, 0.1, 2.0])))
    assert asc.unavailable is None and asc.value == pytest.approx(3.1, abs=TOL)
    assert desc.unavailable is None and desc.value == pytest.approx(1.1, abs=TOL)


def test_hysteresis_counts_a_move_of_exactly_one_metre():
    asc, desc = D.ascent_descent(_tb(_rows(altitude=[10.0, 11.0, 11.0, 10.0, 10.0])))
    assert asc == S.Feature(1.0, None)
    assert desc == S.Feature(1.0, None)


def test_hysteresis_ignores_a_drift_that_never_reaches_one_metre():
    # Each step is 0.4 m but the total rise is 1.6 m; the reference never moves,
    # so after the third step the cumulative 1.2 m does register as one ascent.
    asc, desc = D.ascent_descent(_tb(_rows(altitude=[0.0, 0.4, 0.8, 1.2, 1.6])))
    assert asc == S.Feature(1.2, None)
    assert desc == S.Feature(0.0, None)


def test_hysteresis_skips_absent_altitude_samples():
    asc, desc = D.ascent_descent(_tb(_rows(altitude=[0.0, None, float("nan"), 2.0, None])))
    assert asc == S.Feature(2.0, None)
    assert desc == S.Feature(0.0, None)


def test_fewer_than_two_altitude_samples_is_no_altitude_on_both():
    asc, desc = D.ascent_descent(_tb(_rows(altitude=[None, 5.0, None, None, None])))
    assert asc == S.Feature(None, "no_altitude")
    assert desc == S.Feature(None, "no_altitude")
    asc, desc = D.ascent_descent(_tb(_rows()))
    assert (asc, desc) == (S.Feature(None, "no_altitude"), S.Feature(None, "no_altitude"))


def test_flat_altitude_gives_zero_ascent_and_descent_as_values():
    asc, desc = D.ascent_descent(_tb(_rows(altitude=100.0)))
    assert asc == S.Feature(0.0, None)
    assert desc == S.Feature(0.0, None)


# -- env_features ----------------------------------------------------------------------------------


def test_env_features_are_verbatim_when_present():
    env = D.env_features({"env_temperature_c": 12.5, "env_humidity_pct": 80.0, "env_wind_ms": 3.0})
    assert env == {
        "env_temperature_c": S.Feature(12.5, None),
        "env_humidity_pct": S.Feature(80.0, None),
        "env_wind_ms": S.Feature(3.0, None),
    }


def test_env_features_null_or_missing_are_not_recorded():
    env = D.env_features({"env_temperature_c": None, "env_wind_dir": "NW"})
    assert set(env) == {"env_temperature_c", "env_humidity_pct", "env_wind_ms"}
    assert all(f == S.Feature(None, "not_recorded") for f in env.values())


def test_env_features_zero_is_a_value_not_a_gap():
    env = D.env_features({"env_temperature_c": 0.0, "env_humidity_pct": 0, "env_wind_ms": 0.0})
    assert env["env_temperature_c"] == S.Feature(0.0, None)
    assert env["env_wind_ms"] == S.Feature(0.0, None)


# -- gps_degraded_fraction -------------------------------------------------------------------------


def test_gps_degraded_fraction_is_contributed_distance_over_d():
    # Records 0 and 3 are tagged. Segments contribute 3, 0, 1, 2 m (D = 6); the tagged
    # ones (segments 0 and 3) contribute 3 + 2 = 5 m, so the fraction is 5/6, not 2/4 by count.
    tags = [["gps_degraded"], [], [], ["gps_degraded"], []]
    tb = _tb(_rows(distance=[0.0, 3.0, 3.0, 4.0, 6.0], sample_quality=tags))
    assert tb.D == 6.0
    assert D.gps_degraded_fraction(tb) == pytest.approx(5.0 / 6.0, abs=TOL)


def test_gps_degraded_fraction_is_zero_when_no_sample_is_tagged():
    assert D.gps_degraded_fraction(_tb(_rows())) == 0.0


def test_gps_degraded_fraction_is_none_when_d_is_zero():
    tb = _tb(_rows(distance=0.0, sample_quality=[["gps_degraded"]] * 5))
    assert tb.D == 0.0
    assert D.gps_degraded_fraction(tb) is None


def test_gps_degraded_fraction_reads_the_segment_start_record():
    # Only the last record is tagged; it starts no segment, so nothing counts.
    tags = [[], [], [], [], ["gps_degraded"]]
    assert D.gps_degraded_fraction(_tb(_rows(sample_quality=tags))) == 0.0
