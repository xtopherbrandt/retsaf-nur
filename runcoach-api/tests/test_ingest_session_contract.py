"""``ingestSession`` (``POST /sessions``) against its contract entry, with ``runs_and_captures_only`` (F022 AC9).

Nothing here drives the store. The served document is ``app.openapi()``; the contract is
``contracts/openapi.yaml``. ``check_drift.py`` compares the 2xx codes and the required request
fields but only notes a multipart field the contract lacks, so this test is the guard on the
optional fields and on the 200: it is the declared ``responses={200: ...}`` on the route that puts a
200 in the served document (runtime validation of the skipped body does not need it), and dropping
that declaration turns the success-code comparison red.

The route's ``response_model`` is ``IngestResponse`` alone and the 200 is declared in
``responses=``, so the served 201 is exactly a ``$ref`` to ``IngestResponse`` (a 201 never carries
``IngestSkipped``) and the 200 a ``$ref`` to ``IngestSkipped``; the skip branches return a
``JSONResponse`` of their own. The components are walked property by property with
``tests/support/contract_walk.py`` (which compares ``required`` too, and reads the 201's optional
``filter_applied`` and ``replaced_session_id`` as non-nullable).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml
from runcoach_api.main import app

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
SUPPORT = Path(__file__).parent / "support"
REF_PREFIX = "#/components/schemas/"

SUCCESS_CODES = {"200", "201"}
MULTIPART_FIELDS = {"file", "resting_capture", "runs_and_captures_only"}
# The component each success code returns: the 201 an ingested session, the 200 a file the
# switch's rule does not keep.
SUCCESS_COMPONENT = {"201": "IngestResponse", "200": "IngestSkipped"}


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


def _resolve(doc: dict, schema: dict) -> dict:
    while "$ref" in schema:
        node = doc
        for part in schema["$ref"][2:].split("/"):
            node = node[part]
        schema = node
    return schema


def _named(schema: dict) -> tuple[str | None, set[str]]:
    """A response schema's own ``$ref`` component and every component its ``anyOf`` names."""
    own = schema["$ref"][len(REF_PREFIX) :] if "$ref" in schema else None
    union = {branch["$ref"][len(REF_PREFIX) :] for branch in schema.get("anyOf", [])}
    return own, union


def test_contract_and_served_route_agree_on_codes_and_components() -> None:
    contract = _contract()
    built = app.openapi()
    op = contract["paths"]["/sessions"]["post"]
    built_op = built["paths"]["/sessions"]["post"]
    assert op["operationId"] == "ingestSession"
    assert op["x-readiness"] == "implemented"

    # Success codes: the same set in both documents.
    ours = {code for code in op["responses"] if code.startswith("2")}
    theirs = {code for code in built_op["responses"] if code.startswith("2")}
    print(f"  success codes: contract {sorted(ours)}, served {sorted(theirs)}")
    assert ours == theirs == SUCCESS_CODES, (sorted(ours), sorted(theirs))

    # Each success code names its component in both documents.
    for code, component in SUCCESS_COMPONENT.items():
        contract_schema = op["responses"][code]["content"]["application/json"]["schema"]
        served_schema = built_op["responses"][code]["content"]["application/json"]["schema"]
        own, union = _named(served_schema)
        print(f"  {code}: contract {contract_schema}, served $ref {own} anyOf {sorted(union)}")
        assert contract_schema == {"$ref": f"{REF_PREFIX}{component}"}, code
        assert own == component, (code, own)
        assert union <= set(SUCCESS_COMPONENT.values()), (code, union)

    # The multipart fields: the same names, `file` alone required, the two switches booleans.
    body = op["requestBody"]["content"]["multipart/form-data"]["schema"]
    served_body = _resolve(built, built_op["requestBody"]["content"]["multipart/form-data"]["schema"])
    print(f"  multipart: contract {sorted(body['properties'])}, served {sorted(served_body['properties'])}")
    assert set(body["properties"]) == set(served_body["properties"]) == MULTIPART_FIELDS
    assert body["required"] == served_body["required"] == ["file"]
    for name in ("resting_capture", "runs_and_captures_only"):
        assert body["properties"][name]["type"] == served_body["properties"][name]["type"] == "boolean", name
        assert body["properties"][name]["default"] is served_body["properties"][name]["default"] is False, name

    # Both components, property by property, required lists included.
    seen, compared = contract_walk.walk(contract, built, ["IngestResponse", "IngestSkipped"])
    print(f"  {len(compared)} properties over {sorted(seen)}: {compared}")
    assert seen == {"IngestResponse", "IngestSkipped"}, seen
    assert {"IngestResponse.filter_applied", "IngestResponse.replaced_session_id"} <= set(compared)
    assert {f"IngestSkipped.{f}" for f in ("skipped", "reason", "file_type", "sport", "filter_applied")} <= set(
        compared
    )


def test_the_201_serves_ingest_response_alone() -> None:
    """The served 201 and 200 are each exactly one ``$ref``, and the 201's optional keys are never null.

    A ``response_model`` union merged with ``responses=`` served the 201 as ``anyOf [IngestResponse,
    IngestSkipped]``; this pins the literal schemas, so the union coming back goes red.
    """
    built = app.openapi()
    responses = built["paths"]["/sessions"]["post"]["responses"]
    served = {code: responses[code]["content"]["application/json"]["schema"] for code in sorted(SUCCESS_CODES)}
    print(f"  served success schemas: {served}")
    assert served == {
        "200": {"$ref": f"{REF_PREFIX}IngestSkipped"},
        "201": {"$ref": f"{REF_PREFIX}IngestResponse"},
    }
    response = built["components"]["schemas"]["IngestResponse"]
    print(f"  IngestResponse: {response}")
    assert sorted(response["required"]) == ["quality_flags", "session_id"]
    assert response["properties"]["filter_applied"] == {"type": "boolean", "title": "Filter Applied"}
    assert response["properties"]["replaced_session_id"] == {"type": "string", "title": "Replaced Session Id"}
    skipped = built["components"]["schemas"]["IngestSkipped"]
    print(f"  IngestSkipped.reason {skipped['properties']['reason']}, filter_applied {skipped['properties']['filter_applied']}")
    assert sorted(skipped["properties"]["reason"]["enum"]) == ["no_night", "non_activity", "skipped_sport"]
    assert skipped["properties"]["filter_applied"] == {"type": "boolean", "title": "Filter Applied"}
    assert sorted(skipped["required"]) == ["filter_applied", "reason", "skipped"]
