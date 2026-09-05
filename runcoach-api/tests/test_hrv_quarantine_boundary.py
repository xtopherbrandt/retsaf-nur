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
      ``summary.get``, ``_provenance(session).get``).
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
    """

    _LOOKUP_METHODS = frozenset({"get", "get_value", "has_field"})

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in self._LOOKUP_METHODS
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            self.names.add(node.args[0].value)
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
        if (
            isinstance(node.ctx, ast.Load)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            self.names.add(node.slice.value)
        self.generic_visit(node)


def _names_read_by_the_classification_module() -> frozenset[str]:
    source = Path(hrv_classification.__file__).read_text(encoding="utf-8")
    collector = _FieldReadCollector()
    collector.visit(ast.parse(source))
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
