"""F016 AC5: the four HR anchors, the ordering rule and the anchor version.

The rule (``spec/references/F016-athlete-profile.md``, "The anchor lookup"),
applied to each field's effective value from ``profile.resolve``:

- ``resting_hr_bpm < max_hr_bpm`` is required for both; failing it makes both
  ``order_conflict``;
- threshold HR is checked against whichever of resting and max are available
  and not in conflict, strictly above resting and below max, and fails alone
  as ``order_conflict``; when resting and max conflict it is checked against
  neither;
- a field with no value reports ``missing``, never ``order_conflict``;
- ``sex`` is never ``order_conflict``; absent or ``unspecified`` is ``missing``;
- no plausibility range.

The version (user rulings, 2026-10-06) changes when, and only when, the
anchor's served value changes. An unavailable period does not move it, and
neither does the source.

Three halves:

1. **The ordering rule over plain values** (``profile.order_rule``), one row
   per case. The rows were written from the reference's rule text before the
   implementation, and include the rows expected to be uninteresting.
2. **The invariant** a consumer divides by: when resting and max are both
   served, ``max - resting > 0``. Exhaustive over a small grid, then driven
   through the real path (a stored file plus an entry) for the equal and
   below pairs.
3. **Version sequences through ``POST`` / ``DELETE /sessions`` and
   ``db.write_profile_entries``** (the function ``PATCH /me`` calls). Every
   fixture carries resting, max and threshold HR, so an entry is always
   shadowed by a file; the entry-driven rows use ``sample_run`` patched
   through ``tests/support/fit_patch.py`` so the field falls to the entry.
   Each row reads the version log table directly after each write, so a
   write path that skipped the log cannot pass on a later read.

Corpus facts relied on (decoded with ``fitdecode``): ``hilly_long_run_17k_fr945``
2026-08-09, max 188, threshold 168; ``wrist_ppg_run`` 2026-08-21, 189/169;
``sample_run`` 2026-09-01, 188/169; ``hilly_run_8k_fr945`` 2026-09-29,
188/169; all four running, resting 47, male.
"""

from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path

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
USER_PROFILE, ZONES_TARGET = 3, 7
RESTING_HEART_RATE = 8  # user_profile.resting_heart_rate, uint8

HR = ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm")


# --- half 1: the ordering rule over plain values ---------------------------------------------


def _resolved(resting=None, max_hr=None, threshold=None, sex=None) -> dict[str, profile.FieldValue]:
    """A resolver result holding these values as entries (``None`` = missing)."""
    values = {
        "resting_hr_bpm": resting,
        "max_hr_bpm": max_hr,
        "threshold_hr_bpm": threshold,
        "sex": sex,
    }
    entries = [
        {"entry_id": i, "field": f, "value": v, "set_at": "2026-10-06T00:00:00+00:00"}
        for i, (f, v) in enumerate(values.items(), start=1)
        if v is not None
    ]
    return profile.resolve([], entries)


S, C, M = None, "order_conflict", "missing"  # served, conflict, missing

# (id, resting, max, threshold, sex, expected reasons for resting, max, threshold, sex)
ORDER_ROWS = [
    ("all_in_order_served", 47, 188, 169, "male", (S, S, S, S)),
    ("resting_equal_max_order_conflict", 188, 188, 169, "male", (C, C, S, S)),
    ("max_below_resting_order_conflict", 190, 188, 169, "male", (C, C, S, S)),
    ("resting_max_conflict_threshold_checked_against_neither", 190, 188, 250, "male", (C, C, S, S)),
    ("resting_missing_threshold_above_max_order_conflict", None, 150, 170, "male", (M, S, C, S)),
    ("resting_missing_threshold_below_max_served_in_order", None, 188, 150, "male", (M, S, S, S)),
    ("max_missing_threshold_below_resting_order_conflict", 47, None, 40, "male", (S, M, C, S)),
    ("max_missing_threshold_above_resting_served_in_order", 47, None, 169, "male", (S, M, S, S)),
    ("threshold_equal_resting_order_conflict", 47, 188, 47, "male", (S, S, C, S)),
    ("threshold_equal_max_order_conflict", 47, 188, 188, "male", (S, S, C, S)),
    ("threshold_above_max_order_conflict", 47, 188, 200, "male", (S, S, C, S)),
    ("threshold_below_resting_order_conflict", 47, 188, 40, "male", (S, S, C, S)),
    ("resting_and_max_missing_threshold_served_no_order", None, None, 170, "male", (M, M, S, S)),
    ("threshold_missing_never_order_conflict", 188, 150, None, "male", (C, C, M, S)),
    ("all_missing_never_order_conflict", None, None, None, None, (M, M, M, M)),
    ("max_300_no_plausibility_range_in_order", 47, 300, 169, "female", (S, S, S, S)),
    ("sex_unspecified_missing_never_order_conflict", 190, 188, 169, "unspecified", (C, C, S, M)),
    ("sex_absent_missing_never_order_conflict", 47, 188, 169, None, (S, S, S, M)),
]


@pytest.mark.parametrize(
    ("resting", "max_hr", "threshold", "sex", "expected"),
    [pytest.param(*row[1:], id=row[0]) for row in ORDER_ROWS],
)
def test_order_rule(resting, max_hr, threshold, sex, expected) -> None:
    reasons = profile.order_rule(_resolved(resting, max_hr, threshold, sex))
    got = tuple(reasons[f] for f in profile.ANCHOR_FIELDS)
    print(f"  resting={resting} max={max_hr} threshold={threshold} sex={sex} -> {got}")
    assert got == expected


def test_anchor_lookup_reports_value_source_and_reason_per_order_rule() -> None:
    """``profile.anchors`` serves an in-order field with its resolver source and the logged
    version, and an out-of-order one as unavailable with no value."""
    resolved = _resolved(190, 188, 169, "male")
    logged = {"threshold_hr_bpm": (169, 4), "sex": ("male", 1)}
    served = profile.anchors(resolved, logged)
    for field in ("resting_hr_bpm", "max_hr_bpm"):
        anchor = served[field]
        assert anchor.reason == "order_conflict" and anchor.value is None, anchor
        assert anchor.source is None and anchor.version is None, anchor
    threshold = served["threshold_hr_bpm"]
    assert (threshold.value, threshold.source, threshold.version, threshold.reason) == (
        169,
        "entered",
        4,
        None,
    )
    assert threshold.recorded_at == "2026-10-06T00:00:00+00:00"
    assert served["sex"].version == 1


def test_anchor_lookup_refuses_a_log_that_does_not_hold_the_served_value() -> None:
    """A served value the version log does not hold means a write path skipped the log: the
    lookup raises rather than serving a version that names another value."""
    resolved = _resolved(47, 188, 169, "male")
    with pytest.raises(ValueError, match="max_hr_bpm"):
        profile.anchors(resolved, {"resting_hr_bpm": (47, 1), "max_hr_bpm": (189, 2),
                                   "threshold_hr_bpm": (169, 1), "sex": ("male", 1)})


def test_anchor_next_versions_bump_only_on_a_changed_served_value() -> None:
    """``profile.next_versions`` returns only the anchors whose log row changes: a new served
    value bumps by one, the same value does nothing whatever its source, an unavailable
    anchor does nothing, and a first served value starts at 1."""
    logged = {"resting_hr_bpm": (47, 3), "max_hr_bpm": (188, 2), "threshold_hr_bpm": (169, 5)}
    # resting 47 unchanged; max 189 changes; threshold conflicts (above max); sex first served.
    changes = profile.next_versions(_resolved(47, 189, 200, "male"), logged)
    assert {f: (c.value, c.version) for f, c in changes.items()} == {
        "max_hr_bpm": (189, 3),
        "sex": ("male", 1),
    }
    assert changes["max_hr_bpm"].source == "entered"
    assert changes["max_hr_bpm"].source_session_id is None


# --- half 2: the invariant -------------------------------------------------------------------


def test_anchor_invariant_max_minus_resting_positive_over_a_grid() -> None:
    """For every resting/max pair over a grid with missing values, equal values and both
    orders: when both are served, ``max - resting > 0``. The grid includes the breaking
    pairs (equal, below) and confirms they are the ones refused."""
    grid = [None, 0, 1, 47, 187, 188, 189, 300]
    refused = 0
    for resting, max_hr in itertools.product(grid, grid):
        reasons = profile.order_rule(_resolved(resting, max_hr, None, None))
        if reasons["resting_hr_bpm"] is None and reasons["max_hr_bpm"] is None:
            assert max_hr - resting > 0, (resting, max_hr)
        elif resting is not None and max_hr is not None:
            assert max_hr <= resting, (resting, max_hr, reasons)
            refused += 1
    print(f"  refused {refused} breaking pairs")
    assert refused == sum(1 for r, m in itertools.product(grid, grid) if None not in (r, m) and m <= r)


# --- half 3: through the real path -----------------------------------------------------------


def _raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _upload(client: TestClient, post_fit_bytes, name: str, data: bytes | None = None) -> str:
    response = post_fit_bytes(client, name, _raw(name) if data is None else data)
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _delete(client: TestClient, session_id: str) -> None:
    response = client.delete(f"/sessions/{session_id}")
    assert response.status_code == 204, response.text


def _enter(values: dict) -> None:
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.write_profile_entries(conn, values)
    finally:
        conn.close()


def _log() -> dict[str, tuple]:
    """The version log table as stored: ``{anchor: (value, version, source, source_session_id)}``."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            f"SELECT anchor, value, version, source, source_session_id FROM {db.ANCHOR_VERSIONS_TABLE}"
        ).fetchall()
    finally:
        conn.close()
    return {
        r["anchor"]: (db._json_load(r["value"]), r["version"], r["source"], r["source_session_id"])
        for r in rows
    }


def _anchors(label: str) -> dict[str, profile.Anchor]:
    conn = db.get_connection()
    try:
        served = db.read_hr_anchors(conn)
    finally:
        conn.close()
    print(f"  {label}")
    for field in profile.ANCHOR_FIELDS:
        a = served[field]
        print(f"    {field:<18} value={a.value} source={a.source} version={a.version} reason={a.reason}")
    return served


def _sample_run_without_resting() -> bytes:
    """``sample_run`` with ``user_profile.resting_heart_rate`` 0, which stores absent, so
    resting HR falls to an entry."""
    return fit_patch.set_field(_raw("sample_run.fit"), USER_PROFILE, RESTING_HEART_RATE, 0)


def test_version_moves_only_when_the_value_moves(post_fit_bytes) -> None:
    """188 -> 189 -> 188 -> 188 in chronological order, unpatched: max HR's version moves at
    uploads 2 and 3, not at 4. Threshold 168 -> 169 -> 169 -> 169 moves once; resting 47 and
    sex never move. The log is read straight after each upload, before any lookup."""
    sequence = [
        "hilly_long_run_17k_fr945.fit",
        "wrist_ppg_run.fit",
        "sample_run.fit",
        "hilly_run_8k_fr945.fit",
    ]
    expected = {
        "max_hr_bpm": [(188, 1), (189, 2), (188, 3), (188, 3)],
        "threshold_hr_bpm": [(168, 1), (169, 2), (169, 2), (169, 2)],
        "resting_hr_bpm": [(47, 1)] * 4,
        "sex": [("male", 1)] * 4,
    }
    starts = []
    with TestClient(app) as client:
        for i, name in enumerate(sequence):
            session_id = _upload(client, post_fit_bytes, name)
            log = _log()
            for field, rows in expected.items():
                assert log[field][:2] == rows[i], (name, field, log[field])
            assert log["max_hr_bpm"][2:] == ("fit", session_id) or i == 3, log["max_hr_bpm"]
            served = _anchors(name)
            starts.append(served["max_hr_bpm"].recorded_at)
            for field, rows in expected.items():
                anchor = served[field]
                assert (anchor.value, anchor.version) == rows[i], (name, anchor)
                assert anchor.source == "fit" and anchor.session_id == session_id, anchor
    assert starts == sorted(starts) and len(set(starts)) == 4, starts
    # The fourth upload kept version 3, so the log still names the session that began it.
    assert _log()["max_hr_bpm"][3] != served["max_hr_bpm"].session_id


def test_order_conflict_period_keeps_the_version(post_fit_bytes) -> None:
    """``sample_run`` without resting HR: max 188 is version 1. An entered resting 190 makes
    resting and max ``order_conflict`` (threshold, checked against neither, stays served) and
    leaves the log untouched; clearing the entry brings 188 back with the same version."""
    with TestClient(app) as client:
        session_id = _upload(client, post_fit_bytes, "sample_run.fit", _sample_run_without_resting())
    before = _anchors("stored, no resting")
    assert before["resting_hr_bpm"].reason == "missing"
    assert (before["max_hr_bpm"].value, before["max_hr_bpm"].version) == (188, 1)
    log_before = _log()
    assert "resting_hr_bpm" not in log_before

    _enter({"resting_hr_bpm": 190})
    conflict = _anchors("entered resting 190")
    for field in ("resting_hr_bpm", "max_hr_bpm"):
        assert conflict[field].reason == "order_conflict", conflict[field]
        assert conflict[field].value is None and conflict[field].version is None
    assert conflict["threshold_hr_bpm"].value == 169 and conflict["threshold_hr_bpm"].reason is None
    assert _log() == log_before

    _enter({"resting_hr_bpm": None})
    after = _anchors("resting entry cleared")
    assert (after["max_hr_bpm"].value, after["max_hr_bpm"].version) == (188, 1)
    assert after["max_hr_bpm"].session_id == session_id
    assert after["resting_hr_bpm"].reason == "missing"
    assert _log() == log_before


def test_anchor_invariant_holds_through_a_stored_file_and_an_entry(post_fit_bytes) -> None:
    """The published invariant through the real path: ``sample_run`` without resting HR (max
    188), then entered resting 188 (equal) and 189 (below max's side), each refused as
    ``order_conflict``; entered 187 is served, and ``max - resting`` is 1."""
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, "sample_run.fit", _sample_run_without_resting())
    for resting in (188, 189):
        _enter({"resting_hr_bpm": resting})
        served = _anchors(f"entered resting {resting}")
        assert served["resting_hr_bpm"].reason == "order_conflict", served["resting_hr_bpm"]
        assert served["max_hr_bpm"].reason == "order_conflict", served["max_hr_bpm"]
    _enter({"resting_hr_bpm": 187})
    served = _anchors("entered resting 187")
    resting, max_hr = served["resting_hr_bpm"], served["max_hr_bpm"]
    assert resting.reason is None and max_hr.reason is None
    assert max_hr.value - resting.value > 0
    assert (resting.source, resting.version, max_hr.version) == ("entered", 1, 1)


def test_delete_that_changes_the_value_moves_the_version(post_fit_bytes) -> None:
    """``sample_run`` (09-01, 188) then the earlier ``wrist_ppg_run`` (08-21, 189): 188 stays,
    version 1. Deleting ``sample_run`` makes 189 effective: version 2."""
    with TestClient(app) as client:
        sample = _upload(client, post_fit_bytes, "sample_run.fit")
        wrist = _upload(client, post_fit_bytes, "wrist_ppg_run.fit")
        assert _log()["max_hr_bpm"][:2] == (188, 1)
        _delete(client, sample)
        assert _log()["max_hr_bpm"] == (189, 2, "fit", wrist)
    served = _anchors("sample_run deleted")
    assert (served["max_hr_bpm"].value, served["max_hr_bpm"].version) == (189, 2)
    assert served["max_hr_bpm"].session_id == wrist
    assert served["threshold_hr_bpm"].version == 1


def test_delete_that_keeps_the_value_keeps_the_version(post_fit_bytes) -> None:
    """``sample_run`` and ``hilly_run_8k_fr945`` both carry 188: deleting the later one moves
    the source back to ``sample_run`` and leaves the version at 1."""
    with TestClient(app) as client:
        sample = _upload(client, post_fit_bytes, "sample_run.fit")
        hilly = _upload(client, post_fit_bytes, "hilly_run_8k_fr945.fit")
        assert _anchors("both stored")["max_hr_bpm"].session_id == hilly
        _delete(client, hilly)
    served = _anchors("hilly deleted")
    assert (served["max_hr_bpm"].value, served["max_hr_bpm"].version) == (188, 1)
    assert served["max_hr_bpm"].session_id == sample


def test_delete_of_the_last_session_leaves_the_version_log(post_fit_bytes) -> None:
    """Deleting every session makes the anchors ``missing``; the log keeps its rows (it has no
    ``session_id`` column, so the delete sweep never reaches it), and storing the file again
    serves 188 with the same version."""
    with TestClient(app) as client:
        session_id = _upload(client, post_fit_bytes, "sample_run.fit")
        log_before = _log()
        _delete(client, session_id)
        assert _log() == log_before
        assert _anchors("all deleted")["max_hr_bpm"].reason == "missing"
        again = _upload(client, post_fit_bytes, "sample_run.fit")
    served = _anchors("stored again")
    assert (served["max_hr_bpm"].value, served["max_hr_bpm"].version) == (188, 1)
    assert served["max_hr_bpm"].session_id == again
    assert "anchor_versions" not in db._child_tables()


def test_entered_value_that_changes_the_effective_value_moves_the_version(post_fit_bytes) -> None:
    """``sample_run`` without resting HR, so resting falls to the entry: entered 50 is version
    1, 52 is version 2, 52 again keeps it. A later file carrying resting 47 changes the
    value (version 3); an entry it shadows (55) changes nothing."""
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, "sample_run.fit", _sample_run_without_resting())
        steps = [(50, 1), (52, 2), (52, 2)]
        for value, version in steps:
            _enter({"resting_hr_bpm": value})
            assert _log()["resting_hr_bpm"][:3] == (value, version, "entered")
            served = _anchors(f"entered resting {value}")["resting_hr_bpm"]
            assert (served.value, served.source, served.version) == (value, "entered", version)
        later = _upload(client, post_fit_bytes, "hilly_run_8k_fr945.fit")
    assert _log()["resting_hr_bpm"] == (47, 3, "fit", later)
    _enter({"resting_hr_bpm": 55})
    served = _anchors("entered 55, shadowed")["resting_hr_bpm"]
    assert (served.value, served.source, served.version) == (47, "fit", 3)
    assert served.entered_value == 55


def test_source_change_with_the_same_value_keeps_the_version(post_fit_bytes) -> None:
    """``sample_run`` without resting HR and entered resting 47: version 1, source entered.
    A later file carrying 47 takes over the source; the version stays 1."""
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, "sample_run.fit", _sample_run_without_resting())
        _enter({"resting_hr_bpm": 47})
        assert _anchors("entered 47")["resting_hr_bpm"].source == "entered"
        later = _upload(client, post_fit_bytes, "hilly_run_8k_fr945.fit")
    served = _anchors("file 47")["resting_hr_bpm"]
    assert (served.value, served.source, served.session_id, served.version) == (47, "fit", later, 1)


def test_duplicate_upload_leaves_the_version_log_untouched(post_fit_bytes) -> None:
    """A second upload of the stored file is refused (409) and the log is unchanged."""
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, "sample_run.fit")
        log_before = _log()
        response = post_fit_bytes(client, "sample_run.fit", _raw("sample_run.fit"))
        assert response.status_code == 409, response.text
    assert _log() == log_before


def test_failed_ingest_rolls_back_the_version_log_write(post_fit_bytes, monkeypatch) -> None:
    """The log write is in the ingest transaction: a failure raised straight after it rolls
    back the session and the log rows together."""
    original = db._write_anchor_log
    written: list[int] = []

    def write_then_fail(conn, changes):
        original(conn, changes)
        if changes:
            written.append(len(changes))
            raise RuntimeError("simulated failure after the version log write")

    monkeypatch.setattr(db, "_write_anchor_log", write_then_fail)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = post_fit_bytes(client, "sample_run.fit", _raw("sample_run.fit"))
    assert written == [4], written
    assert response.status_code == 500, response.text
    assert _log() == {}
    conn = db.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    finally:
        conn.close()


def test_init_schema_versions_a_store_that_predates_the_log(post_fit_bytes) -> None:
    """A store whose log rows are gone (as on a database that predates the table) is given
    version 1 for each served value by ``init_schema``, so the lookup can serve it."""
    with TestClient(app) as client:
        _upload(client, post_fit_bytes, "sample_run.fit")
    conn = db.get_connection()
    try:
        with conn:
            conn.execute(f"DELETE FROM {db.ANCHOR_VERSIONS_TABLE}")
        with pytest.raises(ValueError):
            db.read_hr_anchors(conn)
        db.init_schema(conn)
    finally:
        conn.close()
    served = _anchors("after init_schema")
    assert all(served[f].version == 1 for f in profile.ANCHOR_FIELDS), served
