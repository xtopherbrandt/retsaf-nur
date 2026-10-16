"""F025 AC5 (the API half) and AC7 (the candidates half): ``POST /laps/candidates``.

Every test drives the real app through ``TestClient`` against the isolated scratch store the
autouse ``isolated_data_dir`` fixture installs; sessions are written with ``persist_sessions``
(``db.persist``, after ``init_schema``). The operation is read-only: it matches each listed Connect
run to the stored running sessions at its start second, with ``race_tags``' start-second lookup and
without F023's 1 % distance and duration agreement (the attach endpoint's identity check on the
file proves the match, F025 reference section 5).

The adversarial probe table (what the decision reads: the item's ``start_time``, and each stored
row's ``sport``, ``activity_tag``, ``start_time`` and ``laps_read_at``).

| input | result | why |
|---|---|---|
| one running session at the item's second | ``matched``, its id and ``laps_read_at`` | the one stored run |
| none at that second (one a second later) | ``not_stored`` | the match is the exact second, as F023's |
| two running sessions at that second (two devices) | ``ambiguous``, null id | the file cannot be chosen by the start alone |
| a resting capture (either declared tag) at that second, alone | ``not_stored`` | a capture is never a candidate, as F023's matching |
| a resting capture beside one run at that second | ``matched`` the run | the capture does not make it ambiguous |
| a cycling session at that second | ``not_stored`` | sport ``running`` only |
| a stored summary 5 % off Connect's distance, or no summary | ``matched`` | no agreement rule (user ruling) |
| an item that carries ``distance_m`` | 422 | items are ``{connect_activity_id, start_time}`` only |
| ``start_time`` with ``Z`` and with ``+00:00`` | the same answer | ``Z`` is normalised to the stored form |
| ``start_time`` in another zone, with fractions, unparseable | 422 | ``RACE_TAG_START_PATTERN`` and ``fromisoformat`` |
| 0 or 501 items, a repeated id, an extra key | 422 | as ``setRaceTags``; 500 is a 200 |
| two items at one second with one run there | both ``matched`` the run | nothing is written, so no item excludes another |
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import lap_candidates, race_tags
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.models import Session

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
SUPPORT = Path(__file__).parent / "support"
PATH = "/laps/candidates"
OUTCOMES = ("matched", "not_stored", "ambiguous")

START = "2024-03-02T16:43:13+00:00"
START_Z = "2024-03-02T16:43:13Z"
SECOND_LATER_Z = "2024-03-02T16:43:14Z"
TWO_DEVICES = "2024-05-01T06:00:00+00:00"
TWO_DEVICES_Z = "2024-05-01T06:00:00Z"
FR945 = (3720.47, 1333.772)
READ_AT = "2026-10-16T08:00:00.123456+00:00"


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


contract_walk = _load_support("contract_walk")


def _run(
    device: str,
    start: str = START,
    *,
    sport: str = "running",
    tag: str | None = None,
    summary: dict | None = None,
) -> Session:
    return Session(
        session_id=derive_session_id(device, start),
        sport=sport,
        source_vendor="garmin",
        start_time=start,
        source_device=device,
        activity_tag=tag,
        summary={"distance_m": FR945[0], "duration_s": FR945[1]} if summary is None else summary,
    )


def _sid(device: str, start: str = START) -> str:
    return derive_session_id(device, start)


def _item(activity_id: int = 1, start: object = START_Z) -> dict:
    return {"connect_activity_id": activity_id, "start_time": start}


def _post(body: object):
    """POST a body as raw JSON text, as the CLI sends it."""
    return TestClient(app).post(PATH, content=json.dumps(body), headers={"content-type": "application/json"})


def _answers(*items: dict) -> list[dict]:
    resp = _post({"items": list(items)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"items"}, body
    return body["items"]


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    conn = db_module.get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _set_read_at(session_id: str, value: str) -> None:
    with _connection() as conn:
        conn.execute("UPDATE sessions SET laps_read_at = ? WHERE session_id = ?", (value, session_id))
        conn.commit()


def _store_state() -> tuple[dict[str, int], dict[str, bytes]]:
    """Every table's row count, and the bytes of every file in the data dir."""
    with _connection() as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    data_dir = db_module._load_config_cached().data_dir
    files = {p.name: p.read_bytes() for p in sorted(data_dir.iterdir()) if p.is_file()}
    return counts, files


# --- the decision -----------------------------------------------------------------------------------


def test_candidates_matched_not_stored_and_ambiguous(persist_sessions) -> None:
    persist_sessions([_run("fr945"), _run("fr945", TWO_DEVICES), _run("hrm-pro", TWO_DEVICES)])

    answers = _answers(_item(1, START_Z), _item(2, SECOND_LATER_Z), _item(3, TWO_DEVICES_Z), _item(4, START))

    assert answers == [
        {"connect_activity_id": 1, "outcome": "matched", "session_id": _sid("fr945"), "laps_read_at": None},
        {"connect_activity_id": 2, "outcome": "not_stored", "session_id": None, "laps_read_at": None},
        {"connect_activity_id": 3, "outcome": "ambiguous", "session_id": None, "laps_read_at": None},
        {"connect_activity_id": 4, "outcome": "matched", "session_id": _sid("fr945"), "laps_read_at": None},
    ]


def test_items_answer_in_request_order_and_five_hundred_are_accepted(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    items = [
        _item(i, START_Z if i == 250 else f"2023-01-01T00:{i // 60:02d}:{i % 60:02d}Z") for i in range(500)
    ]
    answers = _answers(*items)
    assert [a["connect_activity_id"] for a in answers] == list(range(500))
    assert [a["outcome"] for a in answers].count("matched") == 1
    assert answers[250]["session_id"] == _sid("fr945")


def test_candidates_write_nothing(persist_sessions) -> None:
    persist_sessions([_run("fr945"), _run("fr945", TWO_DEVICES), _run("hrm-pro", TWO_DEVICES)])
    _set_read_at(_sid("fr945"), READ_AT)
    before = _store_state()
    assert before[0]["sessions"] == 3

    answers = _answers(_item(1, START_Z), _item(2, SECOND_LATER_Z), _item(3, TWO_DEVICES_Z))
    assert [a["outcome"] for a in answers] == ["matched", "not_stored", "ambiguous"]
    for refused in ({"items": []}, {"items": [_item(1, "2024-03-02T16:43:13+01:00")]}):
        assert _post(refused).status_code == 422

    after = _store_state()
    assert after[0] == before[0]
    assert after[1].keys() == before[1].keys()
    for name in before[1]:
        assert after[1][name] == before[1][name], name


def test_a_5_percent_distance_difference_is_still_matched(persist_sessions) -> None:
    """A run edited in Connect still gets its laps: the stored summary is never compared."""
    persist_sessions(
        [
            _run("fr945", summary={"distance_m": FR945[0] * 1.05, "duration_s": FR945[1] * 0.9}),
            _run("fr945", TWO_DEVICES, summary={}),
        ]
    )
    answers = _answers(_item(1, START_Z), _item(2, TWO_DEVICES_Z))
    assert [(a["outcome"], a["session_id"]) for a in answers] == [
        ("matched", _sid("fr945")),
        ("matched", _sid("fr945", TWO_DEVICES)),
    ]

    # The same stored row is a race-tag mismatch under F023's 1 % rule, so the two decisions differ
    # on agreement and on nothing else.
    row = {
        "session_id": _sid("fr945"),
        "start_time": START,
        "activity_tag": None,
        "summary": {"distance_m": FR945[0] * 1.05, "duration_s": FR945[1]},
        "laps_read_at": None,
    }
    tag_item = {
        "connect_activity_id": 1,
        "start_time": START_Z,
        "distance_m": FR945[0],
        "duration_s": FR945[1],
        "race": True,
    }
    assert race_tags.decide([tag_item], [row])[0].outcome == "mismatch"
    assert lap_candidates.decide([tag_item], [row])[0]["outcome"] == "matched"

    # An item that carries a distance is refused: the operation takes the start second only.
    assert _post({"items": [{**_item(1), "distance_m": FR945[0]}]}).status_code == 422


def test_resting_captures_are_never_candidates(persist_sessions) -> None:
    capture_a, capture_b = race_tags.RESTING_CAPTURE_TAGS
    persist_sessions(
        [
            _run("fr945", tag=capture_a),
            _run("fr945", TWO_DEVICES, tag=capture_b),
            _run("hrm-pro", TWO_DEVICES),
            _run("edge", "2024-06-01T07:00:00+00:00", sport="cycling"),
            _run("fr945", "2024-07-01T07:00:00+00:00", tag="race"),
        ]
    )
    answers = _answers(
        _item(1, START_Z),
        _item(2, TWO_DEVICES_Z),
        _item(3, "2024-06-01T07:00:00Z"),
        _item(4, "2024-07-01T07:00:00Z"),
    )
    assert [(a["outcome"], a["session_id"]) for a in answers] == [
        ("not_stored", None),
        ("matched", _sid("hrm-pro", TWO_DEVICES)),
        ("not_stored", None),
        ("matched", _sid("fr945", "2024-07-01T07:00:00+00:00")),
    ]


def test_candidates_report_laps_read_at(persist_sessions) -> None:
    persist_sessions([_run("fr945"), _run("fr945", TWO_DEVICES)])
    _set_read_at(_sid("fr945"), READ_AT)
    answers = _answers(_item(1, START_Z), _item(2, TWO_DEVICES_Z))
    assert [(a["outcome"], a["laps_read_at"]) for a in answers] == [("matched", READ_AT), ("matched", None)]


def test_two_items_at_one_second_both_match_the_one_run(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    answers = _answers(_item(1, START_Z), _item(2, START))
    assert [(a["outcome"], a["session_id"]) for a in answers] == [("matched", _sid("fr945"))] * 2


def test_race_tags_and_candidates_share_one_start_second_lookup(persist_sessions, monkeypatch) -> None:
    """Perturb the one lookup and both operations' decisions change with it."""
    persist_sessions([_run("fr945")])
    tag_item = {
        "connect_activity_id": 1,
        "start_time": START_Z,
        "distance_m": FR945[0],
        "duration_s": FR945[1],
        "race": True,
    }

    def _tag_outcome() -> str:
        resp = TestClient(app).put("/race-tags", json={"items": [{**tag_item, "race": False}]})
        assert resp.status_code == 200, resp.text
        return resp.json()["items"][0]["outcome"]

    assert _tag_outcome() == "unchanged"
    assert _answers(_item(1))[0]["outcome"] == "matched"

    # The lookup now finds nothing at any second: both read every item as not stored.
    monkeypatch.setattr(race_tags, "at_start_second", lambda index, start: [])
    assert _tag_outcome() == "not_stored"
    assert _answers(_item(1))[0]["outcome"] == "not_stored"

    # The index now holds no row: both follow it.
    monkeypatch.undo()
    monkeypatch.setattr(race_tags, "index_by_start_second", lambda rows: {})
    assert _tag_outcome() == "not_stored"
    assert _answers(_item(1))[0]["outcome"] == "not_stored"

    monkeypatch.undo()
    assert _tag_outcome() == "unchanged"
    assert _answers(_item(1))[0]["outcome"] == "matched"


# --- the screen -------------------------------------------------------------------------------------


def test_the_body_is_screened_as_set_race_tags(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    hostile = [
        {"items": []},
        {"items": [_item(i, START_Z) for i in range(501)]},
        {},
        {"items": [_item(1), _item(1, TWO_DEVICES_Z)]},
        {"items": [_item(1)], "extra": 1},
        {"items": [{**_item(1), "race": True}]},
        {"items": [{"connect_activity_id": 1}]},
        {"items": [{"start_time": START_Z}]},
        {"items": [_item("1")]},
        {"items": [_item(1.0)]},
        {"items": [_item(True)]},
    ]
    for start in (
        "2024-03-02T16:43:13+01:00",
        "2024-03-02T16:43:13",
        "2024-03-02T16:43:13.000Z",
        "2024-02-30T16:43:13Z",
        "2024-03-02 16:43:13Z",
        "2024-03-02T16:43:13z",
        "",
        1709397793,
        None,
    ):
        hostile.append({"items": [_item(1, start)]})
    for body in hostile:
        assert _post(body).status_code == 422, body


# --- the contract -----------------------------------------------------------------------------------


def test_served_candidates_schema_matches_the_contract() -> None:
    """``check_drift.py`` compares paths, statuses and required request fields, not JSON bodies;
    this walks the request and response components property by property and compares the
    constraints the walker skips."""
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    built = app.openapi()
    methods = {"get", "post", "put", "patch", "delete"}
    assert (
        {m for m in built["paths"][PATH] if m in methods}
        == {m for m in contract["paths"][PATH] if m in methods}
        == {"post"}
    )
    op, built_op = contract["paths"][PATH]["post"], built["paths"][PATH]["post"]
    assert op["operationId"] == built_op["operationId"] == "matchLapCandidates"
    assert op["tags"] == built_op["tags"] == ["Sessions"]
    assert op["x-readiness"] == "implemented"
    assert set(op["responses"]) == set(built_op["responses"]) == {"200", "422"}
    for operation in (op, built_op):
        assert operation["requestBody"]["required"] is True
        assert operation["requestBody"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/LapCandidatesRequest"
        }
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/LapCandidatesResult"
        }

    seen, compared = contract_walk.walk(contract, built, ["LapCandidatesRequest", "LapCandidatesResult"])
    print(f"  {len(compared)} properties over {sorted(seen)}")
    assert seen == {
        "LapCandidatesRequest",
        "LapCandidateItem",
        "LapCandidatesResult",
        "LapCandidateItemResult",
    }, seen

    ours, theirs = contract["components"]["schemas"], built["components"]["schemas"]
    for name in ("LapCandidatesRequest", "LapCandidateItem"):
        assert ours[name]["additionalProperties"] is False is theirs[name]["additionalProperties"], name
    for key in ("minItems", "maxItems"):
        assert (
            ours["LapCandidatesRequest"]["properties"]["items"][key]
            == theirs["LapCandidatesRequest"]["properties"]["items"][key]
            == ours["RaceTagsUpdate"]["properties"]["items"][key]
        ), key
    item, built_item = ours["LapCandidateItem"]["properties"], theirs["LapCandidateItem"]["properties"]
    assert (
        item["start_time"]["pattern"]
        == built_item["start_time"]["pattern"]
        == ours["RaceTagItem"]["properties"]["start_time"]["pattern"]
    )
    assert set(ours["LapCandidateItem"]["required"]) == {"connect_activity_id", "start_time"} == set(item)
    result = ours["LapCandidateItemResult"]
    assert (
        set(result["required"])
        == {"connect_activity_id", "outcome", "session_id", "laps_read_at"}
        == set(result["properties"])
    )
    assert sorted(result["properties"]["outcome"]["enum"]) == sorted(OUTCOMES)
    assert sorted(theirs["LapCandidateItemResult"]["properties"]["outcome"]["enum"]) == sorted(OUTCOMES)
