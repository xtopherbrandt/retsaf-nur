"""``tests/support/contract_validate.py``: validate a live JSON body against a contract schema.

No JSON Schema validator is in the lock, so this reads the small subset ``contracts/openapi.yaml``
uses for the ``GET /state`` determinant: ``type`` (object, array, string, number, integer,
boolean), ``enum``, ``nullable``, ``required``, ``properties``, ``items``, ``$ref``, a
one-member ``allOf`` (the file's way to make a ``$ref`` nullable, since a ``nullable`` beside a
bare ``$ref`` is ignored), and, for ``GET /sessions/{id}``'s ``SessionDetail``,
``exclusiveMinimum`` and ``additionalProperties`` (``false`` refuses an unlisted key, a schema
validates each one), and a ``oneOf`` only together with a ``discriminator`` (its ``propertyName``
and a ``mapping`` of ``$ref`` strings, as the served OpenAPI writes a tagged union): the body's
discriminator value picks the branch, and the body is validated against that branch alone. A
``oneOf`` or a ``discriminator`` alone is not read. Prose keywords (``description``, ``example``, ``default``, ``title``,
``format``) and ``x-`` extensions are not checked. Any other keyword raises ``ValueError`` rather than passing
unread, so a schema this module cannot judge is never reported valid.

A property the schema does not list is allowed unless ``additionalProperties`` is ``false``: the
contract's responses are additive (the engine may serve more). ``validate`` returns every violation as ``"<path>: <what>"``; an empty
list is a valid body. Loaded by tests with ``importlib.util.spec_from_file_location``, as the
other ``tests/support`` modules are.
"""

from __future__ import annotations

from typing import Any

REF_PREFIX = "#/components/schemas/"
CHECKED = {
    "type",
    "enum",
    "nullable",
    "required",
    "properties",
    "items",
    "$ref",
    "allOf",
    "additionalProperties",
    "exclusiveMinimum",
}
PROSE = {"description", "example", "default", "title", "format"}
UNION = {"oneOf", "discriminator"}


def _prose(key: str) -> bool:
    """A prose keyword, or an ``x-`` extension (``x-invariant`` is a sentence, not a constraint)."""
    return key in PROSE or key.startswith("x-")


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
    union = UNION <= schema.keys()
    unknown = {
        key for key in schema if key not in CHECKED and not _prose(key) and not (union and key in UNION)
    }
    if unknown:
        raise ValueError(f"{path}: unsupported keyword(s) {sorted(unknown)}")
    if union:
        return _validate_union(doc, schema, value, path)
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
        rest = {k for k in schema if k != "$ref" and not _prose(k)}
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
    if "exclusiveMinimum" in schema and _type_ok("number", value) and not value > schema["exclusiveMinimum"]:
        errors.append(f"{path}: {value!r} is not above {schema['exclusiveMinimum']}")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: required property {name!r} is missing")
        listed = schema.get("properties", {})
        for name, sub in listed.items():
            if name in value:
                errors.extend(validate(doc, sub, value[name], f"{path}.{name}"))
        extra = schema.get("additionalProperties", True)
        for name in (n for n in value if n not in listed):
            if extra is False:
                errors.append(f"{path}: property {name!r} is not allowed")
            elif isinstance(extra, dict):
                errors.extend(validate(doc, extra, value[name], f"{path}.{name}"))
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(validate(doc, schema["items"], item, f"{path}[{index}]"))
    return errors


def _validate_union(doc: dict, schema: dict, value: Any, path: str) -> list[str]:
    """A ``oneOf`` of ``$ref`` members, the branch chosen by the body's discriminator property.

    The schema must be readable whole, or ``ValueError`` is raised: no keyword beside the union
    but prose, every member a bare ``$ref``, a ``discriminator`` of exactly ``propertyName`` and
    ``mapping``, and every mapping value a ``$ref`` string naming one of the members.
    """
    beside = {key for key in schema if key not in UNION and not _prose(key)}
    if beside:
        raise ValueError(f"{path}: keywords beside a discriminated oneOf are not read: {sorted(beside)}")
    members = schema["oneOf"]
    if not isinstance(members, list) or not all(
        isinstance(member, dict) and set(member) == {"$ref"} for member in members
    ):
        raise ValueError(f"{path}: only a oneOf of bare $ref members is supported, got {members!r}")
    refs = [member["$ref"] for member in members]
    discriminator = schema["discriminator"]
    if not isinstance(discriminator, dict) or set(discriminator) != {"propertyName", "mapping"}:
        raise ValueError(
            f"{path}: a discriminator needs exactly propertyName and mapping, got {discriminator!r}"
        )
    name, mapping = discriminator["propertyName"], discriminator["mapping"]
    if not isinstance(name, str) or not isinstance(mapping, dict) or not mapping:
        raise ValueError(f"{path}: unsupported discriminator {discriminator!r}")
    for target in mapping.values():
        if not isinstance(target, str) or target not in refs:
            raise ValueError(
                f"{path}: discriminator mapping target {target!r} is not a $ref among the oneOf members"
            )
        resolve(doc, target)
    if value is None:
        return [f"{path}: null where the schema is not nullable"]
    if not isinstance(value, dict):
        return [f"{path}: expected object for discriminator {name!r}, got {type(value).__name__} {value!r}"]
    if name not in value:
        return [f"{path}: discriminator property {name!r} is missing"]
    chosen = value[name]
    if not isinstance(chosen, str):
        return [f"{path}.{name}: discriminator expected string, got {type(chosen).__name__} {chosen!r}"]
    if chosen not in mapping:
        return [
            f"{path}: discriminator {name!r} value {chosen!r} is not in discriminator mapping {sorted(mapping)}"
        ]
    return validate(doc, {"$ref": mapping[chosen]}, value, path)
