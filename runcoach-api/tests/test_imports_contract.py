"""``startImport``, ``listImports`` and ``getImport`` against their contract entries (F020 AC11).

Nothing here drives the store. The served document is ``app.openapi()``; the contract is
``contracts/openapi.yaml``. ``check_drift.py`` compares only the 2xx codes, so the error codes of the
F020 reference's section 6 are pinned here, on both documents: each operation's full set of response
codes in the contract, and in the served document, equals the set written below. They differ only
where FastAPI declares a 422 the route never answers (``getImport``'s string path parameter always
validates), which the contract leaves out.
The components are walked property by property with ``tests/support/contract_walk.py`` (which
compares ``required`` too), and ``SessionDetail`` carries ``import_id`` as a nullable string.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml
from runcoach_api.main import app

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
SUPPORT = Path(__file__).parent / "support"

# (path, method) -> (operationId, every response code). 400/409/413 are the reference's section 6
# errors, 422 the missing `file` part, 500 the copy that could not be written or a worker that could
# not start; 404 an unknown import_id.
OPERATIONS = {
    ("/imports", "post"): ("startImport", {"202", "400", "409", "413", "422", "500"}),
    ("/imports", "get"): ("listImports", {"200"}),
    ("/imports/{import_id}", "get"): ("getImport", {"200", "404"}),
}
# The codes FastAPI serves beyond the contract's: a 422 for any route with a parameter, which a
# string path parameter never triggers.
SERVED_ONLY = {("/imports/{import_id}", "get"): {"422"}}
SUCCESS_SCHEMA = {
    ("/imports", "post"): ("202", "Import"),
    ("/imports", "get"): ("200", "ImportPage"),
    ("/imports/{import_id}", "get"): ("200", "Import"),
}
COMPONENTS = {"Import", "ImportPage", "ImportFile", "ImportCounts", "ImportProgress", "ImportUnreadableArchive"}


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


def test_imports_contract_matches_served_routes() -> None:
    contract = _contract()
    built = app.openapi()
    for (path, method), (operation_id, codes) in OPERATIONS.items():
        op = contract["paths"][path][method]
        built_op = built["paths"][path][method]
        where = f"{method.upper()} {path}"
        print(f"  {where}: contract {sorted(op['responses'])}, served {sorted(built_op['responses'])}")
        assert op["operationId"] == built_op["operationId"] == operation_id, where
        assert op["x-readiness"] == "implemented", where
        assert op["tags"] == built_op["tags"] == ["Sessions"], where
        assert set(op["responses"]) == codes, (where, sorted(op["responses"]))
        served_codes = codes | SERVED_ONLY.get((path, method), set())
        assert set(built_op["responses"]) == served_codes, (where, sorted(built_op["responses"]))
        status, component = SUCCESS_SCHEMA[(path, method)]
        assert op["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{component}"
        }, where
        assert built_op["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{component}"
        }, where

    # The multipart part: one required binary `file`, nothing else.
    body = contract["paths"]["/imports"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
    served_body = _resolve(
        built,
        built["paths"]["/imports"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"],
    )
    assert set(body["properties"]) == set(served_body["properties"]) == {"file"}
    assert body["required"] == served_body["required"] == ["file"]
    assert body["properties"]["file"]["type"] == served_body["properties"]["file"]["type"] == "string"
    assert body["properties"]["file"]["format"] == "binary"
    assert served_body["properties"]["file"].get("format", served_body["properties"]["file"].get("contentMediaType")) in {
        "binary",
        "application/octet-stream",
    }

    # The path parameter.
    (param,) = contract["paths"]["/imports/{import_id}"]["get"]["parameters"]
    (served_param,) = built["paths"]["/imports/{import_id}"]["get"]["parameters"]
    assert (param["name"], param["in"], param["required"], param["schema"]["type"]) == (
        served_param["name"],
        served_param["in"],
        served_param["required"],
        served_param["schema"]["type"],
    ) == ("import_id", "path", True, "string")

    # Every component, property by property, required lists included.
    seen, compared = contract_walk.walk(contract, built, ["ImportPage", "Import"])
    print(f"  {len(compared)} properties over {sorted(seen)}")
    assert seen == COMPONENTS, seen
    files = contract_walk.normalised(contract["components"]["schemas"]["Import"]["properties"]["files"])
    assert files == {"nullable": True, "type": "array", "items": {"nullable": False, "ref": "ImportFile"}}, files
    assert "files" not in contract["components"]["schemas"]["Import"]["required"]
    counts = contract["components"]["schemas"]["ImportCounts"]["properties"]
    for name in ("skipped_sport", "non_activity", "not_fit"):
        assert counts[name]["additionalProperties"] == {"type": "integer"}, name
        assert built["components"]["schemas"]["ImportCounts"]["properties"][name]["additionalProperties"] == {
            "type": "integer"
        }, name


def test_session_detail_carries_a_nullable_import_id() -> None:
    detail = _contract()["components"]["schemas"]["SessionDetail"]
    assert contract_walk.normalised(detail["properties"]["import_id"]) == {"nullable": True, "type": "string"}
    assert "import_id" not in detail["required"]
