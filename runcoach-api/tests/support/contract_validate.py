"""``tests/support/contract_validate.py``: validate a live JSON body against a contract schema.

No JSON Schema validator is in the lock, so this reads the small subset ``contracts/openapi.yaml``
uses for the ``GET /state`` determinant: ``type`` (object, array, string, number, integer,
boolean), ``enum``, ``nullable``, ``required``, ``properties``, ``items``, ``$ref`` and a
one-member ``allOf`` (the file's way to make a ``$ref`` nullable, since a ``nullable`` beside a
bare ``$ref`` is ignored). Prose keywords (``description``, ``example``, ``default``, ``title``,
``format``) are not checked. Any other keyword raises ``ValueError`` rather than passing
unread, so a schema this module cannot judge is never reported valid.

A property the schema does not list is allowed: the contract's responses are additive (the
engine may serve more). ``validate`` returns every violation as ``"<path>: <what>"``; an empty
list is a valid body. Loaded by tests with ``importlib.util.spec_from_file_location``, as the
other ``tests/support`` modules are.
"""

from __future__ import annotations

from typing import Any

REF_PREFIX = "#/components/schemas/"
CHECKED = {"type", "enum", "nullable", "required", "properties", "items", "$ref", "allOf"}
PROSE = {"description", "example", "default", "title", "format"}


def resolve(doc: dict, ref: str) -> dict:
    """The component a ``#/components/schemas/<Name>`` reference names."""
    if not ref.startswith(REF_PREFIX):
        raise ValueError(f"unsupported $ref {ref!r}")
    return doc["components"]["schemas"][ref[len(REF_PREFIX) :]]


def _type_ok(kind: str, value: Any) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise ValueError(f"unsupported type {kind!r}")


def validate(doc: dict, schema: dict, value: Any, path: str = "$") -> list[str]:
    """Every way ``value`` breaks ``schema`` (read in ``doc`` for its ``$ref``s), in walk order."""
    unknown = set(schema) - CHECKED - PROSE
    if unknown:
        raise ValueError(f"{path}: unsupported keyword(s) {sorted(unknown)}")
    if "allOf" in schema:
        members = schema["allOf"]
        if len(members) != 1:
            raise ValueError(f"{path}: only a one-member allOf is supported, got {len(members)}")
        if value is None:
            return (
                [] if schema.get("nullable") is True else [f"{path}: null where the schema is not nullable"]
            )
        merged = {k: v for k, v in schema.items() if k not in ("allOf", "nullable")}
        return validate(doc, {**members[0], **merged}, value, path)
    if "$ref" in schema:
        rest = {k for k in schema if k != "$ref" and k not in PROSE}
        if rest:
            raise ValueError(f"{path}: keywords beside a bare $ref are ignored by OpenAPI: {sorted(rest)}")
        return validate(doc, resolve(doc, schema["$ref"]), value, path)
    if value is None:
        return [] if schema.get("nullable") is True else [f"{path}: null where the schema is not nullable"]

    errors: list[str] = []
    kind = schema.get("type")
    if kind is not None and not _type_ok(kind, value):
        return [f"{path}: expected {kind}, got {type(value).__name__} {value!r}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: required property {name!r} is missing")
        for name, sub in schema.get("properties", {}).items():
            if name in value:
                errors.extend(validate(doc, sub, value[name], f"{path}.{name}"))
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(validate(doc, schema["items"], item, f"{path}[{index}]"))
    return errors
