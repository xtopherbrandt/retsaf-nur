"""``tests/support/contract_validate.py`` reads a ``oneOf`` chosen by a ``discriminator``.

The served OpenAPI writes a tagged union inline as ``{"oneOf": [{"$ref": ...}, ...],
"discriminator": {"propertyName": ..., "mapping": {value: "#/components/schemas/..."}}}``. The
validator reads that form only: the body's discriminator property picks the branch through the
mapping, and the body is validated against that branch alone. A ``oneOf`` without a
``discriminator``, a ``discriminator`` without a ``oneOf``, or a discriminator it cannot read
still raises, so a schema the validator cannot judge is never reported valid.

The contract here is a small inline one (two branches, each with a one-value ``name`` enum), not
``contracts/openapi.yaml``. Every mutated body is printed beside the errors it produced.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SUPPORT = Path(__file__).parent / "support"


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


contract_validate = _load_support("contract_validate")

ESTIMATE = "#/components/schemas/Estimate"
UNAVAILABLE = "#/components/schemas/Unavailable"
UNION = {
    "oneOf": [{"$ref": ESTIMATE}, {"$ref": UNAVAILABLE}],
    "discriminator": {"propertyName": "name", "mapping": {"estimate": ESTIMATE, "unavailable": UNAVAILABLE}},
    "title": "Result",
}
CONTRACT = {
    "components": {
        "schemas": {
            "Estimate": {
                "type": "object",
                "required": ["name", "value"],
                "properties": {"name": {"type": "string", "enum": ["estimate"]}, "value": {"type": "number"}},
                "additionalProperties": False,
            },
            "Unavailable": {
                "type": "object",
                "required": ["name", "reason"],
                "properties": {
                    "name": {"type": "string", "enum": ["unavailable"]},
                    "reason": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "Holder": {"type": "object", "required": ["result"], "properties": {"result": UNION}},
        }
    }
}


def _errors(body: object, schema: dict = UNION) -> list[str]:
    errors = contract_validate.validate(CONTRACT, schema, body)
    print(f"  {body!r}: {errors}")
    return errors


def test_one_of_selects_the_branch_by_its_discriminator() -> None:
    """Each branch's valid body passes, and the name decides which branch's rules apply."""
    assert _errors({"name": "estimate", "value": 1.5}) == []
    assert _errors({"name": "unavailable", "reason": "no races"}) == []
    # The estimate's fields under the other name are judged by the Unavailable branch only.
    swapped = _errors({"name": "unavailable", "value": 1.5})
    assert "$: required property 'reason' is missing" in swapped
    assert "$: property 'value' is not allowed" in swapped
    assert not any("'value' is missing" in error for error in swapped), swapped
    swapped = _errors({"name": "estimate", "reason": "no races"})
    assert "$: required property 'value' is missing" in swapped
    assert not any("'reason' is missing" in error for error in swapped), swapped


def test_an_unmapped_name_is_reported() -> None:
    """A name the mapping does not list is reported at the body's path, nested or not."""
    errors = _errors({"name": "guess", "value": 1.5})
    assert errors == [
        "$: discriminator 'name' value 'guess' is not in discriminator mapping ['estimate', 'unavailable']"
    ]
    holder = CONTRACT["components"]["schemas"]["Holder"]
    errors = _errors({"result": {"name": "Estimate", "value": 1.5}}, holder)
    assert errors == [
        "$.result: discriminator 'name' value 'Estimate' is not in discriminator mapping ['estimate', 'unavailable']"
    ]


def test_a_body_failing_its_branch_is_reported() -> None:
    """The selected branch's errors come back as the validator writes them, at their paths."""
    assert _errors({"name": "estimate", "value": "fast"}) == ["$.value: expected number, got str 'fast'"]
    holder = CONTRACT["components"]["schemas"]["Holder"]
    assert _errors({"result": {"name": "estimate", "value": 1.5}}, holder) == []
    assert _errors({"result": {"name": "unavailable", "reason": 3}}, holder) == [
        "$.result.reason: expected string, got int 3"
    ]
    assert _errors({"result": None}, holder) == ["$.result: null where the schema is not nullable"]


def test_a_missing_discriminator_property_is_reported() -> None:
    """No name, a name that is not a string, or a body that is not an object is reported."""
    assert _errors({"value": 1.5}) == ["$: discriminator property 'name' is missing"]
    assert _errors({"name": 3, "value": 1.5}) == ["$.name: discriminator expected string, got int 3"]
    assert _errors({"name": None, "value": 1.5}) == [
        "$.name: discriminator expected string, got NoneType None"
    ]
    assert _errors(["estimate"]) == ["$: expected object for discriminator 'name', got list ['estimate']"]


def test_one_of_without_a_discriminator_still_raises() -> None:
    """Every form the validator does not read raises rather than passing unread."""
    bare = {"oneOf": UNION["oneOf"]}
    alone = {"discriminator": UNION["discriminator"]}
    for schema in (bare, alone):
        with pytest.raises(ValueError, match="unsupported keyword"):
            contract_validate.validate(CONTRACT, schema, {"name": "estimate", "value": 1.5})
    unreadable = {
        "no mapping": {**UNION, "discriminator": {"propertyName": "name"}},
        "no propertyName": {**UNION, "discriminator": {"mapping": UNION["discriminator"]["mapping"]}},
        "extra discriminator key": {**UNION, "discriminator": {**UNION["discriminator"], "x-note": 1}},
        "inline mapping target": {
            **UNION,
            "discriminator": {"propertyName": "name", "mapping": {"estimate": {"type": "object"}}},
        },
        "mapping outside oneOf": {
            **UNION,
            "discriminator": {"propertyName": "name", "mapping": {"estimate": "#/components/schemas/Holder"}},
        },
        "inline oneOf member": {**UNION, "oneOf": [{"type": "object"}, {"$ref": UNAVAILABLE}]},
        "keyword beside the union": {**UNION, "nullable": True},
    }
    for label, schema in unreadable.items():
        with pytest.raises(ValueError) as raised:
            contract_validate.validate(CONTRACT, schema, {"name": "estimate", "value": 1.5})
        print(f"  {label}: {raised.value}")
