"""``segments.recorded_totals``: the lean reader of recorded time and distance (F019 AC6).

The list page needs ``duration_s`` and ``distance_m`` for every session, and it
must get the same numbers F013's features route serves. So ``recorded_totals``
is not a second rule: it is ``build_time_base``'s ``T`` and ``D`` through the
same finite screen and the same per-segment primitive, in a loop that builds no
per-record dataclass. The parity pinned here is **exact** (``==``, never
``approx``): the per-row float operations run in the same order on both paths,
so the two sums are bit-identical.

Three kinds of evidence: constructed rows that reach every branch of the rule
(a ``dt = 0`` duplicate, a break over ``SEGMENT_MAX_DT_S``, a negative ``dt``,
absent and non-finite and boolean distances, a distance regression, rows with
no place on the time axis), the records of every real run in the fixture corpus
read back through the upload path (provenance table in ``fixtures/README.md``,
run proof ``yes``), and a one-primitive pin: replace the primitive and both
paths move together, so the moving-gap rule has exactly one place to change.
The timing test prints both paths' wall time over 50 x 6,000 rows and asserts
nothing; the budget belongs to the list route's own speed pin.
"""

from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from runcoach_api import db as db_module
from runcoach_api.main import app
from runcoach_api.metrics import segments as S

FIXTURES = Path(__file__).parent / "fixtures"
# Every fixture the provenance table gives run proof ``yes``.
RUNNING_FIXTURES = (
    "sample_run.fit",
    "dev_fields_run.fit",
    "wrist_ppg_run.fit",
    "strap_run_hrv.fit",
    "hilly_run_8k_fr945.fit",
    "hilly_long_run_17k_fr945.fit",
)

NAN = float("nan")
INF = float("inf")

# One list that walks every branch of the segment rule, in this order:
# a plain stride, a dt = 0 duplicate, a stride with the distance absent on one
# side (None, nan, inf, -inf, bool), a regression, a break (dt > 5), a stride
# exactly at the bound, a negative dt, then rows with no place on the time
# axis (``t`` missing, None, nan, inf) between two counted strides.
CONSTRUCTED_ROWS: list[dict] = [
    {"t": 0.0, "distance": 0.0},
    {"t": 1.0, "distance": 3.1},
    {"t": 1.0, "distance": 3.1},  # dt = 0 duplicate: not counted, not a break
    {"t": 2.0, "distance": None},  # absent distance: counted for time, 0 m
    {"t": 3.0, "distance": NAN},  # non-finite: absent
    {"t": 4.0, "distance": INF},
    {"t": 5.0, "distance": -INF},
    {"t": 6.0, "distance": True},  # bool: absent, never 1.0
    {"t": 7.0, "distance": 20.0},
    {"t": 8.0, "distance": 19.0},  # regression: 0 m, counted for time
    {"t": 9.0, "distance": 22.3},
    {"t": 15.5, "distance": 40.0},  # dt = 6.5 > 5: a break, nothing contributed
    {"t": 20.5, "distance": 55.0},  # dt = 5.0 exactly: counted
    {"t": 19.0, "distance": 60.0},  # negative dt: not counted, not a break
    {"t": 20.0, "distance": 63.0},
    {"distance": 64.0},  # no ``t`` key: dropped by the screen
    {"t": None, "distance": 65.0},
    {"t": NAN, "distance": 66.0},
    {"t": INF, "distance": 67.0},
    {"t": 21.0, "distance": 66.7},  # the stride from t = 20 spans the dropped rows
    {"t": 22, "distance": 70},  # ints are finite numbers
]

# The same branches as single cases, so a failure names the branch.
CASES = {
    "plain_stride": [{"t": 0.0, "distance": 0.0}, {"t": 1.0, "distance": 3.1}],
    "dt_zero": [{"t": 0.0, "distance": 0.0}, {"t": 0.0, "distance": 0.5}, {"t": 1.0, "distance": 3.0}],
    "break_over_bound": [
        {"t": 0.0, "distance": 0.0},
        {"t": 5.5, "distance": 10.0},
        {"t": 6.5, "distance": 13.0},
    ],
    "at_the_bound": [{"t": 0.0, "distance": 0.0}, {"t": 5.0, "distance": 10.0}],
    "negative_dt": [{"t": 2.0, "distance": 6.0}, {"t": 1.0, "distance": 3.0}, {"t": 3.0, "distance": 9.0}],
    "distance_none": [{"t": 0.0, "distance": 0.0}, {"t": 1.0, "distance": None}, {"t": 2.0, "distance": 6.0}],
    "distance_nan": [{"t": 0.0, "distance": 0.0}, {"t": 1.0, "distance": NAN}, {"t": 2.0, "distance": 6.0}],
    "distance_inf": [{"t": 0.0, "distance": 0.0}, {"t": 1.0, "distance": INF}, {"t": 2.0, "distance": 6.0}],
    "distance_neg_inf": [
        {"t": 0.0, "distance": 0.0},
        {"t": 1.0, "distance": -INF},
        {"t": 2.0, "distance": 6.0},
    ],
    "distance_bool": [{"t": 0.0, "distance": 0.0}, {"t": 1.0, "distance": True}, {"t": 2.0, "distance": 6.0}],
    "distance_missing_key": [{"t": 0.0, "distance": 0.0}, {"t": 1.0}, {"t": 2.0, "distance": 6.0}],
    "regression": [{"t": 0.0, "distance": 10.0}, {"t": 1.0, "distance": 9.0}, {"t": 2.0, "distance": 12.0}],
    "no_t_key": [{"t": 0.0, "distance": 0.0}, {"distance": 1.0}, {"t": 1.0, "distance": 3.0}],
    "t_none": [{"t": 0.0, "distance": 0.0}, {"t": None, "distance": 1.0}, {"t": 1.0, "distance": 3.0}],
    "t_nan": [{"t": 0.0, "distance": 0.0}, {"t": NAN, "distance": 1.0}, {"t": 1.0, "distance": 3.0}],
    "t_inf": [{"t": 0.0, "distance": 0.0}, {"t": INF, "distance": 1.0}, {"t": 1.0, "distance": 3.0}],
    "t_bool": [{"t": 0.0, "distance": 0.0}, {"t": True, "distance": 1.0}, {"t": 1.0, "distance": 3.0}],
    "single_row": [{"t": 0.0, "distance": 0.0}],
    "no_rows": [],
    "only_dropped_rows": [{"t": None}, {"distance": 3.0}],
}


def _time_base_totals(rows: list[dict]) -> tuple[float, float]:
    tb = S.build_time_base(S.screen(rows))
    return tb.T, tb.D


def _same_bits(a: float, b: float) -> bool:
    """``==`` plus the sign of zero, so ``-0.0`` and ``0.0`` do not pass as one."""
    return a == b and math.copysign(1.0, a) == math.copysign(1.0, b)


# -- exact parity with the time base ------------------------------------------------------------


def test_recorded_totals_equals_the_time_base_on_constructed_rows():
    T, D = _time_base_totals(CONSTRUCTED_ROWS)
    totals = S.recorded_totals(CONSTRUCTED_ROWS)
    assert totals == (T, D)
    assert _same_bits(totals[0], T) and _same_bits(totals[1], D)
    # The list reaches every branch: the totals are not the trivial ones.
    assert T == 17.0, T  # twelve unit strides and the one 5 s stride at the bound
    assert D == pytest.approx(3.1 + 3.3 + 15.0 + 3.0 + 3.7 + 3.3), D


@pytest.mark.parametrize("name", sorted(CASES))
def test_recorded_totals_equals_the_time_base_on_each_branch(name):
    rows = CASES[name]
    assert S.recorded_totals(rows) == _time_base_totals(rows)


def test_recorded_totals_of_no_rows_is_zero_and_zero():
    assert S.recorded_totals([]) == (0.0, 0.0)
    assert S.recorded_totals(iter([])) == (0.0, 0.0)


def test_recorded_totals_accepts_an_iterator_and_sqlite_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE r (t REAL, distance REAL)")
    conn.executemany("INSERT INTO r VALUES (?, ?)", [(0.0, 0.0), (1.0, 3.0), (2.0, None), (9.0, 30.0)])
    rows = conn.execute("SELECT t, distance FROM r ORDER BY t").fetchall()
    assert S.recorded_totals(iter(rows)) == _time_base_totals(rows) == (2.0, 3.0)


def test_totals_columns_name_what_the_reader_reads():
    assert S.TOTALS_COLUMNS == ("t", "distance")
    # A row carrying only those columns gives the same totals as a full record.
    full = [{**row, "speed": 3.0, "altitude": 10.0, "heart_rate": 150} for row in CONSTRUCTED_ROWS]
    narrow = [{k: row[k] for k in S.TOTALS_COLUMNS if k in row} for row in CONSTRUCTED_ROWS]
    assert S.recorded_totals(narrow) == S.recorded_totals(full) == _time_base_totals(full)


_value = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10_000, max_value=10_000),
    st.floats(allow_nan=True, allow_infinity=True, width=32),
)
_row = st.fixed_dictionaries({}, optional={"t": _value, "distance": _value})


@settings(max_examples=300, deadline=None)
@given(st.lists(_row, max_size=40))
def test_recorded_totals_equals_the_time_base_on_random_rows(rows):
    T, D = _time_base_totals(rows)
    totals = S.recorded_totals(rows)
    assert totals == (T, D)
    assert _same_bits(totals[0], T) and _same_bits(totals[1], D)


# -- the records of every running fixture, through the upload path --------------------------------


def _upload(client: TestClient, filename: str) -> str:
    created = client.post("/sessions", files={"file": (filename, (FIXTURES / filename).read_bytes())})
    assert created.status_code == 201, created.text
    return created.json()["session_id"]


@pytest.mark.parametrize("filename", RUNNING_FIXTURES)
def test_recorded_totals_equals_the_time_base_on_every_running_fixture(filename):
    with TestClient(app) as client:
        session_id = _upload(client, filename)
    conn = db_module.get_connection()
    try:
        inputs = db_module.read_session_feature_inputs(conn, session_id)
    finally:
        conn.close()
    assert inputs is not None
    _session, rows = inputs
    assert len(rows) > 100, filename
    T, D = _time_base_totals(rows)
    totals = S.recorded_totals(rows)
    assert totals == (T, D)
    assert _same_bits(totals[0], T) and _same_bits(totals[1], D)
    assert T > 0.0 and D > 0.0, (filename, totals)


# -- one primitive, one place -------------------------------------------------------------------


def test_segment_and_recorded_totals_share_one_primitive(monkeypatch):
    """Replace the rule and both paths move together: the moving-gap change edits one function."""
    rows = [{"t": 0.0, "distance": 0.0}, {"t": 1.0, "distance": 3.0}, {"t": 2.0, "distance": 6.0}]
    before = S.recorded_totals(rows)
    assert before == _time_base_totals(rows) == (2.0, 6.0)

    original = S._contribution

    def doubled(dt, d0, d1):
        counted, contributed_m, regressed = original(dt, d0, d1)
        return S.Contribution(counted, 2.0 * contributed_m, regressed)

    monkeypatch.setattr(S, "_contribution", doubled)
    assert S.recorded_totals(rows) == (2.0, 12.0)
    assert _time_base_totals(rows) == (2.0, 12.0)


def test_contribution_is_the_rule_the_module_docstring_states():
    bound = S.SEGMENT_MAX_DT_S
    assert S._contribution(1.0, 0.0, 3.0) == (True, 3.0, False)
    assert S._contribution(bound, 0.0, 3.0) == (True, 3.0, False)
    assert S._contribution(bound + 1e-9, 0.0, 3.0) == (False, 0.0, False)
    assert S._contribution(0.0, 0.0, 3.0) == (False, 0.0, False)
    assert S._contribution(-1.0, 0.0, 3.0) == (False, 0.0, False)
    assert S._contribution(1.0, 3.0, 2.0) == (True, 0.0, True)
    assert S._contribution(1.0, None, 2.0) == (True, 0.0, False)
    assert S._contribution(1.0, 3.0, None) == (True, 0.0, False)
    assert S._contribution(10.0, 3.0, 2.0) == (False, 0.0, False)  # a break is never a regression


# -- timing: the lean loop against the dataclass path ----------------------------------------------


def test_recorded_totals_is_faster_than_the_dataclass_path_over_50_sessions():
    one_session = [
        {"t": float(k), "distance": 2.9 * k + 0.1 * (k % 7), "speed": 2.9, "altitude": 10.0}
        for k in range(6_000)
    ]
    sessions = [list(one_session) for _ in range(50)]
    t0 = time.perf_counter()
    lean = [S.recorded_totals(rows) for rows in sessions]
    t_lean = time.perf_counter() - t0
    t0 = time.perf_counter()
    heavy = [_time_base_totals(rows) for rows in sessions]
    t_heavy = time.perf_counter() - t0
    assert lean == heavy
    print(
        f"\nrecorded_totals over 50 x 6000 rows: {t_lean:.3f} s; "
        f"build_time_base(screen(rows)): {t_heavy:.3f} s; ratio {t_heavy / t_lean:.1f}x"
    )
