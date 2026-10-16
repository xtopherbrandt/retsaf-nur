"""F026 AC1 (the API half): ``PUT /race-tags`` carries a ``threshold_workout`` tag.

``TRANSITIONS`` below is the F026 reference's section 2 table, copied cell by cell from the
reference (``spec/references/F026-threshold-pace-from-workouts.md``, "Transitions, per matched
running session") before ``race_tags._transition`` was changed, not from the implementation: the
row is the stored tag, the column the wanted one. ``decide`` is driven with one agreeing
candidate per cell, so each cell isolates the transition and nothing else.

The request rule, from the same section: ``tag`` (``race``, ``threshold_workout`` or null) is
optional and ``race`` becomes optional; exactly one of the two is required. ``race`` alone reads as
before (true means ``race``, false means null); both is a 422; neither is a 422. "Exactly one" is
judged by which keys the item carries, never by ``is None``: an explicit ``"race": null`` is still a
422 (``race`` is a strict boolean) and an explicit ``"tag": null`` means clear.

The adversarial table (the inputs the request rule reads are which keys are present and their
values):

| input | result | why |
|---|---|---|
| ``race`` and ``tag`` both present (any values, null included) | 422 | one field, one meaning |
| neither ``race`` nor ``tag`` | 422 | nothing says what to store |
| ``"race": null`` alone | 422 | a strict boolean; null is not "absent" |
| ``"tag": null`` alone | 200, clears | the documented clear |
| ``tag`` ``"Race"``, ``"training"``, ``""``, ``1``, ``true``, ``"resting_hrv_check"`` | 422 | the two tags only; Connect's ``training`` key is the CLI's to translate, and a capture tag is never written through this route |
| a stored tag other than null, ``race``, ``threshold_workout`` (``""``, ``Race``, ``THRESHOLD_WORKOUT``, ``interval_session``) | ``other_tag``, not written | the table's last row; tags compare exactly |
| a resting capture at the item's second | never a candidate | F023's exclusion, unchanged for the new tag |
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import race_tags
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.models import Session
from runcoach_api.schemas import RaceTagItem

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
PATH = "/race-tags"
START = "2024-03-02T16:43:13+00:00"
START_Z = "2024-03-02T16:43:13Z"
FR945 = (3720.47, 1333.772)

RACE, WORKOUT, OTHER = "race", "threshold_workout", "interval_session"

# The reference's table: (stored, wanted) -> outcome. Written from the reference, row by row.
TRANSITIONS = {
    (None, None): "unchanged",
    (None, RACE): "tagged",
    (None, WORKOUT): "tagged",
    (RACE, None): "cleared",
    (RACE, RACE): "unchanged",
    (RACE, WORKOUT): "retagged",
    (WORKOUT, None): "cleared",
    (WORKOUT, RACE): "retagged",
    (WORKOUT, WORKOUT): "unchanged",
    (OTHER, None): "other_tag",
    (OTHER, RACE): "other_tag",
    (OTHER, WORKOUT): "other_tag",
}
WRITTEN = {"tagged", "cleared", "retagged"}


def _row(tag: str | None, start: str = START, device: str = "fr945") -> dict:
    return {
        "session_id": derive_session_id(device, start),
        "start_time": start,
        "activity_tag": tag,
        "summary": {"distance_m": FR945[0], "duration_s": FR945[1]},
    }


def _base(activity_id: int = 1, start: str = START_Z) -> dict:
    return {"connect_activity_id": activity_id, "start_time": start, "distance_m": FR945[0], "duration_s": FR945[1]}


def _tag_item(tag: str | None, activity_id: int = 1, start: str = START_Z) -> dict:
    return {**_base(activity_id, start), "tag": tag}


def _race_item(race: object, activity_id: int = 1, start: str = START_Z) -> dict:
    return {**_base(activity_id, start), "race": race}


def _run(device: str, start: str = START, *, tag: str | None = None, sport: str = "running") -> Session:
    return Session(
        session_id=derive_session_id(device, start),
        sport=sport,
        source_vendor="garmin",
        start_time=start,
        source_device=device,
        activity_tag=tag,
        summary={"distance_m": FR945[0], "duration_s": FR945[1]},
    )


def _sid(device: str = "fr945", start: str = START) -> str:
    return derive_session_id(device, start)


def _put(body: object):
    return TestClient(app).put(PATH, content=json.dumps(body), headers={"content-type": "application/json"})


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    conn = db_module.get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _tag(session_id: str) -> str | None:
    with _connection() as conn:
        row = conn.execute("SELECT activity_tag FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    assert row is not None, session_id
    return row["activity_tag"]


def _all_tags() -> list[tuple]:
    with _connection() as conn:
        return sorted(tuple(r) for r in conn.execute("SELECT session_id, activity_tag FROM sessions"))


# --- the table ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("stored", "wanted"), sorted(TRANSITIONS, key=repr), ids=lambda v: repr(v))
def test_every_cell_of_the_transition_table(stored, wanted) -> None:
    expected = TRANSITIONS[(stored, wanted)]
    [decision] = race_tags.decide([_tag_item(wanted)], [_row(stored)])
    print(f"  stored {stored!r} wanted {wanted!r} -> {decision.outcome} write={decision.write}")
    assert decision.outcome == expected
    assert decision.session_id == _sid()
    assert decision.write is (expected in WRITTEN)
    if decision.write:
        assert decision.new_tag == wanted
    assert decision.stored_tag == stored
    assert decision.wanted_tag == wanted
    # The response shape is unchanged: the stored and wanted tags are not served per item.
    assert decision.as_dict() == {
        "connect_activity_id": 1,
        "outcome": expected,
        "session_id": _sid(),
        "candidates": [_sid()],
    }


def test_the_table_covers_every_stored_and_wanted_pair() -> None:
    assert len(TRANSITIONS) == 12
    assert {s for s, _ in TRANSITIONS} == {None, RACE, WORKOUT, OTHER}
    assert {w for _, w in TRANSITIONS} == {None, RACE, WORKOUT}
    assert "retagged" in race_tags.OUTCOMES
    assert set(TRANSITIONS.values()) <= set(race_tags.OUTCOMES)


@pytest.mark.parametrize("stored", ["", "Race", "THRESHOLD_WORKOUT", "training", "resting"])
@pytest.mark.parametrize("wanted", [None, RACE, WORKOUT])
def test_any_other_stored_tag_is_left_as_is(stored, wanted) -> None:
    [decision] = race_tags.decide([_tag_item(wanted)], [_row(stored)])
    assert (decision.outcome, decision.write) == ("other_tag", False)


def test_wanted_tag_reads_tag_first_then_race() -> None:
    assert race_tags.wanted_tag({"tag": WORKOUT}) == WORKOUT
    assert race_tags.wanted_tag({"tag": None}) is None
    assert race_tags.wanted_tag({"tag": RACE}) == RACE
    assert race_tags.wanted_tag({"race": True}) == RACE
    assert race_tags.wanted_tag({"race": False}) is None
    item = RaceTagItem.model_validate(_race_item(True))
    assert item.wanted() == RACE
    assert RaceTagItem.model_validate(_race_item(False)).wanted() is None
    assert RaceTagItem.model_validate(_tag_item(None)).wanted() is None
    assert RaceTagItem.model_validate(_tag_item(WORKOUT)).wanted() == WORKOUT


# --- through the route ---------------------------------------------------------------------------------


def test_race_to_threshold_workout_is_retagged_and_written(persist_sessions) -> None:
    other = "2024-03-02T16:43:20+00:00"
    persist_sessions([_run("fr945", tag=RACE), _run("fr945", other, tag=WORKOUT)])
    response = _put({"items": [_tag_item(WORKOUT, 1), _tag_item(RACE, 2, other)]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == [
        {"connect_activity_id": 1, "outcome": "retagged", "session_id": _sid(), "candidates": [_sid()]},
        {
            "connect_activity_id": 2,
            "outcome": "retagged",
            "session_id": _sid("fr945", other),
            "candidates": [_sid("fr945", other)],
        },
    ]
    assert body["counts"]["retagged"] == 2
    assert _tag(_sid()) == WORKOUT
    assert _tag(_sid("fr945", other)) == RACE
    # Re-sending is idempotent.
    again = _put({"items": [_tag_item(WORKOUT, 1), _tag_item(RACE, 2, other)]}).json()
    assert [i["outcome"] for i in again["items"]] == ["unchanged", "unchanged"]
    assert _tag(_sid()) == WORKOUT


def test_a_race_only_item_reads_as_before(persist_sessions) -> None:
    """``race`` true means ``race`` and false means null: an untagged run is tagged, a race is
    cleared. A route that dumped the sparse item would send ``tag: None`` and read every one as a
    clear: the untagged run would be ``unchanged`` and the race-true race ``cleared``."""
    starts = [f"2024-11-01T09:00:{s:02d}+00:00" for s in range(4)]
    persist_sessions(
        [
            _run("fr945", starts[0]),
            _run("fr945", starts[1], tag=RACE),
            _run("fr945", starts[2]),
            _run("fr945", starts[3], tag=RACE),
        ]
    )
    items = [
        _race_item(True, 1, starts[0]),
        _race_item(False, 2, starts[1]),
        _race_item(False, 3, starts[2]),
        _race_item(True, 4, starts[3]),
    ]
    response = _put({"items": items})
    assert response.status_code == 200, response.text
    assert [i["outcome"] for i in response.json()["items"]] == ["tagged", "cleared", "unchanged", "unchanged"]
    assert [_tag(_sid("fr945", s)) for s in starts] == [RACE, None, None, RACE]


def test_both_race_and_tag_is_422(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    before = _all_tags()
    for extra in ({"race": True, "tag": RACE}, {"race": False, "tag": None}, {"race": True, "tag": WORKOUT}):
        item = {**_base(2, "2024-03-02T16:43:14Z"), **extra}
        response = _put({"items": [_race_item(True), item]})
        assert response.status_code == 422, (extra, response.text)
        assert "exactly one of race and tag" in response.text, response.text
    assert _all_tags() == before


def test_neither_race_nor_tag_is_422(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    before = _all_tags()
    response = _put({"items": [_race_item(True), _base(2, "2024-03-02T16:43:14Z")]})
    assert response.status_code == 422, response.text
    assert _all_tags() == before


def test_an_explicit_race_null_is_still_422(persist_sessions) -> None:
    """``"race": null`` is a 422, while ``"tag": null`` is the documented clear."""
    persist_sessions([_run("fr945", tag=WORKOUT)])
    response = _put({"items": [_race_item(None)]})
    assert response.status_code == 422, response.text
    assert _tag(_sid()) == WORKOUT
    response = _put({"items": [_tag_item(None)]})
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["outcome"] == "cleared"
    assert _tag(_sid()) is None


@pytest.mark.parametrize("tag", ["Race", "training", "", 1, True, "resting_hrv_check", "health_snapshot"])
def test_a_tag_outside_the_two_is_422(persist_sessions, tag) -> None:
    persist_sessions([_run("fr945")])
    before = _all_tags()
    response = _put({"items": [_tag_item(tag)]})
    assert response.status_code == 422, response.text
    assert _all_tags() == before


def test_by_tag_counts(persist_sessions) -> None:
    """``by_tag`` keys ``tagged`` and ``retagged`` by the wanted tag and ``cleared`` by the stored
    one. Each of the six cells has a different count, so a swapped key reads wrong."""
    plan = [
        # (stored, wanted, how many)
        (None, RACE, 1),
        (RACE, None, 2),
        (WORKOUT, RACE, 3),
        (None, WORKOUT, 4),
        (WORKOUT, None, 5),
        (RACE, WORKOUT, 6),
        (RACE, RACE, 1),
        (OTHER, WORKOUT, 1),
    ]
    sessions, items, n = [], [], 0
    for stored, wanted, how_many in plan:
        for _ in range(how_many):
            start = f"2024-11-05T09:{n // 60:02d}:{n % 60:02d}+00:00"
            sessions.append(_run("fr945", start, tag=stored))
            items.append(_tag_item(wanted, n + 1, start))
            n += 1
    items.append(_tag_item(WORKOUT, 999, "2024-12-01T00:00:00Z"))  # nothing stored
    persist_sessions(sessions)
    response = _put({"items": items})
    assert response.status_code == 200, response.text
    counts = response.json()["counts"]
    print(f"  counts {counts}")
    expected_by_tag = {
        "race": {"tagged": 1, "cleared": 2, "retagged": 3},
        "threshold_workout": {"tagged": 4, "cleared": 5, "retagged": 6},
    }
    assert counts == {
        "tagged": 5,
        "cleared": 7,
        "retagged": 9,
        "unchanged": 1,
        "not_stored": 1,
        "mismatch": 0,
        "ambiguous": 0,
        "other_tag": 1,
        "by_tag": expected_by_tag,
    }
    assert list(counts)[:-1] == list(race_tags.OUTCOMES)
    # The pure count over the same decisions agrees with the route.
    decisions = race_tags.decide(
        [_tag_item(w, i + 1, f"2024-01-01T00:00:{i:02d}Z") for i, (_s, w, _n) in enumerate(plan)],
        [_row(s, f"2024-01-01T00:00:{i:02d}+00:00") for i, (s, _w, _n) in enumerate(plan)],
    )
    pure = race_tags.counts(decisions)
    assert pure["by_tag"] == {
        "race": {"tagged": 1, "cleared": 1, "retagged": 1},
        "threshold_workout": {"tagged": 1, "cleared": 1, "retagged": 1},
    }
    assert pure["unchanged"] == 1 and pure["other_tag"] == 1 and pure["retagged"] == 2


def test_a_resting_capture_is_untouched_by_a_workout_tag(persist_sessions) -> None:
    """A resting capture recorded on the Run profile is stored as sport ``running``; its tag keeps
    it out of the candidates, for the workout tag and the clear as for ``race``."""
    t2 = "2024-06-01T07:00:00+00:00"
    persist_sessions(
        [
            _run("cap1", START, tag="resting_hrv_check"),
            _run("cap2", START, tag="health_snapshot"),
            _run("cap1", t2, tag="resting_hrv_check"),
            _run("fr945", t2),
        ]
    )
    response = _put({"items": [_tag_item(WORKOUT, 1), _tag_item(WORKOUT, 2, t2)]})
    assert response.status_code == 200, response.text
    first, second = response.json()["items"]
    assert first == {"connect_activity_id": 1, "outcome": "not_stored", "session_id": None, "candidates": []}
    assert second["outcome"] == "tagged" and second["candidates"] == [_sid("fr945", t2)]
    response = _put({"items": [_tag_item(None, 1)]})
    assert response.json()["items"][0]["outcome"] == "not_stored"
    assert _tag(_sid("cap1")) == "resting_hrv_check"
    assert _tag(_sid("cap2")) == "health_snapshot"
    assert _tag(_sid("cap1", t2)) == "resting_hrv_check"
    assert _tag(_sid("fr945", t2)) == WORKOUT


# --- the contract ---------------------------------------------------------------------------------------


def test_both_422_responses_share_one_contract_shape(persist_sessions) -> None:
    """``getState`` and ``setRaceTags`` document their 422 with one response component, and it
    describes the body both routes serve: FastAPI's ``{detail: [{loc, msg, type}, ...]}``."""
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    get_state = contract["paths"]["/state"]["get"]["responses"]["422"]
    set_tags = contract["paths"][PATH]["put"]["responses"]["422"]
    assert get_state == set_tags, (get_state, set_tags)
    ref = set_tags["$ref"]
    assert ref.startswith("#/components/responses/"), ref
    component = contract["components"]["responses"][ref.rsplit("/", 1)[1]]
    schema = component["content"]["application/json"]["schema"]
    assert schema["required"] == ["detail"]
    detail = schema["properties"]["detail"]
    assert detail["type"] == "array"
    assert set(detail["items"]["required"]) == {"loc", "msg", "type"}

    persist_sessions([_run("fr945")])
    client = TestClient(app)
    served = [
        _put({"items": [_race_item(True), {**_base(2), "race": True, "tag": RACE}]}),
        client.get("/state", params={"as_of": "not a date"}),
    ]
    for response in served:
        assert response.status_code == 422, response.text
        body = response.json()
        assert set(body) == {"detail"}, body
        assert isinstance(body["detail"], list) and body["detail"], body
        for entry in body["detail"]:
            assert {"loc", "msg", "type"} <= set(entry), entry
