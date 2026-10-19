"""F023 AC1, AC7 (the endpoint side) and AC8 (the contract): ``PUT /race-tags``.

Every test drives the real app through ``TestClient`` against the isolated scratch store the
autouse ``isolated_data_dir`` fixture installs; sessions are written with ``persist_sessions``
(``db.persist``, after ``init_schema``). The matching rule itself is pinned in
``test_race_tags_matching.py``; here the route's screen, its response shape and its contract are.

The adversarial probe table (AC7, endpoint side). Each hostile body below sends the hostile item
beside a valid item that would tag a stored run, so a screen that let any item through would
write; ``test_store_unchanged_after_every_422`` asserts that none does.

| input | result | why |
|---|---|---|
| ``start_time`` in another zone (``+01:00``, ``-05:00``), no offset, ``+0000`` | 422 | the stored form is UTC ``+00:00``; another zone names the same instant in a string the match cannot equal, so it would read as ``not_stored`` and look like a missing run |
| ``start_time`` with fractions (``.000Z``, ``.5+00:00``) | 422 | stored starts are whole seconds; the CLI sends whole seconds |
| ``start_time`` unparseable (text, Feb 30, month 13, empty, a space or lower-case ``z``, non-ASCII digits, an epoch int, null) | 422 | a strict string matching ``YYYY-MM-DDTHH:MM:SS`` and ``Z`` or ``+00:00`` in ASCII digits, then ``datetime.fromisoformat``; pydantic's ``datetime`` is not used, as it accepts other zones, fractions and epoch ints |
| ``distance_m`` / ``duration_s`` negative, NaN, Infinity, -Infinity, a string, ``true`` | 422 | ``ge=0``, ``allow_inf_nan=False``, strict numbers; NaN and Infinity arrive as JSON tokens the parser reads, and the 422 handler echoes them as text |
| ``distance_m`` / ``duration_s`` zero | 200, a defined outcome | Connect reads 0 m for a treadmill run without a foot pod, and the watch stores 0 too; the run is still matched by its start second and confirmed by the other value. The 1 % rule taken literally: 0 against 0 agrees, 0 against a non-zero stored value is a ``mismatch`` |
| ``distance_m`` / ``duration_s`` null | 200, a defined outcome | AC1 allows null; a null never agrees, so one candidate is a ``mismatch`` |
| a repeated ``connect_activity_id`` | 422 | AC1: ids are distinct |
| 0 items, 501 items, no ``items`` key | 422 | AC1: 1 to 500; 500 is a 200 |
| ``race`` as ``"true"``, ``1``, null; ``race`` missing with no ``tag`` | 422 | a strict bool; exactly one of ``race`` and ``tag`` (F026; the ``tag`` rows are in ``test_race_tags_transitions.py``) |
| an extra key on the body or an item; an id as a string, a float or ``true`` | 422 | ``extra="forbid"``, a strict int id |
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.models import Session

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
SUPPORT = Path(__file__).parent / "support"
PATH = "/race-tags"
OUTCOMES = ("tagged", "cleared", "retagged", "unchanged", "not_stored", "mismatch", "ambiguous", "other_tag")
TAGS = ("race", "threshold_workout")
WRITES = ("tagged", "cleared", "retagged")

START = "2024-03-02T16:43:13+00:00"
START_Z = "2024-03-02T16:43:13Z"
FR945 = (3720.47, 1333.772)
DEV_3476 = (5018.28, 1844.175)


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
    distance: float | None = FR945[0],
    duration: float | None = FR945[1],
    *,
    tag: str | None = None,
) -> Session:
    summary = {}
    if distance is not None:
        summary["distance_m"] = distance
    if duration is not None:
        summary["duration_s"] = duration
    return Session(
        session_id=derive_session_id(device, start),
        sport="running",
        source_vendor="garmin",
        start_time=start,
        source_device=device,
        activity_tag=tag,
        summary=summary,
    )


def _sid(device: str, start: str = START) -> str:
    return derive_session_id(device, start)


def _item(
    activity_id: int = 1,
    start: object = START_Z,
    distance: object = FR945[0],
    duration: object = FR945[1],
    race: object = True,
) -> dict:
    return {
        "connect_activity_id": activity_id,
        "start_time": start,
        "distance_m": distance,
        "duration_s": duration,
        "race": race,
    }


def _put(body: object):
    """PUT a body as raw JSON text, so the ``NaN`` and ``Infinity`` tokens reach the parser."""
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


def _schema_and_rows() -> tuple[list[tuple], dict[str, list[tuple]]]:
    """``sqlite_master`` and every table's rows, read outside the app."""
    with _connection() as conn:
        schema = [tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name")]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        rows = {t: sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {t}")) for t in tables}
    return schema, rows


def _counts(by_tag: dict | None = None, **nonzero: int) -> dict:
    """Every outcome's count, then ``by_tag`` with the given ``{tag: {outcome: n}}`` cells, zeros elsewhere."""
    given = by_tag or {}
    cells = {tag: {w: given.get(tag, {}).get(w, 0) for w in WRITES} for tag in TAGS}
    return {**{outcome: nonzero.get(outcome, 0) for outcome in OUTCOMES}, "by_tag": cells}


# --- the hostile bodies: each a hostile item beside a valid one that would tag the FR945 run -------

VALID = _item(1)


def _with_valid(hostile_item: dict) -> dict:
    return {"items": [VALID, hostile_item]}


OTHER_ZONE = [
    "2024-03-02T17:43:13+01:00",
    "2024-03-02T11:43:13-05:00",
    "2024-03-02T16:43:13",
    "2024-03-02T16:43:13+0000",
    "2024-03-02T16:43:13-00:00",
]
FRACTIONS = ["2024-03-02T16:43:13.000Z", "2024-03-02T16:43:13.5+00:00", "2024-03-02T16:43:13.000000+00:00"]
UNPARSEABLE = [
    "not a time",
    "2024-02-30T10:00:00Z",
    "2024-13-01T10:00:00Z",
    "2024-03-02T25:00:00Z",
    "",
    "2024-03-02 16:43:13Z",
    "2024-03-02T16:43:13z",
    "٢٠٢٤-03-02T16:43:13Z",
    "2024-03-02",
    1709397793,
    None,
]
BAD_NUMBERS = [-1.0, -0.5, "NaN", "Infinity", "-Infinity", "3720.47", True]


def _number(value: object) -> object:
    """``"NaN"`` and the infinities as floats, so ``json.dumps`` writes the bare tokens."""
    return float(value) if value in ("NaN", "Infinity", "-Infinity") else value


HOSTILE: dict[str, list[dict]] = {
    "other zone": [_with_valid(_item(2, start)) for start in OTHER_ZONE],
    "fractions": [_with_valid(_item(2, start)) for start in FRACTIONS],
    "unparseable": [_with_valid(_item(2, start)) for start in UNPARSEABLE]
    + [_with_valid({k: v for k, v in _item(2).items() if k != "start_time"})],
    "distance": [_with_valid(_item(2, distance=_number(v))) for v in BAD_NUMBERS],
    "duration": [_with_valid(_item(2, duration=_number(v))) for v in BAD_NUMBERS],
    "repeated id": [{"items": [VALID, _item(1, race=False)]}, {"items": [VALID, _item(2), _item(2)]}],
    "item count": [{"items": []}, {"items": [VALID] + [_item(i) for i in range(2, 502)]}, {}, {"items": None}],
    "race": [_with_valid(_item(2, race=v)) for v in ("true", 1, None)]
    + [_with_valid({k: v for k, v in _item(2).items() if k != "race"})],
    "extra or loose": [
        {"items": [VALID], "extra": 1},
        _with_valid({**_item(2), "extra": 1}),
        _with_valid(_item("2")),
        _with_valid(_item(2.0)),
        _with_valid(_item(True)),
    ],
}


def _assert_all_422(group: str) -> None:
    for body in HOSTILE[group]:
        response = _put(body)
        shown = json.dumps(body)[:160]
        print(f"  {group}: {shown} -> {response.status_code}")
        assert response.status_code == 422, (shown, response.status_code, response.text)


# --- AC1: the endpoint -----------------------------------------------------------------------------


def test_one_valid_item_returns_200_tagged(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    response = _put({"items": [VALID]})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [
            {"connect_activity_id": 1, "outcome": "tagged", "session_id": _sid("fr945"), "candidates": [_sid("fr945")]}
        ],
        "counts": _counts({"race": {"tagged": 1}}, tagged=1),
    }
    assert _tag(_sid("fr945")) == "race"


def test_counts_carry_all_eight_outcomes(persist_sessions) -> None:
    starts = [f"2024-11-01T09:00:{s:02d}+00:00" for s in range(8)]
    persist_sessions(
        [
            _run("fr945", starts[0]),  # null, race true -> tagged
            _run("fr945", starts[1], tag="race"),  # race, race false -> cleared
            _run("fr945", starts[7], tag="race"),  # race, tag threshold_workout -> retagged
            _run("fr945", starts[2]),  # null, race false -> unchanged
            # starts[3]: nothing stored -> not_stored
            _run("fr945", starts[4], *DEV_3476),  # one disagreeing candidate -> mismatch
            _run("fr945", starts[5]),
            _run("3476", starts[5]),  # two candidates, both agreeing -> ambiguous
            _run("fr945", starts[6], tag="interval_session"),  # another tag -> other_tag
        ]
    )
    items = [
        _item(10, starts[0].replace("+00:00", "Z")),
        _item(11, starts[1], race=False),
        {k: v for k, v in _item(17, starts[7]).items() if k != "race"} | {"tag": "threshold_workout"},
        _item(12, starts[2], race=False),
        _item(13, starts[3]),
        _item(14, starts[4]),
        _item(15, starts[5]),
        _item(16, starts[6]),
    ]
    response = _put({"items": items})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [i["outcome"] for i in body["items"]] == list(OUTCOMES)
    by_tag = {"race": {"tagged": 1, "cleared": 1}, "threshold_workout": {"retagged": 1}}
    assert body["counts"] == _counts(by_tag, **{o: 1 for o in OUTCOMES})
    assert list(body["counts"]) == [*OUTCOMES, "by_tag"]
    assert body["items"][6]["session_id"] is None
    assert sorted(body["items"][6]["candidates"]) == sorted([_sid("fr945", starts[5]), _sid("3476", starts[5])])
    assert body["items"][4] == {"connect_activity_id": 13, "outcome": "not_stored", "session_id": None, "candidates": []}


def test_items_return_in_request_order(persist_sessions) -> None:
    starts = [f"2024-11-02T09:00:{s:02d}+00:00" for s in range(4)]
    persist_sessions([_run("fr945", starts[0]), _run("fr945", starts[2])])
    ids = [907, 3, 55, 12]
    items = [_item(ids[i], starts[i]) for i in range(4)]
    response = _put({"items": items})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [i["connect_activity_id"] for i in body["items"]] == ids
    assert [i["outcome"] for i in body["items"]] == ["tagged", "not_stored", "tagged", "not_stored"]
    assert body["counts"] == _counts({"race": {"tagged": 2}}, tagged=2, not_stored=2)


def test_five_hundred_items_are_accepted(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    items = [VALID] + [_item(i, f"2025-01-01T00:{i // 60 % 60:02d}:{i % 60:02d}Z") for i in range(2, 501)]
    response = _put({"items": items})
    assert response.status_code == 200, response.text
    assert response.json()["counts"] == _counts({"race": {"tagged": 1}}, tagged=1, not_stored=499)


def test_zero_or_null_values_are_a_defined_outcome_not_a_422(persist_sessions) -> None:
    """Zero is accepted (the schema descriptions say so): 0 against a stored 0 agrees under the
    literal 1 % rule; 0 against a non-zero stored value, and a null, never agree."""
    starts = [f"2024-11-03T09:00:{s:02d}+00:00" for s in range(4)]
    persist_sessions(
        [
            _run("fr945", starts[0], 0.0, 1800.0),
            _run("fr945", starts[1], 5000.0, 1800.0),
            _run("fr945", starts[2], 5000.0, 1800.0),
            _run("fr945", starts[3], 5000.0, 1800.0),
        ]
    )
    response = _put(
        {
            "items": [
                _item(1, starts[0], 0, 1800.0),
                _item(2, starts[1], 0.0, 1800.0),
                _item(3, starts[2], 5000.0, 0),
                _item(4, starts[3], None, 1800.0),
            ]
        }
    )
    assert response.status_code == 200, response.text
    assert [i["outcome"] for i in response.json()["items"]] == ["tagged", "mismatch", "mismatch", "mismatch"]


# --- AC7: hostile input through the endpoint ------------------------------------------------------


def test_start_time_in_another_zone_is_422(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    _assert_all_422("other zone")


def test_fractional_seconds_is_422(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    _assert_all_422("fractions")


def test_unparseable_start_time_is_422(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    _assert_all_422("unparseable")


def test_negative_nan_inf_distance_is_422(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    _assert_all_422("distance")


def test_negative_nan_inf_duration_is_422(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    _assert_all_422("duration")


def test_repeated_activity_id_is_422(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    _assert_all_422("repeated id")
    detail = json.dumps(_put(HOSTILE["repeated id"][1]).json()["detail"])
    assert "connect_activity_id" in detail and "2" in detail, detail


def test_zero_and_501_items_are_422(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    assert len(HOSTILE["item count"][1]["items"]) == 501
    _assert_all_422("item count")


def test_race_as_string_is_422(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    _assert_all_422("race")


def test_an_extra_key_or_a_loose_id_is_422(persist_sessions) -> None:
    persist_sessions([_run("fr945")])
    _assert_all_422("extra or loose")


def test_store_unchanged_after_every_422(persist_sessions) -> None:
    """Every hostile body carries a valid item that would tag the stored run; none writes."""
    persist_sessions([_run("fr945"), _run("fr945", "2024-03-02T16:43:14+00:00", tag="race")])
    before = _schema_and_rows()
    sent = 0
    for group, bodies in HOSTILE.items():
        for body in bodies:
            response = _put(body)
            assert response.status_code == 422, (group, json.dumps(body)[:160])
            assert _schema_and_rows() == before, (group, json.dumps(body)[:160])
            sent += 1
    print(f"  {sent} hostile bodies, store unchanged after each")
    assert sent >= 45
    # The same valid item alone does write, so the bodies above were refused, not inert.
    assert _put({"items": [VALID]}).json()["items"][0]["outcome"] == "tagged"
    assert _schema_and_rows() != before


# --- AC8: the contract ------------------------------------------------------------------------------


def test_served_schemas_match_the_contract() -> None:
    """``check_drift.py`` compares paths, statuses and required request fields, not JSON bodies;
    this walks the request and response components property by property, as
    ``tests/support/contract_walk.py`` does, and compares the constraints the walker skips."""
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    built = app.openapi()
    methods = {"get", "post", "put", "patch", "delete"}
    assert {m for m in built["paths"][PATH] if m in methods} == {m for m in contract["paths"][PATH] if m in methods} == {
        "put"
    }
    op, built_op = contract["paths"][PATH]["put"], built["paths"][PATH]["put"]
    assert op["operationId"] == built_op["operationId"] == "setRaceTags"
    assert op["tags"] == built_op["tags"] == ["Sessions"]
    assert op["x-readiness"] == "implemented"
    assert set(op["responses"]) == set(built_op["responses"]) == {"200", "422"}
    for operation in (op, built_op):
        assert operation["requestBody"]["required"] is True
        assert operation["requestBody"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/RaceTagsUpdate"
        }
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/RaceTagsResult"
        }

    seen, compared = contract_walk.walk(contract, built, ["RaceTagsUpdate", "RaceTagsResult"])
    print(f"  {len(compared)} properties over {sorted(seen)}")
    assert seen == {
        "RaceTagsUpdate",
        "RaceTagItem",
        "RaceTagsResult",
        "RaceTagItemResult",
        "RaceTagCounts",
        "RaceTagCountsByTag",
        "RaceTagWrites",
    }, seen

    ours, theirs = contract["components"]["schemas"], built["components"]["schemas"]
    for name in ("RaceTagsUpdate", "RaceTagItem"):
        assert ours[name]["additionalProperties"] is False is theirs[name]["additionalProperties"], name
    for key in ("minItems", "maxItems"):
        assert ours["RaceTagsUpdate"]["properties"]["items"][key] == theirs["RaceTagsUpdate"]["properties"]["items"][key]
    assert ours["RaceTagsUpdate"]["properties"]["items"]["minItems"] == 1
    assert ours["RaceTagsUpdate"]["properties"]["items"]["maxItems"] == 500
    item, built_item = ours["RaceTagItem"]["properties"], theirs["RaceTagItem"]["properties"]
    assert item["start_time"]["pattern"] == built_item["start_time"]["pattern"]
    for field in ("distance_m", "duration_s"):
        (served_number,) = [b for b in built_item[field]["anyOf"] if b != {"type": "null"}]
        assert item[field]["minimum"] == served_number["minimum"] == 0, field
    # Exactly one of race and tag: neither is required, both are properties.
    assert set(ours["RaceTagItem"]["required"]) == {"connect_activity_id", "start_time", "distance_m", "duration_s"}
    assert {"race", "tag"} <= set(ours["RaceTagItem"]["properties"])
    assert sorted(ours["RaceTagItem"]["properties"]["tag"]["enum"]) == sorted(TAGS)
    assert ours["RaceTagItem"]["properties"]["tag"]["nullable"] is True
    counted = {*OUTCOMES, "by_tag"}
    assert set(ours["RaceTagCounts"]["required"]) == counted == set(ours["RaceTagCounts"]["properties"])
    assert set(ours["RaceTagCountsByTag"]["required"]) == set(TAGS) == set(ours["RaceTagCountsByTag"]["properties"])
    assert set(ours["RaceTagWrites"]["required"]) == set(WRITES) == set(ours["RaceTagWrites"]["properties"])
    assert sorted(ours["RaceTagItemResult"]["properties"]["outcome"]["enum"]) == sorted(OUTCOMES)


# --- the running-session prefilter the race-tag apply and the lap-candidate read share -----------------


def test_race_tags_and_lap_candidates_share_one_running_prefilter(persist_sessions) -> None:
    """Both readers of "the stored running sessions at these start seconds" apply one rule, written
    once (``db._running_sessions_at``): a cycling session at a matching start second is refused by
    the race-tag apply (``not_stored``, nothing written) and by the lap-candidate read (no row),
    while a running session at the same second is admitted by both."""
    ride = Session(
        session_id=_sid("edge530"),
        sport="cycling",
        source_vendor="garmin",
        start_time=START,
        source_device="edge530",
        summary={"distance_m": FR945[0], "duration_s": FR945[1]},
    )
    persist_sessions([ride])
    with _connection() as conn:
        refused = db_module.apply_race_tag_decisions(conn, [_item(1)])
        assert [(d.outcome, d.session_id, d.candidates) for d in refused] == [("not_stored", None, ())]
        assert db_module.read_lap_candidate_rows(conn, [START_Z]) == []
    assert _tag(ride.session_id) is None

    persist_sessions([_run("fr945")])
    with _connection() as conn:
        admitted = db_module.apply_race_tag_decisions(conn, [_item(2)])
        assert [(d.outcome, d.session_id) for d in admitted] == [("tagged", _sid("fr945"))]
        rows = db_module.read_lap_candidate_rows(conn, [START_Z])
        assert [row["session_id"] for row in rows] == [_sid("fr945")]
    assert _tag(_sid("fr945")) == "race" and _tag(ride.session_id) is None

    # One copy of the rule in the store module, not one per reader.
    assert inspect.getsource(db_module).count("sport = 'running'") == 1
