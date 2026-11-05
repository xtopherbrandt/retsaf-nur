"""F031 AC1, AC2 and AC5's schema half: a stored run that Strava types as another sport carries no load.

A running session linked (F030, ``strava_activities.linked_session_id``) to a Strava row that is not
``deleted_in_strava`` and whose ``Activity Type`` is not running family (``Run``, ``Trail Run``,
``Virtual Run``) is unavailable ``strava_not_running`` on every metric, and its body names the row
(``strava_activity_id``). The gate is checked after ``sport_not_running`` and ``declared_capture``
and before ``no_hr``, so a declared capture linked to a ski keeps ``declared_capture``. The load
chart puts the session in the excluded group: its day loses its old value and is not marked, and
it neither ends a stretch of empty days nor starts history.

The link is written here with ``db.write_strava_row`` and the load rebuilt through
``POST /sessions/{id}/load/recompute``, the replace path every Strava import uses; the upload and
the one-time fill read the same row through ``db.read_session_load_inputs``.

**Adversarial probe table** (rule adversarial-input-probes-are-a-task-deliverable), driven through
``session_load.strava_exclusion`` and ``compute_session_load`` in
``test_the_predicate_reads_the_type_and_nothing_else``; the reader filters ``deleted_in_strava``
before the predicate sees the row (``test_a_deleted_in_strava_row_does_not_exclude``).

| input | result | why |
|---|---|---|
| no linked row (``strava`` None) | counted, as before | F031 Negative Class 2(c): unchanged |
| ``Run``, ``Trail Run``, ``Virtual Run`` | counted, ``strava_activity_id`` null | running family |
| ``Alpine Ski``, ``Hike``, ``Walk``, ``Elliptical`` | ``strava_not_running`` | the running-only ruling (2026-10-24) |
| ``run`` (lower case), ``Run `` (trailing space) | ``strava_not_running`` | the type is compared as Strava writes it; the export spells the family exactly |
| ``""`` or None as the type | ``strava_not_running`` | a linked row that names no running type is not running family; the CSV reader always fills the cell |
| sport ``cycling`` linked to ``Alpine Ski`` | ``sport_not_running`` | gate 1 wins |
| tag ``health_snapshot`` linked to ``Alpine Ski`` | ``declared_capture`` | gate 2 wins (AC2) |
| no HR, linked to ``Hike`` | ``strava_not_running`` | checked before ``no_hr``, so the day is not marked |
| ``strava`` not a mapping | ``ValueError`` | a defect of the writer, as for the value blocks |
"""

from __future__ import annotations

import json
import sqlite3
import typing
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api import schemas
from runcoach_api.imports import strava_match
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.metrics import load_chart, session_load
from runcoach_api.models import Session

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
FIXTURES = Path(__file__).parent / "fixtures"
RUN = "sample_run.fit"
REASON = "strava_not_running"
METRICS = ("hr_trimp", "rtss", "srpe")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    conn = db_module.get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _upload(client: TestClient) -> str:
    created = client.post("/sessions", files={"file": (RUN, (FIXTURES / RUN).read_bytes())})
    assert created.status_code == 201, created.text
    return created.json()["session_id"]


def _link(session_id: str | None, activity_id: int, activity_type: str, *, deleted: bool = False) -> None:
    """Write (or replace) one Strava row linked to ``session_id``, as an import stores it."""
    row = {
        "activity_id": activity_id,
        "start_time": "2020-01-01T08:00:00+00:00",
        "activity_type": activity_type,
        "name": f"{activity_type} {activity_id}",
        "description": None,
        "private_note": None,
        "filename": f"activities/{activity_id}.fit",
        "columns": {"Activity ID": str(activity_id), "Activity Type": activity_type},
        "linked_session_id": session_id,
        "match": "csv_start" if session_id else None,
        "outcome": "attached",
        "warnings": [],
        "deleted_in_strava": deleted,
        "import_id": "import-1",
        "updated_at": "2026-11-04T00:00:00+00:00",
    }
    with _connection() as conn, conn:
        db_module.write_strava_row(conn, row)


def _saved(session_id: str) -> dict:
    with _connection() as conn:
        row = conn.execute(
            f"SELECT body FROM {db_module.SESSION_LOADS_TABLE} WHERE session_id = ?", (session_id,)
        ).fetchone()
    assert row is not None, f"no saved load for {session_id}"
    return json.loads(row["body"])


def _saved_row(session_id: str) -> tuple:
    with _connection() as conn:
        row = conn.execute(
            f"SELECT load_value, load_reason FROM {db_module.SESSION_LOADS_TABLE} WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return tuple(row)


def _recompute(client: TestClient, session_id: str) -> dict:
    response = client.post(f"/sessions/{session_id}/load/recompute")
    assert response.status_code == 200, response.text
    body = response.json()
    print(f"  recompute {session_id}: {body['session_load']} strava_activity_id {body['strava_activity_id']}")
    return body


def _session_rows(session_id: str) -> tuple[list[tuple], int]:
    """The session row and its record count, read outside the app."""
    with _connection() as conn:
        session = [tuple(r) for r in conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))]
        records = conn.execute("SELECT COUNT(*) FROM records WHERE session_id = ?", (session_id,)).fetchone()[0]
    return session, records


def _synthetic(start: datetime, sport: str = "running", tag: str | None = None) -> Session:
    start_time = start.isoformat()
    return Session(
        session_id=derive_session_id("fr945", start_time),
        sport=sport,
        source_vendor="garmin",
        start_time=start_time,
        source_device="fr945",
        activity_tag=tag,
    )


def _plant(session_id: str, value: float) -> None:
    """Replace a session's saved load with a counted ``value`` (the chart reads the mirror columns)."""
    body = {"session_id": session_id, "session_load": {"value": value, "unavailable": None}}
    with _connection() as conn, conn:
        conn.execute(f"DELETE FROM {db_module.SESSION_LOADS_TABLE} WHERE session_id = ?", (session_id,))
        db_module._write_session_load(conn, session_id, body)


@pytest.fixture
def freeze(monkeypatch):
    def _freeze(day: date) -> None:
        monkeypatch.setattr(main_module, "_utcnow", lambda: datetime(day.year, day.month, day.day, 12, tzinfo=UTC))

    return _freeze


def _chart(client: TestClient) -> dict:
    response = client.get("/metrics/load")
    assert response.status_code == 200, response.text
    return response.json()


def _day(chart: dict, day: date) -> dict:
    return next(d for d in chart["days"] if d["date"] == day.isoformat())


def _inputs(**overrides) -> dict:
    """A pure-module input mapping with HR records enough to compute a load (chest strap)."""
    rows = [{"dt": 1.0, "heart_rate": 150, "hr_excluded": False} for _ in range(600)]
    features = {"features": {"avg_hr_bpm": {"value": 150.0}, "duration_s": {"value": 600.0}}, "flags": []}
    block = dict(session_load.EMPTY_VALUE)
    inputs = {
        "session_id": "s1",
        "sport": "running",
        "activity_tag": None,
        "hr_source": "chest_strap",
        "quality_flags": [],
        "timer_time_s": 600.0,
        "segment_rows": rows,
        "features": features,
        "resting_hr_bpm": {**block, "value": 50, "source": "session_file"},
        "max_hr_bpm": {**block, "value": 190, "source": "session_file"},
        "threshold_hr_bpm": {**block, "value": 170, "source": "session_file"},
        "sex": {**block, "value": "male", "source": "session_file"},
        "strava": None,
    }
    inputs.update(overrides)
    return inputs


def _assert_excluded(body: dict, activity_id: int) -> None:
    assert body["session_load"] == {"value": None, "unavailable": REASON, "driver": None}
    for metric in METRICS:
        assert body["metrics"][metric]["unavailable"] == REASON, metric
        assert body["metrics"][metric]["value"] is None, metric
    assert body["strava_activity_id"] == activity_id


# ---------------------------------------------------------------------------
# AC1: a Strava non-run is excluded
# ---------------------------------------------------------------------------


def test_a_run_linked_to_alpine_ski_is_strava_not_running_on_every_metric() -> None:
    """A counted run linked to an ``Alpine Ski`` row serves ``strava_not_running`` on session_load,
    hr_trimp, rtss and srpe, names the row, and still reports its inputs; the session and its
    records are unchanged."""
    with TestClient(app) as client:
        session_id = _upload(client)
        before = _saved(session_id)
        assert before["session_load"]["value"] is not None
        rows_before = _session_rows(session_id)
        detail_before = client.get(f"/sessions/{session_id}").json()
        _link(session_id, 101, "Alpine Ski")
        recomputed = _recompute(client, session_id)
        served = client.get(f"/sessions/{session_id}/load")
        detail_after = client.get(f"/sessions/{session_id}").json()
    assert served.status_code == 200, served.text
    body = served.json()
    print(f"  before {before['session_load']} after {body['session_load']}")
    _assert_excluded(body, 101)
    assert recomputed["changed"] is True
    assert body == _saved(session_id)
    assert _saved_row(session_id) == (None, REASON)
    # The inputs are still reported, as under gates 1 and 2.
    assert body["inputs"] == before["inputs"]
    assert body["metrics"]["hr_trimp"]["coefficients"] is None
    assert _session_rows(session_id) == rows_before
    # The session's detail is unchanged but for its ``strava`` block, which now names the row.
    strava_before, strava_after = detail_before.pop("strava"), detail_after.pop("strava")
    assert strava_before is None
    assert (strava_after["activity_id"], strava_after["activity_type"]) == (101, "Alpine Ski")
    assert detail_after == detail_before


def test_its_day_load_drops_by_its_old_value_and_is_not_marked(freeze, persist_sessions) -> None:
    """The run's day also holds a counted 30.0 run. Linked to a ski, the run leaves the day's load
    at 30.0 (its old value gone), sits in the excluded group, and the day is not marked."""
    with TestClient(app) as client:
        session_id = _upload(client)
        start = datetime.fromisoformat(client.get(f"/sessions/{session_id}").json()["start_time"])
        old_value = _saved(session_id)["session_load"]["value"]
        sibling = _synthetic(start + timedelta(minutes=1))
        persist_sessions([sibling])
        _plant(sibling.session_id, 30.0)
        day = start.astimezone(UTC).date()
        freeze(day + timedelta(days=1))
        before = _day(_chart(client), day)
        _link(session_id, 102, "Alpine Ski")
        _recompute(client, session_id)
        after = _day(_chart(client), day)
    print(f"  before load {before['load']} counted {before['counted']}; after load {after['load']} excluded {after['excluded']}")
    assert before["load"] == pytest.approx(old_value + 30.0)
    assert after["load"] == pytest.approx(before["load"] - old_value)
    assert after["counted"] == [{"session_id": sibling.session_id, "load": 30.0}]
    assert after["excluded"] == [{"session_id": session_id, "reason": REASON}]
    assert after["uncounted"] == []
    assert after["marked"] is False


def test_a_no_hr_run_linked_to_a_hike_leaves_its_day_unmarked(freeze, persist_sessions) -> None:
    """A running session with no HR is ``no_hr`` and marks its day; linked to a ``Hike`` row it is
    ``strava_not_running`` (checked before ``no_hr``), excluded, and its day is unmarked."""
    day = date(2022, 8, 27)
    counted = _synthetic(datetime(2022, 8, 27, 7, tzinfo=UTC))
    hike = _synthetic(datetime(2022, 8, 27, 10, tzinfo=UTC))
    persist_sessions([counted, hike])
    _plant(counted.session_id, 30.0)
    freeze(day + timedelta(days=1))
    assert _saved_row(hike.session_id) == (None, "no_hr")
    with TestClient(app) as client:
        before = _day(_chart(client), day)
        _link(hike.session_id, 103, "Hike")
        body = _recompute(client, hike.session_id)
        after = _day(_chart(client), day)
    print(f"  before marked {before['marked']} uncounted {before['uncounted']}; after {after['marked']} {after['excluded']}")
    assert before["marked"] is True and before["uncounted"] == [{"session_id": hike.session_id, "reason": "no_hr"}]
    _assert_excluded(body, 103)
    assert after["marked"] is False
    assert after["uncounted"] == []
    assert after["excluded"] == [{"session_id": hike.session_id, "reason": REASON}]
    assert after["load"] == before["load"] == 30.0


def test_a_run_linked_to_a_run_row_is_unchanged() -> None:
    """Linked to a ``Run``, ``Trail Run`` or ``Virtual Run`` row, the recomputed body equals the one
    saved at upload: the same load, ``strava_activity_id`` null, ``changed`` false."""
    with TestClient(app) as client:
        session_id = _upload(client)
        before = _saved(session_id)
        for activity_type in ("Run", "Trail Run", "Virtual Run"):
            _link(session_id, 104, activity_type)
            body = _recompute(client, session_id)
            print(f"  {activity_type}: {body['session_load']}")
            assert body["changed"] is False, activity_type
            assert _saved(session_id) == before, activity_type
            assert body["strava_activity_id"] is None
    assert before["session_load"]["value"] is not None


def test_a_deleted_in_strava_row_does_not_exclude() -> None:
    """A row marked ``deleted_in_strava`` excludes nothing, even while it still names the session;
    clearing the mark excludes it, and setting it again restores the upload's body."""
    with TestClient(app) as client:
        session_id = _upload(client)
        before = _saved(session_id)
        _link(session_id, 105, "Alpine Ski", deleted=True)
        kept = _recompute(client, session_id)
        _link(session_id, 105, "Alpine Ski")
        excluded = _recompute(client, session_id)
        _link(session_id, 105, "Alpine Ski", deleted=True)
        restored = _recompute(client, session_id)
    assert kept["changed"] is False and kept["session_load"] == before["session_load"]
    _assert_excluded(excluded, 105)
    assert restored["session_load"] == before["session_load"] and restored["strava_activity_id"] is None
    assert _saved(session_id) == before


# ---------------------------------------------------------------------------
# AC2: precedence, and every path agrees
# ---------------------------------------------------------------------------


def test_declared_capture_takes_precedence(persist_sessions) -> None:
    """A declared capture linked to a ski keeps ``declared_capture``; a ride linked to one keeps
    ``sport_not_running``; the predicate is false for both, so a reconcile never sees them as in
    disagreement."""
    capture = _synthetic(datetime(2021, 3, 1, 7, tzinfo=UTC), tag="health_snapshot")
    ride = _synthetic(datetime(2021, 3, 1, 9, tzinfo=UTC), sport="cycling")
    persist_sessions([capture, ride])
    _link(capture.session_id, 106, "Alpine Ski")
    _link(ride.session_id, 107, "Alpine Ski")
    with TestClient(app) as client:
        capture_body = _recompute(client, capture.session_id)
        ride_body = _recompute(client, ride.session_id)
    assert capture_body["session_load"]["unavailable"] == "declared_capture"
    assert {capture_body["metrics"][m]["unavailable"] for m in METRICS} == {"declared_capture"}
    assert capture_body["strava_activity_id"] is None
    assert ride_body["session_load"]["unavailable"] == "sport_not_running"
    assert ride_body["strava_activity_id"] is None
    ski = {"activity_type": "Alpine Ski", "activity_id": 106}
    assert session_load.strava_exclusion("running", "health_snapshot", ski) is False
    assert session_load.strava_exclusion("running", "resting_hrv_check", ski) is False
    assert session_load.strava_exclusion("other", None, ski) is False
    assert session_load.strava_exclusion("running", None, ski) is True


def test_upload_recompute_and_fill_build_the_same_body() -> None:
    """The same stored data gives one body on every path: the recompute route, the one-time fill
    (``_fill_session_loads`` on a session with no saved load) and an upload of a run whose Strava
    row is already linked (the row outlives a deleted session, and the re-upload derives the same
    session id). Each is ``strava_not_running``; a recompute after it still serves it."""
    with TestClient(app) as client:
        session_id = _upload(client)
        _link(session_id, 108, "Alpine Ski")
        recomputed = _recompute(client, session_id)
        by_recompute = _saved(session_id)
        with _connection() as conn, conn:
            conn.execute(f"DELETE FROM {db_module.SESSION_LOADS_TABLE} WHERE session_id = ?", (session_id,))
            db_module._fill_session_loads(conn)
        by_fill = _saved(session_id)
        assert client.delete(f"/sessions/{session_id}").status_code == 204
        assert _upload(client) == session_id
        by_upload = _saved(session_id)
        again = _recompute(client, session_id)
    print(f"  recompute {by_recompute['session_load']} fill {by_fill['session_load']} upload {by_upload['session_load']}")
    _assert_excluded(by_recompute, 108)
    assert by_fill == by_recompute
    assert by_upload == by_recompute
    assert again["changed"] is False
    assert {k: v for k, v in recomputed.items() if k != "changed"} == by_recompute


# ---------------------------------------------------------------------------
# AC1's gap clause, and the counted body
# ---------------------------------------------------------------------------


def test_an_excluded_session_alone_in_a_gap_reads_as_no_session(freeze, persist_sessions) -> None:
    """Counted runs on D0 and D0 + 30, a no-HR run on D0 + 15 and another on D0 - 5. Unlinked, the
    two no-HR runs are uncounted: one ends the empty stretch, the other starts history. Linked to
    non-runs, they are excluded and the chart's ``gaps``, ``after_gap`` and ``first_day`` equal
    those of the same store with both sessions deleted."""
    d0 = date(2019, 6, 1)
    first = _synthetic(datetime(2019, 6, 1, 8, tzinfo=UTC))
    back = _synthetic(datetime(2019, 7, 1, 8, tzinfo=UTC))
    inside = _synthetic(datetime(2019, 6, 16, 8, tzinfo=UTC))
    earlier = _synthetic(datetime(2019, 5, 27, 8, tzinfo=UTC))
    persist_sessions([first, back, inside, earlier])
    _plant(first.session_id, 40.0)
    _plant(back.session_id, 50.0)
    freeze(date(2019, 7, 10))

    def shape(chart: dict) -> dict:
        return {
            "first_day": chart["first_day"],
            "gaps": chart["gaps"],
            "after_gap": [d["date"] for d in chart["days"] if d["after_gap"]],
            "marked": [d["date"] for d in chart["days"] if d["marked"]],
            "loads": [(d["date"], d["load"], d["ctl"], d["atl"]) for d in chart["days"]],
        }

    with TestClient(app) as client:
        unlinked = shape(_chart(client))
        _link(inside.session_id, 109, "Stair-Stepper")
        _link(earlier.session_id, 110, "Kayaking")
        _recompute(client, inside.session_id)
        _recompute(client, earlier.session_id)
        linked = shape(_chart(client))
        for session in (inside, earlier):
            assert client.delete(f"/sessions/{session.session_id}").status_code == 204
        without = shape(_chart(client))
    print(f"  unlinked first_day {unlinked['first_day']} gaps {unlinked['gaps']}")
    print(f"  linked   first_day {linked['first_day']} gaps {linked['gaps']} after_gap {linked['after_gap'][:2]}...")
    assert unlinked["gaps"] == [] and unlinked["first_day"] == "2019-05-27"
    assert linked == without
    assert linked["first_day"] == d0.isoformat()
    assert linked["gaps"] == [{"first_empty_day": "2019-06-02", "last_empty_day": "2019-06-30", "empty_days": 29}]
    assert linked["after_gap"][0] == "2019-07-01"
    assert linked["marked"] == []


def test_a_counted_body_carries_no_strava_activity_id(persist_sessions) -> None:
    """``strava_activity_id`` is set only under ``strava_not_running``: an unlinked counted run, a
    run linked to a Run row and an uncounted (``no_hr``) run linked to a Run row all carry null,
    so linking rewrites no body that is not excluded."""
    nohr = _synthetic(datetime(2021, 3, 2, 7, tzinfo=UTC))
    persist_sessions([nohr])
    with TestClient(app) as client:
        session_id = _upload(client)
        unlinked = client.get(f"/sessions/{session_id}/load").json()
        _link(session_id, 111, "Run")
        _link(nohr.session_id, 112, "Run")
        linked = _recompute(client, session_id)
        uncounted = _recompute(client, nohr.session_id)
    assert unlinked["session_load"]["value"] is not None and unlinked["strava_activity_id"] is None
    assert linked["session_load"] == unlinked["session_load"] and linked["strava_activity_id"] is None
    assert uncounted["session_load"]["unavailable"] == "no_hr" and uncounted["strava_activity_id"] is None
    # The pure module: only the excluded reason names the row.
    run_row = {"activity_type": "Run", "activity_id": 113}
    assert session_load.compute_session_load(_inputs(strava=run_row))["strava_activity_id"] is None
    assert session_load.compute_session_load(_inputs())["strava_activity_id"] is None
    ski_row = {"activity_type": "Alpine Ski", "activity_id": 114}
    assert session_load.compute_session_load(_inputs(strava=ski_row))["strava_activity_id"] == 114


def test_the_predicate_reads_the_type_and_nothing_else() -> None:
    """The probe table's rows, through the pure module."""
    counted = session_load.compute_session_load(_inputs())
    assert counted["session_load"]["unavailable"] is None
    for activity_type in ("Run", "Trail Run", "Virtual Run"):
        body = session_load.compute_session_load(_inputs(strava={"activity_type": activity_type, "activity_id": 1}))
        assert body["session_load"] == counted["session_load"], activity_type
        assert body["metrics"] == counted["metrics"], activity_type
    for activity_type in ("Alpine Ski", "Hike", "Walk", "Elliptical", "run", "Run ", "", None):
        body = session_load.compute_session_load(_inputs(strava={"activity_type": activity_type, "activity_id": 2}))
        _assert_excluded(body, 2)
        assert body["flags"] == [], activity_type
    no_hr = _inputs(strava={"activity_type": "Hike", "activity_id": 3}, segment_rows=[])
    assert session_load.compute_session_load(no_hr)["session_load"]["unavailable"] == REASON
    assert session_load.compute_session_load(_inputs(segment_rows=[]))["session_load"]["unavailable"] == "no_hr"
    with pytest.raises(ValueError, match="strava"):
        session_load.compute_session_load(_inputs(strava=["Alpine Ski", 4]))


# ---------------------------------------------------------------------------
# AC5: every schema site
# ---------------------------------------------------------------------------


def _literal(model: type, field_name: str) -> set[str]:
    return set(typing.get_args(model.model_fields[field_name].annotation))


def test_served_reasons_are_in_every_contract_enum() -> None:
    """``SERVED_REASONS`` is in the contract's and the model's ``SessionLoadReason``; the session-wide
    reasons are in every Rtss and Srpe enum; the exclusion reasons are ``LoadChartExcluded``'s enum
    and ``LoadChartReason`` is the rest, in the contract and the models alike."""
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))["components"]["schemas"]
    served = set(session_load.SERVED_REASONS)
    wide = set(session_load.SESSION_WIDE_REASONS)
    excluded = set(load_chart.EXCLUSION_REASONS)
    print(f"  served {sorted(served)}\n  session-wide {sorted(wide)}\n  excluded {sorted(excluded)}")
    assert REASON in served and REASON in wide and REASON in excluded
    assert served <= set(contract["SessionLoadReason"]["enum"])
    assert served <= {r.value for r in schemas.SessionLoadReason}
    for name, model in (("Rtss", schemas.Rtss), ("Srpe", schemas.Srpe)):
        assert wide <= set(contract[name]["properties"]["unavailable"]["enum"]), name
        assert wide <= _literal(model, "unavailable"), name
    assert set(contract["LoadChartExcluded"]["properties"]["reason"]["enum"]) == excluded
    assert _literal(schemas.LoadChartExcluded, "reason") == excluded
    assert set(contract["LoadChartReason"]["enum"]) == served - excluded
    assert {r.value for r in schemas.LoadChartReason} == served - excluded
    assert wide == excluded
    for name in ("SessionLoad", "SessionLoadRecompute"):
        prop = contract[name]["properties"]["strava_activity_id"]
        assert prop["type"] == "integer" and prop["nullable"] is True, name
        assert "strava_activity_id" not in contract[name]["required"], name
    for text in (
        session_load.__doc__,
        load_chart.__doc__,
        contract["SessionLoadReason"]["description"],
        contract["LoadChartReason"]["description"],
        contract["LoadChartExcluded"]["description"],
        schemas.LoadChartReason.__doc__,
        schemas.LoadChartExcluded.__doc__,
    ):
        assert REASON in " ".join(text.split()), text[:80]


def test_both_load_routes_validate_for_an_excluded_session(freeze, persist_sessions) -> None:
    """``GET /sessions/{id}/load`` and ``GET /metrics/load`` answer 200 for an excluded session (each
    validates its response against its model), and the served pieces read as the models."""
    with TestClient(app) as client:
        session_id = _upload(client)
        start = datetime.fromisoformat(client.get(f"/sessions/{session_id}").json()["start_time"])
        sibling = _synthetic(start + timedelta(minutes=1))
        persist_sessions([sibling])
        _plant(sibling.session_id, 30.0)
        freeze(start.date() + timedelta(days=1))
        _link(session_id, 115, "E-Bike Ride")
        _recompute(client, session_id)
        load = client.get(f"/sessions/{session_id}/load")
        chart = client.get("/metrics/load")
    assert load.status_code == 200, load.text
    assert chart.status_code == 200, chart.text
    model = schemas.SessionLoad(**load.json())
    assert model.strava_activity_id == 115 and model.session_load.unavailable == REASON
    day = _day(chart.json(), start.date())
    excluded = [schemas.LoadChartExcluded(**e) for e in day["excluded"]]
    assert [(e.session_id, e.reason) for e in excluded] == [(session_id, REASON)]


_COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def test_the_served_chart_reason_counts_the_exclusion_reasons_load_chart_holds() -> None:
    """The ``LoadChartReason`` description ``app.openapi()`` serves (its docstring) and the
    contract's each name how many exclusion reasons there are; that count word equals
    ``len(load_chart.EXCLUSION_REASONS)``, and each exclusion reason is named."""
    served = app.openapi()["components"]["schemas"]["LoadChartReason"]["description"]
    written = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))["components"]["schemas"]["LoadChartReason"][
        "description"
    ]
    for text in (served, written):
        flat = " ".join(text.split())
        print(flat)
        counts = [n for word, n in _COUNT_WORDS.items() if f" {word} exclusion" in flat]
        assert counts == [len(load_chart.EXCLUSION_REASONS)], flat
        assert all(reason in flat for reason in load_chart.EXCLUSION_REASONS), flat


def test_session_load_and_strava_match_hold_the_same_running_family() -> None:
    """``session_load`` stays a pure module, so it keeps its own copy of Strava's running family;
    the copy equals the matcher's."""
    assert session_load.STRAVA_RUNNING_TYPES == strava_match.RUNNING_FAMILY
