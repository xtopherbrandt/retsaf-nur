"""The ``/goal`` operations in ``contracts/openapi.yaml`` against the served routes (F032 AC9, the
contract half; reference sections 6 and 10).

``check_drift.py`` compares paths, methods, success codes and required request fields only, so the
responses are checked here. ``tests/support/contract_validate.py`` validates the live ``PUT /goal``,
``GET /goal`` and ``POST /goal/preview`` bodies against ``GoalContract`` and ``PaceTarget``, and
hand-built fitted-line, fallback and unavailable bodies (null ``ConfidenceValue``s) the same way; a
non-null ``gap_to_goal`` and a status outside ``active`` and ``completed`` must go red. The
components the served OpenAPI names the same way as the contract are walked with
``tests/support/contract_walk.py``; ``PaceTarget`` and ``GoalContract`` are compared by hand because
the contract's ``ConfidenceValue`` is the served ``PaceTargetValue``.

Races are stored as ``test_goal_preview_route.py`` stores them (its helpers are loaded by path:
the suite runs under ``--import-mode=importlib``), under the autouse isolated store, with the clock
frozen through ``main._utcnow``.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from runcoach_api import main as main_module
from runcoach_api.main import app

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
HERE = Path(__file__).parent
CONFIDENCE_VALUE = "#/components/schemas/ConfidenceValue"
VALIDATION_422 = {"$ref": "#/components/responses/RequestValidationError"}
OPERATIONS = (
    ("/goal", "get", "getGoal", "GoalContract", None),
    ("/goal", "put", "setGoal", "GoalContract", "GoalContractInput"),
    ("/goal/preview", "post", "previewGoal", "PaceTarget", "GoalPreviewInput"),
)
# The components the served document names as the contract does; the walk follows their $refs.
WALK_ROOTS = {
    "GoalRace",
    "GoalConfidence",
    "GoalTimeGap",
    "GoalProvenance",
    "GoalLeaveOneOutSet",
    "PaceTargetParameters",
    "GoalContractInput",
    "GoalPreviewInput",
}
WALK_REACHED = WALK_ROOTS | {"GoalRaceUsed", "GoalNewestRace", "GoalLeaveOneOut", "GoalRaceInput", "GoalPreviewRaceInput"}
BY_HAND = {"projected_time_s", "avg_pace_s_per_km"}


def _load_by_path(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


contract_validate = _load_by_path("contract_validate", HERE / "support" / "contract_validate.py")
contract_walk = _load_by_path("contract_walk", HERE / "support" / "contract_walk.py")
preview = _load_by_path("goal_preview_route_helpers", HERE / "test_goal_preview_route.py")

NOW = preview.NOW
TODAY = preview.TODAY
HALF_M = preview.HALF_M
E2 = preview.E2


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


@pytest.fixture(scope="module")
def contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def served() -> dict:
    return app.openapi()


def _schema(contract: dict, name: str) -> dict:
    return contract["components"]["schemas"][name]


def _validate(contract: dict, name: str, body, label: str) -> list[str]:
    errors = contract_validate.validate(contract, {"$ref": f"#/components/schemas/{name}"}, body)
    print(f"  {label} against {name}: {errors or 'valid'}")
    return errors


# ---------------------------------------------------------------------------
# The operations
# ---------------------------------------------------------------------------


def test_goal_operations_are_implemented(contract: dict, served: dict) -> None:
    """The three operations are ``implemented`` and exact to the served document: operation id,
    tags, the optional ``as_of`` on ``getGoal`` and ``previewGoal`` and none on ``setGoal``, the
    request body components, the success schema, the shared 422 and the 404, and a 500 for the load
    chart beside the served codes. The Goal tag says so, ``GoalContract.status`` is the served two
    values, and the version is ``0.6.0-draft``."""
    for path, method, operation_id, success, body in OPERATIONS:
        op = contract["paths"][path][method]
        built = served["paths"][path][method]
        print(f"  {method.upper()} {path}: readiness {op['x-readiness']}, responses {sorted(op['responses'])}")
        assert op["x-readiness"] == "implemented"
        assert op["operationId"] == built["operationId"] == operation_id
        assert op["tags"] == built["tags"] == ["Goal"]
        params = [(p["name"], p["in"], p.get("required", False)) for p in op.get("parameters", [])]
        built_params = [(p["name"], p["in"], p["required"]) for p in built.get("parameters", [])]
        assert params == built_params == ([] if method == "put" else [("as_of", "query", False)])
        if params:
            assert op["parameters"][0]["schema"]["type"] == "string"
        if body is None:
            assert "requestBody" not in op and "requestBody" not in built
        else:
            ours = op["requestBody"]["content"]["application/json"]["schema"]
            theirs = built["requestBody"]["content"]["application/json"]["schema"]
            assert ours == theirs == {"$ref": f"#/components/schemas/{body}"}
            assert op["requestBody"]["required"] is True
        assert set(op["responses"]) == set(built["responses"]) | {"500"}
        assert op["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{success}"
        }
        assert op["responses"]["422"] == VALIDATION_422 == contract["paths"]["/state"]["get"]["responses"]["422"]
        assert "chart" in op["responses"]["500"]["description"]
    assert contract["info"]["version"] == "0.6.0-draft"
    [tag] = [t for t in contract["tags"] if t["name"] == "Goal"]
    print(f"  Goal tag: {tag['description']}")
    assert "x-readiness: implemented" in tag["description"]
    not_found = contract["paths"]["/goal"]["get"]["responses"]["404"]
    assert "no_goal" in not_found["description"]
    assert not_found["content"]["application/json"]["schema"]["properties"]["detail"]["enum"] == ["no_goal"]

    status = _schema(contract, "GoalContract")["properties"]["status"]
    assert status["enum"] == served["components"]["schemas"]["GoalContract"]["properties"]["status"]["enum"]
    assert status["enum"] == ["active", "completed"]

    for name in ("GoalContract", "PaceTarget"):
        ours, theirs = _schema(contract, name), served["components"]["schemas"][name]
        assert set(ours["properties"]) == set(theirs["properties"]), name
        assert set(ours["required"]) == set(theirs["required"]), name
        for field, prop in ours["properties"].items():
            if field in BY_HAND:
                continue
            got, want = contract_walk.normalised(prop), contract_walk.normalised(theirs["properties"][field])
            assert got == want, f"{name}.{field}: contract {got} != served {want}"
    target = _schema(contract, "PaceTarget")["properties"]
    for field in BY_HAND:
        assert target[field]["allOf"] == [{"$ref": CONFIDENCE_VALUE}] and target[field]["nullable"] is True
        assert contract_walk.normalised(served["components"]["schemas"]["PaceTarget"]["properties"][field]) == {
            "nullable": True,
            "ref": "PaceTargetValue",
        }
    assert target["gap_to_goal"]["type"] == "null"
    confidence_value = _schema(contract, "ConfidenceValue")["properties"]
    pace_value = served["components"]["schemas"]["PaceTargetValue"]
    assert set(pace_value["properties"]) <= set(confidence_value)
    assert set(pace_value["properties"]["unit"]["enum"]) == {"s", "s/km"}
    gap = _schema(contract, "StateModel")["properties"]["gap_to_goal"]
    print(f"  StateModel.gap_to_goal: {gap['description']}")
    assert "no goal" not in gap["description"].lower()

    visited, compared = contract_walk.walk(contract, served, WALK_ROOTS)
    print(f"  walked {sorted(visited)}: {len(compared)} fields")
    assert visited == WALK_REACHED


# ---------------------------------------------------------------------------
# The live responses
# ---------------------------------------------------------------------------


def test_put_and_get_goal_validate(contract: dict) -> None:
    """Real responses: ``PUT /goal`` and ``GET /goal`` (with and without ``as_of``) validate against
    ``GoalContract``, the preview against ``PaceTarget``, and the two request bodies against their
    input components; each body carries exactly the contract's properties."""
    put_body = preview._goal(HALF_M, race_date=TODAY, target_time_s=5400.0)
    preview_body = {"race": {"distance_m": HALF_M, "name": "Fall half"}, "target_time_s": 5400.0}
    with TestClient(app) as client:
        preview._races(client, *E2)
        put = client.put("/goal", json=put_body)
        assert put.status_code == 200, put.text
        got = client.get("/goal")
        assert got.status_code == 200, got.text
        dated = client.get("/goal", params={"as_of": "2026-10-13"})
        assert dated.status_code == 200, dated.text
        previewed = preview._preview(client, preview_body)
    assert _validate(contract, "GoalContractInput", put_body, "PUT body") == []
    assert _validate(contract, "GoalPreviewInput", preview_body, "preview body") == []
    for label, body in (("PUT /goal", put.json()), ("GET /goal", got.json()), ("GET /goal?as_of", dated.json())):
        assert set(body) == set(_schema(contract, "GoalContract")["properties"]), sorted(body)
        assert set(body["target"]) == set(_schema(contract, "PaceTarget")["properties"]), sorted(body["target"])
        assert _validate(contract, "GoalContract", body, label) == []
    assert set(previewed) == set(_schema(contract, "PaceTarget")["properties"]), sorted(previewed)
    assert _validate(contract, "PaceTarget", previewed, "POST /goal/preview") == []
    assert put.json()["target"]["route"] == previewed["route"] == "fitted_line"
    assert put.json()["status"] == "active" and dated.json()["status"] == "active"
    assert previewed["race"]["name"] == "Fall half"


# ---------------------------------------------------------------------------
# Hand-built bodies
# ---------------------------------------------------------------------------

RACE_USED = {
    "session_id": "fr945-2026-09-06T07:00:00+00:00",
    "name": None,
    "start_time": "2026-09-06T07:00:00Z",
    "gap_equivalent_m": 5000.0,
    "duration_s": 1200.0,
    "weight": 1.0,
    "residual": 0.001,
    "fitness_before": 42.0,
}
PARAMETERS = {
    "high_max_uncertainty": 0.03,
    "medium_max_uncertainty": 0.06,
    "fitness_gap": 0.3,
    "max_extrapolation": 2.0,
    "calibrated_min_m": 1500.0,
    "calibrated_max_m": 42195.0,
    "population_b": 1.06,
    "tie_slack": 1e-9,
}
ASSUMPTIONS = [
    "flat_course",
    "still_air",
    "cool_conditions",
    "no_economy_adjustment",
    "no_durability_adjustment",
    "projected_for_today_not_race_day",
    "watch_timer_time",
]


def _fitted_line() -> dict:
    return {
        "as_of": "2026-10-14T12:00:00Z",
        "timezone": "UTC",
        "race": {"date": "2026-11-01", "distance_m": 21097.5, "name": None},
        "target_time_s": 5400.0,
        "unavailable": None,
        "route": "fitted_line",
        "projected_time_s": {
            "value": 5542.8,
            "unit": "s",
            "interval": {"low": 5400.1, "high": 5689.3},
            "confidence": "medium",
            "provisional": False,
        },
        "avg_pace_s_per_km": {
            "value": 262.7,
            "unit": "s/km",
            "interval": {"low": 255.9, "high": 269.7},
            "confidence": "medium",
            "provisional": False,
        },
        "halfwidth": 0.0264,
        "confidence": {"label": "medium", "basis": "range", "uncertainty": 0.0264, "reasons": []},
        "goal_time_gap": {"gap_s": -142.8, "gap_fraction": -0.0258, "within_range": True},
        "provenance": {
            "b": 1.072,
            "error": 0.011,
            "label": "high",
            "population_default": False,
            "n_eff": 4.0,
            "races": [RACE_USED],
            "newest_race": {"session_id": RACE_USED["session_id"], "name": None, "start_time": RACE_USED["start_time"]},
            "fitness_at_races": 42.0,
            "fitness_now": 44.5,
        },
        "leave_one_out": {
            "races": [{"session_id": RACE_USED["session_id"], "route": "fitted_line", "projected_time_s": 5550.0, "shift": 0.0013}],
            "largest": {"session_id": RACE_USED["session_id"], "route": "fitted_line", "projected_time_s": 5550.0, "shift": 0.0013},
        },
        "effort_note": None,
        "assumptions": ASSUMPTIONS,
        "parameters": PARAMETERS,
        "gap_to_goal": None,
    }


def _fallback() -> dict:
    """Route ``closest_race``: no interval, ``provisional``, no basis, no leave-one-out."""
    body = _fitted_line()
    body["route"] = "closest_race"
    for field in ("projected_time_s", "avg_pace_s_per_km"):
        body[field]["interval"] = None
        body[field]["provisional"] = True
        body[field]["confidence"] = "low"
    body["halfwidth"] = None
    body["confidence"] = {"label": "low", "basis": None, "uncertainty": None, "reasons": ["population_default"]}
    body["goal_time_gap"]["within_range"] = None
    body["provenance"].update(b=1.06, error=None, label=None, population_default=True, n_eff=1.0)
    body["leave_one_out"] = None
    return body


def _unavailable() -> dict:
    """``no_races``: every figure null, the assumptions and parameters still served."""
    body = _fitted_line()
    body.update(
        unavailable="no_races",
        route=None,
        projected_time_s=None,
        avg_pace_s_per_km=None,
        halfwidth=None,
        confidence=None,
        goal_time_gap=None,
        leave_one_out=None,
    )
    body["provenance"] = {
        "b": None,
        "error": None,
        "label": None,
        "population_default": False,
        "n_eff": None,
        "races": [],
        "newest_race": None,
        "fitness_at_races": None,
        "fitness_now": None,
    }
    return body


def _goal_contract(target: dict, status: str = "active") -> dict:
    return {
        "id": "7",
        "race": target["race"],
        "target_time_s": target["target_time_s"],
        "status": status,
        "created_at": "2026-10-14T12:00:00Z",
        "target": target,
    }


MUTATIONS = [
    ("status outside the enum", lambda c: c.update(status="superseded"), "$.status"),
    ("unavailable outside the enum", lambda c: c["target"].update(unavailable="no_goal"), "$.target.unavailable"),
    ("a time with no value", lambda c: c["target"]["projected_time_s"].pop("value"), "$.target.projected_time_s"),
    ("a route outside the enum", lambda c: c["target"].update(route="population"), "$.target.route"),
    ("created_at missing", lambda c: c.pop("created_at"), "created_at"),
    ("id as a number", lambda c: c.update(id=7), "$.id"),
    ("a race with no name key", lambda c: c["target"]["race"].pop("name"), "$.target.race"),
]


def test_validator_accepts_fitted_line_fallback_and_unavailable(contract: dict) -> None:
    """The three response shapes validate as ``PaceTarget`` and inside an ``active`` and a
    ``completed`` ``GoalContract``; each mutation of a valid body is reported at its path."""
    for label, body in (("fitted line", _fitted_line()), ("fallback", _fallback()), ("unavailable", _unavailable())):
        assert _validate(contract, "PaceTarget", body, label) == []
        for status in ("active", "completed"):
            assert _validate(contract, "GoalContract", _goal_contract(body, status), f"{label} {status}") == []
    for label, mutate, expected in MUTATIONS:
        mutated = _goal_contract(_fitted_line())
        mutate(mutated)
        errors = contract_validate.validate(contract, _schema(contract, "GoalContract"), mutated)
        print(f"  {label}: {errors}")
        assert any(expected in error for error in errors), (label, errors)


def test_validator_refuses_a_non_null_gap_to_goal(contract: dict) -> None:
    """``gap_to_goal`` is typed null-only: an empty object and a ``GapToGoal``-shaped object are
    both refused at ``$.gap_to_goal``, as is a non-null ``effort_note``; null passes."""
    schema = _schema(contract, "PaceTarget")
    assert contract_validate.validate(contract, schema, _fitted_line()) == []
    for label, value in (("empty object", {}), ("GapToGoal shape", {"summary": "slower", "components": []})):
        body = _fitted_line()
        body["gap_to_goal"] = value
        errors = contract_validate.validate(contract, schema, body)
        print(f"  gap_to_goal {label}: {errors}")
        assert errors and all(error.startswith("$.gap_to_goal") for error in errors), errors
    body = _fitted_line()
    body["effort_note"] = "hold even splits"
    errors = contract_validate.validate(contract, schema, body)
    print(f"  effort_note set: {errors}")
    assert errors and all(error.startswith("$.effort_note") for error in errors), errors


# ---------------------------------------------------------------------------
# The 422 and the 404
# ---------------------------------------------------------------------------


def _assert_fastapi_detail(response, status: int = 422) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    print(f"  {status}: {body}")
    assert set(body) == {"detail"} and isinstance(body["detail"], list) and body["detail"], body
    for entry in body["detail"]:
        assert {"loc", "msg", "type"} <= set(entry), entry
        assert all(isinstance(part, (str, int)) and not isinstance(part, bool) for part in entry["loc"]), entry


def test_preview_422_is_declared(contract: dict) -> None:
    """``previewGoal``'s ``responses`` carry the shared 422 (FastAPI's ``{detail: [...]}``), and
    three real 422s (a refused field, a race date before the reference date, a malformed
    ``as_of``) are that body; ``getGoal``'s 404 body validates against its contract schema."""
    op = contract["paths"]["/goal/preview"]["post"]
    assert op["responses"]["422"] == VALIDATION_422
    schema = contract["components"]["responses"]["RequestValidationError"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["detail"] and schema["properties"]["detail"]["type"] == "array"
    with TestClient(app) as client:
        refused = client.post("/goal/preview", json={"race": {"distance_m": 0}})
        before = client.post("/goal/preview", json={"race": {"distance_m": HALF_M, "date": "2026-10-01"}})
        malformed = client.post("/goal/preview", json={"race": {"distance_m": HALF_M}}, params={"as_of": "yesterday"})
        missing = client.get("/goal")
    for response in (refused, before, malformed):
        _assert_fastapi_detail(response)
    assert [e["loc"] for e in before.json()["detail"]] == [["body", "race", "date"]]
    assert [e["loc"] for e in malformed.json()["detail"]] == [["query", "as_of"]]
    assert missing.status_code == 404, missing.text
    not_found = contract["paths"]["/goal"]["get"]["responses"]["404"]["content"]["application/json"]["schema"]
    errors = contract_validate.validate(contract, not_found, missing.json())
    print(f"  404 {missing.json()}: {errors or 'valid'}")
    assert errors == [] and missing.json() == {"detail": "no_goal"}
