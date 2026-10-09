"""T072: ``DELETE /sessions/{id}`` -- the way out of the upgrade window.

The 2026-09-06 declaration amendment (F004) opens a window in which a
capture recorded before the athlete adds their profile name to
``api.toml`` ingests as an ordinary session with no reading. F004's
Decision Log accepts "the athlete loses F004-window history until they
re-ingest" -- but re-ingest was not *possible*: ``db.py``'s
``UNIQUE (source_device, start_time)`` turns a re-upload into a 409,
and nothing removed the row. This module pins the recovery path:
delete the session, fix the config, upload the file again.

Scoped exclusively to the delete route and ``db.delete_session``. The
409 itself is **unchanged** and stays pinned by
``test_duplicate_upload.py`` -- deleting first is what makes the
re-upload succeed, not a relaxed constraint.

Real fixtures, decoded for real, per ``.claude/rules/project-testing.md``.
``strap_hrv_capture.fit`` is used for the child-row assertions because it
carries 149 ``hrv`` messages / 165 beats, so ``rr_intervals`` rows
actually exist to be deleted; ``rr_reconstruction`` runs unconditionally
in ``pipeline.ingest_fit_bytes``, so those rows are written regardless of
whether the capture routes as a resting-HRV reading under the test
config's empty ``resting_hrv_profile_names``.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runcoach_api import db
from runcoach_api.main import app
from runcoach_api.models import Record, RRInterval, Session

FIXTURES = Path(__file__).parent / "fixtures"

# Carries beats, so deleting it has child rr_intervals rows to remove.
BEATS_FIXTURE = "strap_hrv_capture.fit"
# The plain training-session fixture the duplicate-upload suite uses.
RUN_FIXTURE = "sample_run.fit"


def _upload(client: TestClient, filename: str):
    return client.post(
        "/sessions", files={"file": (filename, (FIXTURES / filename).read_bytes())}
    )


def _child_row_counts(session_id: str) -> dict[str, int]:
    """Row counts in every non-``sessions`` table carrying a ``session_id``.

    Read from the live schema rather than a hand-written table list, for
    the same reason ``db._expected_schema`` is derived from the DDL: a
    hand-listed set only contains the tables whoever wrote it remembered,
    and this assertion's whole job is to catch an orphan in the table
    nobody thought of.
    """
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        tables = [
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        counts = {}
        for table in tables:
            if table == "sessions":
                continue
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "session_id" not in columns:
                continue
            cur = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (session_id,)
            )
            counts[table] = cur.fetchone()[0]
        return counts
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The headline behaviour: upload -> delete -> re-upload succeeds
# ---------------------------------------------------------------------------


def test_delete_then_reupload_same_file_returns_201_not_409() -> None:
    """The first failing test named by the task: today the second upload
    is a 409 because nothing can remove the row that owns the
    ``(source_device, start_time)`` slot."""
    with TestClient(app) as client:
        first = _upload(client, BEATS_FIXTURE)
        assert first.status_code == 201, first.text
        session_id = first.json()["session_id"]

        deleted = client.delete(f"/sessions/{session_id}")
        assert deleted.status_code == 204, deleted.text

        second = _upload(client, BEATS_FIXTURE)

    assert second.status_code == 201, second.text
    # Same file, same deterministic session id -- the slot was genuinely
    # freed rather than a second row squeezing in beside the first.
    assert second.json()["session_id"] == session_id


def test_reupload_without_delete_still_returns_409() -> None:
    """The negative control for the test above. Without it, that test
    would still pass if the delete route did nothing and the 409 had
    simply been relaxed -- which is the one change this task must not
    make."""
    with TestClient(app) as client:
        first = _upload(client, BEATS_FIXTURE)
        assert first.status_code == 201, first.text
        second = _upload(client, BEATS_FIXTURE)

    assert second.status_code == 409


def test_delete_returns_204_with_no_body() -> None:
    with TestClient(app) as client:
        created = _upload(client, RUN_FIXTURE)
        assert created.status_code == 201, created.text
        response = client.delete(f"/sessions/{created.json()['session_id']}")

    assert response.status_code == 204
    assert response.content == b""


def test_deleted_session_is_gone_from_get() -> None:
    with TestClient(app) as client:
        created = _upload(client, RUN_FIXTURE)
        assert created.status_code == 201, created.text
        session_id = created.json()["session_id"]

        assert client.delete(f"/sessions/{session_id}").status_code == 204
        after = client.get(f"/sessions/{session_id}")

    assert after.status_code == 404
    assert session_id in after.text


# ---------------------------------------------------------------------------
# Child rows: nothing is orphaned
# ---------------------------------------------------------------------------


def test_delete_removes_every_child_row_including_rr_intervals() -> None:
    with TestClient(app) as client:
        created = _upload(client, BEATS_FIXTURE)
        assert created.status_code == 201, created.text
        session_id = created.json()["session_id"]

        before = _child_row_counts(session_id)
        # The fixture must actually exercise the expensive child table --
        # a delete test against a session with zero beats cannot fail.
        assert before["rr_intervals"] > 0, before
        assert before["records"] > 0, before

        assert client.delete(f"/sessions/{session_id}").status_code == 204

        after = _child_row_counts(session_id)

    assert after == dict.fromkeys(before, 0), after


def test_delete_removes_quarantine_sidecar_rows() -> None:
    """``quarantine_sidecar`` is the child table least likely to be
    remembered -- §2.3.6 keeps it deliberately outside the canonical
    schema, and ``get_session_detail`` does not read it. Driven at the
    ``db`` layer so a sidecar value is guaranteed present rather than
    depending on which vendor fields a given fixture happens to carry.
    """
    session = Session(
        session_id="s-delete-sidecar",
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-01T00:00:00+00:00",
        source_device="delete-device",
    )
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.persist(
            conn,
            session,
            [Record(t=0.0, heart_rate=120)],
            [RRInterval(seq=0, rr_ms=800.0, rr_source="chest_strap_ecg")],
            {"vo2max_estimate": 55, "avg_stress": 19},
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM quarantine_sidecar WHERE session_id = ?",
                (session.session_id,),
            ).fetchone()[0]
            == 2
        )

        assert db.delete_session(conn, session.session_id) is True

        for table in ("records", "rr_intervals", "quarantine_sidecar"):
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (session.session_id,)
            ).fetchone()[0]
            assert count == 0, table
        assert db.get_session_detail(conn, session.session_id) is None
    finally:
        conn.close()


def test_child_table_enumeration_is_derived_from_the_schema_ddl() -> None:
    """Pins the strategy the task mandates: the delete walks every table
    ``_SCHEMA_DDL`` declares with a ``session_id`` column, so a table
    added later is covered without editing the delete."""
    expected = {
        table
        for table, columns in db._expected_schema().items()
        if table != "sessions" and "session_id" in columns
    }
    assert expected == {"records", "rr_intervals", "quarantine_sidecar", "session_loads"}
    assert set(db._child_tables()) == expected


def test_delete_leaves_other_sessions_and_their_children_intact() -> None:
    keep = Session(
        session_id="s-delete-keep",
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-02T00:00:00+00:00",
        source_device="delete-device",
    )
    drop = Session(
        session_id="s-delete-drop",
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-03T00:00:00+00:00",
        source_device="delete-device",
    )
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        for session in (keep, drop):
            db.persist(
                conn,
                session,
                [Record(t=0.0, heart_rate=120)],
                [RRInterval(seq=0, rr_ms=800.0, rr_source="chest_strap_ecg")],
                {"vo2max_estimate": 55},
            )

        assert db.delete_session(conn, drop.session_id) is True

        assert db.get_session_detail(conn, keep.session_id) is not None
        for table in ("records", "rr_intervals", "quarantine_sidecar"):
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (keep.session_id,)
            ).fetchone()[0]
            assert count == 1, table
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Why an explicit ordered delete rather than ON DELETE CASCADE
# ---------------------------------------------------------------------------


def test_connection_enforces_foreign_keys_and_ddl_declares_no_cascade() -> None:
    """The task asks for the cascade question to be settled by reading
    the code, not by assuming SQLite's documented default. Both halves
    are pinned here: ``get_connection`` turns foreign keys **on**, and
    the DDL declares no ``ON DELETE`` action -- so a parent-first delete
    would be *refused*, and an explicit child-first delete is the only
    correct order."""
    assert "ON DELETE" not in db._SCHEMA_DDL.upper()

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

        db.persist(
            conn,
            Session(
                session_id="s-delete-fk",
                sport="running",
                source_vendor="garmin",
                start_time="2026-01-04T00:00:00+00:00",
                source_device="delete-device",
            ),
            [Record(t=0.0, heart_rate=120)],
            [],
            {},
        )
        # Parent-first is a constraint violation, which is exactly why
        # relying on a cascade here would be wrong.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM sessions WHERE session_id = 's-delete-fk'")
    finally:
        conn.rollback()
        conn.close()


def test_failed_delete_mid_transaction_leaves_the_session_fully_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial delete is worse than the 409 this task relieves: it
    leaves orphan beats the next ingest of the same file cannot
    displace. ``_delete_child_rows`` is a seam for exactly this reason
    (the same seam ``persist``'s four insert helpers provide) --
    ``sqlite3.Connection`` is an immutable C type and cannot be
    monkeypatched itself."""
    session = Session(
        session_id="s-delete-chaos",
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-05T00:00:00+00:00",
        source_device="delete-device",
    )
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.persist(
            conn,
            session,
            [Record(t=0.0, heart_rate=120), Record(t=1.0, heart_rate=121)],
            [RRInterval(seq=0, rr_ms=800.0, rr_source="chest_strap_ecg")],
            {"vo2max_estimate": 55},
        )

        def _explode(conn_, session_id):
            # Delete the beats for real, *then* fail -- so a missing
            # rollback shows up as a stripped session rather than as a
            # no-op.
            conn_.execute("DELETE FROM rr_intervals WHERE session_id = ?", (session_id,))
            raise RuntimeError("simulated mid-delete failure")

        monkeypatch.setattr(db, "_delete_child_rows", _explode)

        with pytest.raises(RuntimeError):
            db.delete_session(conn, session.session_id)

        monkeypatch.undo()

        detail = db.get_session_detail(conn, session.session_id)
        assert detail is not None
        assert len(detail["records"]) == 2
        assert len(detail["rr_intervals"]) == 1
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM quarantine_sidecar WHERE session_id = ?",
                (session.session_id,),
            ).fetchone()[0]
            == 1
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Adversarial input probes (.claude/rules/learnings/
# adversarial-input-probes-are-a-task-deliverable.md)
# ---------------------------------------------------------------------------


def test_delete_unknown_id_returns_404() -> None:
    with TestClient(app) as client:
        response = client.delete("/sessions/no-such-session")

    assert response.status_code == 404
    assert "no-such-session" in response.text


def test_delete_same_id_twice_second_is_404() -> None:
    with TestClient(app) as client:
        created = _upload(client, RUN_FIXTURE)
        assert created.status_code == 201, created.text
        session_id = created.json()["session_id"]

        assert client.delete(f"/sessions/{session_id}").status_code == 204
        second = client.delete(f"/sessions/{session_id}")

    assert second.status_code == 404


@pytest.mark.parametrize(
    "session_id",
    [
        pytest.param("' OR 1=1 --", id="sql_boolean_injection"),
        pytest.param("x'; DROP TABLE sessions; --", id="sql_drop_table"),
        pytest.param("..%2F..%2Fetc%2Fpasswd", id="path_traversal_encoded"),
        pytest.param("%2E%2E%2F%2E%2E%2Fruncoach.db", id="path_traversal_db_file"),
        pytest.param("%20", id="whitespace_only"),
        pytest.param("0", id="numeric_looking"),
        pytest.param("%C3%A9%F0%9F%98%80", id="non_ascii"),
        pytest.param("s" * 4000, id="very_long"),
        pytest.param("null", id="literal_null_word"),
    ],
)
def test_degenerate_ids_are_ordinary_404s_and_destroy_nothing(session_id: str) -> None:
    """Every id shape is a plain string to a parameterised query. The
    surviving session is the assertion that matters: a probe that only
    checked the status code would not notice a dropped table."""
    with TestClient(app) as client:
        created = _upload(client, RUN_FIXTURE)
        assert created.status_code == 201, created.text
        real_id = created.json()["session_id"]

        response = client.delete(f"/sessions/{session_id}")

        assert response.status_code == 404, response.text
        # The real session, its table and its child rows all survive.
        assert client.get(f"/sessions/{real_id}").status_code == 200

    counts = _child_row_counts(real_id)
    assert counts["records"] > 0, counts


def test_delete_with_an_empty_id_never_reaches_the_route() -> None:
    """``DELETE /sessions/`` carries no id at all. Starlette's
    ``redirect_slashes`` sends it to ``/sessions``, which has a POST
    handler and no DELETE one, so it is a 405 -- never a 204, and never
    a delete-everything. The assertion that matters is the last one."""
    with TestClient(app) as client:
        created = _upload(client, RUN_FIXTURE)
        assert created.status_code == 201, created.text
        real_id = created.json()["session_id"]

        response = client.delete("/sessions/")

        assert response.status_code == 405, response.text
        assert client.get(f"/sessions/{real_id}").status_code == 200


def test_concurrent_deletes_of_the_same_session_yield_exactly_one_204() -> None:
    """Two athletes' tabs, one session. The delete must not report
    success twice -- a second 204 would tell the caller it removed
    something it did not."""
    with TestClient(app) as client:
        created = _upload(client, BEATS_FIXTURE)
        assert created.status_code == 201, created.text
        session_id = created.json()["session_id"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = [
                future.result().status_code
                for future in [
                    pool.submit(client.delete, f"/sessions/{session_id}") for _ in range(4)
                ]
            ]

    assert sorted(statuses) == [204, 404, 404, 404], statuses
    assert _child_row_counts(session_id) == {
        "records": 0,
        "rr_intervals": 0,
        "quarantine_sidecar": 0,
        "session_loads": 0,
    }


def test_back_to_back_db_deletes_on_separate_connections_report_once() -> None:
    """The ``db``-layer twin of the test above, isolating the row-count
    contract from the HTTP layer -- mirroring
    ``test_duplicate_upload.py``'s back-to-back persist chaos test."""
    session = Session(
        session_id="s-delete-race",
        sport="running",
        source_vendor="garmin",
        start_time="2026-01-06T00:00:00+00:00",
        source_device="delete-device",
    )
    writer = db.get_connection()
    second = db.get_connection()
    try:
        db.init_schema(writer)
        db.persist(writer, session, [Record(t=0.0, heart_rate=120)], [], {})

        assert db.delete_session(writer, session.session_id) is True
        assert db.delete_session(second, session.session_id) is False
    finally:
        writer.close()
        second.close()


def test_delete_on_an_id_that_was_never_ingested_leaves_the_store_untouched() -> None:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        assert db.delete_session(conn, "") is False
        assert db.delete_session(conn, "s-never-existed") is False
    finally:
        conn.close()
