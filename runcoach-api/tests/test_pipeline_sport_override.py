"""F030: the sport override ``pipeline.ingest_fit_bytes`` takes for a Strava ``Run`` row.

A file the watch recorded on another sport (the athlete's Walk Runs, recorded on
the Cycling profile) is stored as running when Strava lists it as a run. Every
step of ``POST /sessions`` runs on the watch's own sport (``mapping.to_canonical``,
``hrv_classification.classify``, ``quality_gates.apply``); then, only when the
canonical sport is not running, the override sets ``sport`` and ``name`` and
appends ``sport_retyped_by_strava``. ``keep`` runs after it, so the keep-or-skip
rule reads ``running``. ``IngestResult.retyped`` says whether the override applied.

Fixtures cited: ``strap_run_hrv.fit`` (a run with RR beats, run proof per the
fixtures README) and its cycling copy (``session.sport`` and ``sport.sport`` set
to cycling by ``tests/support/strava_archive.cycling_copy``), which stands in for
a watch file recorded on another sport. The copy keeps the run's beats, so
``classify`` writes its undeclared-capture note on it, the branch the override
must leave as an upload leaves it.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.ingestion import pipeline
from runcoach_api.ingestion.pipeline import SportOverride
from runcoach_api.main import app
from runcoach_api.models import Session

TESTS = Path(__file__).parent
FIXTURES = TESTS / "fixtures"
RUN_WITH_BEATS = "strap_run_hrv.fit"
ACTIVITY_NAME = "Walk Run #4"
FLAG = "sport_retyped_by_strava"

_SPEC = importlib.util.spec_from_file_location("strava_archive_t", TESTS / "support" / "strava_archive.py")
assert _SPEC is not None and _SPEC.loader is not None
strava_archive = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(strava_archive)

# The columns the override is allowed to differ on from an upload of the same
# file, plus the three a store assigns per write.
OVERRIDDEN = {"sport", "name", "quality_flags"}
PER_WRITE = {"session_id", "upload_order", "import_id"}


def _run() -> bytes:
    return (FIXTURES / RUN_WITH_BEATS).read_bytes()


def _cycling() -> bytes:
    return strava_archive.cycling_copy(_run())


def _override() -> SportOverride:
    return SportOverride(name=ACTIVITY_NAME)


def _rows(data_dir: Path, sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql, params)]
    finally:
        conn.close()


def _session_row(data_dir: Path) -> dict:
    """The store's one session row, its two write-clock readings blanked: ``laps_read_at`` and
    ``context.ingested_at`` hold the time of the write, so no two writes of one file share them."""
    rows = _rows(data_dir, "SELECT * FROM sessions")
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["laps_read_at"] is not None
    row["laps_read_at"] = "<write time>"
    context = json.loads(row["context"])
    assert context["ingested_at"] is not None
    context["ingested_at"] = "<write time>"
    row["context"] = json.dumps(context, sort_keys=True)
    return row


def _provenance(row: dict) -> dict:
    return json.loads(row["context"])["provenance"]


def _upload(post_fit_bytes, raw: bytes) -> str:
    with TestClient(app) as client:
        response = post_fit_bytes(client, "activity.fit", raw)
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def test_override_stores_running_flagged_sport_retyped_by_strava(isolated_data_dir: Path) -> None:
    result = pipeline.ingest_fit_bytes(_cycling(), override=_override())

    assert result.retyped is True
    assert result.skipped is False
    assert FLAG in result.quality_flags
    row = _session_row(isolated_data_dir)
    assert row["session_id"] == result.session_id
    assert row["sport"] == "running"
    assert row["name"] == ACTIVITY_NAME
    assert json.loads(row["quality_flags"]).count(FLAG) == 1
    # The saved load is a running load: present, not compared with a cycling upload's.
    loads = _rows(isolated_data_dir, "SELECT * FROM session_loads WHERE session_id = ?", (result.session_id,))
    assert len(loads) == 1
    assert loads[0]["load_value"] is not None, loads[0]


def test_override_on_a_running_file_changes_nothing_and_reports_not_retyped(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    """A running file is stored as an upload stores it: its own name, no flag."""
    plain_dir = tmp_path / "plain"
    plain = pipeline.ingest_fit_bytes(_run(), data_dir=plain_dir)
    result = pipeline.ingest_fit_bytes(_run(), override=_override())

    assert result.retyped is False
    assert plain.retyped is False
    assert FLAG not in result.quality_flags
    overridden_row = _session_row(isolated_data_dir)
    plain_row = _session_row(plain_dir)
    assert overridden_row["name"] != ACTIVITY_NAME
    assert overridden_row == plain_row


def test_every_other_column_equals_an_upload_of_the_file(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    upload_id = _upload(post_fit_bytes, _cycling())
    retyped_dir = tmp_path / "retyped"
    result = pipeline.ingest_fit_bytes(_cycling(), override=_override(), data_dir=retyped_dir)
    assert result.retyped is True

    uploaded = _session_row(isolated_data_dir)
    retyped = _session_row(retyped_dir)
    assert uploaded["sport"] == "other"
    assert retyped["sport"] == "running"
    assert retyped["name"] == ACTIVITY_NAME
    assert json.loads(retyped["quality_flags"]) == [*json.loads(uploaded["quality_flags"]), FLAG]
    compared = set(uploaded) - OVERRIDDEN - PER_WRITE
    assert compared, uploaded
    differing = {column for column in compared if uploaded[column] != retyped[column]}
    assert differing == set(), {column: (uploaded[column], retyped[column]) for column in differing}

    def records(data_dir: Path, session_id: str) -> list[dict]:
        rows = _rows(data_dir, "SELECT * FROM records WHERE session_id = ? ORDER BY t", (session_id,))
        return [{k: v for k, v in row.items() if k != "session_id"} for row in rows]

    uploaded_records = records(isolated_data_dir, upload_id)
    assert uploaded_records
    assert records(retyped_dir, result.session_id) == uploaded_records


def test_classification_and_gates_run_on_the_watch_sport(
    isolated_data_dir: Path, tmp_path: Path, post_fit_bytes
) -> None:
    """The cycling copy takes classify's undeclared-capture path; the override leaves its output as an upload's."""
    _upload(post_fit_bytes, _cycling())
    retyped_dir = tmp_path / "retyped"
    pipeline.ingest_fit_bytes(_cycling(), override=_override(), data_dir=retyped_dir)

    uploaded = _session_row(isolated_data_dir)
    retyped = _session_row(retyped_dir)
    note = _provenance(uploaded).get("hrv_undeclared_capture_candidate")
    assert note is not None, _provenance(uploaded)
    assert _provenance(retyped).get("hrv_undeclared_capture_candidate") == note
    for column in (
        "activity_tag",
        "hrv_source_tier",
        "resting_rmssd_ms",
        "rr_source",
        "recording_interval",
        "hr_source",
    ):
        assert retyped[column] == uploaded[column], column
    # The quality gates' flags are the upload's, the retype flag appended after them.
    assert json.loads(retyped["quality_flags"])[:-1] == json.loads(uploaded["quality_flags"])


def test_provenance_keeps_the_watch_sport_and_profile(isolated_data_dir: Path) -> None:
    pipeline.ingest_fit_bytes(_cycling(), override=_override())

    row = _session_row(isolated_data_dir)
    provenance = _provenance(row)
    assert provenance["raw_sport_value"] == "cycling"
    assert provenance["sport_profile_name"] == "Run"  # the copy keeps the source file's profile name
    assert row["sport"] == "running"


def test_no_override_leaves_ingest_byte_identical(isolated_data_dir: Path, tmp_path: Path) -> None:
    """``override=None`` stores what a call without the keyword stores, table for table."""
    explicit_dir = tmp_path / "explicit"
    default = pipeline.ingest_fit_bytes(_cycling())
    explicit = pipeline.ingest_fit_bytes(_cycling(), override=None, data_dir=explicit_dir)

    assert default == explicit
    assert default.retyped is False
    assert _session_row(isolated_data_dir) == _session_row(explicit_dir)
    for table in ("records", "rr_intervals", "quarantine_sidecar", "session_loads"):
        assert _rows(isolated_data_dir, f"SELECT * FROM {table}") == _rows(
            explicit_dir, f"SELECT * FROM {table}"
        ), table
    row = _session_row(isolated_data_dir)
    assert row["sport"] == "other"
    assert FLAG not in json.loads(row["quality_flags"])


def test_keep_reads_the_overridden_sport(isolated_data_dir: Path) -> None:
    seen: list[tuple[str, str | None, list[str]]] = []

    def keep(session: Session) -> bool:
        seen.append((session.sport, session.name, list(session.quality_flags)))
        return False

    result = pipeline.ingest_fit_bytes(_cycling(), override=_override(), keep=keep)

    assert seen == [("running", ACTIVITY_NAME, [*seen[0][2][:-1], FLAG])]
    assert result.skipped is True
    assert result.retyped is True
    # keep runs before any connection is opened: a refused file leaves no store.
    assert not (isolated_data_dir / db.DB_FILENAME).exists()
