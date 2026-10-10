"""``tests/support/contract_walk.py``: compare contract components with the served OpenAPI, property by property.

Loaded by tests with ``importlib.util.spec_from_file_location``, as the other ``tests/support``
modules are. Shared by the ``/me``, ``getSessionLoad`` and ``getLoadChart`` contract walks.

``normalised`` reads one schema node in either document's spelling: the contract's
``nullable: true`` and FastAPI's ``anyOf: [X, {type: null}]`` both read as nullable X, a
one-member ``allOf`` is its member, a ``const`` reads as a one-value ``enum``, an enum's order is
not compared, and an array's ``items`` is normalised the same way, so the enums and components
behind a list are compared rather than skipped. ``walk`` follows every ``$ref`` (through nested
``items`` too) from the given roots and compares each component reached.
"""

from __future__ import annotations

from collections.abc import Iterable

REF_PREFIX = "#/components/schemas/"
# The keywords compared per property. A description, a title or a default is prose and is not compared.
COMPARED_KEYWORDS = ("type", "enum", "format", "exclusiveMinimum")


def ref_name(ref: str) -> str:
    assert ref.startswith(REF_PREFIX), ref
    return ref[len(REF_PREFIX) :]


def normalised(prop: dict) -> dict:
    """One schema node as ``{type, nullable, enum, format, exclusiveMinimum, ref, items}``."""
    prop = dict(prop)
    nullable = bool(prop.pop("nullable", False))
    if "anyOf" in prop:
        branches = prop.pop("anyOf")
        rest = [b for b in branches if b != {"type": "null"}]
        assert len(rest) == 1 and len(rest) < len(branches), f"not an optional single type: {branches}"
        nullable = True
        prop = {**prop, **rest[0]}
    if "const" in prop:
        prop["enum"] = [prop.pop("const")]
    if "allOf" in prop:
        (only,) = prop.pop("allOf")
        prop = {**prop, **only}
    out = {"nullable": nullable}
    if "$ref" in prop:
        out["ref"] = ref_name(prop["$ref"])
    for key in COMPARED_KEYWORDS:
        if key in prop:
            out[key] = sorted(prop[key]) if key == "enum" else prop[key]
    if "items" in prop:
        out["items"] = normalised(prop["items"])
    assert "ref" in out or "type" in out, f"a node with neither a type nor a $ref: {prop}"
    return out


def refs(node: dict) -> list[str]:
    """Every component a normalised node names, through ``ref`` and nested ``items``."""
    found = [node["ref"]] if "ref" in node else []
    if "items" in node:
        found.extend(refs(node["items"]))
    return found


def walk(contract: dict, built: dict, roots: Iterable[str]) -> tuple[set[str], list[str]]:
    """Compare every component reachable from ``roots`` in ``contract`` and ``built`` alike.

    An object component has the same property names in both, each property normalised equal,
    the same ``required`` set and the same ``additionalProperties``; a component without
    properties (an enum) is compared as one node. Returns the components visited and the
    ``Component.field`` (or ``Component``) names compared.
    """
    queue, seen, compared = sorted(roots), set(), []
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        ours, theirs = contract["components"]["schemas"][name], built["components"]["schemas"][name]
        if "properties" not in theirs:
            assert "properties" not in ours, name
            got, want = normalised(ours), normalised(theirs)
            assert got == want, f"{name}: contract {got} != served {want}"
            compared.append(name)
            continue
        assert set(ours["properties"]) == set(theirs["properties"]), name
        for field in ours["properties"]:
            want = normalised(theirs["properties"][field])
            got = normalised(ours["properties"][field])
            assert got == want, f"{name}.{field}: contract {got} != served {want}"
            compared.append(f"{name}.{field}")
            queue.extend(refs(got))
        assert set(ours.get("required", [])) == set(theirs.get("required", [])), name
        assert ours.get("additionalProperties", True) == theirs.get("additionalProperties", True), name
    return seen, compared
