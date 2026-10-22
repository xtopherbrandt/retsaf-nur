"""``GET /sessions`` against its contract entry (F019 AC8): ``listSessions`` is implemented and exact.

Nothing here drives the store. The served document is ``app.openapi()``; the contract is
``contracts/openapi.yaml``. ``SessionSummary`` is walked property by property with
``tests/support/contract_walk.py`` (which compares ``required`` too); the ``Page`` envelope is compared
by hand, because the contract's ``Page.items`` is ``{type: array, items: {}}`` and an untyped
``items: {}`` trips ``contract_walk.normalised``; the ``since`` and ``limit`` parameters are resolved
through their ``$ref`` into ``components/parameters`` and compared on name, type, minimum, maximum and
default, as the load chart's test compares ``from`` and ``to``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml
from runcoach_api.main import app

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
SUPPORT = Path(__file__).parent / "support"
LIST_PATH = "/sessions"
SUMMARY_FIELDS = {
    "session_id",
    "start_time",
    "sport",
    "activity_tag",
    "name",
    "distance_m",
    "duration_s",
    "avg_pace_s_per_km",
    "quality_flags",
    "workout_id",
}


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


contract_walk = _load_support("contract_walk")


def _contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


def _resolve_parameter(contract: dict, node: dict) -> dict:
    """A parameter entry, through its ``$ref`` into ``components/parameters`` when it has one."""
    if "$ref" not in node:
        return node
    prefix = "#/components/parameters/"
    assert node["$ref"].startswith(prefix), node
    return contract["components"]["parameters"][node["$ref"][len(prefix) :]]


def _served_scalar(schema: dict) -> dict:
    """The one typed branch of a served parameter schema: ``anyOf [X, null]`` reads as ``X``."""
    if "anyOf" in schema:
        rest = [b for b in schema["anyOf"] if b != {"type": "null"}]
        assert len(rest) == 1, schema
        return {**{k: v for k, v in schema.items() if k != "anyOf"}, **rest[0]}
    return schema


def _page_schema(contract: dict) -> tuple[dict, dict]:
    """The contract's 200 schema as ``(Page component, the items override)``."""
    schema = contract["paths"][LIST_PATH]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    envelope, override = schema["allOf"]
    assert envelope == {"$ref": "#/components/schemas/Page"}, envelope
    return contract["components"]["schemas"]["Page"], override


def test_the_contract_session_summary_page_and_parameters_match_the_served_route() -> None:
    contract = _contract()
    built = app.openapi()
    op = contract["paths"][LIST_PATH]["get"]
    built_op = built["paths"][LIST_PATH]["get"]
    assert op["operationId"] == built_op["operationId"] == "listSessions"
    assert op["tags"] == built_op["tags"] == ["Sessions"]
    assert op["x-readiness"] == "implemented"
    assert set(op["responses"]) == set(built_op["responses"]) == {"200", "422"}
    assert "{detail}" in op["responses"]["422"]["description"], op["responses"]["422"]
    assert op["responses"]["422"]["content"] == {"application/json": {"schema": {"type": "object"}}}

    # SessionSummary, property by property, required list included.
    seen, compared = contract_walk.walk(contract, built, ["SessionSummary"])
    print(f"  {len(compared)} properties over {sorted(seen)}")
    assert seen == {"SessionSummary"}, seen
    assert {c.split(".", 1)[1] for c in compared} == SUMMARY_FIELDS, compared
    component = contract["components"]["schemas"]["SessionSummary"]
    assert set(component["required"]) == SUMMARY_FIELDS, component.get("required")
    assert "planned shape" not in component["description"], component["description"]
    name = contract_walk.normalised(component["properties"]["name"])
    assert name == {"nullable": True, "type": "string"}, name

    # The Page envelope by hand: Page.items is an untyped {} in the shared component.
    page, override = _page_schema(contract)
    served_page = built["components"]["schemas"]["SessionPage"]
    assert built_op["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SessionPage"
    }
    assert set(page["properties"]) == set(served_page["properties"]) == {"items", "next_cursor"}
    assert set(page["required"]) == set(served_page["required"]) == {"items"}
    assert page["properties"]["items"] == {"type": "array", "items": {}}
    items = contract_walk.normalised(override["properties"]["items"])
    assert items == contract_walk.normalised(served_page["properties"]["items"]), items
    assert items["items"]["ref"] == "SessionSummary", items
    cursor = contract_walk.normalised(page["properties"]["next_cursor"])
    assert cursor == contract_walk.normalised(served_page["properties"]["next_cursor"]), cursor
    assert cursor == {"nullable": True, "type": "string"}, cursor

    # since and limit through their $ref: name, type, minimum, maximum, default.
    params = {p["name"]: p for p in (_resolve_parameter(contract, p) for p in op["parameters"])}
    built_params = {p["name"]: p for p in built_op["parameters"]}
    assert set(params) == set(built_params) == {"since", "limit", "include_nights"}
    for name in ("since", "limit"):
        assert params[name]["in"] == built_params[name]["in"] == "query"
        assert not params[name].get("required", False) and not built_params[name].get("required", False)
        ours, theirs = params[name]["schema"], _served_scalar(built_params[name]["schema"])
        for keyword in ("type", "minimum", "maximum", "default"):
            assert ours.get(keyword) == theirs.get(keyword), f"{name}.{keyword}: {ours} != {theirs}"
    assert params["since"]["schema"] == {"type": "string"}
    assert params["limit"]["schema"] == {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}

    # The Sessions tag says list is implemented, on one line.
    tag = next(t for t in contract["tags"] if t["name"] == "Sessions")["description"]
    assert "list" in tag and "implemented" in tag, tag
    assert "\n" not in tag, tag


def test_the_shared_paging_components_are_unchanged() -> None:
    """``Since``, ``Limit`` and ``Page`` are shared with the planned ``/changes`` and ``/coach/messages``:
    this feature reads them through ``$ref`` and does not edit them."""
    contract = _contract()
    parameters = contract["components"]["parameters"]
    assert parameters["Since"] == {
        "name": "since",
        "in": "query",
        "required": False,
        "schema": {"type": "string"},
        "description": "Opaque cursor from a prior page's next_cursor.",
    }
    assert parameters["Limit"]["schema"] == {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}
    page = contract["components"]["schemas"]["Page"]
    assert page["required"] == ["items"] and set(page["properties"]) == {"items", "next_cursor"}
    assert contract["paths"][LIST_PATH]["get"]["parameters"][:2] == [
        {"$ref": "#/components/parameters/Since"},
        {"$ref": "#/components/parameters/Limit"},
    ]


def test_the_list_parameters_are_since_limit_and_include_nights() -> None:
    """F028 AC6: ``include_nights`` is an optional query boolean, default false, written inline on
    ``listSessions`` (the shared ``Since`` and ``Limit`` stay as they are), and served the same."""
    contract = _contract()
    op = contract["paths"][LIST_PATH]["get"]
    assert op["parameters"] == [
        {"$ref": "#/components/parameters/Since"},
        {"$ref": "#/components/parameters/Limit"},
        {
            "name": "include_nights",
            "in": "query",
            "required": False,
            "schema": {"type": "boolean", "default": False},
            "description": op["parameters"][2]["description"],
        },
    ], op["parameters"]
    description = op["parameters"][2]["description"]
    assert "overnight_hrv" in description and "\n" not in description.strip(), description
    built = {p["name"]: p for p in app.openapi()["paths"][LIST_PATH]["get"]["parameters"]}
    print(f"  served parameters: {sorted(built)}")
    assert list(built) == ["since", "limit", "include_nights"], list(built)
    served = built["include_nights"]
    assert served["in"] == "query" and not served.get("required", False), served
    schema = _served_scalar(served["schema"])
    assert (schema.get("type"), schema.get("default")) == ("boolean", False), schema


def test_the_list_blocks_of_the_contract_are_ascii_only() -> None:
    """``check_drift.py`` opens the contract without an encoding, so a Windows console reads it as
    cp1252; the blocks this feature edited carry no non-ASCII text."""
    text = CONTRACT.read_text(encoding="utf-8")
    start = text.index("      operationId: listSessions")
    end = text.index("  /sessions/{session_id}:", start)
    assert text[start:end].isascii(), "the listSessions operation carries non-ASCII text"
    start = text.index("    SessionSummary:")
    end = text.index("    # ---", start)
    assert text[start:end].isascii(), "the SessionSummary component carries non-ASCII text"
    tag = next(t for t in _contract()["tags"] if t["name"] == "Sessions")
    assert tag["description"].isascii()
