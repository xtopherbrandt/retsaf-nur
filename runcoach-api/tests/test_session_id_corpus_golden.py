"""T247 (F007 AC5): the corpus ``session_id`` golden table.

oracle: the fixture file bytes at c2839b6, measured before F007's code
existed; literals, never computed at test time.

``tests/test_session_id_determinism.py`` proves ``session_id`` is
deterministic and is the derivation applied to ``(source_device,
start_time)``. Neither assertion pins the *values*: a change to
``_build_source_device`` that moves every id consistently keeps both
green. F007 AC5 promises that ``source_device`` and the dedup key stay
exactly what they are today, so this module commits the eleven
``(source_device, session_id)`` pairs the pre-feature build produces for
the real fixture corpus and re-derives them through the real decoder,
through ``mapping.to_canonical`` per file and through
``pipeline.ingest_fit_bytes`` into a fresh isolated database.

The expected values are literals pasted from a measurement at c2839b6.
They are deliberately not recomputed from ``derive_session_id`` here --
that would pin the function against itself. If a row goes red, the
change under test altered the session identity the local-first rebuild
path, the ``UNIQUE (source_device, start_time)`` constraint and the
decision log all key on; see section 2.2.1 of the canonical schema spec.

Perturbation evidence (recorded in this module's commit body): reading
the *last* ``device_info`` instead of the first in ``_build_source_device``
turns rows red; the module is green at base by design.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runcoach_api import db
from runcoach_api.ingestion import fit_parser, mapping, pipeline

FIXTURES = Path(__file__).parent / "fixtures"

# fixture name -> (source_device, session_id), measured at c2839b6.
GOLDEN: dict[str, tuple[str, str]] = {
    "dev_fields_run.fit": ("fr955 fw19.18", "bfd1daaa50481984871f00479a8ad04d"),
    "sample_health_snapshot.fit": ("fr945_lte fw17.4", "e25a77b6fa3e126533bbe348a0060460"),
    "sample_run.fit": ("fr945_lte fw17.4", "c5f50427f21507dcfa21adf0d041b150"),
    "strap_cool_down_walk.fit": ("fr945_lte fw17.4", "92a80fc19e03808b9e78ad3f711a8096"),
    "strap_health_snapshot.fit": ("fr945_lte fw17.4", "67eacae08761f2e1fb5b5f27a3d424f0"),
    "strap_health_snapshot_hrv.fit": ("fr945_lte fw17.4", "4d1eac5cec302899ebeb98bf8dcc8230"),
    "strap_hrv_capture.fit": ("fr945_lte fw17.4", "e8660d1ee6bf79dcbe38350934965a66"),
    "strap_hrv_sample_run.fit": ("fr945_lte fw17.4", "7f23e5730c518efe39ab10e08334388f"),
    "strap_run_hrv.fit": ("fr945_lte fw17.4", "f57866666721d813f3968239a20d9c67"),
    "wrist_ppg_hrv_snapshot.fit": ("fr945_lte fw17.4", "4c7c31e2ce5a8afb286286298c4bdc87"),
    "wrist_ppg_run.fit": ("fr945_lte fw17.4", "8c4dbd7fc5397beddb38cffcd9a12e09"),
}


def test_fixture_directory_holds_exactly_the_golden_names() -> None:
    """A fixture added to ``tests/fixtures/`` without a golden row would
    otherwise fall silently outside the pin; one removed would leave a
    row nothing exercises. The guard is a collected test, not a
    module-level assert, so its failure is reported as one."""
    on_disk = sorted(p.name for p in FIXTURES.glob("*.fit"))
    assert on_disk == sorted(GOLDEN), (
        "fixture corpus and golden table disagree: "
        f"only on disk {sorted(set(on_disk) - set(GOLDEN))}, "
        f"only in table {sorted(set(GOLDEN) - set(on_disk))}"
    )


@pytest.mark.parametrize("fixture_name", sorted(GOLDEN))
def test_corpus_session_id_matches_golden(fixture_name: str) -> None:
    """Per file, through the real decoder and mapper, no database."""
    session, _records = mapping.to_canonical(
        fit_parser.decode((FIXTURES / fixture_name).read_bytes())
    )

    assert (session.source_device, session.session_id) == GOLDEN[fixture_name]


def test_reingesting_the_corpus_persists_the_golden_table(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    """AC5's re-ingest wording: every fixture through
    ``pipeline.ingest_fit_bytes`` into one fresh database, then the
    persisted ``(session_id, source_device)`` rows read back equal the
    table. Resting captures route through the resting-HRV path and still
    persist a session row, so eleven rows are expected, never fewer."""
    # The autouse fixture must have pointed the DB layer at tmp_path before
    # init_schema runs; verify the resolved path rather than trusting it.
    resolved_data_dir = db._load_config_cached().data_dir
    assert resolved_data_dir == isolated_data_dir
    assert tmp_path in resolved_data_dir.parents

    for fixture_name in sorted(GOLDEN):
        result = pipeline.ingest_fit_bytes((FIXTURES / fixture_name).read_bytes())
        assert result.session_id == GOLDEN[fixture_name][1], fixture_name

    conn = sqlite3.connect(resolved_data_dir / db.DB_FILENAME)
    try:
        rows = conn.execute(
            "SELECT session_id, source_device FROM sessions ORDER BY session_id"
        ).fetchall()
    finally:
        conn.close()

    expected = sorted((session_id, source_device) for source_device, session_id in GOLDEN.values())
    assert rows == expected
