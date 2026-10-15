"""F023 AC2 and AC3: the race-tag matching rule and its one-transaction apply.

Every test goes through ``db.apply_race_tags`` against the isolated scratch store, with sessions
written by the ``persist_sessions`` fixture (``db.persist``), so the candidate SQL (sport, the
NULL-safe resting-capture exclusion, the start-second match with ``Z`` normalised to ``+00:00``)
is exercised together with the pure rule in ``runcoach_api.race_tags``.

``CONTRACT`` below is the AC2 table plus the AC3 transitions, written from the feature file
(spec/features F023, AC2 and AC3) before ``race_tags.py`` existed, not from the implementation:
each row names its candidates and what the feature says must come back. The pair rows use the
measured 2024 shape: the FR945 run 3720.47 m / 1333.772 s and the ``3476`` run 5018.28 m /
1844.175 s at one start second, Connect's item 3720.47 / 1333.772.
"""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db, race_tags
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.models import Session

FIXTURES = Path(__file__).parent / "fixtures"

START = "2024-03-02T16:43:13+00:00"
START_Z = "2024-03-02T16:43:13Z"
OTHER_START = "2024-03-26T17:02:18+00:00"

FR945 = (3720.47, 1333.772)
DEV_3476 = (5018.28, 1844.175)
CONNECT = (3720.47, 1333.772)


def _run(
    device: str,
    start: str = START,
    distance: float | None = FR945[0],
    duration: float | None = FR945[1],
    *,
    tag: str | None = None,
    sport: str = "running",
    summary: dict | None = None,
) -> Session:
    if summary is None:
        summary = {}
        if distance is not None:
            summary["distance_m"] = distance
        if duration is not None:
            summary["duration_s"] = duration
    return Session(
        session_id=derive_session_id(device, start),
        sport=sport,
        source_vendor="garmin",
        start_time=start,
        source_device=device,
        activity_tag=tag,
        summary=summary,
    )


def _item(
    activity_id: int = 1,
    start: str = START_Z,
    distance: float | None = CONNECT[0],
    duration: float | None = CONNECT[1],
    race: bool = True,
) -> dict:
    return {
        "connect_activity_id": activity_id,
        "start_time": start,
        "distance_m": distance,
        "duration_s": duration,
        "race": race,
    }


def _apply(items: list[dict]) -> list[dict]:
    conn = db.get_connection()
    try:
        return db.apply_race_tags(conn, items)
    finally:
        conn.close()


def _tag(session_id: str) -> str | None:
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT activity_tag FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None, session_id
    return row["activity_tag"]


# --- the contract table, written from the feature file before the rule ---------------------------
#
# Each row: (name, stored sessions as (device, distance, duration, tag), item (distance, duration,
# race), expected outcome, expected session_id device or None, expected candidate devices,
# expected tag on each stored device afterwards).
CONTRACT = [
    # AC2 row 1: no candidate.
    ("0 candidates", [], (CONNECT[0], CONNECT[1], True), "not_stored", None, [], {}),
    # AC2 row 2 with AC3 race true, null -> race.
    (
        "1 agreeing, null, race",
        [("fr945", *FR945, None)],
        (*CONNECT, True),
        "tagged",
        "fr945",
        ["fr945"],
        {"fr945": "race"},
    ),
    # AC3: race true on a race tag.
    (
        "1 agreeing, race, race",
        [("fr945", *FR945, "race")],
        (*CONNECT, True),
        "unchanged",
        "fr945",
        ["fr945"],
        {"fr945": "race"},
    ),
    # AC3: race false on a race tag.
    (
        "1 agreeing, race, not race",
        [("fr945", *FR945, "race")],
        (*CONNECT, False),
        "cleared",
        "fr945",
        ["fr945"],
        {"fr945": None},
    ),
    # AC3: race false on null.
    (
        "1 agreeing, null, not race",
        [("fr945", *FR945, None)],
        (*CONNECT, False),
        "unchanged",
        "fr945",
        ["fr945"],
        {"fr945": None},
    ),
    # AC3: any other stored tag, either race value.
    (
        "1 agreeing, other tag, race",
        [("fr945", *FR945, "interval")],
        (*CONNECT, True),
        "other_tag",
        "fr945",
        ["fr945"],
        {"fr945": "interval"},
    ),
    (
        "1 agreeing, other tag, not race",
        [("fr945", *FR945, "interval")],
        (*CONNECT, False),
        "other_tag",
        "fr945",
        ["fr945"],
        {"fr945": "interval"},
    ),
    # AC2 row 3: one candidate, not agreeing.
    (
        "1 disagreeing",
        [("3476", *DEV_3476, None)],
        (*CONNECT, True),
        "mismatch",
        "3476",
        ["3476"],
        {"3476": None},
    ),
    # AC2 row 4: two candidates, exactly one agreeing.
    (
        "2, one agreeing",
        [("fr945", *FR945, None), ("3476", *DEV_3476, None)],
        (*CONNECT, True),
        "tagged",
        "fr945",
        ["fr945", "3476"],
        {"fr945": "race", "3476": None},
    ),
    # AC2 row 5: two candidates, none agreeing.
    (
        "2, none agreeing",
        [("fr945", *FR945, None), ("3476", *DEV_3476, None)],
        (4300.0, 1600.0, True),
        "ambiguous",
        None,
        ["fr945", "3476"],
        {"fr945": None, "3476": None},
    ),
    # AC2 row 5: two candidates, both agreeing.
    (
        "2, both agreeing",
        [("fr945", *FR945, None), ("fr955", *FR945, None)],
        (*CONNECT, True),
        "ambiguous",
        None,
        ["fr945", "fr955"],
        {"fr945": None, "fr955": None},
    ),
    # AC2 row 5 with three candidates, two agreeing.
    (
        "3, two agreeing",
        [("fr945", *FR945, None), ("fr955", *FR945, None), ("3476", *DEV_3476, None)],
        (*CONNECT, True),
        "ambiguous",
        None,
        ["fr945", "fr955", "3476"],
        {"fr945": None, "fr955": None, "3476": None},
    ),
    # AC2 row 4 with three candidates, one agreeing.
    (
        "3, one agreeing",
        [("fr945", *FR945, None), ("fr955", 4300.0, 1600.0, None), ("3476", *DEV_3476, None)],
        (*CONNECT, True),
        "tagged",
        "fr945",
        ["fr945", "fr955", "3476"],
        {"fr945": "race", "fr955": None, "3476": None},
    ),
]


def _sid(device: str) -> str:
    return derive_session_id(device, START)


@pytest.mark.parametrize("row", CONTRACT, ids=[r[0] for r in CONTRACT])
def test_contract_table_row(persist_sessions, row) -> None:
    _name, stored, (distance, duration, race), outcome, session_device, candidate_devices, after = row
    persist_sessions([_run(device, START, d, t, tag=tag) for device, d, t, tag in stored])
    [got] = _apply([_item(7, START_Z, distance, duration, race)])
    assert got["connect_activity_id"] == 7
    assert got["outcome"] == outcome
    assert got["session_id"] == (None if session_device is None else _sid(session_device))
    assert sorted(got["candidates"]) == sorted(_sid(d) for d in candidate_devices)
    for device, tag in after.items():
        assert _tag(_sid(device)) == tag


# --- the thirteen named tests ----------------------------------------------------------------------


def test_no_candidate_is_not_stored(persist_sessions) -> None:
    """No stored running session at the item's second: ``not_stored``, ``session_id`` null. A run
    one second later, a non-running session at the second, and an empty store all count as none."""
    persist_sessions(
        [
            _run("fr945", "2024-03-02T16:43:14+00:00"),
            _run("edge", START, sport="cycling"),
        ]
    )
    [got] = _apply([_item(1)])
    assert got == {"connect_activity_id": 1, "outcome": "not_stored", "session_id": None, "candidates": []}
    assert _tag(_sid("edge")) is None
    assert _tag(derive_session_id("fr945", "2024-03-02T16:43:14+00:00")) is None


def test_one_agreeing_candidate_is_tagged(persist_sessions) -> None:
    """One untagged candidate agreeing within 1 %: ``tagged``, its tag is now ``race``. The item's
    ``Z`` start matches the stored ``+00:00`` form; stored values exactly 1 % of the item's away,
    above and below, still agree (``|s - d| <= 0.01 d``, the bound inclusive)."""
    persist_sessions([_run("fr945", START, 1010.0, 404.0), _run("fr945", OTHER_START, 990.0, 396.0)])
    got = _apply([_item(1, START_Z, 1000.0, 400.0), _item(2, OTHER_START, 1000.0, 400.0)])
    assert [g["outcome"] for g in got] == ["tagged", "tagged"]
    assert got[0]["session_id"] == _sid("fr945")
    assert got[0]["candidates"] == [_sid("fr945")]
    assert got[1]["session_id"] == derive_session_id("fr945", OTHER_START)
    assert _tag(_sid("fr945")) == "race"
    assert _tag(derive_session_id("fr945", OTHER_START)) == "race"


def test_one_disagreeing_candidate_is_mismatch(persist_sessions) -> None:
    """One candidate off by more than 1 % on distance or on duration alone: ``mismatch``, the
    candidate named, nothing written."""
    persist_sessions([_run("fr945", START, 1000.0, 400.0), _run("fr945", OTHER_START, 1000.0, 400.0)])
    got = _apply(
        [
            _item(1, START_Z, 1000.0, 404.1),  # duration 1.01 % off, distance exact
            _item(2, OTHER_START, 1010.2, 400.0),  # distance 1.02 % off, duration exact
        ]
    )
    assert [g["outcome"] for g in got] == ["mismatch", "mismatch"]
    assert got[0]["session_id"] == _sid("fr945")
    assert got[0]["candidates"] == [_sid("fr945")]
    assert got[1]["session_id"] == derive_session_id("fr945", OTHER_START)
    assert _tag(_sid("fr945")) is None
    assert _tag(derive_session_id("fr945", OTHER_START)) is None


def test_pair_with_one_agreeing_tags_connects_copy(persist_sessions) -> None:
    """The measured 2024 pair: the FR945 run agrees with Connect, the ``3476`` run does not."""
    persist_sessions([_run("fr945", START, *FR945), _run("3476", START, *DEV_3476)])
    [got] = _apply([_item(1, START_Z, *CONNECT)])
    assert got["outcome"] == "tagged"
    assert got["session_id"] == _sid("fr945")
    assert sorted(got["candidates"]) == sorted([_sid("fr945"), _sid("3476")])
    assert _tag(_sid("fr945")) == "race"
    assert _tag(_sid("3476")) is None


def test_pair_with_none_agreeing_is_ambiguous(persist_sessions) -> None:
    persist_sessions([_run("fr945", START, *FR945), _run("3476", START, *DEV_3476)])
    [got] = _apply([_item(1, START_Z, 4300.0, 1600.0)])
    assert got["outcome"] == "ambiguous"
    assert got["session_id"] is None
    assert sorted(got["candidates"]) == sorted([_sid("fr945"), _sid("3476")])
    assert _tag(_sid("fr945")) is None
    assert _tag(_sid("3476")) is None


def test_pair_with_both_agreeing_is_ambiguous(persist_sessions) -> None:
    persist_sessions([_run("fr945", START, *FR945), _run("fr955", START, 3721.0, 1334.0)])
    [got] = _apply([_item(1, START_Z, *CONNECT)])
    assert got["outcome"] == "ambiguous"
    assert got["session_id"] is None
    assert sorted(got["candidates"]) == sorted([_sid("fr945"), _sid("fr955")])
    assert _tag(_sid("fr945")) is None
    assert _tag(_sid("fr955")) is None


def test_null_distance_never_agrees(persist_sessions) -> None:
    """Agreement needs all four numbers present and finite. A null on the item, a stored summary
    missing the key or holding null, NaN or infinity: the lone candidate is a ``mismatch``, never
    tagged. A pair where the only agreeing-looking run has a missing value is ``ambiguous``."""
    starts = [f"2024-05-01T10:00:{s:02d}+00:00" for s in range(10)]
    persist_sessions(
        [
            _run("fr945", starts[0]),
            _run("fr945", starts[1]),
            _run("fr945", starts[2], summary={"duration_s": FR945[1]}),
            _run("fr945", starts[3], summary={"distance_m": None, "duration_s": FR945[1]}),
            _run("fr945", starts[4], summary={"distance_m": math.nan, "duration_s": FR945[1]}),
            _run("fr945", starts[5], summary={"distance_m": math.inf, "duration_s": FR945[1]}),
            _run("fr945", starts[6], summary={"distance_m": FR945[0], "duration_s": math.nan}),
            _run("fr945", starts[7], summary={}),
            _run("fr945", starts[8], summary={"distance_m": FR945[0]}),
            _run("fr945", starts[9], summary={"distance_m": "3720.47", "duration_s": FR945[1]}),
        ]
    )
    items = [
        _item(1, starts[0], None, CONNECT[1]),
        _item(2, starts[1], CONNECT[0], None),
    ] + [_item(i + 1, starts[i], *CONNECT) for i in range(2, 10)]
    got = _apply(items)
    assert [g["outcome"] for g in got] == ["mismatch"] * 10
    for i, g in enumerate(got):
        assert g["session_id"] == derive_session_id("fr945", starts[i])
        assert _tag(derive_session_id("fr945", starts[i])) is None
    # An item with NaN or infinity of its own never agrees either.
    persist_sessions([_run("fr945", OTHER_START)])
    got = _apply([_item(20, OTHER_START, math.nan, CONNECT[1]), _item(21, OTHER_START, CONNECT[0], math.inf)])
    assert [g["outcome"] for g in got] == ["mismatch", "mismatch"]
    assert _tag(derive_session_id("fr945", OTHER_START)) is None


def test_resting_capture_on_run_profile_is_never_a_candidate(persist_sessions) -> None:
    """A resting capture stored as sport ``running`` (recorded on the Run profile), with either
    capture tag, is never a candidate: alone at the second the item is ``not_stored``; beside one
    run the run is the lone candidate; two captures plus one run at one second tag the run. The
    captures keep their tags, even when their summary agrees with the item."""
    t2 = "2024-06-01T07:00:00+00:00"
    t3 = "2024-06-02T07:00:00+00:00"
    persist_sessions(
        [
            _run("cap1", START, *FR945, tag="resting_hrv_check"),
            _run("cap1", t2, *FR945, tag="health_snapshot"),
            _run("fr945", t2, *FR945),
            _run("cap1", t3, *FR945, tag="resting_hrv_check"),
            _run("cap2", t3, *FR945, tag="health_snapshot"),
            _run("fr945", t3, *FR945),
        ]
    )
    got = _apply([_item(1, START_Z), _item(2, t2), _item(3, t3, race=False), _item(4, t3)])
    # Items 3 and 4 both reach the t3 run: the same-session rule makes both ambiguous. Run them apart.
    assert got[0] == {"connect_activity_id": 1, "outcome": "not_stored", "session_id": None, "candidates": []}
    assert got[1]["outcome"] == "tagged"
    assert got[1]["candidates"] == [derive_session_id("fr945", t2)]
    assert [g["outcome"] for g in got[2:]] == ["ambiguous", "ambiguous"]
    [got4] = _apply([_item(4, t3)])
    assert got4["outcome"] == "tagged"
    assert got4["candidates"] == [derive_session_id("fr945", t3)]
    assert _tag(derive_session_id("cap1", START)) == "resting_hrv_check"
    assert _tag(derive_session_id("cap1", t2)) == "health_snapshot"
    assert _tag(derive_session_id("cap1", t3)) == "resting_hrv_check"
    assert _tag(derive_session_id("cap2", t3)) == "health_snapshot"
    # race false never clears a capture tag either: the capture is not a candidate at all.
    [got5] = _apply([_item(5, START_Z, race=False)])
    assert got5["outcome"] == "not_stored"
    assert _tag(derive_session_id("cap1", START)) == "resting_hrv_check"


def test_two_items_applying_to_one_session_are_both_ambiguous(persist_sessions) -> None:
    """Two items that would both apply to one session: neither applied, both ``ambiguous``. An
    item that is a ``mismatch`` on a session another item applies to does not count."""
    persist_sessions([_run("fr945", START, *FR945), _run("fr945", OTHER_START, *FR945)])
    got = _apply(
        [
            _item(1, START_Z, *CONNECT, race=True),
            _item(2, START, *CONNECT, race=False),
            _item(3, OTHER_START, *CONNECT, race=True),
            _item(4, OTHER_START, 5018.28, 1844.175, race=False),
        ]
    )
    assert [g["outcome"] for g in got] == ["ambiguous", "ambiguous", "tagged", "mismatch"]
    assert got[0]["session_id"] is None and got[1]["session_id"] is None
    assert got[0]["candidates"] == [_sid("fr945")]
    assert got[1]["candidates"] == [_sid("fr945")]
    assert _tag(_sid("fr945")) is None
    assert _tag(derive_session_id("fr945", OTHER_START)) == "race"
    # Two identical race items on one session are still two appliers.
    [a, b] = _apply([_item(5, START_Z), _item(6, START_Z)])
    assert (a["outcome"], b["outcome"]) == ("ambiguous", "ambiguous")
    assert _tag(_sid("fr945")) is None


def test_race_false_clears_only_a_race_tag(persist_sessions) -> None:
    starts = ["2024-07-05T01:20:12+00:00", "2024-08-03T15:39:01+00:00", "2024-09-01T06:00:00+00:00"]
    persist_sessions(
        [
            _run("fr945", starts[0], tag="race"),
            _run("fr945", starts[1]),
            _run("fr945", starts[2], tag="long_run"),
        ]
    )
    got = _apply([_item(i + 1, s, race=False) for i, s in enumerate(starts)])
    assert [g["outcome"] for g in got] == ["cleared", "unchanged", "other_tag"]
    assert [_tag(derive_session_id("fr945", s)) for s in starts] == [None, None, "long_run"]


def test_other_tag_is_left_untouched(persist_sessions) -> None:
    """A tag that is neither null, ``race`` nor a capture tag: a candidate, reported ``other_tag``
    with its session, never rewritten by race true or race false."""
    persist_sessions([_run("fr945", START, tag="tempo"), _run("fr945", OTHER_START, tag="tempo")])
    got = _apply([_item(1, START_Z, race=True), _item(2, OTHER_START, race=False)])
    assert [g["outcome"] for g in got] == ["other_tag", "other_tag"]
    assert got[0]["session_id"] == _sid("fr945")
    assert got[1]["session_id"] == derive_session_id("fr945", OTHER_START)
    assert _tag(_sid("fr945")) == "tempo"
    assert _tag(derive_session_id("fr945", OTHER_START)) == "tempo"


def test_resend_turns_tagged_into_unchanged(persist_sessions) -> None:
    starts = [f"2024-10-0{d}T08:00:00+00:00" for d in range(1, 6)]
    persist_sessions(
        [
            _run("fr945", starts[0]),
            _run("fr945", starts[1], tag="race"),
            _run("3476", starts[2], *DEV_3476),
            _run("fr945", starts[3], tag="tempo"),
        ]
    )
    items = [
        _item(1, starts[0], race=True),
        _item(2, starts[1], race=False),
        _item(3, starts[2], race=True),
        _item(4, starts[3], race=True),
        _item(5, starts[4], race=True),
    ]
    first = _apply(items)
    second = _apply(items)
    assert [g["outcome"] for g in first] == ["tagged", "cleared", "mismatch", "other_tag", "not_stored"]
    assert [g["outcome"] for g in second] == ["unchanged", "unchanged", "mismatch", "other_tag", "not_stored"]
    for a, b in zip(first, second, strict=True):
        assert (a["connect_activity_id"], a["session_id"], a["candidates"]) == (
            b["connect_activity_id"],
            b["session_id"],
            b["candidates"],
        )


def _session_snapshot(session_id: str) -> dict:
    """Every row of every table keyed by ``session_id`` that hangs off this session."""
    conn = db.get_connection()
    try:
        tables = [
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
            if any(c["name"] == "session_id" for c in conn.execute(f"PRAGMA table_info({r['name']})"))
        ]
        snap = {}
        for table in tables:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE session_id = ? ORDER BY rowid", (session_id,)
            ).fetchall()
            snap[table] = [dict(r) for r in rows]
        return snap
    finally:
        conn.close()


def test_only_activity_tag_changes() -> None:
    """Upload ``sample_run.fit``, tag it from its own stored summary, and compare every other
    column, the records, the saved load and the served features before and after."""
    with TestClient(app) as client:
        post = client.post(
            "/sessions", files={"file": ("sample_run.fit", (FIXTURES / "sample_run.fit").read_bytes())}
        )
        assert post.status_code == 201, post.text
        session_id = post.json()["session_id"]
        detail = client.get(f"/sessions/{session_id}").json()
        load_before = client.get(f"/sessions/{session_id}/load")
        features_before = client.get(f"/sessions/{session_id}/features")
        assert load_before.status_code == 200, load_before.text
        assert features_before.status_code == 200, features_before.text
        snap_before = _session_snapshot(session_id)
        assert snap_before["sessions"][0]["activity_tag"] is None
        assert snap_before["records"], "the fixture's records should be stored"

        summary = detail["summary"]
        start = detail["start_time"].replace("+00:00", "Z")
        [got] = _apply([_item(42, start, summary["distance_m"], summary["duration_s"])])
        assert got["outcome"] == "tagged"
        assert got["session_id"] == session_id

        load_after = client.get(f"/sessions/{session_id}/load")
        features_after = client.get(f"/sessions/{session_id}/features")
        snap_after = _session_snapshot(session_id)
        detail_after = client.get(f"/sessions/{session_id}").json()

    assert snap_after["sessions"][0].pop("activity_tag") == "race"
    snap_before["sessions"][0].pop("activity_tag")
    assert snap_after == snap_before
    assert load_after.json() == load_before.json()
    assert features_after.json() == features_before.json()
    assert detail_after.pop("activity_tag") == "race"
    detail.pop("activity_tag")
    assert detail_after == detail


# --- the transaction, the empty request and the pure module ----------------------------------------


def test_a_fault_mid_apply_rolls_every_write_back(persist_sessions, monkeypatch) -> None:
    persist_sessions([_run("fr945", START), _run("fr945", OTHER_START)])
    real = db._write_race_tag
    calls = []

    def write_then_fail(conn, session_id, tag):
        calls.append(session_id)
        real(conn, session_id, tag)
        if len(calls) == 2:
            raise RuntimeError("fault after the second write")

    monkeypatch.setattr(db, "_write_race_tag", write_then_fail)
    with pytest.raises(RuntimeError):
        _apply([_item(1, START_Z), _item(2, OTHER_START)])
    assert len(calls) == 2
    assert _tag(_sid("fr945")) is None
    assert _tag(derive_session_id("fr945", OTHER_START)) is None
    # The connection is usable again: the lock was released by the rollback.
    monkeypatch.setattr(db, "_write_race_tag", real)
    assert [g["outcome"] for g in _apply([_item(1, START_Z)])] == ["tagged"]


def test_no_items_reads_and_writes_nothing(persist_sessions) -> None:
    persist_sessions([_run("fr945", START)])
    assert _apply([]) == []
    assert _tag(_sid("fr945")) is None


def test_outcomes_come_back_in_request_order(persist_sessions) -> None:
    persist_sessions([_run("fr945", START), _run("3476", OTHER_START, *DEV_3476)])
    got = _apply([_item(9, "2020-01-01T00:00:00Z"), _item(3, OTHER_START), _item(5, START_Z)])
    assert [(g["connect_activity_id"], g["outcome"]) for g in got] == [
        (9, "not_stored"),
        (3, "mismatch"),
        (5, "tagged"),
    ]


def test_the_rule_module_imports_no_db_config_or_fastapi() -> None:
    tree = ast.parse(Path(race_tags.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    forbidden = {
        n
        for n in names
        if n.split(".")[0] in {"fastapi", "sqlite3"}
        or n.endswith((".db", ".config"))
        or n in {"runcoach_api.db", "runcoach_api.config"}
    }
    assert not forbidden, forbidden


def test_capture_tags_are_the_load_rule_constant() -> None:
    from runcoach_api.metrics.session_load import DECLARED_CAPTURE_TAGS

    assert race_tags.RESTING_CAPTURE_TAGS is DECLARED_CAPTURE_TAGS


def test_stored_nan_round_trips_as_nan(persist_sessions) -> None:
    """The NaN rows above rely on ``json`` writing and reading ``NaN``; pin that here."""
    persist_sessions([_run("fr945", START, summary={"distance_m": math.nan, "duration_s": 1.0})])
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT summary FROM sessions WHERE session_id = ?", (_sid("fr945"),)).fetchone()
    finally:
        conn.close()
    assert math.isnan(json.loads(row["summary"])["distance_m"])


# --- the adversarial probe table: degenerate values the rule reads ---------------------------------


def test_zero_and_negative_values_follow_the_formula(persist_sessions) -> None:
    """``|s - d| <= 0.01 d`` taken literally. Zero on both sides agrees (a run with no distance
    on the watch and in Connect, as a treadmill run without a foot pod reads, is still confirmed
    by its start second and duration). Zero on one side only, and any negative item value, never
    agrees: the bound is then 0 or negative. The route screens negatives before this rule."""
    starts = [f"2024-11-01T09:00:{s:02d}+00:00" for s in range(5)]
    persist_sessions(
        [
            _run("fr945", starts[0], 0.0, 1800.0),
            _run("fr945", starts[1], 0.0, 1800.0),
            _run("fr945", starts[2], 5000.0, 1800.0),
            _run("fr945", starts[3], -5000.0, 1800.0),
            _run("fr945", starts[4], 5000.0, 0.0),
        ]
    )
    got = _apply(
        [
            _item(1, starts[0], 0.0, 1800.0),
            _item(2, starts[1], 10.0, 1800.0),
            _item(3, starts[2], 0.0, 1800.0),
            _item(4, starts[3], -5000.0, 1800.0),
            _item(5, starts[4], 5000.0, 0.0),
        ]
    )
    assert [g["outcome"] for g in got] == ["tagged", "mismatch", "mismatch", "mismatch", "tagged"]


def test_a_start_time_not_in_the_stored_form_matches_nothing(persist_sessions) -> None:
    """Only ``Z`` is normalised. The same instant in another zone, or with a fraction of a second,
    is not the stored string, so it has no candidate (the route refuses both with a 422)."""
    persist_sessions([_run("fr945", START)])
    got = _apply(
        [
            _item(1, "2024-03-02T17:43:13+01:00"),
            _item(2, "2024-03-02T16:43:13.000Z"),
            _item(3, "2024-03-02 16:43:13+00:00"),
        ]
    )
    assert [g["outcome"] for g in got] == ["not_stored"] * 3
    assert _tag(_sid("fr945")) is None


def test_a_bool_or_string_number_never_agrees(persist_sessions) -> None:
    """``True`` is an ``int`` in Python; it is not a distance."""
    persist_sessions(
        [
            _run("fr945", START, summary={"distance_m": True, "duration_s": True}),
            _run("fr945", OTHER_START, 1.0, 1.0),
        ]
    )
    got = _apply([_item(1, START_Z, 1.0, 1.0), _item(2, OTHER_START, True, True)])
    assert [g["outcome"] for g in got] == ["mismatch", "mismatch"]


def test_an_int_too_large_for_a_float_never_agrees_never_raises() -> None:
    """``json.loads`` returns an int beyond float range as an int; ``float()`` of it raises
    ``OverflowError``, which the screen reads as not a number."""
    huge = 10**400
    item = {"distance_m": 5000.0, "duration_s": 1800.0}
    assert race_tags.agrees(item, {"distance_m": 5000.0, "duration_s": 1800.0})
    assert not race_tags.agrees(item, {"distance_m": huge, "duration_s": 1800.0})
    assert not race_tags.agrees({"distance_m": 5000.0, "duration_s": huge}, dict(item))


def test_a_null_summary_never_agrees(persist_sessions) -> None:
    session = _run("fr945", START)
    session.summary = None
    persist_sessions([session])
    [got] = _apply([_item(1)])
    assert got["outcome"] == "mismatch"
