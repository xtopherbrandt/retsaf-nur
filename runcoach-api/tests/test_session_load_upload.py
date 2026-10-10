"""The storage half of F017: every upload saves its load body inside ``persist``'s transaction.

F017 AC1 through real uploads, AC8's save-at-upload, the forced-fault rollback
and the delete, AC2's base rows (``hr_time_fraction`` without a clamp), and
one integration row each for ``no_hr`` and ``sport_not_running``.

**The oracle** (``tests/support/trimp_oracle.py``) decodes the same bytes with
``fitdecode`` and applies the F017 reference's time basis and formulas; it
imports nothing from ``runcoach_api``. The saved ``session_loads.body`` is
read back from the isolated store, never from a route (no route serves it in
this slice), and the three numbers are printed beside the oracle's so the
probe's ``-rA`` output shows the figures. Only fixtures with run proof ``yes``
in ``fixtures/README.md`` are cited as runs
(``.claude/rules/learnings/cite-only-real-activity-fixtures-as-proof.md``);
all six carry their own resting, max, threshold and sex, so every HR input
reads ``session_file``.

**The chaos row** patches ``db._save_session_load`` (the seam ``persist``
calls after the anchor sync) to raise: the upload is a 500 and the store
holds no session, record, RR beat, sidecar row or load, because the save
runs inside the same ``with conn:`` as the four inserts.

**The seam rows** patch real bytes through ``tests/support/fit_patch.py``:
every ``heart_rate`` blanked (gate 3, ``no_hr``) and a ``session.sport`` of
cycling (gate 1, stored ``other``, ``sport_not_running``).
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.ingestion import mapping, pipeline
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"
TOLERANCE = 0.01


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


trimp_oracle = _load_support("trimp_oracle")
fit_patch = _load_support("fit_patch")

# Every fixture with run proof ``yes`` in fixtures/README.md.
RUN_FIXTURES = (
    "sample_run.fit",
    "dev_fields_run.fit",
    "wrist_ppg_run.fit",
    "strap_run_hrv.fit",
    "hilly_run_8k_fr945.fit",
    "hilly_long_run_17k_fr945.fit",
)
HR_FIELDS = ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm", "sex")

SESSION_MESSAGE = 18
SESSION_SPORT_FIELD = 5
SPORT_CYCLING = 2
RECORD_HEART_RATE_FIELD = 3

TABLES = ("sessions", "records", "rr_intervals", "quarantine_sidecar", "session_loads")


def _upload(client: TestClient, name: str, raw: bytes | None = None) -> str:
    response = client.post("/sessions", files={"file": (name, raw or (FIXTURES / name).read_bytes())})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _saved(session_id: str) -> tuple[float | None, str | None, dict]:
    """The ``session_loads`` row as stored: ``(load_value, load_reason, body)``, body JSON-decoded."""
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT load_value, load_reason, body FROM session_loads WHERE session_id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"no saved load for {session_id}"
    return row["load_value"], row["load_reason"], json.loads(row["body"])


def _counts() -> dict[str, int]:
    conn = db.get_connection()
    try:
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}
    finally:
        conn.close()


def _stored_timer_time(session_id: str) -> float | None:
    conn = db.get_connection()
    try:
        return conn.execute("SELECT timer_time_s FROM sessions WHERE session_id = ?", (session_id,)).fetchone()[0]
    finally:
        conn.close()


# --- AC1 and AC8: the saved body on every real run ------------------------------------------------


@pytest.mark.parametrize("name", RUN_FIXTURES)
def test_sample_run_saves_its_hr_trimp_at_upload(name: str) -> None:
    oracle = trimp_oracle.trimp_for_file(FIXTURES / name)
    with TestClient(app) as client:
        session_id = _upload(client, name)
        features = client.get(f"/sessions/{session_id}/features")
        assert features.status_code == 200, features.text
        served_avg_hr = features.json()["features"]["avg_hr_bpm"]["value"]

    load_value, load_reason, body = _saved(session_id)
    saved = (
        body["metrics"]["hr_trimp"]["value"],
        body["metrics"]["hr_trimp"]["threshold_hour_reference"],
        body["session_load"]["value"],
    )
    print(
        f"{name}: trimp oracle {oracle.trimp:.4f} saved {saved[0]:.4f} | "
        f"reference oracle {oracle.threshold_hour_reference:.4f} saved {saved[1]:.4f} | "
        f"load oracle {oracle.session_load:.4f} saved {saved[2]:.4f} | "
        f"hr_time_s oracle {oracle.hr_time_s} saved {body['inputs']['hr_time_s']} | "
        f"avg_hr oracle {oracle.avg_hr_bpm:.4f} saved {body['inputs']['avg_hr_bpm']:.4f} "
        f"features {served_avg_hr:.4f} | timer_time_s {body['inputs']['timer_time_s']}"
    )
    for got, want in zip(saved, (oracle.trimp, oracle.threshold_hour_reference, oracle.session_load)):
        assert got == pytest.approx(want, abs=TOLERANCE)
    assert body["session_id"] == session_id
    assert body["sport"] == "running"
    assert body["inputs"]["hr_time_s"] == pytest.approx(oracle.hr_time_s, abs=TOLERANCE)
    assert body["inputs"]["avg_hr_bpm"] == served_avg_hr
    assert body["inputs"]["timer_time_s"] == pytest.approx(oracle.timer_time_s)
    assert _stored_timer_time(session_id) == pytest.approx(oracle.timer_time_s)
    for field_name in HR_FIELDS:
        assert body["inputs"][field_name]["source"] == "session_file", field_name
        assert body["inputs"][field_name]["session_id"] == session_id, field_name
        assert body["inputs"][field_name]["anchor_version"] is None, field_name
    assert body["metrics"]["hr_trimp"]["coefficients"] == "male"
    assert body["session_load"]["driver"] == "hr_trimp"
    assert body["session_load"]["unavailable"] is None
    assert body["metrics"]["rtss"] == {"value": None, "unavailable": "no_threshold_pace"}
    assert body["metrics"]["srpe"] == {"value": None, "unavailable": "no_rpe"}
    # The two columns mirror session_load.value and session_load.unavailable.
    assert load_value == body["session_load"]["value"]
    assert load_reason is None


# --- AC2: hr_time_fraction on the full runs, no clamp ---------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected", "tolerance"),
    [
        ("wrist_ppg_run.fit", 1.0, 0.01),
        ("sample_run.fit", 1.0008, 0.001),
        ("strap_run_hrv.fit", 0.9972, 0.001),
    ],
)
def test_hr_time_fraction_is_saved_without_a_clamp(name: str, expected: float, tolerance: float) -> None:
    with TestClient(app) as client:
        session_id = _upload(client, name)
    _, _, body = _saved(session_id)
    fraction = body["inputs"]["hr_time_fraction"]
    print(f"{name}: hr_time_fraction saved {fraction:.4f} expected {expected} (+/- {tolerance})")
    assert fraction == pytest.approx(expected, abs=tolerance)
    assert fraction == pytest.approx(body["inputs"]["hr_time_s"] / body["inputs"]["timer_time_s"])


# --- gates 1 and 3 through real uploads ---------------------------------------------------------


def test_a_run_whose_records_carry_no_usable_hr_saves_no_hr() -> None:
    raw = fit_patch.blank_record_field(
        (FIXTURES / "sample_run.fit").read_bytes(), RECORD_HEART_RATE_FIELD, 0.0, 10**6
    )
    with TestClient(app) as client:
        session_id = _upload(client, "sample_run.fit", raw)
    load_value, load_reason, body = _saved(session_id)
    assert body["sport"] == "running"
    assert body["inputs"]["hr_time_s"] == 0.0
    assert body["inputs"]["avg_hr_bpm"] is None
    assert body["metrics"]["hr_trimp"] == {
        "value": None,
        "unavailable": "no_hr",
        "unavailable_fields": [],
        "threshold_hour_reference": None,
        "coefficients": None,
    }
    assert body["session_load"] == {"value": None, "unavailable": "no_hr", "driver": None}
    assert body["metrics"]["rtss"] == {"value": None, "unavailable": "no_threshold_pace"}
    assert body["metrics"]["srpe"] == {"value": None, "unavailable": "no_rpe"}
    assert (load_value, load_reason) == (None, "no_hr")
    # The inputs are still reported: the file's own values and its timer time.
    assert body["inputs"]["resting_hr_bpm"]["value"] == 47
    assert body["inputs"]["timer_time_s"] == pytest.approx(2816.667)


def test_a_cycling_upload_saves_sport_not_running() -> None:
    raw = fit_patch.set_field((FIXTURES / "sample_run.fit").read_bytes(), SESSION_MESSAGE, SESSION_SPORT_FIELD, SPORT_CYCLING)
    with TestClient(app) as client:
        session_id = _upload(client, "sample_run.fit", raw)
        assert client.get(f"/sessions/{session_id}").json()["sport"] == "other"
    load_value, load_reason, body = _saved(session_id)
    assert body["sport"] == "other"
    for metric in ("hr_trimp", "rtss", "srpe"):
        assert body["metrics"][metric]["value"] is None
        assert body["metrics"][metric]["unavailable"] == "sport_not_running"
    assert body["session_load"] == {"value": None, "unavailable": "sport_not_running", "driver": None}
    assert (load_value, load_reason) == (None, "sport_not_running")
    assert body["inputs"]["max_hr_bpm"]["value"] == 188


# --- AC8: the forced fault rolls the whole upload back ------------------------------------------


def test_a_load_fault_rolls_back_the_whole_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    fired: list[str] = []

    def boom(conn, session_id, *, resolved=None):
        fired.append(session_id)
        raise RuntimeError("simulated fault computing the load")

    # Scoped with monkeypatch.context(): undoing the shared fixture would also undo the
    # autouse isolated data dir.
    with monkeypatch.context() as m:
        m.setattr(db, "_save_session_load", boom)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/sessions", files={"file": ("sample_run.fit", (FIXTURES / "sample_run.fit").read_bytes())}
            )
    assert response.status_code == 500, response.text
    assert len(fired) == 1, fired
    counts = _counts()
    print(f"after the fault: {response.status_code}, rows {counts}")
    assert counts == {table: 0 for table in TABLES}

    # Control: the same upload, unpatched, stores the session and its load.
    with TestClient(app) as client:
        session_id = _upload(client, "sample_run.fit")
    assert session_id == fired[0]
    assert _counts()["session_loads"] == 1
    assert _saved(session_id)[0] == pytest.approx(47.36, abs=TOLERANCE)


# --- AC8: the delete removes the saved load -----------------------------------------------------


def test_delete_removes_the_saved_load() -> None:
    with TestClient(app) as client:
        session_id = _upload(client, "sample_run.fit")
        assert _counts()["session_loads"] == 1
        assert client.delete(f"/sessions/{session_id}").status_code == 204
    assert _counts() == {table: 0 for table in TABLES}
    assert "session_loads" in db._child_tables()


# --- the seams and the schema ---------------------------------------------------------------------


def test_a_second_save_for_one_session_fails_loudly() -> None:
    with TestClient(app) as client:
        session_id = _upload(client, "sample_run.fit")
    conn = db.get_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db._write_session_load(conn, session_id, {"session_load": {"value": 1.0, "unavailable": None}})
    finally:
        conn.close()
    assert _saved(session_id)[0] == pytest.approx(47.36, abs=TOLERANCE)


def test_the_schema_carries_the_column_and_the_table() -> None:
    schema = db._expected_schema()
    assert schema["sessions"]["timer_time_s"] == "REAL"
    assert schema["session_loads"] == {
        "session_id": "TEXT",
        "load_value": "REAL",
        "load_reason": "TEXT",
        "body": "TEXT",
    }


# --- the timer time reads like the summary's duration: unparseable means null, never a 500 -----


UNPARSEABLE_TIMER_TIMES = ((150.0, 1.0), "150", True, float("nan"), float("inf"))


@pytest.mark.parametrize("timer_time", UNPARSEABLE_TIMER_TIMES, ids=("tuple", "string", "bool", "nan", "inf"))
def test_an_unparseable_timer_time_maps_to_null(timer_time, synthetic) -> None:
    """``fitdecode`` types a field by the file's own declared base type, so a crafted
    or corrupt definition can hand ``total_timer_time`` over as a tuple or a string
    (``test_resting_hrv_tier1.py`` enumerates the forms). ``_build_summary`` already
    reads that value through ``_getter`` and the resting-HRV veto path then records
    "present but unparseable"; ``timer_time_s`` must read the same field the same way
    rather than raise from ``float()`` and turn the veto into a 500."""
    session, _records = mapping.to_canonical(synthetic(total_timer_time=timer_time, total_distance=0.0))
    assert session.timer_time_s is None


def test_an_upload_with_an_unparseable_timer_time_is_not_a_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real decoded ``sample_run.fit`` frames, with the session's ``total_timer_time``
    value replaced by the tuple ``fitdecode`` hands over for a multi-element field, are
    served to the real pipeline in place of ``fit_parser.decode``'s answer: the whole
    upload stores, with a null timer time and no clamp-free fraction to compute."""
    raw = (FIXTURES / "sample_run.fit").read_bytes()
    messages = pipeline.fit_parser.decode(raw)
    session_msg = next(msg for msg in messages if msg.name == "session")
    session_msg.get_field("total_timer_time").value = (150.0, 1.0)
    with monkeypatch.context() as m:
        m.setattr(pipeline.fit_parser, "decode", lambda _raw: messages)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/sessions", files={"file": ("sample_run.fit", raw)})
    assert response.status_code == 201, response.text
    session_id = response.json()["session_id"]
    assert _stored_timer_time(session_id) is None
    load_value, load_reason, body = _saved(session_id)
    print(f"unparseable timer time: stored {(load_value, load_reason)}, inputs {body['inputs']}")
    assert body["inputs"]["timer_time_s"] is None
    assert body["inputs"]["hr_time_fraction"] is None
