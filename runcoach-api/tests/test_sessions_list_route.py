"""``GET /sessions``: newest first, keyset-paged, with the summary numbers (F019 AC1-AC5, AC6's index).

Every value row is compared with a live oracle, never a pinned number: a running
session's ``distance_m``, ``duration_s`` and ``avg_pace_s_per_km`` must equal the
same session's ``GET /sessions/{id}/features`` values to 1e-9, including the
walk and the resting sample the corpus stores as ``running``. The 13 fixtures
have no moving gap, so one is constructed by dropping 60 s of records out of a
real run (``tests/support/fit_patch.py``), across which the device distance
advances at running speed; the features route flags it ``moving_gap``, and the
list must count it the same way.

The order, the paging and the cursor are driven through the real app against
the autouse isolated store. Constructed sessions go in through ``db.persist``
(``models.Session``), so a tie on ``start_time``, a run with no records, a
stationary run and a 51-session store cost no FIT bytes. The cursor probe
table attacks ``since`` with every shape the AC names; each refusal is
FastAPI's default ``{detail: [...]}`` with ``loc`` ``["query", "since"]``.
"""

from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.metrics import session_list
from runcoach_api.models import Context, Record, Session

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"
ALL_FIXTURES = sorted(path.name for path in FIXTURES.glob("*.fit"))
NUMBERS = ("distance_m", "duration_s", "avg_pace_s_per_km")
SUMMARY_KEYS = {
    "session_id",
    "start_time",
    "sport",
    "activity_tag",
    "name",
    "distance_m",
    "duration_s",
    "avg_pace_s_per_km",
    "quality_flags",
    "workout_id",
}
# The keys ``GET /sessions/{id}`` served before the list existed (``db.get_session_detail``).
DETAIL_KEYS = {
    "session_id",
    "athlete_id",
    "start_time",
    "sport",
    "activity_tag",
    "source_vendor",
    "source_device",
    "recording_interval",
    "hr_source",
    "rr_valid_fraction",
    "rmssd_precomputed",
    "resting_rmssd_ms",
    "hrv_source_tier",
    "rr_source",
    "quality_flags",
    "summary",
    "context",
    "records",
    "rr_intervals",
}


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")


# --- helpers ----------------------------------------------------------------------------------------


def _upload(client: TestClient, name: str, raw: bytes | None = None) -> str:
    response = client.post("/sessions", files={"file": (name, raw or (FIXTURES / name).read_bytes())})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _upload_corpus(client: TestClient) -> list[str]:
    assert len(ALL_FIXTURES) == 13, ALL_FIXTURES
    return [_upload(client, name) for name in ALL_FIXTURES]


def _stored(session_id: str) -> dict:
    conn = db_module.get_connection()
    try:
        row = conn.execute(
            "SELECT start_time, name, context FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, session_id
    return {"start_time": row["start_time"], "name": row["name"], "context": db_module._json_load(row["context"])}


def _list(client: TestClient, **params) -> dict:
    response = client.get("/sessions", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _features(client: TestClient, session_id: str) -> dict:
    response = client.get(f"/sessions/{session_id}/features")
    assert response.status_code == 200, response.text
    return response.json()["features"]


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= 1e-9


def _session(start_time: str, device: str, sport: str = "other", profile: str | None = None) -> Session:
    provenance = {} if profile is None else {"sport_profile_name": profile}
    return Session(
        session_id=derive_session_id(device, start_time),
        sport=sport,
        source_vendor="garmin",
        start_time=start_time,
        source_device=device,
        context=Context(provenance=provenance),
    )


def _persist(session: Session, records: list[Record] | None = None) -> str:
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        db_module.persist(conn, session, records or [], [], {})
    finally:
        conn.close()
    return session.session_id


def _iso(day: int, hour: int = 6) -> str:
    return f"2026-03-{day:02d}T{hour:02d}:00:00+00:00"


def _cursor(start_time: str, session_id: str) -> str:
    return base64.urlsafe_b64encode(f"{start_time}|{session_id}".encode()).decode("ascii")


def _walk(client: TestClient, limit: int) -> list[dict]:
    pages = []
    since = None
    while True:
        page = _list(client, limit=limit) if since is None else _list(client, limit=limit, since=since)
        pages.append(page)
        since = page["next_cursor"]
        if since is None:
            return pages
        assert len(pages) < 100, "the walk did not terminate"


# --- AC1: order and the summary numbers -------------------------------------------------------------


def test_list_is_newest_first_and_matches_features_to_1e_9() -> None:
    """The first failing test: 13 fixtures, order by ``start_time`` desc then ``session_id`` desc,
    every summary field, and the three numbers equal to the features route's for every running
    session (the walk and the resting sample included), null for every other sport."""
    with TestClient(app) as client:
        ids = _upload_corpus(client)
        page = _list(client)
        assert page["next_cursor"] is None
        items = page["items"]
        assert [item["session_id"] for item in items] == sorted(
            ids, key=lambda sid: (_stored(sid)["start_time"], sid), reverse=True
        )
        running = 0
        for item in items:
            assert set(item) == SUMMARY_KEYS, item
            detail = client.get(f"/sessions/{item['session_id']}").json()
            stored = _stored(item["session_id"])
            assert item["start_time"] == stored["start_time"] == detail["start_time"]
            assert item["sport"] == detail["sport"]
            assert item["activity_tag"] == detail["activity_tag"]
            assert item["quality_flags"] == detail["quality_flags"]
            assert item["workout_id"] is None
            expected_name = stored["name"] or session_list.session_name(
                None, (stored["context"] or {}).get("provenance", {}).get("sport_profile_name")
            )
            assert item["name"] == expected_name
            if item["sport"] == "running":
                running += 1
                features = _features(client, item["session_id"])
                for number in NUMBERS:
                    assert _close(item[number], features[number]["value"]), (item["session_id"], number)
            else:
                assert all(item[number] is None for number in NUMBERS), item
        # The walk and the resting sample are stored running and were compared above.
        assert running >= 8, [(item["session_id"], item["sport"]) for item in items]
        print(f"{len(items)} sessions listed, {running} running compared with the features route")


def test_a_constructed_moving_gap_run_matches_features_to_1e_9() -> None:
    """60 s of records dropped out of a real run: the device distance advances at running speed
    across the gap, the features route counts it (``moving_gap``), and so must the list."""
    raw = fit_patch.drop_records((FIXTURES / "sample_run.fit").read_bytes(), 300.0, 360.0)
    with TestClient(app) as client:
        session_id = _upload(client, "sample_run_moving_gap.fit", raw)
        features = client.get(f"/sessions/{session_id}/features").json()
        assert "moving_gap" in features["flags"], features["flags"]
        [item] = _list(client)["items"]
    assert item["session_id"] == session_id
    for number in NUMBERS:
        assert features["features"][number]["value"] is not None
        assert _close(item[number], features["features"][number]["value"]), number
    print({number: item[number] for number in NUMBERS})


def test_a_tie_on_start_time_lists_the_larger_session_id_first_and_a_run_with_no_records_is_null() -> None:
    first = _persist(_session(_iso(1), "device-a"))
    second = _persist(_session(_iso(1), "device-b"))
    bare_run = _persist(_session(_iso(2), "device-c", sport="running"))
    with TestClient(app) as client:
        items = _list(client)["items"]
    assert [item["session_id"] for item in items] == [bare_run, max(first, second), min(first, second)]
    assert all(items[0][number] is None for number in NUMBERS)


def test_a_running_session_with_no_counted_segment_lists_three_nulls() -> None:
    """Records at 10 s spacing with dd = 0: no segment is counted, so T = D = 0 and the three numbers
    are null, exactly the features route's unavailable values."""
    records = [Record(t=float(10 * k), distance=100.0) for k in range(30)]
    session_id = _persist(_session(_iso(3), "device-still", sport="running"), records)
    with TestClient(app) as client:
        [item] = _list(client)["items"]
        features = _features(client, session_id)
    for number in NUMBERS:
        assert item[number] is None, number
        assert features[number]["value"] is None, number


# --- AC2 read side ----------------------------------------------------------------------------------


def test_a_nulled_name_column_falls_back_to_the_stored_profile_name() -> None:
    with TestClient(app) as client:
        session_id = _upload(client, "wrist_ppg_run.fit")
        conn = db_module.get_connection()
        try:
            conn.execute("UPDATE sessions SET name = NULL WHERE session_id = ?", (session_id,))
            conn.commit()
        finally:
            conn.close()
        [item] = _list(client)["items"]
    assert _stored(session_id)["name"] is None
    assert item["name"] == "Run"


def test_session_detail_is_unchanged_by_the_name_column() -> None:
    with TestClient(app) as client:
        session_id = _upload(client, "sample_run.fit")
        detail = client.get(f"/sessions/{session_id}")
    assert detail.status_code == 200
    assert set(detail.json()) == DETAIL_KEYS
    assert "name" not in detail.json()


# --- AC3: paging ------------------------------------------------------------------------------------


def test_limit_defaults_to_50() -> None:
    for day in range(1, 32):
        _persist(_session(_iso(day, 6), "device-d"))
        if day <= 20:
            _persist(_session(_iso(day, 18), "device-d"))
    with TestClient(app) as client:
        page = _list(client)
        assert len(page["items"]) == 50
        assert page["next_cursor"] is not None
        rest = _list(client, since=page["next_cursor"])
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None
    limit = next(
        p for p in app.openapi()["paths"]["/sessions"]["get"]["parameters"] if p["name"] == "limit"
    )
    assert limit["schema"]["default"] == 50
    assert (limit["schema"]["minimum"], limit["schema"]["maximum"]) == (1, 200)


@pytest.mark.parametrize("bad", ["0", "201", "x"])
def test_a_bad_limit_is_a_422(bad: str) -> None:
    with TestClient(app) as client:
        response = client.get("/sessions", params={"limit": bad})
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["query", "limit"]


def test_walking_the_pages_returns_every_session_once() -> None:
    with TestClient(app) as client:
        ids = _upload_corpus(client)
        pages = _walk(client, limit=2)
        whole = _list(client)["items"]
    assert [len(page["items"]) for page in pages] == [2, 2, 2, 2, 2, 2, 1]
    walked = [item["session_id"] for page in pages for item in page["items"]]
    assert walked == [item["session_id"] for item in whole]
    assert sorted(walked) == sorted(ids)
    assert pages[-1]["next_cursor"] is None
    print(f"{len(pages)} pages of at most 2 over {len(ids)} sessions")


def test_an_exact_multiple_of_the_limit_has_no_empty_last_page() -> None:
    ids = [_persist(_session(_iso(day), "device-e")) for day in range(1, 5)]
    with TestClient(app) as client:
        pages = _walk(client, limit=2)
    assert [len(page["items"]) for page in pages] == [2, 2]
    assert pages[1]["next_cursor"] is None
    assert sorted(item["session_id"] for page in pages for item in page["items"]) == sorted(ids)


def test_a_newer_upload_and_a_deleted_last_row_between_pages_neither_repeat_nor_skip() -> None:
    a, b, c, d = (_persist(_session(_iso(day), "device-f")) for day in (10, 9, 8, 7))
    with TestClient(app) as client:
        page1 = _list(client, limit=2)
        assert [item["session_id"] for item in page1["items"]] == [a, b]
        _persist(_session(_iso(11), "device-f"))  # newer than every listed session
        assert client.delete(f"/sessions/{b}").status_code == 204
        page2 = _list(client, limit=2, since=page1["next_cursor"])
    assert [item["session_id"] for item in page2["items"]] == [c, d]
    assert page2["next_cursor"] is None


# --- AC4: the cursor is validated -------------------------------------------------------------------

GOOD_TIME = "2026-03-01T06:00:00+00:00"
GOOD_ID = "0123456789abcdef0123456789abcdef"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


CURSOR_REFUSALS = {
    "empty_string": "",
    "missing_padding": _cursor(GOOD_TIME, GOOD_ID).rstrip("="),
    "non_utf8_bytes": _b64(b"\xff\xfe\xfd|" + GOOD_ID.encode()),
    "one_part": _b64(GOOD_TIME.encode()),
    "three_parts": _b64(f"{GOOD_TIME}|{GOOD_ID}|extra".encode()),
    "separator_inside_the_id": _b64(f"{GOOD_TIME}|{GOOD_ID[:16]}|{GOOD_ID[16:]}".encode()),
    "ten_kilobytes": "A" * 10240,
    "plus_two_offset": _cursor("2026-03-01T06:00:00+02:00", GOOD_ID),
    "naive_time": _cursor("2026-03-01T06:00:00", GOOD_ID),
    "not_a_time": _cursor("yesterday", GOOD_ID),
}


@pytest.mark.parametrize("since", list(CURSOR_REFUSALS.values()), ids=list(CURSOR_REFUSALS))
def test_a_bad_cursor_is_a_422_on_query_since(since: str) -> None:
    with TestClient(app) as client:
        response = client.get("/sessions", params={"since": since})
    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) == {"detail"}
    [error] = body["detail"]
    assert error["loc"] == ["query", "since"]
    assert {"type", "loc", "msg", "input"} <= set(error)
    print(f"{error['msg']!r}")


def test_a_z_time_cursor_is_served_and_normalised() -> None:
    """A ``Z`` time is aware UTC: served, and compared as the stored ``+00:00`` spelling."""
    ids = [_persist(_session(_iso(day), "device-g")) for day in (3, 2, 1)]
    with TestClient(app) as client:
        page = _list(client, since=_cursor("2026-03-02T06:00:00Z", ids[1]))
    assert [item["session_id"] for item in page["items"]] == [ids[2]]


def test_a_forged_cursor_of_the_right_shape_is_served() -> None:
    ids = [_persist(_session(_iso(day), "device-h")) for day in (3, 2, 1)]
    with TestClient(app) as client:
        page = _list(client, since=_cursor("2026-03-02T06:00:00+00:00", "zzz-not-an-id"))
    # "zzz..." sorts after every hex id, so the day-2 row itself is older than the cursor.
    assert [item["session_id"] for item in page["items"]] == [ids[1], ids[2]]


# --- AC5: the empty store and a bad row -------------------------------------------------------------


def test_an_empty_store_is_200_with_no_items() -> None:
    with TestClient(app) as client:
        page = _list(client)
    assert page == {"items": [], "next_cursor": None}


def test_a_row_whose_numbers_cannot_be_computed_lists_three_nulls(monkeypatch: pytest.MonkeyPatch) -> None:
    """The seam: the records read of one session raises; that row's three numbers are null, the page
    is 200 and the other running session's numbers are untouched."""
    with TestClient(app) as client:
        broken = _upload(client, "sample_run.fit")
        healthy = _upload(client, "wrist_ppg_run.fit")
        healthy_numbers = {number: _features(client, healthy)[number]["value"] for number in NUMBERS}
        real_reader = db_module.read_session_totals_rows

        def _reader(conn, session_id):
            if session_id == broken:
                raise RuntimeError("the records of this session cannot be read")
            return real_reader(conn, session_id)

        monkeypatch.setattr(db_module, "read_session_totals_rows", _reader)
        items = {item["session_id"]: item for item in _list(client)["items"]}
    assert all(items[broken][number] is None for number in NUMBERS)
    assert all(_close(items[healthy][number], healthy_numbers[number]) for number in NUMBERS)


def test_a_store_missing_the_name_column_is_the_named_500() -> None:
    with TestClient(app) as client:
        _upload(client, "sample_run.fit")
        conn = db_module.get_connection()
        try:
            conn.execute("ALTER TABLE sessions DROP COLUMN name")
            conn.commit()
        finally:
            conn.close()
        response = client.get("/sessions")
    assert response.status_code == 500, response.text
    detail = response.json()["detail"]
    assert "restart the API" in detail and "name" in detail
    assert "OperationalError" not in detail


# --- AC6: the index ---------------------------------------------------------------------------------


def test_startup_creates_the_records_index_on_session_id_and_t() -> None:
    with TestClient(app):
        pass
    conn = db_module.get_connection()
    try:
        indexes = conn.execute("PRAGMA index_list(records)").fetchall()
        columns = {
            row["name"]: [c["name"] for c in conn.execute(f"PRAGMA index_info({row['name']})")] for row in indexes
        }
    finally:
        conn.close()
    assert ["session_id", "t"] in columns.values(), columns
