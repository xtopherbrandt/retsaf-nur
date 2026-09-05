"""T047: several resting-HRV readings on one day are all retained.

F004 ``@should``: "Several readings on one day are all retained" -- two
Health Snapshot exports recorded at different times on the same morning are
stored as **two** separate readings with their own timestamps, and neither is
discarded, because choosing the day's representative value is E003's job.

**This is a regression guard, not a build.** The behaviour is already
guaranteed by existing infrastructure: ``mapping.derive_session_id()`` hashes
``(source_device, start_time)`` and ``sessions`` carries
``UNIQUE (source_device, start_time)``, so two captures at different times get
different ids and both persist. What this file adds is the proof that F004 did
not break it -- the tempting shortcut while building a resting-HRV feature is
to collapse or overwrite same-day readings at ingestion, which would silently
destroy the data E003's HRV trend, SWC band and baseline-reset rule are built
on. Every assertion below exists to make that shortcut fail a test.

**Same device, different times -- deliberately.** Varying ``source_device`` as
well would let the whole file pass for the wrong reason: it would prove that
two *different* devices don't collide, not that two same-device same-day
readings are both kept. ``_capture()`` therefore holds the device fixed and
varies only ``start_time``.

**And the inverse must stay true.** A genuinely re-uploaded identical file is
still the existing duplicate-upload behaviour (409, one row) -- see
``test_duplicate_upload.py``. "Retain every reading" must not degrade into
"never deduplicate anything", so that boundary is asserted here too.

Synthetic ``_FakeMsg`` sets are used for the same-day pair (the corpus has no
two snapshots recorded on one calendar date). This is structural persistence
behaviour, not fitdecode parsing, so ``.claude/rules/project-testing.md``'s
real-fixture requirement does not bite; the two real snapshot fixtures are
still driven end-to-end through ``POST /sessions`` below as the same-device
multi-reading proof over genuinely decoded input.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runcoach_api import db
from runcoach_api.ingestion import hrv_classification, mapping
from runcoach_api.ingestion.exceptions import DuplicateSessionError
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"

# Both real Health Snapshots come from the *same* device (fr945_lte fw17.4)
# and both route Tier 2 since T039 landed -- 37 ms and 51 ms respectively.
SNAPSHOT_FIXTURES = {
    "sample_health_snapshot.fit": 37,
    "strap_health_snapshot.fit": 51,
}

# One morning. 06:12 and 06:41 -- a strap capture on waking and a Health
# Snapshot half an hour later, the scenario F004 describes.
MORNING = datetime(2026, 3, 14, 6, 12, tzinfo=timezone.utc)
LATER_THAT_MORNING = MORNING + timedelta(minutes=29)
LATER_STILL = MORNING + timedelta(hours=6)

# The task's first failing test names these two values explicitly.
FIRST_RMSSD = 37
SECOND_RMSSD = 41

# Held constant across every synthetic capture in this file -- see the module
# docstring on why varying it would make the whole file vacuous.
DEVICE_PRODUCT = "fr945_lte"
DEVICE_FIRMWARE = 17.4
EXPECTED_DEVICE = f"{DEVICE_PRODUCT} fw{DEVICE_FIRMWARE}"


class _FakeMsg:
    """Minimal stand-in for a ``fitdecode.FitDataMessage``.

    The same shape ``test_mapping_sport_handling.py`` and
    ``test_resting_hrv_tier2.py`` use: only ``.name``,
    ``get_value(name, fallback=None)`` and ``.fields`` are read by
    ``mapping.to_canonical`` and ``hrv_classification.classify``.
    """

    def __init__(self, name: str, values: dict) -> None:
        self.name = name
        self._values = values
        self.fields: list = []

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)


def _messages(start: datetime, rmssd_hrv: int) -> list[_FakeMsg]:
    """A sport-60 Health Snapshot message set: one file_id, one device_info,
    one session roll-up carrying ``rmssd_hrv``, and one record.

    ``file_id`` / ``device_info`` are present so ``_build_source_device``
    resolves a real device string rather than its "unknown" sentinel -- the
    retention guarantee has to hold for identifiable devices, which is the
    case that actually occurs.
    """
    return [
        _FakeMsg("file_id", {"garmin_product": DEVICE_PRODUCT}),
        _FakeMsg(
            "device_info",
            {"garmin_product": DEVICE_PRODUCT, "software_version": DEVICE_FIRMWARE},
        ),
        _FakeMsg(
            "session",
            {
                "sport": 60,
                "start_time": start,
                "rmssd_hrv": rmssd_hrv,
                "total_timer_time": 120.0,
            },
        ),
        _FakeMsg("record", {"timestamp": start, "heart_rate": 58}),
    ]


def _capture(start: datetime, rmssd_hrv: int):
    """One classified resting-HRV capture, ready to persist.

    Drives the real ``mapping.to_canonical`` -> ``hrv_classification.classify``
    path so ``session_id``, ``source_device`` and ``rmssd_precomputed`` are all
    produced by production code rather than hand-written.
    """
    session, records = mapping.to_canonical(_messages(start, rmssd_hrv))
    hrv_classification.classify(_messages(start, rmssd_hrv), session, [])
    return session, records


def _persist(conn: sqlite3.Connection, session, records) -> None:
    db.persist(conn, session, records, [], {})


@pytest.fixture
def conn():
    connection = db.get_connection()
    db.init_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


def _session_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT session_id, start_time, source_device, rmssd_precomputed, "
            "hrv_source_tier FROM sessions ORDER BY start_time"
        )
    )


def _ingest(client: TestClient, filename: str):
    raw = (FIXTURES / filename).read_bytes()
    return client.post("/sessions", files={"file": (filename, raw)})


# ---------------------------------------------------------------------------
# The premise: the pair really is same-device, same-day, different-time
# ---------------------------------------------------------------------------


def test_the_two_captures_share_a_device_and_a_calendar_date() -> None:
    """Guards the whole file against passing for the wrong reason. If these
    two captures ever differed by device or by date, every retention
    assertion below would be proving something far weaker than F004 asks."""
    first, _ = _capture(MORNING, FIRST_RMSSD)
    second, _ = _capture(LATER_THAT_MORNING, SECOND_RMSSD)

    assert first.source_device == second.source_device == EXPECTED_DEVICE
    assert first.start_time[:10] == second.start_time[:10]
    assert first.start_time != second.start_time


def test_the_two_captures_are_both_tier_2_readings() -> None:
    """Both carry a reading, so "both retained" is a claim about readings and
    not merely about two empty session rows."""
    first, _ = _capture(MORNING, FIRST_RMSSD)
    second, _ = _capture(LATER_THAT_MORNING, SECOND_RMSSD)

    for session, expected in ((first, FIRST_RMSSD), (second, SECOND_RMSSD)):
        assert session.hrv_source_tier == "health_snapshot"
        assert session.rmssd_precomputed == expected
        assert session.activity_tag == "health_snapshot"


def test_same_day_captures_at_different_times_get_distinct_session_ids() -> None:
    """``derive_session_id`` hashes ``(source_device, start_time)``. Same
    device, different time -> different id, so the two never contend for one
    primary key in the first place."""
    first, _ = _capture(MORNING, FIRST_RMSSD)
    second, _ = _capture(LATER_THAT_MORNING, SECOND_RMSSD)

    assert first.session_id != second.session_id


# ---------------------------------------------------------------------------
# The guarantee: both rows land, and neither replaces the other
# ---------------------------------------------------------------------------


def test_two_same_day_readings_persist_as_two_rows(conn) -> None:
    """The task's first failing test: two sport-60 message sets carrying
    ``rmssd_hrv`` 37 and 41 at two different ``start_time``s on one calendar
    date produce two distinct session rows."""
    _persist(conn, *_capture(MORNING, FIRST_RMSSD))
    _persist(conn, *_capture(LATER_THAT_MORNING, SECOND_RMSSD))

    rows = _session_rows(conn)

    assert len(rows) == 2
    assert {row["session_id"] for row in rows} == {
        _capture(MORNING, FIRST_RMSSD)[0].session_id,
        _capture(LATER_THAT_MORNING, SECOND_RMSSD)[0].session_id,
    }


def test_each_same_day_row_keeps_its_own_rmssd_and_timestamp(conn) -> None:
    """"...with their own timestamps". Neither reading is overwritten by the
    other, and the second does not inherit the first's value."""
    _persist(conn, *_capture(MORNING, FIRST_RMSSD))
    _persist(conn, *_capture(LATER_THAT_MORNING, SECOND_RMSSD))

    rows = _session_rows(conn)

    assert [row["start_time"] for row in rows] == [
        MORNING.isoformat(),
        LATER_THAT_MORNING.isoformat(),
    ]
    assert [row["rmssd_precomputed"] for row in rows] == [FIRST_RMSSD, SECOND_RMSSD]


def test_the_second_same_day_capture_is_not_rejected_as_a_duplicate(conn) -> None:
    """Dedup is keyed on ``(source_device, start_time)`` and must stay that
    way. A second reading later the same morning is a new reading, not a
    re-upload -- ``db.persist`` must not raise."""
    _persist(conn, *_capture(MORNING, FIRST_RMSSD))

    _persist(conn, *_capture(LATER_THAT_MORNING, SECOND_RMSSD))  # must not raise

    assert len(_session_rows(conn)) == 2


def test_both_same_day_readings_read_back_independently(conn) -> None:
    """Retrieval, not just storage: ``get_session_detail`` returns each
    reading's own value, so nothing collapses on the way out either."""
    first, _ = _capture(MORNING, FIRST_RMSSD)
    second, _ = _capture(LATER_THAT_MORNING, SECOND_RMSSD)
    _persist(conn, first, [])
    _persist(conn, second, [])

    first_detail = db.get_session_detail(conn, first.session_id)
    second_detail = db.get_session_detail(conn, second.session_id)

    assert first_detail is not None and second_detail is not None
    assert first_detail["rmssd_precomputed"] == FIRST_RMSSD
    assert second_detail["rmssd_precomputed"] == SECOND_RMSSD
    assert first_detail["start_time"] == MORNING.isoformat()
    assert second_detail["start_time"] == LATER_THAT_MORNING.isoformat()


def test_a_third_reading_on_the_same_day_is_also_retained(conn) -> None:
    """"Several", not "two". A latest-wins or keep-the-first rule that
    survived the two-reading case would be caught here."""
    for start, rmssd in (
        (MORNING, FIRST_RMSSD),
        (LATER_THAT_MORNING, SECOND_RMSSD),
        (LATER_STILL, 44),
    ):
        _persist(conn, *_capture(start, rmssd))

    rows = _session_rows(conn)

    assert len(rows) == 3
    assert [row["rmssd_precomputed"] for row in rows] == [FIRST_RMSSD, SECOND_RMSSD, 44]
    assert len({row["start_time"][:10] for row in rows}) == 1  # all one calendar date


def test_the_order_the_readings_arrive_in_does_not_change_the_outcome(conn) -> None:
    """A later-timestamped capture uploaded *first* must not cause the
    earlier one to be treated as stale and dropped."""
    _persist(conn, *_capture(LATER_THAT_MORNING, SECOND_RMSSD))
    _persist(conn, *_capture(MORNING, FIRST_RMSSD))

    rows = _session_rows(conn)

    assert [row["rmssd_precomputed"] for row in rows] == [FIRST_RMSSD, SECOND_RMSSD]


# ---------------------------------------------------------------------------
# The structural guard: dedup is keyed on the instant, never on the day
# ---------------------------------------------------------------------------


def test_the_sessions_unique_key_is_the_instant_not_the_calendar_day(conn) -> None:
    """Reads the constraint off the live schema rather than trusting the DDL
    string. Anyone re-keying dedup to a date (or dropping ``start_time`` from
    the key) collapses same-day readings, and fails here."""
    unique_keys = {
        tuple(
            row["name"]
            for row in conn.execute(f"PRAGMA index_info({index['name']})")
        )
        for index in conn.execute("PRAGMA index_list(sessions)")
        if index["unique"]
    }

    assert ("source_device", "start_time") in unique_keys


def test_no_day_level_column_or_index_has_been_introduced(conn) -> None:
    """"Don't add a 'latest reading per day' query or view" -- that is E003's
    concern and belongs nowhere in ingestion."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}

    assert not {c for c in columns if "date" in c or c.endswith("_day")}

    objects = conn.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('view', 'trigger')"
    ).fetchall()
    assert objects == []


# ---------------------------------------------------------------------------
# The inverse boundary: an identical re-upload is still a duplicate
# ---------------------------------------------------------------------------


def test_re_persisting_the_identical_capture_is_still_a_duplicate(conn) -> None:
    """"Retain every reading" must not degrade into "never deduplicate".
    Same device *and* same instant is the existing duplicate-upload case --
    consistent with ``test_duplicate_upload.py``."""
    first, records = _capture(MORNING, FIRST_RMSSD)
    _persist(conn, first, records)

    with pytest.raises(DuplicateSessionError) as exc_info:
        _persist(conn, *_capture(MORNING, FIRST_RMSSD))

    assert exc_info.value.existing_session_id == first.session_id
    assert len(_session_rows(conn)) == 1


# ---------------------------------------------------------------------------
# End to end over real decoded FIT input
# ---------------------------------------------------------------------------


def test_both_real_snapshot_fixtures_come_from_the_same_device() -> None:
    """The premise of the end-to-end test below: ``sample_health_snapshot.fit``
    and ``strap_health_snapshot.fit`` were recorded on one device
    (fr945_lte fw17.4) at different instants, so retaining both is the
    same-device multi-reading guarantee over genuinely decoded input."""
    devices = set()
    starts = set()
    for filename in SNAPSHOT_FIXTURES:
        with TestClient(app) as client:
            response = _ingest(client, filename)
            assert response.status_code == 201, response.text
            body = client.get(f"/sessions/{response.json()['session_id']}").json()
        devices.add(body["source_device"])
        starts.add(body["start_time"])

    assert len(devices) == 1
    assert len(starts) == 2


def test_two_real_snapshots_from_one_device_both_survive_ingestion() -> None:
    """Both fixtures route Tier 2 (37 ms and 51 ms). Uploaded into one
    database, both must still be there afterwards with their own values --
    the second must not displace the first."""
    with TestClient(app) as client:
        session_ids = {}
        for filename in SNAPSHOT_FIXTURES:
            response = _ingest(client, filename)
            assert response.status_code == 201, response.text
            session_ids[filename] = response.json()["session_id"]

        for filename, expected in SNAPSHOT_FIXTURES.items():
            body = client.get(f"/sessions/{session_ids[filename]}").json()
            assert body["rmssd_precomputed"] == expected
            assert body["hrv_source_tier"] == "health_snapshot"

    assert len(set(session_ids.values())) == 2

    connection = db.get_connection()
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE hrv_source_tier = 'health_snapshot'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert count == 2


def test_re_uploading_one_snapshot_is_still_a_409_and_leaves_the_other_alone() -> None:
    """The duplicate-upload path is unchanged by any of the above: a genuine
    re-upload is rejected 409 as a no-op, while the *other* day's reading is
    untouched."""
    with TestClient(app) as client:
        first = _ingest(client, "sample_health_snapshot.fit")
        assert first.status_code == 201
        other = _ingest(client, "strap_health_snapshot.fit")
        assert other.status_code == 201

        again = _ingest(client, "sample_health_snapshot.fit")
        assert again.status_code == 409
        assert first.json()["session_id"] in again.text

        other_body = client.get(f"/sessions/{other.json()['session_id']}").json()

    assert other_body["rmssd_precomputed"] == SNAPSHOT_FIXTURES["strap_health_snapshot.fit"]

    connection = db.get_connection()
    try:
        count = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        connection.close()

    assert count == 2
