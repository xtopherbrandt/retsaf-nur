"""F016 AC6: the degenerate-input table, every row through ``POST /sessions``.

Each row uploads ``sample_run.fit`` patched in memory by
``tests/support/fit_patch.py`` (no ``.fit`` file is written) and reads back
what is stored: the ``sessions`` row's profile columns, the anchor version log
and the four anchors as ``db.read_hr_anchors`` serves them. None of the rows
calls the lookup on hand-built values; ``tests/test_hr_anchors.py`` does that.

``sample_run`` (2026-09-01, running, no developer fields) carries resting 47,
max 188, threshold 169, male, activity class 50. ``wrist_ppg_run``
(2026-08-21, running) carries resting 47, max 189, threshold 169: it is the
earlier file a fall-through row falls to, and its max 189 tells the two files
apart.

Four groups:

1. **Values the mapping screens or keeps** (resting 0, max as the uint8
   invalid value, ``gender`` 0) and **the ordering rule** (resting equal to
   max, max below resting, threshold above max, below resting, equal to
   resting): one parametrised test, the stored column and every anchor's
   value and reason per row.
2. **``activity_class``** 0, 100 and 0x80 + 50: the raw integer is stored.
3. **``zones_target`` gone two ways**: present with every field invalid, and
   removed. Max and threshold fall through to the earlier file while resting
   still comes from the later one.
4. **Uploads that must change nothing**, each carrying max 199: a duplicate
   (409), a failure inside ``persist`` (500, rolled back) and a refusal before
   ``persist`` (400). Each compares a snapshot of every profile-bearing table
   before and after, then shows the same max 199 does move the profile when
   the upload is not refused, so an unchanged snapshot is not a probe that
   could not see a change.
"""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import fitdecode
import pytest
from fastapi.testclient import TestClient
from runcoach_api import db, profile
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"

_SPEC = importlib.util.spec_from_file_location(
    "fit_patch", Path(__file__).parent / "support" / "fit_patch.py"
)
assert _SPEC is not None and _SPEC.loader is not None
fit_patch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fit_patch)

# FIT global message numbers and the field numbers patched below.
USER_PROFILE, ZONES_TARGET, SPORT, SESSION = 3, 7, 12, 18
GENDER = 1  # user_profile.gender, enum
RESTING_HEART_RATE = 8  # user_profile.resting_heart_rate, uint8
ACTIVITY_CLASS = 17  # user_profile.activity_class, enum
MAX_HEART_RATE = 1  # zones_target.max_heart_rate, uint8
THRESHOLD_HEART_RATE = 2  # zones_target.threshold_heart_rate, uint8

UINT8_INVALID = 0xFF

# FIT base types whose invalid value is every bit set: enum, uint8, uint16, uint32.
ALL_ONES_INVALID = {0x00, 0x02, 0x84, 0x86}

PROFILE_COLUMNS = (
    "sex",
    "body_mass_kg",
    "height_cm",
    "resting_hr_bpm",
    "max_hr_bpm",
    "threshold_hr_bpm",
    "garmin_activity_class",
)


def _raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _sample(*patches: tuple[int, int, int]) -> bytes:
    """``sample_run`` with each ``(global_num, field_num, raw value)`` set."""
    data = _raw("sample_run.fit")
    for global_num, field_num, value in patches:
        data = fit_patch.set_field(data, global_num, field_num, value)
    return data


def _upload(client: TestClient, post_fit_bytes, data: bytes, name: str = "sample_run.fit") -> str:
    response = post_fit_bytes(client, name, data)
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _stored(session_id: str) -> dict:
    conn = db.get_connection()
    try:
        row = conn.execute(
            f"SELECT {', '.join(PROFILE_COLUMNS)} FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, session_id
    return {c: row[c] for c in PROFILE_COLUMNS}


def _anchors(label: str) -> dict[str, profile.Anchor]:
    conn = db.get_connection()
    try:
        served = db.read_hr_anchors(conn)
    finally:
        conn.close()
    print(f"\n  {label}")
    for field in profile.ANCHOR_FIELDS:
        a = served[field]
        print(
            f"    {field:<18} value={a.value!r} source={a.source} version={a.version} "
            f"session={a.session_id} reason={a.reason}"
        )
    return served


def _snapshot() -> dict:
    """Every row a profile write could touch: the sessions with their profile columns,
    the entered values and the anchor version log."""
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        sessions = [
            tuple(r)
            for r in conn.execute(
                f"SELECT session_id, start_time, upload_order, sport, {', '.join(PROFILE_COLUMNS)} "
                "FROM sessions ORDER BY session_id"
            )
        ]
        entries = [tuple(r) for r in conn.execute(f"SELECT * FROM {db.PROFILE_ENTRIES_TABLE} ORDER BY 1")]
        log = [tuple(r) for r in conn.execute(f"SELECT * FROM {db.ANCHOR_VERSIONS_TABLE} ORDER BY 1")]
    finally:
        conn.close()
    return {"sessions": sessions, "entries": entries, "anchor_versions": log}


def _messages(data: bytes, name: str) -> list[dict]:
    """Every ``name`` data message in ``data`` as ``{field: raw value}``, via ``fitdecode`` alone."""
    found = []
    with fitdecode.FitReader(io.BytesIO(data)) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == name:
                found.append({f.name: f.raw_value for f in frame.fields})
    return found


# --- group 1: screened values and the ordering rule --------------------------------------------

S, C, M = None, "order_conflict", "missing"  # served, conflict, missing

# (id, patches, stored columns the patch must produce,
#  expected (value, reason) per anchor in ANCHOR_FIELDS order)
ANCHOR_ROWS = [
    (
        "resting_zero_missing",
        [(USER_PROFILE, RESTING_HEART_RATE, 0)],
        {"resting_hr_bpm": None},
        ((None, M), (188, S), (169, S), ("male", S)),
    ),
    (
        "max_uint8_invalid_missing",
        [(ZONES_TARGET, MAX_HEART_RATE, UINT8_INVALID)],
        {"max_hr_bpm": None},
        ((47, S), (None, M), (169, S), ("male", S)),
    ),
    (
        "gender_zero_female",
        [(USER_PROFILE, GENDER, 0)],
        {"sex": "female"},
        ((47, S), (188, S), (169, S), ("female", S)),
    ),
    (
        "resting_equals_max",
        [(USER_PROFILE, RESTING_HEART_RATE, 188)],
        {"resting_hr_bpm": 188, "max_hr_bpm": 188},
        ((None, C), (None, C), (169, S), ("male", S)),
    ),
    (
        "max_below_resting",
        [(ZONES_TARGET, MAX_HEART_RATE, 46)],
        {"resting_hr_bpm": 47, "max_hr_bpm": 46},
        ((None, C), (None, C), (169, S), ("male", S)),
    ),
    (
        "threshold_above_max",
        [(ZONES_TARGET, THRESHOLD_HEART_RATE, 200)],
        {"threshold_hr_bpm": 200},
        ((47, S), (188, S), (None, C), ("male", S)),
    ),
    (
        "threshold_below_resting",
        [(ZONES_TARGET, THRESHOLD_HEART_RATE, 40)],
        {"threshold_hr_bpm": 40},
        ((47, S), (188, S), (None, C), ("male", S)),
    ),
    (
        "threshold_equals_resting",
        [(ZONES_TARGET, THRESHOLD_HEART_RATE, 47)],
        {"threshold_hr_bpm": 47},
        ((47, S), (188, S), (None, C), ("male", S)),
    ),
]


@pytest.mark.parametrize(
    ("patches", "stored", "expected"),
    [pytest.param(*row[1:], id=row[0]) for row in ANCHOR_ROWS],
)
def test_degenerate_input_through_upload(post_fit_bytes, patches, stored, expected) -> None:
    with TestClient(app) as client:
        session_id = _upload(client, post_fit_bytes, _sample(*patches))
    columns = _stored(session_id)
    print(f"  stored: {columns}")
    for column, value in stored.items():
        assert columns[column] == value, (column, columns)

    served = _anchors(f"patches {patches}")
    for field, (value, reason) in zip(profile.ANCHOR_FIELDS, expected, strict=True):
        anchor = served[field]
        assert (anchor.value, anchor.reason) == (value, reason), anchor
        if reason is None:
            assert (anchor.source, anchor.session_id, anchor.version) == ("fit", session_id, 1), anchor
        else:
            assert anchor.source is None and anchor.version is None, anchor

    # The log holds exactly the served anchors: a refused one never started a version.
    conn = db.get_connection()
    try:
        logged = {r["anchor"] for r in conn.execute(f"SELECT anchor FROM {db.ANCHOR_VERSIONS_TABLE}")}
    finally:
        conn.close()
    assert logged == {
        f for f, (_, reason) in zip(profile.ANCHOR_FIELDS, expected, strict=True) if reason is None
    }


# --- group 2: activity_class keeps its raw integer ---------------------------------------------


@pytest.mark.parametrize(
    "raw_class",
    [
        pytest.param(0, id="zero"),
        pytest.param(100, id="level_max_100"),
        pytest.param(0x80 + 50, id="athlete_bit"),
    ],
)
def test_activity_class_stores_the_raw_integer(post_fit_bytes, raw_class: int) -> None:
    data = _sample((USER_PROFILE, ACTIVITY_CLASS, raw_class))
    assert _messages(data, "user_profile")[0]["activity_class"] == raw_class
    with TestClient(app) as client:
        session_id = _upload(client, post_fit_bytes, data)
    columns = _stored(session_id)
    print(f"\n  activity_class {raw_class:#x}: stored {columns['garmin_activity_class']!r}")
    stored = columns["garmin_activity_class"]
    assert stored == raw_class and type(stored) is int, columns
    # The other six columns are the unpatched file's.
    assert {c: v for c, v in columns.items() if c != "garmin_activity_class"} == {
        "sex": "male",
        "body_mass_kg": 71.7,
        "height_cm": 180,
        "resting_hr_bpm": 47,
        "max_hr_bpm": 188,
        "threshold_hr_bpm": 169,
    }


# --- group 3: zones_target present but invalid, and removed ------------------------------------


def _zones_every_field_invalid() -> bytes:
    """``sample_run`` with every field of its one ``zones_target`` message set to its base
    type's invalid value (all bits set for the unsigned and enum types it uses)."""
    data = _raw("sample_run.fit")
    records = [
        r for r in fit_patch.walk(data) if r.definition.global_num == ZONES_TARGET and not r.is_definition
    ]
    assert len(records) == 1, records
    for field in records[0].definition.fields:
        assert field.base_type in ALL_ONES_INVALID, field
        data = fit_patch.set_field(data, ZONES_TARGET, field.num, (1 << (8 * field.size)) - 1)
    return data


def _zones_removed() -> bytes:
    return fit_patch.remove_messages(_raw("sample_run.fit"), ZONES_TARGET)


@pytest.mark.parametrize(
    ("build", "messages_left"),
    [
        pytest.param(_zones_every_field_invalid, 1, id="zones_target_every_field_invalid"),
        pytest.param(_zones_removed, 0, id="zones_target_removed"),
    ],
)
def test_zones_target_gone_falls_through_to_the_earlier_file(post_fit_bytes, build, messages_left) -> None:
    data = build()
    zones = _messages(data, "zones_target")
    # The two rows are distinct files: one keeps the message with nothing valid in it.
    assert len(zones) == messages_left, zones
    assert all(v is None for message in zones for v in message.values()), zones

    with TestClient(app) as client:
        earlier = _upload(client, post_fit_bytes, _raw("wrist_ppg_run.fit"), "wrist_ppg_run.fit")
        later = _upload(client, post_fit_bytes, data)
    columns = _stored(later)
    print(f"\n  later file stored: {columns}")
    assert columns["max_hr_bpm"] is None and columns["threshold_hr_bpm"] is None, columns
    assert columns["resting_hr_bpm"] == 47, columns

    served = _anchors(build.__name__)
    assert (served["max_hr_bpm"].value, served["max_hr_bpm"].session_id) == (189, earlier)
    assert (served["threshold_hr_bpm"].value, served["threshold_hr_bpm"].session_id) == (169, earlier)
    assert (served["resting_hr_bpm"].value, served["resting_hr_bpm"].session_id) == (47, later)
    assert (served["sex"].value, served["sex"].session_id) == ("male", later)
    assert all(served[f].version == 1 and served[f].reason is None for f in profile.ANCHOR_FIELDS)


# --- group 4: uploads that change nothing ------------------------------------------------------


def _max_199() -> bytes:
    return _sample((ZONES_TARGET, MAX_HEART_RATE, 199))


def test_duplicate_of_the_latest_file_with_max_199_changes_nothing(post_fit_bytes) -> None:
    """A patched copy keeps ``sample_run``'s device and start time, so it is a duplicate
    whose bytes differ: had anything been written before the dedupe, max 199 would show."""
    with TestClient(app) as client:
        stored = _upload(client, post_fit_bytes, _raw("sample_run.fit"))
        before = _snapshot()
        response = post_fit_bytes(client, "sample_run.fit", _max_199())
        assert response.status_code == 409, response.text
        assert stored in response.text
        after = _snapshot()
        print(f"\n  before={before}\n  after={after}")
        assert after == before
        served = _anchors("after the 409")
        assert (served["max_hr_bpm"].value, served["max_hr_bpm"].version) == (188, 1)

        # Control: once the original is gone the same bytes are stored and move max.
        assert client.delete(f"/sessions/{stored}").status_code == 204
        again = _upload(client, post_fit_bytes, _max_199())
    served = _anchors("control: the patched copy stored")
    assert (served["max_hr_bpm"].value, served["max_hr_bpm"].version) == (199, 2)
    assert served["max_hr_bpm"].session_id == again


def _fail_in_helper(real, fired: list):
    """Fail in place of the helper: the session, record and RR inserts have run inside the
    transaction; the quarantine sidecar and the anchor log have not."""

    def boom(*args, **kwargs):
        fired.append("failed before the anchor log")
        raise RuntimeError("simulated failure inside persist")

    return boom


def _fail_after_log_write(real, fired: list):
    """The real log write, then a failure, for the first write that changes a row (the
    schema repair at app startup also calls the helper, with nothing to change)."""

    def write_then_fail(conn, changes):
        real(conn, changes)
        if changes:
            fired.append(f"failed after the anchor log wrote {sorted(changes)}")
            raise RuntimeError("simulated failure after the anchor log write")

    return write_then_fail


@pytest.mark.parametrize(
    ("helper", "make"),
    [
        pytest.param("_insert_quarantine_sidecar", _fail_in_helper, id="insert_helper_fails"),
        pytest.param("_write_anchor_log", _fail_after_log_write, id="fails_after_the_log_write"),
    ],
)
def test_failure_inside_persist_with_max_199_changes_nothing(
    post_fit_bytes, monkeypatch, helper: str, make
) -> None:
    """``wrist_ppg_run`` (max 189) is stored; ``sample_run`` with max 199, later in time, would
    win. ``persist`` fails part-way: no session, no profile value and no version moves."""
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, _raw("wrist_ppg_run.fit"), "wrist_ppg_run.fit")
    before = _snapshot()
    assert ("max_hr_bpm", 189) in {(r[0], db._json_load(r[1])) for r in before["anchor_versions"]}

    fired: list[str] = []
    # Scoped with monkeypatch.context(): undoing the shared fixture would also undo the
    # autouse isolated data dir.
    with monkeypatch.context() as m:
        m.setattr(db, helper, make(getattr(db, helper), fired))
        with TestClient(app, raise_server_exceptions=False) as client:
            response = post_fit_bytes(client, "sample_run.fit", _max_199())
    print(f"\n  {helper}: {fired} -> {response.status_code}")
    assert len(fired) == 1, fired
    if helper == "_write_anchor_log":
        assert "max_hr_bpm" in fired[0], fired
    assert response.status_code == 500, response.text
    after = _snapshot()
    print(f"\n  before={before}\n  after={after}")
    assert after == before
    served = _anchors(f"after {helper} failed")
    assert (served["max_hr_bpm"].value, served["max_hr_bpm"].version) == (189, 1)

    # Control: the same upload, unpatched, stores 199 as the next version.
    with TestClient(app) as client:
        stored = _upload(client, post_fit_bytes, _max_199())
    served = _anchors("control: retried without the failure")
    assert (served["max_hr_bpm"].value, served["max_hr_bpm"].version) == (199, 2)
    assert served["max_hr_bpm"].session_id == stored


def test_refusal_before_persist_with_max_199_changes_nothing(post_fit_bytes) -> None:
    """``sample_run`` with max 199 and its ``session`` and ``sport`` messages removed is
    refused with 400 before ``persist``; ``wrist_ppg_run``'s max 189 stays at version 1."""
    refused = fit_patch.remove_messages(fit_patch.remove_messages(_max_199(), SESSION), SPORT)
    assert _messages(refused, "zones_target")[0]["max_heart_rate"] == 199
    assert not _messages(refused, "session") and not _messages(refused, "sport")
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, _raw("wrist_ppg_run.fit"), "wrist_ppg_run.fit")
        before = _snapshot()
        response = post_fit_bytes(client, "sample_run.fit", refused)
        print(f"\n  refusal: {response.status_code} {response.text}")
        assert response.status_code == 400, response.text
        assert _snapshot() == before
        served = _anchors("after the 400")
        assert (served["max_hr_bpm"].value, served["max_hr_bpm"].version) == (189, 1)

        # Control: the same max 199 with its session and sport messages is stored.
        stored = _upload(client, post_fit_bytes, _max_199())
    served = _anchors("control: max 199 with session and sport")
    assert (served["max_hr_bpm"].value, served["max_hr_bpm"].version, served["max_hr_bpm"].session_id) == (
        199,
        2,
        stored,
    )
