"""F016 AC2 and AC4: each profile field's effective value, files first, then entries.

The rule (``spec/references/F016-athlete-profile.md``, "Effective value, per
field"; admitted by research/00 PRIN-28):

1. the value from the stored session with the latest ``start_time`` whose file
   carries the field -- for ``max_hr_bpm`` and ``threshold_hr_bpm`` only
   sessions whose stored ``sport`` is ``running`` count -- with the later
   upload winning a tie in start time;
2. else the latest entered value;
3. else unavailable, reason ``missing``.

Two halves:

1. **The AC2 seam rows and AC4, end to end.** Every session is stored through
   ``POST /sessions`` from real or patched fixture bytes
   (``tests/support/fit_patch.py``), deleted through ``DELETE /sessions/{id}``,
   and entries are written through ``db.write_profile_entries`` (the function
   ``PATCH /me`` will call). The profile is read back with
   ``db.read_profile_inputs`` and resolved by ``profile.resolve``. Each test
   prints the fields it asserts on (run with ``-s``).
2. **The resolver over plain rows**: precedence, the tie-break, the running
   filter's reach and a null clear, with nothing stored.

Where a row's two files carry the same value, the test asserts the source
``session_id`` too, so a resolver that picked the wrong file cannot pass on
values alone. The corpus facts the rows rely on (a census through
``POST /sessions``): ``sample_run`` 2026-09-01, running, max 188, threshold
169, 71.7 kg; ``hilly_run_8k_fr945`` 2026-09-29, running, 188/169;
``strap_hrv_capture`` 2026-09-06, stored ``other``, threshold 160;
``wrist_ppg_run`` 2026-08-21, running, max 189, threshold 169;
``strap_run_hrv`` 2026-09-06, running; ``strap_cool_down_walk`` 2026-09-06,
stored ``running``; ``dev_fields_run`` another athlete, 82.6 kg, 189/162.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api import profile
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"

_SPEC = importlib.util.spec_from_file_location(
    "fit_patch", Path(__file__).parent / "support" / "fit_patch.py"
)
assert _SPEC is not None and _SPEC.loader is not None
fit_patch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fit_patch)

# FIT global message numbers and the field numbers patched below.
USER_PROFILE, ZONES_TARGET, SESSION = 3, 7, 18
WEIGHT = 4  # user_profile.weight, uint16, scale 10 (kg)
MAX_HEART_RATE = 1  # zones_target.max_heart_rate, uint8
START_TIME = 2  # session.start_time, uint32

BODY_FIELDS = ("sex", "body_mass_kg", "height_cm", "resting_hr_bpm")


def _raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _session_start_raw(data: bytes) -> int:
    """``session.start_time``'s raw value in ``data``, read with ``fitdecode`` alone."""
    import io

    import fitdecode

    with fitdecode.FitReader(io.BytesIO(data)) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.global_mesg_num == SESSION:
                return frame.get_raw_value("start_time")
    raise AssertionError("no session message")


def _upload(client: TestClient, post_fit_bytes, name: str, data: bytes | None = None) -> str:
    response = post_fit_bytes(client, name, _raw(name) if data is None else data)
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _stored(session_id: str, *columns: str) -> dict:
    conn = db.get_connection()
    try:
        row = conn.execute(
            f"SELECT {', '.join(columns)} FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, session_id
    return {c: row[c] for c in columns}


def _enter(values: dict, set_at: str | None = None) -> str:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        return db.write_profile_entries(conn, values, set_at=set_at)
    finally:
        conn.close()


def _profile(*shown: str) -> dict[str, profile.FieldValue]:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        sessions, entries = db.read_profile_inputs(conn)
    finally:
        conn.close()
    resolved = profile.resolve(sessions, entries)
    for field in shown or profile.ENTERED_FIELDS:
        print(f"  {field:<18} {resolved[field]}")
    return resolved


def _assert_fit(field: profile.FieldValue, value, session_id: str, start_time: str | None = None) -> None:
    assert field.source == "fit", field
    assert field.value == value, field
    assert field.session_id == session_id, field
    assert field.reason is None, field
    if start_time is not None:
        assert field.recorded_at == start_time, field


def _assert_missing(field: profile.FieldValue) -> None:
    assert field.value is None and field.source is None and field.session_id is None, field
    assert field.recorded_at is None, field
    assert field.reason == "missing", field


# --- AC2 row 1 ------------------------------------------------------------------------------


def _hilly_with_max_191() -> bytes:
    return fit_patch.set_field(_raw("hilly_run_8k_fr945.fit"), ZONES_TARGET, MAX_HEART_RATE, 191)


@pytest.mark.parametrize("order", ["191_last", "191_first"])
def test_later_start_time_wins_whatever_the_upload_order(order: str, post_fit_bytes) -> None:
    """``sample_run`` (09-01, max 188) and ``hilly_run_8k_fr945`` patched to 191 (09-29): the
    later start time supplies 191 whichever file is uploaded last."""
    with TestClient(app) as client:
        if order == "191_last":
            sample = _upload(client, post_fit_bytes, "sample_run.fit")
            hilly = _upload(client, post_fit_bytes, "hilly_run_8k_fr945.fit", _hilly_with_max_191())
        else:
            hilly = _upload(client, post_fit_bytes, "hilly_run_8k_fr945.fit", _hilly_with_max_191())
            sample = _upload(client, post_fit_bytes, "sample_run.fit")
    hilly_start = _stored(hilly, "start_time")["start_time"]
    assert hilly_start > _stored(sample, "start_time")["start_time"]

    resolved = _profile("max_hr_bpm", "threshold_hr_bpm")
    _assert_fit(resolved["max_hr_bpm"], 191, hilly, hilly_start)
    _assert_fit(resolved["threshold_hr_bpm"], 169, hilly, hilly_start)


# --- AC2 row 2 and the walk row: the running filter ------------------------------------------


def test_running_filter_keeps_threshold_from_the_run_over_a_later_snapshot(post_fit_bytes) -> None:
    """``sample_run`` (threshold 169) then ``strap_hrv_capture`` (09-06, stored ``other``,
    threshold 160), its weight patched to 70.0 kg: max and threshold HR stay with the run,
    while resting HR and the body fields come from the snapshot."""
    snapshot_bytes = fit_patch.set_field(_raw("strap_hrv_capture.fit"), USER_PROFILE, WEIGHT, 700)
    with TestClient(app) as client:
        run = _upload(client, post_fit_bytes, "sample_run.fit")
        snapshot = _upload(client, post_fit_bytes, "strap_hrv_capture.fit", snapshot_bytes)
    stored = _stored(snapshot, "sport", "threshold_hr_bpm", "start_time")
    assert stored["sport"] != "running" and stored["threshold_hr_bpm"] == 160, stored
    assert stored["start_time"] > _stored(run, "start_time")["start_time"]

    resolved = _profile()
    _assert_fit(resolved["threshold_hr_bpm"], 169, run)
    _assert_fit(resolved["max_hr_bpm"], 188, run)
    _assert_fit(resolved["body_mass_kg"], 70.0, snapshot, stored["start_time"])
    _assert_fit(resolved["resting_hr_bpm"], 47, snapshot)
    _assert_fit(resolved["height_cm"], 180, snapshot)
    _assert_fit(resolved["sex"], "male", snapshot)


def test_walk_stored_running_counts_for_max_and_threshold_hr(post_fit_bytes) -> None:
    """``strap_cool_down_walk`` is a walk recorded on the Run profile and stored ``running``:
    stored sport decides, so its max HR (patched to 190) and threshold HR are effective over
    the earlier ``sample_run``."""
    walk_bytes = fit_patch.set_field(_raw("strap_cool_down_walk.fit"), ZONES_TARGET, MAX_HEART_RATE, 190)
    with TestClient(app) as client:
        run = _upload(client, post_fit_bytes, "sample_run.fit")
        walk = _upload(client, post_fit_bytes, "strap_cool_down_walk.fit", walk_bytes)
    stored = _stored(walk, "sport", "start_time")
    assert stored["sport"] == "running", stored
    assert stored["start_time"] > _stored(run, "start_time")["start_time"]

    resolved = _profile("max_hr_bpm", "threshold_hr_bpm")
    _assert_fit(resolved["max_hr_bpm"], 190, walk, stored["start_time"])
    _assert_fit(resolved["threshold_hr_bpm"], 169, walk, stored["start_time"])


# --- AC2 row 3: a tie in start time --------------------------------------------------------------


@pytest.mark.parametrize("last", ["dev_fields_run", "sample_run"])
def test_same_start_time_the_later_upload_wins(last: str, post_fit_bytes) -> None:
    """``dev_fields_run`` with ``session.start_time`` patched to ``sample_run``'s (a different
    device, so no 409): the later upload's values win, 82.6 / 189 / 162 against
    71.7 / 188 / 169."""
    dev_bytes = fit_patch.set_field(
        _raw("dev_fields_run.fit"), SESSION, START_TIME, _session_start_raw(_raw("sample_run.fit"))
    )
    with TestClient(app) as client:
        if last == "dev_fields_run":
            other = _upload(client, post_fit_bytes, "sample_run.fit")
            winner = _upload(client, post_fit_bytes, "dev_fields_run.fit", dev_bytes)
            expected = {"body_mass_kg": 82.6, "max_hr_bpm": 189, "threshold_hr_bpm": 162, "height_cm": 189}
        else:
            other = _upload(client, post_fit_bytes, "dev_fields_run.fit", dev_bytes)
            winner = _upload(client, post_fit_bytes, "sample_run.fit")
            expected = {"body_mass_kg": 71.7, "max_hr_bpm": 188, "threshold_hr_bpm": 169, "height_cm": 180}
    assert _stored(winner, "start_time") == _stored(other, "start_time")

    resolved = _profile(*expected)
    for field, value in expected.items():
        _assert_fit(resolved[field], value, winner)


# --- AC2 row 4: user_profile without zones_target ------------------------------------------------


def test_file_without_zones_target_leaves_hr_to_the_earlier_file(post_fit_bytes) -> None:
    """``wrist_ppg_run`` (08-21, max 189, threshold 169), then ``strap_run_hrv`` (09-06) with
    ``zones_target`` removed: max and threshold HR come from the earlier file, the body fields
    from the later one. Both files carry 71.7 kg, so the source session is what moves."""
    later_bytes = fit_patch.remove_messages(_raw("strap_run_hrv.fit"), ZONES_TARGET)
    with TestClient(app) as client:
        earlier = _upload(client, post_fit_bytes, "wrist_ppg_run.fit")
        later = _upload(client, post_fit_bytes, "strap_run_hrv.fit", later_bytes)
    stored = _stored(later, "max_hr_bpm", "threshold_hr_bpm", "body_mass_kg", "start_time")
    assert stored["max_hr_bpm"] is None and stored["threshold_hr_bpm"] is None, stored
    assert stored["body_mass_kg"] == 71.7 == _stored(earlier, "body_mass_kg")["body_mass_kg"]

    resolved = _profile()
    _assert_fit(resolved["max_hr_bpm"], 189, earlier)
    _assert_fit(resolved["threshold_hr_bpm"], 169, earlier)
    _assert_fit(resolved["body_mass_kg"], 71.7, later, stored["start_time"])
    _assert_fit(resolved["height_cm"], 180, later)
    _assert_fit(resolved["resting_hr_bpm"], 47, later)
    _assert_fit(resolved["sex"], "male", later)


# --- AC2 rows 5 to 7: entries --------------------------------------------------------------------


def test_no_file_carries_the_field_the_entry_is_effective(post_fit_bytes) -> None:
    """A stored run without ``zones_target`` and entered max 190, threshold 165 and a birth
    date (which no file carries): those three are ``entered``; the body fields stay ``fit``."""
    with TestClient(app) as client:
        run = _upload(
            client,
            post_fit_bytes,
            "strap_run_hrv.fit",
            fit_patch.remove_messages(_raw("strap_run_hrv.fit"), ZONES_TARGET),
        )
    set_at = _enter({"max_hr_bpm": 190, "threshold_hr_bpm": 165, "birth_date": "1980-05-01"})

    resolved = _profile()
    for field, value in (("max_hr_bpm", 190), ("threshold_hr_bpm", 165), ("birth_date", "1980-05-01")):
        entry = resolved[field]
        assert entry.source == "entered" and entry.value == value, entry
        assert entry.session_id is None and entry.recorded_at == set_at, entry
        assert entry.entered_value == value and entry.reason is None, entry
    _assert_fit(resolved["body_mass_kg"], 71.7, run)


def test_neither_file_nor_entry_is_unavailable_missing(post_fit_bytes) -> None:
    """A stored run without ``zones_target`` and no entries: max and threshold HR and the birth
    date are unavailable, reason ``missing``; an empty store makes every field so."""
    resolved = _profile()
    for field in profile.ENTERED_FIELDS:
        _assert_missing(resolved[field])

    with TestClient(app) as client:
        _upload(
            client,
            post_fit_bytes,
            "strap_run_hrv.fit",
            fit_patch.remove_messages(_raw("strap_run_hrv.fit"), ZONES_TARGET),
        )
    resolved = _profile()
    for field in ("max_hr_bpm", "threshold_hr_bpm", "birth_date"):
        _assert_missing(resolved[field])
        assert resolved[field].entered_value is None
    assert resolved["body_mass_kg"].source == "fit"


def test_cleared_entry_falls_to_unavailable_not_the_earlier_entry() -> None:
    """Max HR entered 190 and then cleared with null: unavailable, never back to 190. The
    threshold entry written beside the first max entry stands: each field resolves alone."""
    _enter({"max_hr_bpm": 190, "threshold_hr_bpm": 165}, set_at="2026-10-01T08:00:00+00:00")
    _enter({"max_hr_bpm": None}, set_at="2026-10-02T08:00:00+00:00")

    resolved = _profile("max_hr_bpm", "threshold_hr_bpm")
    _assert_missing(resolved["max_hr_bpm"])
    assert resolved["max_hr_bpm"].entered_value is None
    assert resolved["threshold_hr_bpm"].source == "entered"
    assert resolved["threshold_hr_bpm"].value == 165


def test_cleared_entry_falls_to_the_file_when_one_carries_the_field(post_fit_bytes) -> None:
    """An entry cleared after an earlier entry, with ``sample_run`` stored: the file's 188 is
    effective and no entered value is reported, never the earlier 195."""
    _enter({"max_hr_bpm": 195})
    _enter({"max_hr_bpm": None})
    with TestClient(app) as client:
        run = _upload(client, post_fit_bytes, "sample_run.fit")

    resolved = _profile("max_hr_bpm")
    _assert_fit(resolved["max_hr_bpm"], 188, run)
    assert resolved["max_hr_bpm"].entered_value is None


def test_the_entered_value_is_reported_when_a_file_shadows_it(post_fit_bytes) -> None:
    """Entered max 195 and a stored ``sample_run`` carrying 188: value 188 from the file, and
    the latest entered value 195 beside it (AC3 reads it)."""
    _enter({"max_hr_bpm": 195})
    with TestClient(app) as client:
        run = _upload(client, post_fit_bytes, "sample_run.fit")

    resolved = _profile("max_hr_bpm")
    _assert_fit(resolved["max_hr_bpm"], 188, run)
    assert resolved["max_hr_bpm"].entered_value == 195


# --- AC4: deleting the source session recomputes -----------------------------------------------


def test_delete_the_later_session_recomputes_from_the_earlier(post_fit_bytes) -> None:
    """The row 1 pair; deleting the 191 session makes the effective max 188 from
    ``sample_run``."""
    with TestClient(app) as client:
        sample = _upload(client, post_fit_bytes, "sample_run.fit")
        hilly = _upload(client, post_fit_bytes, "hilly_run_8k_fr945.fit", _hilly_with_max_191())
        _assert_fit(_profile("max_hr_bpm")["max_hr_bpm"], 191, hilly)
        assert client.delete(f"/sessions/{hilly}").status_code == 204

    resolved = _profile("max_hr_bpm")
    _assert_fit(resolved["max_hr_bpm"], 188, sample, _stored(sample, "start_time")["start_time"])


def test_delete_the_last_carrying_session_falls_to_the_entry(post_fit_bytes) -> None:
    """Entered max 195 under a stored ``sample_run``; deleting the run serves the entry."""
    set_at = _enter({"max_hr_bpm": 195})
    with TestClient(app) as client:
        run = _upload(client, post_fit_bytes, "sample_run.fit")
        _assert_fit(_profile("max_hr_bpm")["max_hr_bpm"], 188, run)
        assert client.delete(f"/sessions/{run}").status_code == 204

    entry = _profile("max_hr_bpm")["max_hr_bpm"]
    assert entry.source == "entered" and entry.value == 195 and entry.recorded_at == set_at, entry


def test_delete_the_last_carrying_session_leaves_it_unavailable(post_fit_bytes) -> None:
    """No entry; deleting the only stored run makes every field unavailable, ``missing``."""
    with TestClient(app) as client:
        run = _upload(client, post_fit_bytes, "sample_run.fit")
        assert client.delete(f"/sessions/{run}").status_code == 204

    resolved = _profile()
    for field in profile.ENTERED_FIELDS:
        _assert_missing(resolved[field])


def test_delete_never_sweeps_the_entered_values_table() -> None:
    """The entered-values table has no ``session_id`` column, so ``_child_tables()`` never
    names it and a session delete leaves every entry in place."""
    assert "session_id" not in db._expected_schema()[db.PROFILE_ENTRIES_TABLE]
    assert db.PROFILE_ENTRIES_TABLE not in db._child_tables()


# --- the resolver over plain rows ------------------------------------------------------------


def _row(session_id: str, start_time: str, upload_order: int, sport: str = "running", **values) -> dict:
    row = {"session_id": session_id, "start_time": start_time, "upload_order": upload_order, "sport": sport}
    row.update({field: None for field in profile.FIT_FIELDS})
    row.update(values)
    return row


def _entry(entry_id: int, field: str, value, set_at: str = "2026-10-01T00:00:00+00:00") -> dict:
    return {"entry_id": entry_id, "field": field, "value": value, "set_at": set_at}


def test_resolver_effective_value_takes_the_latest_start_time_not_the_latest_upload() -> None:
    rows = [
        _row("late", "2026-09-29T12:00:00+00:00", 1, max_hr_bpm=191),
        _row("early", "2026-09-01T12:00:00+00:00", 2, max_hr_bpm=188),
    ]
    resolved = profile.resolve(rows, [])
    assert (resolved["max_hr_bpm"].value, resolved["max_hr_bpm"].session_id) == (191, "late")


def test_resolver_effective_tie_in_start_time_goes_to_the_higher_upload_order() -> None:
    same = "2026-09-01T12:00:00+00:00"
    for first, second in ((("a", 1), ("b", 2)), (("b", 2), ("a", 1))):
        rows = [
            _row(first[0], same, first[1], body_mass_kg=70.0 if first[0] == "a" else 80.0),
            _row(second[0], same, second[1], body_mass_kg=70.0 if second[0] == "a" else 80.0),
        ]
        resolved = profile.resolve(rows, [])
        assert (resolved["body_mass_kg"].value, resolved["body_mass_kg"].session_id) == (80.0, "b")


def test_resolver_running_filter_reaches_max_and_threshold_only() -> None:
    rows = [
        _row("run", "2026-09-01T12:00:00+00:00", 1, max_hr_bpm=188, threshold_hr_bpm=169, resting_hr_bpm=47),
        _row(
            "snap", "2026-09-06T12:00:00+00:00", 2, sport="other",
            max_hr_bpm=187, threshold_hr_bpm=160, resting_hr_bpm=45,
        ),
    ]
    resolved = profile.resolve(rows, [])
    assert resolved["max_hr_bpm"].session_id == "run"
    assert resolved["threshold_hr_bpm"].session_id == "run"
    assert (resolved["resting_hr_bpm"].value, resolved["resting_hr_bpm"].session_id) == (45, "snap")


def test_resolver_effective_entry_is_the_last_written_and_a_null_clears_it() -> None:
    entries = [
        _entry(1, "max_hr_bpm", 190),
        _entry(2, "max_hr_bpm", 192, set_at="2026-10-02T00:00:00+00:00"),
    ]
    resolved = profile.resolve([], list(reversed(entries)))
    assert resolved["max_hr_bpm"].value == 192
    assert resolved["max_hr_bpm"].recorded_at == "2026-10-02T00:00:00+00:00"

    resolved = profile.resolve([], entries + [_entry(3, "max_hr_bpm", None)])
    assert resolved["max_hr_bpm"].reason == "missing"
    assert resolved["max_hr_bpm"].entered_value is None


def test_write_profile_entries_effective_rows_one_per_field_and_refuses_unknown_fields() -> None:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.write_profile_entries(conn, {"sex": "female", "height_cm": None})
        db.write_profile_entries(conn, {})
        with pytest.raises(ValueError, match="garmin_activity_class"):
            db.write_profile_entries(conn, {"garmin_activity_class": 50, "sex": "male"})
        _, entries = db.read_profile_inputs(conn)
    finally:
        conn.close()
    assert [(e["field"], e["value"]) for e in entries] == [("sex", "female"), ("height_cm", None)]
