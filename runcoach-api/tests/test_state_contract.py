"""``getState`` in ``contracts/openapi.yaml`` against the served route (F024 AC6, the contract half).

``check_drift.py`` compares paths, methods, success codes and required parameters only, so the
response is checked here. ``tests/support/contract_validate.py`` validates each live
``determinants[]`` entry against the contract's ``Determinant`` (type, enum, nullable, required,
properties, items, ``$ref``, one-member ``allOf``), for an estimate and for both unavailable
reasons; a mutated body must go red. ``StateModel`` is not walked whole: its other properties are
validated one by one. ``ConfidenceValue`` is compared with the served ``ThresholdPaceValue`` by
hand, because the contract's inline ``interval`` is a named component when served and
``PaceTarget`` uses ``ConfidenceValue`` too. The three components only ``getState`` uses
(``ThresholdEffort``, ``ThresholdExclusion``, ``ThresholdPaceParameters``) carry the served names
and are walked with ``tests/support/contract_walk.py``.

Races are stored as ``test_state_route.py`` stores them: ``db.persist`` with synthetic 1 Hz flat
records, under the autouse isolated store, with the clock frozen through ``main._utcnow``.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.models import Context, Record, Session, SessionLap, SessionWorkoutStep

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
SUPPORT = Path(__file__).parent / "support"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
NEW_DETERMINANT_FIELDS = ("unavailable", "error", "short_races_only", "efforts", "excluded", "parameters")
THRESHOLD_COMPONENTS = {"ThresholdEffort", "ThresholdExclusion", "ThresholdPaceParameters"}


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


contract_validate = _load_support("contract_validate")
contract_walk = _load_support("contract_walk")


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


@pytest.fixture(scope="module")
def contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


def _schema(contract: dict, name: str) -> dict:
    return contract["components"]["schemas"][name]


def _store(
    start: datetime,
    *,
    pace: float = 270.0,
    duration_s: float = 1800.0,
    flags: list[str] | None = None,
    with_records: bool = True,
) -> str:
    start_time = start.isoformat()
    session = Session(
        session_id=derive_session_id("fr945", start_time),
        sport="running",
        source_vendor="garmin",
        start_time=start_time,
        source_device="fr945",
        activity_tag="race",
        timer_time_s=duration_s,
        quality_flags=list(flags or []),
        summary={"duration_s": duration_s},
        context=Context(),
    )
    speed = 1000.0 / pace
    records = (
        [
            Record(t=float(t), distance=t * speed, speed=speed, altitude=100.0, heart_rate=150.0)
            for t in range(int(duration_s) + 1)
        ]
        if with_records
        else []
    )
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        db_module.persist(conn, session, records, [], {})
    finally:
        conn.close()
    return session.session_id


def _corrupt(session_id: str, column: str, value) -> None:
    conn = db_module.get_connection()
    try:
        conn.execute(f"UPDATE sessions SET {column} = ? WHERE session_id = ?", (value, session_id))
        conn.commit()
    finally:
        conn.close()


def _body() -> dict:
    with TestClient(app) as client:
        response = client.get("/state")
    assert response.status_code == 200, response.text
    return response.json()


def _assert_valid(contract: dict, body: dict) -> dict:
    """Validate each determinant against ``Determinant`` and the other ``StateModel`` properties one
    by one; print the slice compared. Returns the one determinant."""
    state = _schema(contract, "StateModel")
    assert set(body) == set(state["properties"]), sorted(body)
    for name, value in body.items():
        if name == "determinants":
            continue
        errors = contract_validate.validate(contract, state["properties"][name], value, f"$.{name}")
        print(f"  StateModel.{name} = {value!r}: {errors or 'valid'}")
        assert errors == [], errors
    [determinant] = body["determinants"]
    errors = contract_validate.validate(
        contract, _schema(contract, "Determinant"), determinant, "$.determinants[0]"
    )
    print(f"  Determinant keys {sorted(determinant)}: {errors or 'valid'}")
    assert errors == [], errors
    return determinant


def _estimate_body() -> dict:
    _store(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), pace=270.0, duration_s=2400.0)
    _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC), pace=262.0, duration_s=1500.0)
    _store(datetime(2026, 9, 20, 7, 0, tzinfo=UTC), duration_s=11400.0, with_records=False)
    _store(datetime(2026, 9, 27, 7, 0, tzinfo=UTC), flags=["smart_recording"])
    return _body()


def test_estimate_response_validates_against_the_contract(contract: dict) -> None:
    determinant = _assert_valid(contract, _estimate_body())
    assert determinant["unavailable"] is None
    assert determinant["estimate"] is not None
    assert len(determinant["efforts"]) == 2
    assert [e["reason"] for e in determinant["excluded"]] == ["outside_threshold_band", "smart_recording"]
    assert determinant["trend"] == {"direction": "unknown", "rate": None}


def test_unavailable_response_validates_against_the_contract(contract: dict) -> None:
    """``no_qualifying_effort`` with every reason the reader adds for a stored value it cannot read."""
    marathon = _store(datetime(2026, 9, 1, 7, 0, tzinfo=UTC), duration_s=11400.0, with_records=False)
    flags = _store(datetime(2026, 9, 6, 7, 0, tzinfo=UTC))
    context = _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC))
    summary = _store(datetime(2026, 9, 20, 7, 0, tzinfo=UTC))
    _corrupt(flags, "quality_flags", "not json")
    _corrupt(context, "context", "not json")
    _corrupt(summary, "timer_time_s", None)
    _corrupt(summary, "summary", "not json")
    determinant = _assert_valid(contract, _body())
    assert determinant["estimate"] is None
    assert determinant["unavailable"] == "no_qualifying_effort"
    assert [(e["session_id"], e["reason"]) for e in determinant["excluded"]] == [
        (marathon, "outside_threshold_band"),
        (flags, "quality_flags_unreadable"),
        (context, "features_unavailable"),
        (summary, "summary_unreadable"),
    ]


def test_estimate_not_finite_response_validates_against_the_contract(
    contract: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finite but absurd GAP pace: no estimate and no error, the race still in ``efforts`` with its
    GAP pace and gain, the parameters served."""
    race = _store(datetime(2026, 9, 13, 7, 0, tzinfo=UTC))
    real = db_module.session_features.compute_session_features

    def absurd(session, rows):
        body = real(session, rows)
        body["features"]["gap_avg_pace_s_per_km"] = {"value": 1.7e308, "unavailable": None}
        return body

    monkeypatch.setattr(db_module.session_features, "compute_session_features", absurd)
    determinant = _assert_valid(contract, _body())
    assert determinant["unavailable"] == "estimate_not_finite"
    assert determinant["estimate"] is None and determinant["error"] is None
    [effort] = determinant["efforts"]
    assert effort["session_id"] == race
    assert effort["gap_pace_s_per_km"] == 1.7e308 and effort["gain"] == 1.0
    assert determinant["parameters"]["doubling_days"] == 42


def _store_workout(start: datetime, *, active_s: float = 1200.0, laps: str = "read") -> str:
    """A Training-tagged workout: 300 s warm-up, one active lap of ``active_s`` at 255 s/km naming
    active step 1, 300 s cool-down, 1 Hz flat records. ``laps`` is ``read``, ``none`` (never
    read), ``no_end`` (the active lap's end null) or ``free`` (laps read, none active)."""
    plan = [("warmup", 300.0, 360.0, 0), ("active", active_s, 255.0, 1), ("cooldown", 300.0, 390.0, 2)]
    records, distance, t = [Record(t=0.0, distance=0.0, speed=1000.0 / 360.0, altitude=100.0)], 0.0, 0
    lap_rows, lap_start = [], 0.0
    for index, (intensity, seconds, pace, step) in enumerate(plan):
        for _ in range(int(seconds)):
            t += 1
            distance += 1000.0 / pace
            records.append(Record(t=float(t), distance=distance, speed=1000.0 / pace, altitude=100.0))
        lap = SessionLap(
            lap_index=index,
            start_t=lap_start,
            end_t=lap_start + seconds,
            timer_s=seconds,
            intensity="interval" if laps == "free" else intensity,
            wkt_step_index=None if laps == "free" else step,
        )
        if laps == "no_end" and intensity == "active":
            lap.end_t = None
        lap_rows.append(lap)
        lap_start += seconds
    steps = [
        SessionWorkoutStep(step_index=0, intensity="warmup", duration_type="time"),
        SessionWorkoutStep(step_index=1, intensity="active", duration_type="time"),
        SessionWorkoutStep(step_index=2, intensity="cooldown", duration_type="time"),
    ]
    start_time = start.isoformat()
    total = sum(seconds for _i, seconds, _p, _s in plan)
    session = Session(
        session_id=derive_session_id("fr945", start_time),
        sport="running",
        source_vendor="garmin",
        start_time=start_time,
        source_device="fr945",
        activity_tag="threshold_workout",
        timer_time_s=total,
        summary={"duration_s": total},
        context=Context(),
    )
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        db_module.persist(conn, session, records, [], {}, laps=None if laps == "none" else (lap_rows, steps))
    finally:
        conn.close()
    return session.session_id


def test_estimate_and_unavailable_validate_against_the_contract(contract: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """F026: a race and a workout used, with every new exclusion reason beside them, validate; then
    the workouts alone, none used, validate as ``no_qualifying_effort``."""
    race = _store(datetime(2026, 9, 6, 7, 0, tzinfo=UTC), pace=270.0, duration_s=2400.0)
    workout = _store_workout(datetime(2026, 9, 13, 7, 0, tzinfo=UTC))
    not_read = _store_workout(datetime(2026, 9, 14, 7, 0, tzinfo=UTC), laps="none")
    no_end = _store_workout(datetime(2026, 9, 15, 7, 0, tzinfo=UTC), laps="no_end")
    free = _store_workout(datetime(2026, 9, 16, 7, 0, tzinfo=UTC), laps="free")
    short = _store_workout(datetime(2026, 9, 17, 7, 0, tzinfo=UTC), active_s=599.0)
    unreadable = _store_workout(datetime(2026, 9, 18, 7, 0, tzinfo=UTC))
    real = db_module.read_workout_laps

    def read_workout_laps(conn, session_id):
        if session_id == unreadable:
            raise RuntimeError("a lap row that does not read")
        return real(conn, session_id)

    monkeypatch.setattr(db_module, "read_workout_laps", read_workout_laps)
    determinant = _assert_valid(contract, _body())
    assert determinant["unavailable"] is None and determinant["estimate"] is not None
    assert [(e["session_id"], e["kind"], e["active_blocks"]) for e in determinant["efforts"]] == [
        (race, "race", None),
        (workout, "threshold_workout", 1),
    ]
    assert determinant["parameters"]["workout_min_active_s"] == 600
    expected = [
        (not_read, "laps_not_read"),
        (no_end, "lap_times_unreadable"),
        (free, "no_workout_steps"),
        (short, "active_time_below_minimum"),
        (unreadable, "laps_unreadable"),
    ]
    assert [(e["session_id"], e["reason"], e["kind"]) for e in determinant["excluded"]] == [
        (s, r, "threshold_workout") for s, r in expected
    ]
    reasons = set(_schema(contract, "ThresholdExclusion")["properties"]["reason"]["enum"])
    assert {r for _s, r in expected} <= reasons

    _corrupt(race, "activity_tag", None)
    _corrupt(workout, "activity_tag", None)
    determinant = _assert_valid(contract, _body())
    assert determinant["estimate"] is None and determinant["unavailable"] == "no_qualifying_effort"
    assert determinant["efforts"] == [] and determinant["short_races_only"] is False
    assert [(e["session_id"], e["reason"]) for e in determinant["excluded"]] == expected


MUTATIONS = [
    ("drop efforts", lambda d: d.pop("efforts"), "required property 'efforts'"),
    ("drop parameters", lambda d: d.pop("parameters"), "required property 'parameters'"),
    ("unknown name", lambda d: d.update(name="threshold"), "$.name"),
    ("unknown unavailable", lambda d: d.update(unavailable="bogus"), "$.unavailable"),
    ("string error", lambda d: d.update(error="small"), "$.error"),
    ("null short_races_only", lambda d: d.update(short_races_only=None), "$.short_races_only"),
    ("string trend rate", lambda d: d["trend"].update(rate="fast"), "$.trend.rate"),
    ("unknown trend direction", lambda d: d["trend"].update(direction="sideways"), "$.trend.direction"),
    (
        "string interval low",
        lambda d: d["estimate"]["interval"].update(low="4:15"),
        "$.estimate.interval.low",
    ),
    ("unknown confidence", lambda d: d["estimate"].update(confidence="certain"), "$.estimate.confidence"),
    ("estimate without value", lambda d: d["estimate"].pop("value"), "required property 'value'"),
    ("string provisional", lambda d: d["estimate"].update(provisional="no"), "$.estimate.provisional"),
    ("null gain", lambda d: d["efforts"][0].update(gain=None), "$.efforts[0].gain"),
    ("string gap_coverage", lambda d: d["efforts"][0].update(gap_coverage="1"), "$.efforts[0].gap_coverage"),
    ("unknown reason", lambda d: d["excluded"][0].update(reason="too_hilly"), "$.excluded[0].reason"),
    ("unknown effort kind", lambda d: d["efforts"][0].update(kind="workout"), "$.efforts[0].kind"),
    ("string active_blocks", lambda d: d["efforts"][0].update(active_blocks="1"), "$.efforts[0].active_blocks"),
    ("drop exclusion kind", lambda d: d["excluded"][0].pop("kind"), "required property 'kind'"),
    ("excluded not a list", lambda d: d.update(excluded={}), "$.excluded"),
    (
        "float doubling_days",
        lambda d: d["parameters"].update(doubling_days=42.5),
        "$.parameters.doubling_days",
    ),
    (
        "bool typical_error",
        lambda d: d["parameters"].update(typical_error=True),
        "$.parameters.typical_error",
    ),
]


def test_validator_rejects_a_mutated_body(contract: dict) -> None:
    """Each mutation of a valid live determinant is reported at its path; the original stays valid."""
    [determinant] = _estimate_body()["determinants"]
    schema = _schema(contract, "Determinant")
    assert contract_validate.validate(contract, schema, determinant) == []
    for label, mutate, expected in MUTATIONS:
        mutated = copy.deepcopy(determinant)
        mutate(mutated)
        errors = contract_validate.validate(contract, schema, mutated)
        print(f"  {label}: {errors}")
        assert any(expected in error for error in errors), (label, errors)
    with pytest.raises(ValueError, match="unsupported keyword"):
        contract_validate.validate(contract, {"oneOf": [{"type": "string"}]}, "x")
    with pytest.raises(ValueError, match="ignored by OpenAPI"):
        contract_validate.validate(contract, {"$ref": "#/components/schemas/Trend", "nullable": True}, None)
    with pytest.raises(ValueError, match="one-member allOf"):
        contract_validate.validate(contract, {"allOf": [{"type": "string"}, {"type": "number"}]}, "x")


def test_contract_lists_as_of_and_is_implemented(contract: dict) -> None:
    op = contract["paths"]["/state"]["get"]
    served = app.openapi()
    built_op = served["paths"]["/state"]["get"]
    assert op["operationId"] == built_op["operationId"] == "getState"
    assert op["x-readiness"] == "implemented"
    assert op["tags"] == built_op["tags"] == ["State & Metrics"]
    [as_of] = op["parameters"]
    [built_as_of] = built_op["parameters"]
    print(f"  contract as_of {as_of}; served {built_as_of}")
    assert (as_of["name"], as_of["in"], as_of.get("required", False)) == ("as_of", "query", False)
    assert (built_as_of["name"], built_as_of["in"], built_as_of["required"]) == ("as_of", "query", False)
    assert as_of["schema"]["type"] == "string"
    assert set(op["responses"]) == set(built_op["responses"]) == {"200", "422"}
    assert op["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/StateModel"
    }

    state = _schema(contract, "StateModel")
    assert set(state["properties"]) == set(served["components"]["schemas"]["StateModel"]["properties"])
    assert set(state.get("required", [])) == set(served["components"]["schemas"]["StateModel"]["required"])
    assert state["properties"]["timezone"]["type"] == "string"
    assert state["properties"]["gap_to_goal"] == {
        "allOf": [{"$ref": "#/components/schemas/GapToGoal"}],
        "nullable": True,
        "description": state["properties"]["gap_to_goal"]["description"],
    }

    determinant = _schema(contract, "Determinant")
    built_determinant = served["components"]["schemas"]["Determinant"]
    assert set(determinant["properties"]) == set(built_determinant["properties"])
    assert set(NEW_DETERMINANT_FIELDS) <= set(determinant["properties"])
    assert set(determinant["required"]) == set(built_determinant["required"])
    estimate = determinant["properties"]["estimate"]
    assert (
        estimate["allOf"] == [{"$ref": "#/components/schemas/ConfidenceValue"}]
        and estimate["nullable"] is True
    )
    assert "functional_threshold_pace" in " ".join(determinant["description"].split())
    assert "Only `functional_threshold_pace` is served" in " ".join(determinant["description"].split())
    assert contract_walk.normalised(determinant["properties"]["unavailable"]) == contract_walk.normalised(
        built_determinant["properties"]["unavailable"]
    )
    # trend.rate's type is pinned by the contract alone: a nullable number.
    rate = _schema(contract, "Trend")["properties"]["rate"]
    assert (rate["type"], rate["nullable"]) == ("number", True)

    visited, compared = contract_walk.walk(contract, served, THRESHOLD_COMPONENTS)
    print(f"  walked {sorted(visited)}: {len(compared)} fields")
    assert visited == THRESHOLD_COMPONENTS
    reason = _schema(contract, "ThresholdExclusion")["properties"]["reason"]["enum"]
    assert {"summary_unreadable", "quality_flags_unreadable", "features_unavailable"} <= set(reason)


def test_confidence_value_fields_match_by_hand(contract: dict) -> None:
    """The served ``ThresholdPaceValue`` against the contract's ``ConfidenceValue``, field by field.

    The served value narrows the contract's: ``unit`` is the constant ``s/km`` (the contract's
    nullable string), ``interval`` is required and a named component with ``low`` and ``high``
    required (the contract's nullable inline object)."""
    ours = _schema(contract, "ConfidenceValue")
    theirs = app.openapi()["components"]["schemas"]["ThresholdPaceValue"]
    interval = app.openapi()["components"]["schemas"]["ThresholdPaceInterval"]
    props, served = ours["properties"], theirs["properties"]
    print(f"  contract {sorted(props)}; served {sorted(served)}")
    assert set(props) == set(served) == {"value", "unit", "interval", "confidence", "provisional"}
    assert props["value"] == {"type": "number"} and served["value"]["type"] == "number"
    assert props["unit"]["type"] == served["unit"]["type"] == "string" and served["unit"]["const"] == "s/km"
    assert served["interval"] == {"$ref": "#/components/schemas/ThresholdPaceInterval"}
    assert props["interval"]["type"] == interval["type"] == "object"
    assert set(props["interval"]["properties"]) == set(interval["properties"]) == {"low", "high"}
    for end in ("low", "high"):
        assert props["interval"]["properties"][end]["type"] == interval["properties"][end]["type"] == "number"
    assert props["confidence"]["type"] == served["confidence"]["type"] == "string"
    assert props["confidence"]["enum"] == served["confidence"]["enum"] == ["low", "medium", "high"]
    assert props["provisional"]["type"] == served["provisional"]["type"] == "boolean"
    assert set(ours["required"]) <= set(theirs["required"])
