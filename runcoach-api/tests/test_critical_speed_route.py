"""``GET /state`` serves critical speed (CS) and D' as its third determinant (F033 AC5, AC6;
reference section 6).

Runs are stored as ``support/goal_seed.py`` stores them: ``db.persist`` with synthetic 1 Hz flat
records at a constant speed, so F013's GAP speed is the speed run and a run's best stretch at a
length L it covers is ``L * speed``. Every saved load is replaced through
``support/session_loads.py``, so the load chart is the test's. The clock is frozen through
``main._utcnow`` and every request runs under the autouse isolated store, whose
``athlete_timezone`` is UTC.

The expected fits come from ``support/cs_oracle.py``, which imports nothing from ``runcoach_api``;
the reference section 1 constants are typed in here from the reference.
"""

from __future__ import annotations

import copy
import dataclasses
import importlib.util
import json
import math
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.main import app
from runcoach_api.metrics import critical_speed
from runcoach_api.schemas import (
    CriticalSpeedDeterminant,
    CriticalSpeedExclusion,
    CriticalSpeedParameters,
    CriticalSpeedStretch,
    DPrime,
)

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
SUPPORT = Path(__file__).parent / "support"


def _load_support(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


goal_seed = _load_support("goal_seed")
cs_oracle = _load_support("cs_oracle")
contract_validate = _load_support("contract_validate")
contract_walk = _load_support("contract_walk")

NOW = goal_seed.NOW  # 2026-10-14 12:00 UTC
NAMES = ["functional_threshold_pace", "riegel_exponent", "critical_speed"]
UNAVAILABLE = ["no_candidate_runs", "too_few_stretches", "spread_too_narrow", "longest_far_off", "estimate_not_finite"]

# F033 reference section 1, typed in from the reference (not read from the estimator), with the
# far-off cut of the F034 reference section 1.
REFERENCE_PARAMETERS = {
    "window_days": 90,
    "stretch_lengths_s": [120, 180, 300, 480, 720, 1200],
    "pause_break_s": 10,
    "population_b": 1.06,
    "far_off_s_per_km": 4,
    "min_stretches": 3,
    "max_shortest_s": 300,
    "min_longest_s": 720,
    "short_race_max_s": 1800,
    "coverage": 0.80,
}


def _sept(day: int) -> datetime:
    return datetime(2026, 9, day, 7, 0, tzinfo=UTC)


# Six flat runs, (start, pace s/km, time s): each one's best at its expected length is the only
# choice there, from the longest length down (one run per length), and each shorter stretch is
# faster than every longer one, so all six are kept. The far-off excess is about +2.8 s/km, under
# the 4 s/km cut (the 720 s pace of 245 predicts about 252.19 at 1200 s).
CURVE = (
    (_sept(1), 255.0, 1300.0),  # 1200 s
    (_sept(3), 245.0, 800.0),  # 720 s
    (_sept(5), 235.0, 500.0),  # 480 s
    (_sept(7), 225.0, 320.0),  # 300 s
    (_sept(9), 215.0, 200.0),  # 180 s
    (_sept(11), 205.0, 130.0),  # 120 s
)
CURVE_LENGTHS = (1200, 720, 480, 300, 180, 120)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


@pytest.fixture(scope="module")
def contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _run(start: datetime, pace: float, time_s: float, *, name: str | None = None) -> str:
    """An untagged flat run of ``time_s`` at ``pace`` s/km."""
    return goal_seed._store(start, time_s * 1000.0 / pace, time_s, name=name)


def _runs(*runs: tuple[datetime, float, float]) -> list[str]:
    return [_run(*run) for run in runs]


def _sql(statement: str, *params) -> None:
    conn = db_module.get_connection()
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def _state(client: TestClient, as_of: str | None = None) -> dict:
    response = client.get("/state", params={} if as_of is None else {"as_of": as_of})
    assert response.status_code == 200, response.text
    return response.json()


def _cs(body: dict) -> dict:
    names = [d["name"] for d in body["determinants"]]
    assert names == NAMES, names
    return body["determinants"][2]


def _assert_validates(contract: dict, entry: dict) -> None:
    errors = contract_validate.validate(contract, contract["components"]["schemas"]["Determinant"], entry, "$.cs")
    print(f"  {entry['name']} unavailable={entry['unavailable']}: {errors or 'valid'}")
    assert errors == [], errors


def _print_stretches(entry: dict) -> None:
    for s in entry["stretches"]:
        print(
            f"  {s['length_s']:>5} s {s['distance_m']!r:>22} m pace {s['pace_s_per_km']!r} "
            f"{s['session_id'][:10]} {s['start_time']} drop={s['drop_reason']} by={s['beaten_by_s']}"
        )


# ---------------------------------------------------------------------------
# The entry
# ---------------------------------------------------------------------------


def test_the_critical_speed_entry_carries_exactly_the_schema_keys(contract: dict) -> None:
    """The third entry is ``critical_speed``: its keys, and each stretch's, exclusion's, D''s and
    the parameters' keys, are exactly the pydantic model's and the contract component's, so no
    estimator field (D''s standard error among them) leaks into the response; each served stretch
    is the expected run's best at its length, all kept, and every figure is the pace the run was
    stored at."""
    with TestClient(app) as client:
        ids = _runs(*CURVE)
        goal_seed._plant_every_load()
        _sql("UPDATE sessions SET quality_flags = ? WHERE session_id = ?", '["smart_recording"]', ids[0])
        flagged = ids[0]
        # Takes the 1200 s length in the flagged run's place (excess about +3.8, under the cut).
        extra = _run(_sept(2), 256.0, 1300.0)
        goal_seed._plant_every_load()
        entry = _cs(_state(client))
    _print_stretches(entry)
    schemas = contract["components"]["schemas"]
    print(f"  keys {sorted(entry)}")
    assert set(entry) == set(CriticalSpeedDeterminant.model_fields) == set(schemas["CriticalSpeedDeterminant"]["properties"])
    assert "d_prime_error_m" not in entry
    assert set(entry["d_prime"]) == set(DPrime.model_fields) == set(schemas["DPrime"]["properties"])
    assert set(entry["parameters"]) == set(CriticalSpeedParameters.model_fields)
    assert set(entry["parameters"]) == set(schemas["CriticalSpeedParameters"]["properties"])
    for stretch in entry["stretches"]:
        assert set(stretch) == set(CriticalSpeedStretch.model_fields) == set(schemas["CriticalSpeedStretch"]["properties"])
    [exclusion] = entry["excluded"]
    assert set(exclusion) == set(CriticalSpeedExclusion.model_fields) == set(schemas["CriticalSpeedExclusion"]["properties"])
    assert (exclusion["session_id"], exclusion["reason"], exclusion["source"]) == (flagged, "smart_recording", "stored")

    assert entry["unavailable"] is None
    assert entry["trend"] == {"direction": "unknown", "rate": None}
    assert entry["estimate"]["unit"] == "s/km" and entry["estimate"]["provisional"] is False
    assert entry["estimate"]["confidence"] == "low"
    expected = dict(zip(CURVE_LENGTHS, [extra, *ids[1:]], strict=True))
    paces = dict(zip(CURVE_LENGTHS, [256.0] + [p for _s, p, _t in CURVE[1:]], strict=True))
    assert [s["length_s"] for s in entry["stretches"]] == sorted(CURVE_LENGTHS)
    for s in entry["stretches"]:
        assert s["session_id"] == expected[s["length_s"]]
        assert s["drop_reason"] is None and s["beaten_by_s"] is None
        assert s["distance_m"] == pytest.approx(s["length_s"] * 1000.0 / paces[s["length_s"]], rel=1e-9)
        assert s["pace_s_per_km"] == pytest.approx(paces[s["length_s"]], rel=1e-9)
        assert s["activity_tag"] is None and s["name"] is None
    assert entry["stretches"][-1]["duration_s"] == 1300.0
    assert entry["d_prime_not_positive"] is False
    assert entry["label_held_at_medium"] is False


def test_critical_speed_interval_brackets_cs_and_d_prime() -> None:
    """CS and D' sit inside their intervals, and every figure is the independent oracle's fit of the
    served kept stretches; the stretches themselves are the stored paces' distances."""
    with TestClient(app) as client:
        _runs(*CURVE)
        goal_seed._plant_every_load()
        entry = _cs(_state(client))
    _print_stretches(entry)
    points = [(float(L), L * 1000.0 / pace) for L, (_s, pace, _t) in zip(CURVE_LENGTHS, CURVE, strict=True)]
    points.sort()
    line = cs_oracle.line(points)
    estimate, d_prime = entry["estimate"], entry["d_prime"]
    print(f"  served CS {estimate}; D' {d_prime}; se {entry['error_m_per_s']}; t {entry['t_multiplier']}")
    print(f"  oracle CS {line.pace} [{line.pace_low}, {line.pace_high}]; D' {line.d_prime} [{line.d_prime_low}, {line.d_prime_high}]")
    interval = estimate["interval"]
    assert interval["low"] < estimate["value"] < interval["high"]
    assert d_prime["low_m"] < d_prime["value_m"] < d_prime["high_m"]
    assert estimate["value"] == pytest.approx(line.pace, rel=1e-9)
    assert interval["low"] == pytest.approx(line.pace_low, rel=1e-9)
    assert interval["high"] == pytest.approx(line.pace_high, rel=1e-9)
    assert d_prime["value_m"] == pytest.approx(line.d_prime, rel=1e-9)
    assert d_prime["low_m"] == pytest.approx(line.d_prime_low, rel=1e-9)
    assert d_prime["high_m"] == pytest.approx(line.d_prime_high, rel=1e-9)
    assert entry["error_m_per_s"] == pytest.approx(line.se, rel=1e-9)
    assert entry["t_multiplier"] == pytest.approx(line.t, rel=1e-6)
    assert entry["far_off_excess_s_per_km"] == pytest.approx(cs_oracle.far_off_excess(points[-2], points[-1]), rel=1e-9)


def test_critical_speed_parameters_equal_the_reference() -> None:
    """``parameters`` is reference section 1, values and JSON types alike: ``pause_break_s`` is the
    integer 10 the reference writes, not ``10.0``."""
    with TestClient(app) as client:
        response = client.get("/state")
    assert response.status_code == 200, response.text
    parameters = _cs(response.json())["parameters"]
    print(f"  served {parameters}")
    assert parameters == REFERENCE_PARAMETERS
    assert {k: type(v) for k, v in parameters.items()} == {k: type(v) for k, v in REFERENCE_PARAMETERS.items()}
    assert '"pause_break_s":10,' in response.text


# ---------------------------------------------------------------------------
# AC5: as_of, read-only, independence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tagged", "label"), [((), "low"), ((1200,), "medium"), ((1200, 720), "medium"), ((1200, 720, 300), "high")]
)
def test_the_label_and_each_stretch_s_run_are_served_as_the_estimator_reads_them(
    tagged: tuple[int, ...], label: str
) -> None:
    """The ``CURVE`` runs with the 1200 s and 720 s runs named, and the runs at the ``tagged``
    lengths tagged Race (1300 s, 800 s and 320 s, all 1800 s or less): the label counts them (0
    ``low``, 1 ``medium``, 2 or more ``high``), and ``high`` is held at ``medium`` unless a race
    fills a stretch of 300 s or less (F034 reference section 1); each stretch carries its run's
    tag, name and duration."""
    names = ("Threshold workout", "Speed Repeats")
    with TestClient(app) as client:
        ids = [_run(*CURVE[0], name=names[0]), _run(*CURVE[1], name=names[1]), *_runs(*CURVE[2:])]
        by_id = dict(zip(CURVE_LENGTHS, ids, strict=True))
        if tagged:
            goal_seed._put_tags(client, [by_id[length] for length in tagged])
        goal_seed._plant_every_load()
        entry = _cs(_state(client))
    _print_stretches(entry)
    by_length = {s["length_s"]: s for s in entry["stretches"]}
    print(f"  label {entry['estimate']['confidence']}; tags {[s['activity_tag'] for s in entry['stretches']]}")
    assert entry["estimate"]["confidence"] == label
    # Two races, both at 720 s or longer: held. Three with one at 300 s, one, or none: not held.
    assert entry["label_held_at_medium"] is (tagged == (1200, 720))
    for n, length in enumerate((1200, 720)):
        stretch = by_length[length]
        assert stretch["session_id"] == ids[n]
        assert stretch["name"] == names[n]
        assert stretch["duration_s"] == CURVE[n][2]
    for length in CURVE_LENGTHS:
        assert by_length[length]["session_id"] == by_id[length]
        assert by_length[length]["activity_tag"] == ("race" if length in tagged else None)
    assert all(by_length[length]["name"] is None for length in (480, 300, 180, 120))


def _goal_bodies(client: TestClient) -> tuple[bytes, bytes]:
    read = client.get("/goal")
    previewed = client.post("/goal/preview", json=goal_seed._goal(goal_seed.MARATHON_M))
    assert read.status_code == previewed.status_code == 200, (read.text, previewed.text)
    return read.content, previewed.content


@pytest.mark.parametrize("held", [False, True], ids=["unheld", "held"])
def test_other_entries_and_goal_bodies_are_byte_identical_with_and_without_cs(
    monkeypatch: pytest.MonkeyPatch, held: bool
) -> None:
    """One store with tagged races and CS candidates. Off: ``main._riegel_at`` is wrapped so no
    predicate reaches the reader (``db._read_cs_rows`` is never called, and the CS entry is built
    from no rows). On: the CS entry is served from the runs. The threshold and Riegel entries and
    the ``GET /goal`` and ``POST /goal/preview`` bodies are byte-identical. Unheld, the 5K race
    fills the 1200 s stretch alone (``medium``); held, the ``CURVE`` 480 s run is tagged Race as
    well, so two races are kept and none at 300 s or less (``medium``, ``label_held_at_medium``)."""
    calls: list[int] = []
    real_read = db_module._read_cs_rows

    def counted(*args, **kwargs):
        calls.append(1)
        return real_read(*args, **kwargs)

    monkeypatch.setattr(db_module, "_read_cs_rows", counted)
    real_at = main_module._riegel_at

    def without_cs(*args, **kwargs):
        kwargs.pop("cs_needs_records", None)
        return real_at(*args, **kwargs)

    with TestClient(app) as client:
        races = goal_seed._races(client, *goal_seed.E2)
        curve = _runs(*CURVE)
        if held:
            goal_seed._put_tags(client, [*races, curve[CURVE_LENGTHS.index(480)]])
        goal_seed._plant_every_load()
        assert client.put("/goal", json=goal_seed._goal(goal_seed.HALF_M, race_date="2027-01-01")).status_code == 200
        on_state = client.get("/state")
        on_goal = _goal_bodies(client)
        on_calls = len(calls)
        monkeypatch.setattr(main_module, "_riegel_at", without_cs)
        off_state = client.get("/state")
        off_goal = _goal_bodies(client)
    off_calls = len(calls) - on_calls
    on, off = on_state.json(), off_state.json()
    on_cs, off_cs = _cs(on), _cs(off)
    print(f"  _read_cs_rows calls: on {on_calls}, off {off_calls}")
    print(f"  on CS {on_cs['estimate']} with {len(on_cs['stretches'])} stretches; off {off_cs['unavailable']}")
    _print_stretches(on_cs)
    print(f"  on label {on_cs['estimate']['confidence']}, held {on_cs['label_held_at_medium']}")
    assert on_calls == 1 and off_calls == 0
    assert on_cs["unavailable"] is None and len(on_cs["stretches"]) == 6
    assert on_cs["estimate"]["confidence"] == "medium"
    assert on_cs["label_held_at_medium"] is held
    assert off_cs["label_held_at_medium"] is None
    assert off_cs["unavailable"] == "no_candidate_runs" and off_cs["stretches"] == [] and off_cs["excluded"] == []
    on_two = json.dumps(on["determinants"][:2], sort_keys=False)
    off_two = json.dumps(off["determinants"][:2], sort_keys=False)
    print(f"  threshold/riegel bytes on {len(on_two)} off {len(off_two)}; equal {on_two == off_two}")
    assert on["determinants"][0]["estimate"] is not None and on["determinants"][1]["estimate"] is not None
    assert on_two == off_two
    assert (on["as_of"], on["timezone"]) == (off["as_of"], off["timezone"])
    print(f"  goal bodies equal {on_goal[0] == off_goal[0]}, preview equal {on_goal[1] == off_goal[1]}")
    assert on_goal == off_goal


def test_state_writes_nothing_with_cs() -> None:
    """Row counts of every table and the sha256 of every store file are unchanged by ``GET /state``,
    now and at a past ``as_of``, while the CS entry is served from the runs."""
    with TestClient(app) as client:
        goal_seed._races(client, goal_seed.E2[0])
        _runs(*CURVE)
        goal_seed._plant_every_load()
        before = goal_seed._snapshot()
        entry = _cs(_state(client))
        past = _cs(_state(client, "2026-09-20"))
        after = goal_seed._snapshot()
    print(f"  counts {before[0]}; digest {before[1][:12]} -> {after[1][:12]}")
    print(f"  CS now {entry['estimate']['value']}; at 2026-09-20 {past['unavailable']} {len(past['stretches'])} stretches")
    assert entry["unavailable"] is None and past["stretches"]
    assert before == after


def test_runs_after_as_of_are_ignored_by_cs() -> None:
    """At ``as_of`` 2026-09-20T12:00Z a fast run after it is neither used nor listed (read at now, it
    takes the 1200 s length), and the window is measured from the instant used: a flagged run
    exactly 90 days before it is not listed, one 89.99 days before it is."""
    as_of = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    with TestClient(app) as client:
        _runs(*CURVE)
        after = _run(datetime(2026, 9, 25, 7, 0, tzinfo=UTC), 230.0, 1300.0)
        at_90 = _run(as_of - timedelta(days=90), 300.0, 600.0)
        at_8999 = _run(as_of - timedelta(days=89.99), 300.0, 600.0)
        for session_id in (at_90, at_8999):
            _sql("UPDATE sessions SET quality_flags = ? WHERE session_id = ?", '["distance_regressed"]', session_id)
        goal_seed._plant_every_load()
        past = _cs(_state(client, as_of.isoformat()))
        now = _cs(_state(client))
    _print_stretches(past)
    seen = {s["session_id"] for s in past["stretches"]} | {e["session_id"] for e in past["excluded"]}
    print(f"  excluded at as_of {[(e['session_id'][:10], e['reason']) for e in past['excluded']]}")
    assert after not in seen
    assert at_90 not in seen
    assert [(e["session_id"], e["reason"], e["source"]) for e in past["excluded"]] == [
        (at_8999, "distance_regressed", "stored")
    ]
    assert {s["length_s"]: s["session_id"] for s in now["stretches"]}[1200] == after


def test_goal_routes_never_read_cs_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """``GET /goal`` and ``POST /goal/preview`` never call ``db._read_cs_rows``; ``GET /state`` on the
    same store calls it once."""
    calls: list[str] = []
    real = db_module._read_cs_rows

    def spy(*args, **kwargs):
        calls.append("read")
        return real(*args, **kwargs)

    monkeypatch.setattr(db_module, "_read_cs_rows", spy)
    with TestClient(app) as client:
        goal_seed._races(client, *goal_seed.E2)
        _runs(*CURVE)
        goal_seed._plant_every_load()
        assert client.put("/goal", json=goal_seed._goal(goal_seed.HALF_M, race_date="2027-01-01")).status_code == 200
        _goal_bodies(client)
        goal_calls = len(calls)
        _state(client)
    print(f"  calls by the goal routes {goal_calls}; by GET /state {len(calls) - goal_calls}")
    assert goal_calls == 0
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# AC6: the contract validates each response
# ---------------------------------------------------------------------------


def test_a_low_response_validates(contract: dict) -> None:
    """A served ``low`` entry validates against the contract's ``Determinant``; a body missing
    ``d_prime``, or with a reason outside the enum, goes red."""
    with TestClient(app) as client:
        _runs(*CURVE)
        goal_seed._plant_every_load()
        body = _state(client)
    entry = _cs(body)
    assert entry["estimate"]["confidence"] == "low"
    for determinant in body["determinants"]:
        _assert_validates(contract, determinant)
    union = contract["components"]["schemas"]["Determinant"]
    missing = {k: v for k, v in entry.items() if k != "d_prime"}
    errors = contract_validate.validate(contract, union, missing)
    print(f"  without d_prime: {errors}")
    assert any("d_prime" in e for e in errors), errors
    bad = copy.deepcopy(entry)
    bad["stretches"][0]["drop_reason"] = "slower"
    errors = contract_validate.validate(contract, union, bad)
    print(f"  bad drop reason: {errors}")
    assert errors


def _seed_unavailable(reason: str) -> None:
    if reason == "too_few_stretches":
        _runs(CURVE[0], CURVE[1])
    elif reason == "spread_too_narrow":
        _runs(CURVE[2], CURVE[3], CURVE[4])  # longest kept 480 s
    elif reason == "longest_far_off":
        _runs((_sept(1), 300.0, 1300.0), *CURVE[1:])  # 1200 s about 48 s/km slower than predicted
    elif reason == "estimate_not_finite":
        # Three stretches, 120 s at 12 m/s, 300 s and 1200 s at 3 m/s: the slow bound CS - t se
        # (t 3.08 at one degree of freedom) falls below 0.
        _runs((_sept(1), 1000.0 / 3.0, 1210.0), (_sept(3), 1000.0 / 3.0, 310.0), (_sept(5), 1000.0 / 12.0, 130.0))
    goal_seed._plant_every_load()


@pytest.mark.parametrize("reason", UNAVAILABLE)
def test_each_unavailable_response_validates(contract: dict, reason: str) -> None:
    """Each of the five reasons is served through the route with ``estimate``, ``d_prime`` and the
    fit figures null, its stretches still served, and validates against the contract."""
    with TestClient(app) as client:
        _seed_unavailable(reason)
        entry = _cs(_state(client))
    _print_stretches(entry)
    print(f"  {reason}: excess {entry['far_off_excess_s_per_km']}")
    assert entry["unavailable"] == reason
    assert entry["estimate"] is None and entry["d_prime"] is None
    assert entry["error_m_per_s"] is None and entry["t_multiplier"] is None and entry["d_prime_not_positive"] is None
    assert entry["label_held_at_medium"] is None
    assert entry["parameters"] == REFERENCE_PARAMETERS
    if reason == "no_candidate_runs":
        assert entry["stretches"] == []
    else:
        assert entry["stretches"]
    if reason == "longest_far_off":
        assert entry["far_off_excess_s_per_km"] > 4
    else:
        assert entry["far_off_excess_s_per_km"] is None
    _assert_validates(contract, entry)


@pytest.mark.parametrize("case", [*UNAVAILABLE, "turned_not_finite_flag_false", "turned_not_finite_flag_true"])
def test_label_held_at_medium_is_null_whenever_unavailable(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    """``label_held_at_medium`` is null on every ``unavailable`` entry: each of the five reasons
    through the route, and a fitted result that ``main._cs_determinant`` turns into
    ``estimate_not_finite`` (an infinite interval end) whatever the estimator's flag says, false or
    true. The field is gated on the served outcome, not on the estimator's ``unavailable``."""
    if case in UNAVAILABLE:
        reason = case
        with TestClient(app) as client:
            _seed_unavailable(reason)
            entry = _cs(_state(client))
    else:
        reason = "estimate_not_finite"
        flag = case.endswith("true")
        real_estimate = critical_speed.estimate
        seen: list[tuple[object, object]] = []

        def infinite_end(*args, **kwargs):
            result = real_estimate(*args, **kwargs)
            seen.append((result.unavailable, result.label_held_at_medium))
            return dataclasses.replace(result, interval_high_s_per_km=math.inf, label_held_at_medium=flag)

        monkeypatch.setattr(main_module.critical_speed, "estimate", infinite_end)
        with TestClient(app) as client:
            _runs(*CURVE)
            goal_seed._plant_every_load()
            entry = _cs(_state(client))
        print(f"  estimator (unavailable, held) {seen}; replaced with an infinite interval end, held {flag}")
        assert seen == [(None, False)]
        assert entry["d_prime_not_positive"] is None and entry["far_off_excess_s_per_km"] is None
    print(f"  {case}: unavailable {entry['unavailable']}, label_held_at_medium {entry['label_held_at_medium']!r}")
    assert entry["unavailable"] == reason and entry["estimate"] is None
    assert entry["label_held_at_medium"] is None


def test_a_held_response_validates(contract: dict) -> None:
    """The ``CURVE`` runs with the 1200 s and 720 s runs tagged Race (two counted races, none at a
    stretch of 300 s or less): ``medium`` with ``label_held_at_medium`` true, and the entry
    validates against the contract's ``Determinant``. The same entry with the key missing, or a
    non-boolean in it, goes red."""
    with TestClient(app) as client:
        ids = _runs(*CURVE)
        goal_seed._put_tags(client, ids[:2])
        goal_seed._plant_every_load()
        body = _state(client)
    entry = _cs(body)
    _print_stretches(entry)
    print(f"  label {entry['estimate']['confidence']}, held {entry['label_held_at_medium']!r}")
    assert entry["estimate"]["confidence"] == "medium"
    assert entry["label_held_at_medium"] is True
    for determinant in body["determinants"]:
        _assert_validates(contract, determinant)
    union = contract["components"]["schemas"]["Determinant"]
    missing = {k: v for k, v in entry.items() if k != "label_held_at_medium"}
    errors = contract_validate.validate(contract, union, missing)
    print(f"  without label_held_at_medium: {errors}")
    assert any("label_held_at_medium" in e for e in errors), errors
    errors = contract_validate.validate(contract, union, {**entry, "label_held_at_medium": "yes"})
    print(f"  label_held_at_medium 'yes': {errors}")
    assert any("label_held_at_medium" in e for e in errors), errors


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_served_far_off_wording_names_the_cut(contract: dict) -> None:
    """The ``unavailable`` description, as served and in the contract, states the far-off cut as
    ``critical_speed.FAR_OFF_S_PER_KM`` and no other number of s/km; the label descriptions name
    the hold (``label_held_at_medium``) and its 300 s short end, ``MAX_SHORTEST_S``."""
    cut, short_end = critical_speed.FAR_OFF_S_PER_KM, critical_speed.MAX_SHORTEST_S
    schemas = app.openapi()["components"]["schemas"]
    ours = contract["components"]["schemas"]
    unavailable = {
        "served": _flat(schemas["CriticalSpeedDeterminant"]["properties"]["unavailable"]["description"]),
        "contract": _flat(ours["CriticalSpeedDeterminant"]["properties"]["unavailable"]["description"]),
    }
    labels = {
        "served": _flat(schemas["CriticalSpeedValue"]["properties"]["confidence"]["description"]),
        "contract": _flat(ours["CriticalSpeedDeterminant"]["properties"]["estimate"]["description"]),
    }
    for where, text in unavailable.items():
        cuts = re.findall(r"more than (\S+) s/km", text)
        print(f"  {where} unavailable: cuts {cuts}; {text}")
        assert cuts == [str(cut)], (where, cuts)
    for where, text in labels.items():
        print(f"  {where} label: {text}")
        assert "label_held_at_medium" in text and f"{short_end} s or less" in text, where
        assert not re.search(r"2 or more `high`\)", text), where


CS_COMPONENTS = {"CriticalSpeedStretch", "CriticalSpeedExclusion", "CriticalSpeedParameters", "DPrime"}


def test_contract_critical_speed_components_match_the_served(contract: dict) -> None:
    """The components only ``CriticalSpeedDeterminant`` uses are walked against the served OpenAPI
    with ``contract_walk.walk``; the determinant itself and its ``estimate`` are compared by hand, as
    ``RiegelDeterminant`` is (the contract's ``ConfidenceValue`` and ``Trend`` are named components
    of their own when served)."""
    served = app.openapi()
    schemas = served["components"]["schemas"]
    visited, compared = contract_walk.walk(contract, served, CS_COMPONENTS)
    print(f"  walked {sorted(visited)}: {len(compared)} fields")
    assert visited == CS_COMPONENTS
    ours, theirs = contract["components"]["schemas"]["CriticalSpeedDeterminant"], schemas["CriticalSpeedDeterminant"]
    print(f"  CriticalSpeedDeterminant contract {sorted(ours['properties'])}; served {sorted(theirs['properties'])}")
    assert set(ours["properties"]) == set(theirs["properties"])
    assert set(ours["required"]) == set(theirs["required"])
    props, built = ours["properties"], theirs["properties"]
    assert props["name"] == {"type": "string", "enum": ["critical_speed"]}
    assert (built["name"]["type"], built["name"]["const"]) == ("string", "critical_speed")
    assert props["estimate"]["allOf"] == [{"$ref": "#/components/schemas/ConfidenceValue"}]
    assert props["estimate"]["nullable"] is True
    assert built["estimate"]["anyOf"] == [{"$ref": "#/components/schemas/CriticalSpeedValue"}, {"type": "null"}]
    assert props["trend"] == {"$ref": "#/components/schemas/Trend"}
    assert built["trend"] == {"$ref": "#/components/schemas/DeterminantTrend"}
    hand = ("unavailable", "d_prime", "d_prime_not_positive", "label_held_at_medium", "error_m_per_s",
            "t_multiplier", "far_off_excess_s_per_km", "stretches", "excluded", "parameters")  # fmt: skip
    assert set(hand) | {"name", "estimate", "trend"} == set(props)
    for field in hand:
        got, want = contract_walk.normalised(props[field]), contract_walk.normalised(built[field])
        print(f"  CriticalSpeedDeterminant.{field}: contract {got}; served {want}")
        assert got == want, field
    assert sorted(props["unavailable"]["enum"]) == sorted(UNAVAILABLE)
    value = schemas["CriticalSpeedValue"]["properties"]
    confidence = contract["components"]["schemas"]["ConfidenceValue"]["properties"]
    assert set(confidence) == set(value)
    assert value["unit"]["const"] == "s/km"
    assert value["confidence"]["enum"] == confidence["confidence"]["enum"] == ["low", "medium", "high"]


def test_contract_withdraws_the_planned_d_prime_name(contract: dict) -> None:
    """``Determinant``'s description no longer lists ``d_prime`` as a planned determinant (D' rides
    inside the ``critical_speed`` entry) and names ``critical_speed`` among the served ones."""
    description = " ".join(contract["components"]["schemas"]["Determinant"]["description"].split())
    print(f"  {description}")
    assert "`d_prime`" not in description
    assert "`critical_speed` (`CriticalSpeedDeterminant`)" in description


# ---------------------------------------------------------------------------
# Adversarial rows: the predicate runs inside the snapshot, where an exception is a 500
# ---------------------------------------------------------------------------


def test_bad_cs_rows_never_fail_the_state_read(contract: dict) -> None:
    """Stored values only a write from outside the API could leave, on sessions the CS read lists:
    each is excluded or served, the response is a 200 and validates. A run name or tag that is not
    text (a blob) is served null."""
    with TestClient(app) as client:
        ids = _runs(*CURVE)
        odd = _runs(
            (_sept(12), 300.0, 400.0),
            (_sept(13), 300.0, 400.0),
            (_sept(14), 300.0, 400.0),
            (_sept(15), 300.0, 400.0),
            (_sept(16), 300.0, 400.0),
            (_sept(17), 300.0, 400.0),
            (_sept(18), 300.0, 400.0),
        )
        goal_seed._plant_every_load()
        _sql("UPDATE sessions SET quality_flags = ? WHERE session_id = ?", "{{not json", odd[0])
        _sql("UPDATE sessions SET quality_flags = ? WHERE session_id = ?", '{"a": 1}', odd[1])
        _sql("UPDATE sessions SET sport = ? WHERE session_id = ?", b"\xff\xfe", odd[2])
        _sql("UPDATE sessions SET timer_time_s = ?, summary = ? WHERE session_id = ?", "abc", "not json", odd[3])
        _sql("UPDATE sessions SET quality_flags = ? WHERE session_id = ?", "[1e999]", odd[4])
        _sql("DELETE FROM records WHERE session_id = ?", odd[5])
        _sql("UPDATE sessions SET activity_tag = ? WHERE session_id = ?", "health_snapshot", odd[6])
        _sql("UPDATE sessions SET name = ?, activity_tag = ? WHERE session_id = ?", b"\xff\xfe", b"\x80", ids[0])
        response = client.get("/state")
        first_day = client.get("/state", params={"as_of": "0001-01-01"})
    print(f"  status {response.status_code}; first day {first_day.status_code}")
    assert response.status_code == 200, response.text
    assert first_day.status_code == 200, first_day.text
    entry = _cs(response.json())
    _print_stretches(entry)
    reasons = {e["session_id"]: e["reason"] for e in entry["excluded"]}
    print(f"  excluded {[(odd.index(s) if s in odd else s[:10], r) for s, r in reasons.items()]}")
    assert reasons[odd[0]] == reasons[odd[1]] == "quality_flags_unreadable"
    assert reasons[odd[2]] == "not_running"
    assert reasons[odd[6]] == "declared_capture"
    # No records: F013 computes over none, so the run is a candidate with no stretch at any length.
    assert odd[5] not in reasons and odd[5] not in {s["session_id"] for s in entry["stretches"]}
    [longest] = [s for s in entry["stretches"] if s["length_s"] == 1200]
    assert longest["session_id"] == ids[0] and longest["name"] is None and longest["activity_tag"] is None
    _assert_validates(contract, entry)
    assert _cs(first_day.json())["unavailable"] == "no_candidate_runs"


def test_a_nested_json_quality_flags_never_fails_the_state_read() -> None:
    """A ``quality_flags`` nested deeper than the JSON decoder recurses, on a running session: the
    state read still answers (an unreadable flag text is an exclusion, never a 500)."""
    with TestClient(app) as client:
        ids = _runs(*CURVE)
        goal_seed._plant_every_load()
        _sql("UPDATE sessions SET quality_flags = ? WHERE session_id = ?", "[" * 100000, ids[1])
        response = client.get("/state")
    print(f"  status {response.status_code} {response.text[:200]}")
    assert response.status_code == 200, response.text
    excluded = [(e["session_id"], e["reason"]) for e in _cs(response.json())["excluded"]]
    print(f"  excluded {excluded}")
    assert excluded == [(ids[1], "quality_flags_unreadable")]
