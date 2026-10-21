"""The names a module reads values under, recovered from its source (an AST walk).

Loaded by tests with ``importlib.util.spec_from_file_location``, as the other
``tests/support`` modules are. ``test_hrv_quarantine_boundary.py`` reconciles
``hrv_classification``'s reads against ``HRV_INPUT_FIELDS`` with it and
``test_overnight_quarantine_boundary.py`` reconciles ``ingestion/overnight.py``'s
reads against ``OVERNIGHT_INPUT_FIELDS``; the class docstring says what the
walk sees and what it cannot.
"""

from __future__ import annotations

import ast


class FieldReadCollector(ast.NodeVisitor):
    """Every *name* a module reads a value under.

    Three read shapes, which together cover how the module gets at
    anything a FIT file or an upstream stage put in front of it:

    - ``x.get("name")`` / ``x.get_value("name")`` / ``x.has_field("name")``
      -- the decoded-message and dict lookups (``message.get_value``,
      ``summary.get``, ``_provenance(session).get``). ``get_values``,
      ``get_field`` and ``get_fields`` are watched alongside them:
      ``fitdecode.records.FitDataMessage`` exposes all five, each
      taking the same ``field_name_or_num`` first argument, and a read
      through a sibling accessor is the same read.
    - ``getattr(x, "name")`` -- an attribute read spelled dynamically.
      The literal (and module-constant) form is recoverable, so it is
      collected; a genuinely dynamic name lands in ``unresolved``.
    - ``session.name`` in a *load* position -- attribute reads off the
      canonical ``Session``. Stores are excluded deliberately: writing
      ``session.rmssd_precomputed`` is the module's *output*, not an
      input to its decision.
    - ``x["name"]`` in a load position -- subscripted dict reads, which
      the module does not use today but which a future edit might.

    Deliberately over-broad on the receiver: it does not matter *what*
    object a quarantined name is read off, only that the module reads
    it. Over-collection costs a one-line classification in
    ``HRV_NON_INPUT_READS``; under-collection would let a read slip past
    the invariant.

    **Lookup keys are resolved through module-level constants.** The
    collector originally accepted string *literals* only, which left it
    blind to ``hrv_classification``'s own dominant convention -- a named
    constant per key (``_SNAPSHOT_RAW_SPORT_VALUE``, ``_PROVENANCE_*``,
    ``_FLAG_*``). An edit written that idiomatic way::

        _FIELD_BODY_BATTERY = "body_battery"
        ...
        msg.get_value(_FIELD_BODY_BATTERY, fallback=None)

    passes an ``ast.Name``, was silently dropped, and so appeared in
    neither the "unclassified" nor the "stale" half of the
    reconciliation -- leaving both green while a quarantined
    vendor-derived field fed the HRV decision. ``constants`` is the
    module's own ``{target: value}`` map over top-level string-constant
    assignments, and a ``Name`` key is resolved through it.

    **An unresolvable lookup key is recorded, never ignored.** Silent
    non-collection is what made the guard defeatable in the first place,
    so a key that is neither a string literal nor a resolvable
    module-level string constant lands in ``unresolved`` and
    ``assert_every_lookup_key_is_resolvable`` turns that into a
    failure. A constant bound to a non-string (``_SNAPSHOT_RAW_SPORT_VALUE
    = 60`` is a *value*, not a key) is unresolvable too, rather than
    being injected into ``names`` as a bogus field name.

    **A lookup with no key in the positional slot is unresolvable, not
    absent.** ``visit_Call`` used to require ``node.args``, so
    ``msg.get_value(field_name_or_num="avg_stress")`` -- legal, because
    ``fitdecode.records`` declares that parameter positional-or-keyword
    -- left ``node.args`` empty and the call was skipped outright.
    Keyword-passed, ``**kwargs``-spread and ``*args``-starred keys now
    all land in ``unresolved`` instead.

    Loudness is scoped to *lookup call* keys. Subscript slices get the
    same constant resolution but stay quiet when unresolvable, because
    ``x[i]`` is overwhelmingly ordinary indexing (and ``list[str]`` an
    annotation) rather than a field read -- making those loud would
    report noise, not smuggling.
    """

    _LOOKUP_METHODS = frozenset(
        {"get", "get_value", "get_values", "get_field", "get_fields", "has_field"}
    )

    def __init__(self, constants: dict[str, str] | None = None) -> None:
        self.names: set[str] = set()
        self.unresolved: set[str] = set()
        self._constants = constants or {}

    def _resolve(self, node: ast.expr) -> str | None:
        """The string this key node denotes, or ``None`` if unknowable."""
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.Name):
            return self._constants.get(node.id)
        return None

    def _key_position(self, func: ast.expr) -> int | None:
        """Which positional argument carries the field name, if any."""
        if isinstance(func, ast.Attribute) and func.attr in self._LOOKUP_METHODS:
            return 0
        if isinstance(func, ast.Name) and func.id == "getattr":
            return 1
        return None

    def visit_Call(self, node: ast.Call) -> None:
        position = self._key_position(node.func)
        if position is not None:
            if len(node.args) > position:
                key = node.args[position]
                resolved = self._resolve(key)
                if resolved is not None:
                    self.names.add(resolved)
                else:
                    self.unresolved.add(ast.unparse(key))
            else:
                # The key is not in the positional slot at all: passed by
                # keyword (``get_value(field_name_or_num="avg_stress")``,
                # which ``fitdecode`` accepts), spread from a ``**kwargs``
                # mapping, or swallowed by a ``*args`` star before it. The
                # call reads *some* field and this collector cannot say
                # which, so it is reported rather than skipped -- skipping
                # is what let the keyword spelling read a quarantined
                # field with every assertion below staying green.
                self.unresolved.add(ast.unparse(node))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "session"
            and isinstance(node.ctx, ast.Load)
        ):
            self.names.add(node.attr)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, ast.Load):
            resolved = self._resolve(node.slice)
            if resolved is not None:
                self.names.add(resolved)
        self.generic_visit(node)


def module_level_string_constants(tree: ast.Module) -> dict[str, str]:
    """``{target: value}`` for every top-level ``NAME = "literal"``.

    One pass over the module body only: a name bound inside a function
    is not a module-level constant, and resolving through one would be
    guessing at flow rather than reading a declaration.
    """
    constants: dict[str, str] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets = [statement.target]
        else:
            continue
        value = statement.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value.value
    return constants


def collect_reads(source: str) -> FieldReadCollector:
    """Walk ``source``, resolving lookup keys through its own constants."""
    tree = ast.parse(source)
    collector = FieldReadCollector(module_level_string_constants(tree))
    collector.visit(tree)
    return collector


def assert_every_lookup_key_is_resolvable(collector: FieldReadCollector) -> None:
    assert not collector.unresolved, (
        "lookup key(s) cannot be resolved to a field name statically, so the "
        "reconciliation below is blind to them: "
        f"{sorted(collector.unresolved)}"
    )
