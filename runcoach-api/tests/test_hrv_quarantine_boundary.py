"""T046: a quarantined vendor-derived field can never become an HRV input.

F004's quarantine boundary, asserted as a **negative invariant** rather
than as a positive path. Spec §2.2.3 draws the line precisely: Garmin's
overnight HRV *Status classification* (Balanced/Unbalanced/Low) is a
vendor-derived composite and is quarantined -- never a trend input --
while the numeric rMSSD underneath it is a standard statistic and *is*
admissible. "The label is a black box, the number is not."

There is no positive path to test today: ``_VENDOR_DERIVED_FIELDS``
registers four session fields and nothing HRV-related, no fixture in the
corpus carries an HRV Status classification, and ``hrv_status_summary``
is a monitoring/wellness message an *activity* FIT file uploaded to
``POST /sessions`` would not carry at all. Specifying the positive path
would have produced a branch that cannot fire on any real file, green
against a fabricated value. The invariant below is testable **today**,
against a real fixture.

Three layers, and all three are needed:

1. **Structural** -- ``hrv_classification.HRV_INPUT_FIELDS`` and
   ``quarantine._VENDOR_DERIVED_FIELDS["session"]`` are disjoint. This
   catches the failure mode no fixture-based test can: a future edit
   that registers an HRV field in quarantine, or reads a quarantined
   field as an HRV input. It would be introduced by *code*, not data.
2. **Honesty of the declared set** -- a disjointness assertion is only
   as good as the set it checks, so ``HRV_INPUT_FIELDS`` is verified
   against the classification module's *actual* field reads, recovered
   by walking its AST. A future task that adds a field read without
   declaring it fails here rather than silently shrinking the invariant
   to nothing. The AST-recovered set is *also* checked against the
   quarantine registry directly, so the boundary cannot be dodged by
   filing a quarantined name under the non-input escape hatch.
   **What layer 2 cannot see, stated plainly.** The AST walk recovers a
   field name only where the source *names* it -- as a literal, or as a
   module-level string constant. One shape defeats that by construction:
   iterating a message's own field list and matching at runtime, the way
   ``rr_reconstruction._developer_field_candidates`` does::

       for field_data in msg.fields:
           if field_data.units == "ms":
               ...

   No field name appears anywhere in that source, so there is nothing to
   collect and nothing to report -- a quarantined value reached this way
   would pass layer 2 in silence. Making bare ``.fields`` access loud was
   rejected: it would fire on every ordinary iteration and train the
   reader to ignore the failure. ``hrv_classification`` does not iterate
   ``.fields`` today, and layers 1 and 3 still hold if it ever does; this
   is a documented limit, not a covered case.

3. **Fixture-based regression** -- ``sample_health_snapshot.fit``
   carries quarantined ``avg_stress = 19`` and admissible
   ``rmssd_hrv = 37`` on the **same session message**. The stress score
   must reach only ``quarantine_sidecar`` and the rMSSD only
   ``sessions``, neither leaking into the other's home. Mirrors
   ``test_quarantine.py``'s
   ``test_canonical_get_response_never_contains_training_effect``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runcoach_api import db
from runcoach_api.ingestion import hrv_classification, quarantine
from runcoach_api.main import app

SNAPSHOT_FIXTURE = Path(__file__).parent / "fixtures" / "sample_health_snapshot.fit"

# The fixture's two values, verified by decoding its single ``session``
# message (F004 reference document §6): a quarantined stress score and an
# admissible device rMSSD, side by side on the same message.
FIXTURE_QUARANTINED_AVG_STRESS = 19
FIXTURE_DEVICE_RMSSD = 37

# The field names the classification path is known to read, spelled out
# here independently of the module's own constant. If
# ``HRV_INPUT_FIELDS`` were quietly narrowed -- to ``set()``, or to only
# the names already known to be safe -- the disjointness assertion would
# still pass while proving nothing, so the declared set is pinned from
# the outside as well as verified against the source.
KNOWN_CLASSIFICATION_INPUTS = frozenset(
    {
        # the Tier-2 capability signal, off the ``session`` message
        "rmssd_hrv",
        # the Tier-2 identity signal, off mapping.py's provenance
        "raw_sport_value",
        # the Tier-1 resting discriminator, off ``session.summary``
        "duration_s",
        "distance_m",
        "avg_heart_rate",
        # the artefact-survival quality gate
        "rr_valid_fraction",
    }
)


def _banned_session_fields() -> frozenset[str]:
    return frozenset(quarantine._VENDOR_DERIVED_FIELDS["session"])


# ---------------------------------------------------------------------------
# recovering the module's actual field reads from its source
# ---------------------------------------------------------------------------


class _FieldReadCollector(ast.NodeVisitor):
    """Every *name* ``hrv_classification`` reads a value under.

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
    ``_assert_every_lookup_key_is_resolvable`` turns that into a
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


def _module_level_string_constants(tree: ast.Module) -> dict[str, str]:
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


def _collect_reads(source: str) -> _FieldReadCollector:
    """Walk ``source``, resolving lookup keys through its own constants."""
    tree = ast.parse(source)
    collector = _FieldReadCollector(_module_level_string_constants(tree))
    collector.visit(tree)
    return collector


def _assert_every_lookup_key_is_resolvable(collector: _FieldReadCollector) -> None:
    assert not collector.unresolved, (
        "lookup key(s) cannot be resolved to a field name statically, so the "
        "reconciliation below is blind to them: "
        f"{sorted(collector.unresolved)}"
    )


def _names_read_by_the_classification_module() -> frozenset[str]:
    source = Path(hrv_classification.__file__).read_text(encoding="utf-8")
    collector = _collect_reads(source)
    _assert_every_lookup_key_is_resolvable(collector)
    return frozenset(collector.names)


# ---------------------------------------------------------------------------
# 1. structural -- the two sets are disjoint
# ---------------------------------------------------------------------------


def test_hrv_input_fields_is_disjoint_from_the_quarantine_registry() -> None:
    overlap = _banned_session_fields() & frozenset(hrv_classification.HRV_INPUT_FIELDS)

    assert not overlap, f"quarantined field feeds an HRV input: {sorted(overlap)}"


def test_no_quarantined_name_is_read_anywhere_in_the_classification_module() -> None:
    # Strictly stronger than the assertion above, and it closes the one
    # hole in it: a quarantined read could otherwise be declared a
    # "non-input" and slip past. The module must not read a quarantined
    # name in *any* capacity.
    overlap = _banned_session_fields() & _names_read_by_the_classification_module()

    assert not overlap, f"hrv_classification reads a quarantined field: {sorted(overlap)}"


def test_the_disjointness_assertion_actually_bites() -> None:
    # Confirm the guard fails when the boundary is crossed, rather than
    # passing vacuously because one of the sets is empty. Registering an
    # HRV input in the quarantine registry is exactly the future edit
    # this invariant exists to catch.
    doctored = dict(quarantine._VENDOR_DERIVED_FIELDS)
    doctored["session"] = (*doctored["session"], "rmssd_hrv")

    overlap = frozenset(doctored["session"]) & frozenset(hrv_classification.HRV_INPUT_FIELDS)

    assert overlap == {"rmssd_hrv"}


# ---------------------------------------------------------------------------
# 2. the declared set is honest, and stays honest
# ---------------------------------------------------------------------------


def test_hrv_input_fields_enumerates_every_known_classification_input() -> None:
    missing = KNOWN_CLASSIFICATION_INPUTS - frozenset(hrv_classification.HRV_INPUT_FIELDS)

    assert not missing, f"HRV_INPUT_FIELDS omits a field the classifier reads: {sorted(missing)}"


def test_hrv_input_fields_matches_the_modules_actual_field_reads() -> None:
    """Every name the module reads is classified, one bucket or the other.

    This is what stops ``HRV_INPUT_FIELDS`` drifting as later tasks add
    field reads: a new read that is declared in neither constant fails
    here, naming itself in the failure message, and the author has to
    decide which bucket it belongs in. A narrow ``HRV_INPUT_FIELDS``
    therefore cannot survive a new read going unremarked.
    """
    declared = frozenset(hrv_classification.HRV_INPUT_FIELDS) | frozenset(
        hrv_classification.HRV_NON_INPUT_READS
    )
    discovered = _names_read_by_the_classification_module()

    unclassified = discovered - declared
    assert not unclassified, (
        "hrv_classification reads names declared in neither HRV_INPUT_FIELDS nor "
        f"HRV_NON_INPUT_READS: {sorted(unclassified)}"
    )

    stale = declared - discovered
    assert not stale, f"declared but no longer read by hrv_classification: {sorted(stale)}"


def test_the_two_declared_buckets_do_not_overlap() -> None:
    overlap = frozenset(hrv_classification.HRV_INPUT_FIELDS) & frozenset(
        hrv_classification.HRV_NON_INPUT_READS
    )

    assert not overlap, f"a name is declared both an input and a non-input: {sorted(overlap)}"


@pytest.mark.parametrize("constant_name", ["HRV_INPUT_FIELDS", "HRV_NON_INPUT_READS"])
def test_the_declared_constants_are_importable_string_sets(constant_name: str) -> None:
    # "Don't make HRV_INPUT_FIELDS a comment or a docstring list. It has
    # to be an importable value or the structural assertion is theatre."
    value = getattr(hrv_classification, constant_name)

    assert isinstance(value, frozenset)
    assert value
    assert all(isinstance(name, str) for name in value)


# ---------------------------------------------------------------------------
# 3. fixture-based regression -- neither value reaches the other's home
# ---------------------------------------------------------------------------


def test_quarantined_stress_never_leaks_into_the_canonical_response() -> None:
    with TestClient(app) as client:
        post_response = client.post(
            "/sessions",
            files={"file": ("sample_health_snapshot.fit", SNAPSHOT_FIXTURE.read_bytes())},
        )
        assert post_response.status_code == 201
        session_id = post_response.json()["session_id"]

        get_response = client.get(f"/sessions/{session_id}")

    assert get_response.status_code == 200
    body = get_response.json()

    # The admissible half survives the round trip...
    assert body["rmssd_precomputed"] == FIXTURE_DEVICE_RMSSD
    assert body["hrv_source_tier"] == "health_snapshot"

    # ...and the quarantined half appears nowhere in the body, under any
    # of the four registered names.
    rendered = str(body).lower()
    for banned in _banned_session_fields():
        assert banned not in rendered, (
            f"quarantined value leaked into the canonical response: {banned}"
        )


def test_stress_reaches_only_the_sidecar_and_rmssd_only_the_session_row() -> None:
    with TestClient(app) as client:
        post_response = client.post(
            "/sessions",
            files={"file": ("sample_health_snapshot.fit", SNAPSHOT_FIXTURE.read_bytes())},
        )
        assert post_response.status_code == 201
        session_id = post_response.json()["session_id"]

    conn = db.get_connection()
    try:
        cur = conn.execute(
            "SELECT field_name, value FROM quarantine_sidecar WHERE session_id = ?",
            (session_id,),
        )
        sidecar = {field_name: value for field_name, value in cur.fetchall()}
        cur = conn.execute(
            "SELECT rmssd_precomputed, hrv_source_tier FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()

    # db.persist() stores quarantine values via json.dumps(...), so read
    # them back the same way test_quarantine.py does.
    assert float(json.loads(sidecar["avg_stress"])) == FIXTURE_QUARANTINED_AVG_STRESS

    # The sidecar is the stress score's only home: no HRV input of any
    # kind may be sitting in there alongside it.
    assert not frozenset(sidecar) & frozenset(hrv_classification.HRV_INPUT_FIELDS)

    # And the session row carries the admissible number, sourced from the
    # device rMSSD -- not from anything quarantined.
    assert row is not None
    assert row[0] == FIXTURE_DEVICE_RMSSD
    assert row[1] == "health_snapshot"


# ---------------------------------------------------------------------------
# 4. the collector itself cannot be dodged by the module's own convention
#
# The reconciliation in section 2 is only as good as what the collector
# sees. It used to see *string literals only*, while
# ``hrv_classification``'s dominant convention is a named constant per key
# (``_SNAPSHOT_RAW_SPORT_VALUE``, ``_PROVENANCE_*``, ``_FLAG_*``). An edit
# written the module's own idiomatic way --
#
#     _FIELD_BODY_BATTERY = "body_battery"
#     ...
#     msg.get_value(_FIELD_BODY_BATTERY, fallback=None)
#
# -- passes an ``ast.Name``, was silently never collected, and so was
# neither "unclassified" nor "stale": both assertions above stayed green
# while a quarantined vendor-derived field fed the HRV decision. That is
# precisely the failure this module exists to prevent, so the collector
# now resolves module-level string constants, and *fails loudly* on a
# lookup key it cannot resolve rather than dropping it on the floor.
# ---------------------------------------------------------------------------


def test_the_collector_resolves_a_module_level_constant_lookup_key() -> None:
    """A named constant used as a lookup key is collected under its value."""
    collector = _collect_reads(
        '_FIELD_BODY_BATTERY = "body_battery"\n'
        "def f(msg):\n"
        "    return msg.get_value(_FIELD_BODY_BATTERY, fallback=None)\n"
    )

    assert "body_battery" in collector.names
    assert not collector.unresolved


@pytest.mark.parametrize(
    "method", ["get", "get_value", "get_values", "get_field", "get_fields", "has_field"]
)
def test_constant_resolution_covers_every_lookup_method(method: str) -> None:
    collector = _collect_reads(
        f'_KEY = "avg_stress"\ndef f(x):\n    return x.{method}(_KEY)\n'
    )

    assert "avg_stress" in collector.names


def test_the_collector_fails_loudly_on_an_unresolvable_lookup_key() -> None:
    """Silent non-collection is what made the guard defeatable.

    A key that is neither a literal nor a resolvable module-level
    constant must be *reported*, not ignored -- otherwise the next
    indirection (a parameter, an attribute, a dict lookup) reopens the
    exact hole the constant map just closed.
    """
    collector = _collect_reads("def f(msg, key):\n    return msg.get_value(key)\n")

    assert not collector.names
    assert collector.unresolved == {"key"}


def test_an_unresolvable_lookup_key_is_reported_by_the_reconciliation() -> None:
    """The loud failure has to reach an assertion, not just an attribute."""
    with pytest.raises(AssertionError, match="cannot be resolved"):
        _assert_every_lookup_key_is_resolvable(
            _collect_reads("def f(msg, key):\n    return msg.get_value(key)\n")
        )


def test_no_lookup_key_in_the_classification_module_is_unresolvable() -> None:
    """The live guard, against the real source.

    Every lookup key in ``hrv_classification`` must be recoverable
    statically. The moment one is not, the reconciliation above is
    blind to it and this fails rather than passing quietly.
    """
    _assert_every_lookup_key_is_resolvable(
        _collect_reads(Path(hrv_classification.__file__).read_text(encoding="utf-8"))
    )


def test_the_reconciliation_bites_on_a_constant_indirected_quarantined_read() -> None:
    """The permanent form of the manual proof, mirroring
    ``test_the_disjointness_assertion_actually_bites``.

    Take the real module source, append a read of a quarantined field
    written the module's own idiomatic way -- a named constant, not a
    literal -- and confirm the collector now sees it. Before the fix
    this returned the untouched set and the guard stayed green while a
    quarantined field fed the decision.
    """
    banned = sorted(_banned_session_fields())[0]
    real_source = Path(hrv_classification.__file__).read_text(encoding="utf-8")
    doctored = (
        f'{real_source}\n\n_FIELD_SMUGGLED = "{banned}"\n\n\n'
        "def _smuggle(message):\n"
        "    return message.get_value(_FIELD_SMUGGLED, fallback=None)\n"
    )

    discovered = frozenset(_collect_reads(doctored).names)

    # The guard sees it...
    assert banned in discovered
    # ...and both section-2 assertions it feeds now fail on it.
    declared = frozenset(hrv_classification.HRV_INPUT_FIELDS) | frozenset(
        hrv_classification.HRV_NON_INPUT_READS
    )
    assert discovered - declared == {banned}
    assert _banned_session_fields() & discovered == {banned}


def test_a_non_string_constant_is_not_mistaken_for_a_field_name() -> None:
    """``_SNAPSHOT_RAW_SPORT_VALUE = 60`` is a *value*, not a key.

    Resolving it into the discovered set would inject a bogus name and
    fail the reconciliation for the wrong reason.
    """
    collector = _collect_reads(
        "_RAW_SPORT = 60\ndef f(x):\n    return x.get(_RAW_SPORT)\n"
    )

    assert not collector.names
    assert collector.unresolved == {"_RAW_SPORT"}


# ---------------------------------------------------------------------------
# 5. the two bypasses the collector used to be blind to
#
# ``visit_Call`` used to require ``node.args`` -- a positional key -- so
# a lookup written with the key passed *by keyword* left ``node.args``
# empty and the call was skipped outright: neither collected nor
# reported. ``fitdecode.records`` makes that spelling legal (its
# ``field_name_or_num`` parameter is positional-or-keyword), so
#
#     msg.get_value(field_name_or_num="avg_stress")
#
# read a quarantined field while both section-2 assertions stayed green.
# The second bypass was the *method* set: only ``get``/``get_value``/
# ``has_field`` were watched, while ``FitDataMessage`` exposes
# ``get_values``/``get_field``/``get_fields`` reading the same named
# field, and ``getattr(obj, "name")`` reaches an attribute dynamically.
# ---------------------------------------------------------------------------


def test_a_keyword_passed_lookup_key_is_not_invisible() -> None:
    """The shape that used to be skipped entirely must now be loud."""
    collector = _collect_reads(
        "def f(msg):\n"
        '    return msg.get_value(field_name_or_num="avg_stress", fallback=None)\n'
    )

    assert collector.unresolved, "a keyword-passed lookup key was silently skipped"
    assert any("avg_stress" in entry for entry in collector.unresolved)


def test_the_reconciliation_bites_on_a_keyword_passed_quarantined_read() -> None:
    """The permanent proof, against the real module source.

    Take ``hrv_classification`` as it is on disk, append a read of a
    quarantined field with the key passed by keyword, and confirm the
    guard now refuses it. Before the fix the collector skipped the call
    outright, every section-2 assertion passed, and the quarantined
    read fed the HRV decision unnoticed. The source is doctored **in
    memory only** -- the file on disk is never written.
    """
    banned = sorted(_banned_session_fields())[0]
    real_source = Path(hrv_classification.__file__).read_text(encoding="utf-8")
    doctored = (
        f"{real_source}\n\n\n"
        "def _smuggle(message):\n"
        f'    return message.get_value(field_name_or_num="{banned}", fallback=None)\n'
    )

    collector = _collect_reads(doctored)

    # It is reported rather than dropped...
    assert collector.unresolved
    assert any(banned in entry for entry in collector.unresolved)
    # ...and the report reaches the assertion the reconciliation runs.
    with pytest.raises(AssertionError, match="cannot be resolved"):
        _assert_every_lookup_key_is_resolvable(collector)


def test_a_sibling_reader_method_is_watched_too() -> None:
    """``get_values`` reads the same named field ``get_value`` does."""
    banned = sorted(_banned_session_fields())[0]
    real_source = Path(hrv_classification.__file__).read_text(encoding="utf-8")
    doctored = (
        f'{real_source}\n\n_FIELD_SMUGGLED = "{banned}"\n\n\n'
        "def _smuggle(message):\n"
        "    return message.get_values(_FIELD_SMUGGLED)\n"
    )

    discovered = frozenset(_collect_reads(doctored).names)

    assert banned in discovered
    assert _banned_session_fields() & discovered == {banned}


def test_a_getattr_read_is_collected_under_its_literal_name() -> None:
    """``getattr(session, "avg_stress")`` is an attribute read spelled
    dynamically; the literal form is recoverable, so it is collected."""
    collector = _collect_reads('def f(obj):\n    return getattr(obj, "avg_stress", None)\n')

    assert "avg_stress" in collector.names
    assert not collector.unresolved


def test_a_dynamic_getattr_is_reported_rather_than_ignored() -> None:
    collector = _collect_reads("def f(obj, name):\n    return getattr(obj, name)\n")

    assert not collector.names
    assert collector.unresolved == {"name"}


def test_doctoring_never_touches_the_module_on_disk() -> None:
    """Every bite test above rewrites source in memory, never on disk."""
    path = Path(hrv_classification.__file__)
    before = path.read_bytes()

    test_the_reconciliation_bites_on_a_keyword_passed_quarantined_read()
    test_a_sibling_reader_method_is_watched_too()

    assert path.read_bytes() == before
