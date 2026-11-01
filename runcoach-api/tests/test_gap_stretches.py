"""F033 AC2, the pure half: a run's best GAP stretch at each length (reference section 3).

``metrics.gap_stretches.best_stretches`` reads F013's per-segment stream (``session_features.
segment_rows``, counted segments only). A segment's GAP distance is ``v_actual * g * dt``; the best
stretch at length L is the largest GAP distance over a window of consecutive segments whose summed
``dt`` first reaches L, scaled to exactly L. A window holding a moving-gap segment, or one that
crosses a pause over ``pause_break_s`` (10 s; 10.0 s is spanned, 10.1 s breaks), is not eligible.

The synthetic tests isolate one rule each; the brute force below is written from the reference text
and imports nothing from the module but the function under test. The two fixture runs have run
proof ``yes`` in ``fixtures/README.md`` (``wrist_ppg_run``: real run, 1 Hz, 81 s and 11 s pauses;
``strap_run_hrv``: real run, 1 Hz, 229 s pause) and are read through the upload path and the shipped
``segment_rows``.
"""

from __future__ import annotations

import ast
import math
import random
from itertools import pairwise
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api.main import app
from runcoach_api.metrics import gap_stretches as GS
from runcoach_api.metrics import session_features as SF

FIXTURES = Path(__file__).parent / "fixtures"
MODULE = Path(GS.__file__)
LENGTHS = (120, 180, 300, 480, 720, 1200)


def _seg(t: float, dt: float = 1.0, v: float = 3.0, g: float = 1.0, moving_gap: bool = False) -> dict:
    return {"t_start": t, "dt": dt, "v_actual": v, "g": g, "moving_gap": moving_gap}


def _run(
    speeds: list[float], *, pauses: dict[int, float] | None = None, gaps: set[int] = frozenset()
) -> list[dict]:
    """1 s segments at ``speeds``; ``pauses[k]`` seconds of uncounted time before segment k."""
    pauses = pauses or {}
    out, t = [], 0.0
    for k, v in enumerate(speeds):
        t += pauses.get(k, 0.0)
        out.append(_seg(t, 1.0, v, moving_gap=k in gaps))
        t += 1.0
    return out


# -- the oracle: an O(n^2) search written from the reference text ---------------------------------


def _brute(segs: list[dict], length: float, pause_break_s: float = 10.0):
    best = None
    for i in range(len(segs)):
        total = dist = 0.0
        ok = True
        for j in range(i, len(segs)):
            s = segs[j]
            if s["moving_gap"]:
                ok = False
            if j > i:
                prev = segs[j - 1]
                if s["t_start"] - (prev["t_start"] + prev["dt"]) > pause_break_s:
                    ok = False
            total += s["dt"]
            dist += s["v_actual"] * s["g"] * s["dt"]
            if total >= length:
                if ok:
                    scaled = dist * length / total
                    if best is None or scaled > best[0]:
                        best = (scaled, total, segs[i]["t_start"])
                break
    return best


def test_best_stretch_matches_a_brute_force_search():
    rng = random.Random(20261101)
    segs, t = [], 0.0
    n = 1900
    pause_at = {300: 5.0, 700: 10.0, 1100: 10.1}
    gap_at = 1500
    for k in range(n):
        t += pause_at.get(k, 0.0)
        dt = rng.choice((1.0, 2.0))
        segs.append(_seg(t, dt, v=rng.uniform(2.0, 5.0), g=rng.uniform(0.85, 1.25), moving_gap=k == gap_at))
        t += dt
    got = GS.best_stretches(segs)
    assert GS.STRETCH_LENGTHS_S == LENGTHS and GS.PAUSE_BREAK_S == 10.0
    assert set(got) == set(LENGTHS)
    for length in LENGTHS:
        want = _brute(segs, length)
        b = got[length]
        print(
            f"L={length}: module {b.distance_m:.6f} m over {b.counted_s} s from t={b.first_t_start}; brute {want}"
        )
        assert b.length_s == length
        assert b.distance_m == pytest.approx(want[0], rel=1e-12)
        assert (b.counted_s, b.first_t_start) == (want[1], want[2])
    # The no-pause variant agrees with the brute force too, so the pause rule is what differs.
    free = GS.best_stretches(segs, pause_break_s=math.inf)
    for length in LENGTHS:
        want = _brute(segs, length, math.inf)
        assert free[length].distance_m == pytest.approx(want[0], rel=1e-12)
        assert free[length].first_t_start == want[2]


def test_a_moving_gap_window_is_not_eligible():
    # 300 s at 5 m/s (a moving gap in its middle), 60 s at 3, 300 s at 4, 400 s at 3.
    speeds = [5.0] * 300 + [3.0] * 60 + [4.0] * 300 + [3.0] * 400
    gapped = _run(speeds, gaps={150})
    best = GS.best_stretches(gapped, lengths=(300,))[300]
    # The next-fastest eligible window starts just after the gap: 149 s at 5, 60 at 3, 91 at 4.
    assert best == GS.BestStretch(length_s=300, distance_m=1289.0, counted_s=300.0, first_t_start=151.0)
    # The same run without the flag: the 5 m/s block wins, so the flag is what moved the best.
    plain = GS.best_stretches(_run(speeds), lengths=(300,))[300]
    assert plain == GS.BestStretch(length_s=300, distance_m=1500.0, counted_s=300.0, first_t_start=0.0)


@pytest.mark.parametrize(("pause", "spanned"), [(10.0, True), (10.1, False)], ids=["10.0", "10.1"])
def test_a_pause_of_10_0_s_is_spanned_and_10_1_s_breaks(pause, spanned):
    # 200 s at 3 m/s, 100 s at 4, the pause, 100 s at 4, 200 s at 3.
    speeds = [3.0] * 200 + [4.0] * 200 + [3.0] * 200
    segs = _run(speeds, pauses={300: pause})
    best = GS.best_stretches(segs, lengths=(120,))[120]
    assert best.counted_s == 120.0  # the pause's time is not counted
    if spanned:
        assert best.distance_m == 480.0 and best.first_t_start == 200.0
    else:
        # Either side holds 100 s at 4 and 20 s at 3: the earlier window wins the tie.
        assert best.distance_m == 460.0 and best.first_t_start == 180.0
    # A pause before a window's first segment is outside the window: the fastest block starts on
    # the first segment after the pause and is eligible either way.
    after = GS.best_stretches(
        _run([3.0] * 200 + [5.0] * 120 + [3.0] * 100, pauses={200: pause}), lengths=(120,)
    )
    assert after[120] == GS.BestStretch(
        length_s=120, distance_m=600.0, counted_s=120.0, first_t_start=200.0 + pause
    )


def test_a_run_shorter_than_a_length_has_no_stretch():
    segs = _run([3.0] * 299)
    got = GS.best_stretches(segs)
    assert set(got) == {120, 180}
    assert GS.best_stretches(_run([3.0] * 300))[300].counted_s == 300.0
    assert GS.best_stretches([]) == {}
    # Counted time, not elapsed: a 400 s pause does not make 299 s of running reach 300.
    assert 300 not in GS.best_stretches(_run([3.0] * 299, pauses={100: 400.0}), pause_break_s=math.inf)


BAD_VALUES = [
    ("t_start", math.nan),
    ("t_start", math.inf),
    ("dt", math.nan),
    ("dt", math.inf),
    ("dt", 0.0),
    ("dt", -1.0),
    ("v_actual", math.nan),
    ("v_actual", -math.inf),
    ("v_actual", None),
    ("g", math.nan),
    ("g", math.inf),
    ("g", True),
]


@pytest.mark.parametrize(("field", "value"), BAD_VALUES, ids=[f"{f}={v}" for f, v in BAD_VALUES])
def test_a_non_finite_segment_breaks_windows_like_a_moving_gap(field, value):
    # 300 s at 5 m/s with the bad segment in its middle, then 300 s at 4 and 600 s at 3: every
    # window after the bad segment must still be read (no NaN carried in a running sum).
    speeds = [5.0] * 300 + [3.0] * 60 + [4.0] * 300 + [3.0] * 600
    bad = _run(speeds)
    bad[150][field] = value
    flagged = _run(speeds, gaps={150})
    got = GS.best_stretches(bad)
    assert got == GS.best_stretches(flagged)
    assert got[300] == GS.BestStretch(length_s=300, distance_m=1289.0, counted_s=300.0, first_t_start=151.0)
    assert all(math.isfinite(b.distance_m) and math.isfinite(b.counted_s) for b in got.values())


# -- the fixtures, through the upload path and the shipped segment stream --------------------------


def _segments(filename: str) -> list[dict]:
    with TestClient(app) as client:
        created = client.post("/sessions", files={"file": (filename, (FIXTURES / filename).read_bytes())})
        assert created.status_code == 201, created.text
        session_id = created.json()["session_id"]
    conn = db_module.get_connection()
    try:
        inputs = db_module.read_session_feature_inputs(conn, session_id)
    finally:
        conn.close()
    assert inputs is not None
    session, rows = inputs
    return SF.segment_rows(session, rows)


def _window(segs: list[dict], best: GS.BestStretch) -> list[dict]:
    i = next(k for k, s in enumerate(segs) if s["t_start"] == best.first_t_start)
    out, total = [], 0.0
    for s in segs[i:]:
        out.append(s)
        total += s["dt"]
        if total >= best.length_s:
            return out
    raise AssertionError("the window never reaches its length")


def _pauses(segs: list[dict]) -> list[tuple[float, float]]:
    return [
        (b["t_start"], b["t_start"] - (a["t_start"] + a["dt"]))
        for a, b in pairwise(segs)
        if b["t_start"] - (a["t_start"] + a["dt"]) > 0.0
    ]


@pytest.mark.parametrize(
    ("filename", "pause_after"),
    [("strap_run_hrv.fit", [457.0]), ("wrist_ppg_run.fit", [252.0, 353.0])],
    ids=["strap_run_hrv", "wrist_ppg_run"],
)
def test_fixture_stretches_never_cross_a_pause(filename, pause_after):
    segs = _segments(filename)
    pauses = [(t, gap) for t, gap in _pauses(segs) if gap > 10.0]
    print(f"{filename}: {len(segs)} segments, pauses over 10 s (t_start after, seconds): {pauses}")
    # The pauses the provenance table names are the ones the stream carries.
    ends = []
    for a, b in pairwise(segs):
        if b["t_start"] - (a["t_start"] + a["dt"]) > 10.0:
            ends.append(a["t_start"] + a["dt"])
    assert [round(e) for e in ends] == pause_after
    got = GS.best_stretches(segs)
    assert got
    for length, best in sorted(got.items()):
        window = _window(segs, best)
        crossed = [gap for _t, gap in _pauses(window) if gap > 10.0]
        print(
            f"  L={length}: t {best.first_t_start}-{window[-1]['t_start'] + window[-1]['dt']}, "
            f"{best.distance_m:.3f} m over {best.counted_s} s, pauses inside {crossed}"
        )
        assert not crossed, (length, crossed)
        assert not any(s["moving_gap"] for s in window)
    # One stretch recomputed here, independently of the module's prefix sums.
    best = got[300]
    window = _window(segs, best)
    counted = 0.0
    dist = 0.0
    for s in window:
        counted += s["dt"]
        dist += s["v_actual"] * s["g"] * s["dt"]
    assert best.counted_s == pytest.approx(counted, abs=1e-9)
    assert best.distance_m == pytest.approx(dist * 300 / counted, rel=1e-12)


def test_ignoring_pauses_changes_wrist_ppg_run():
    segs = _segments("wrist_ppg_run.fit")
    ruled = GS.best_stretches(segs)
    free = GS.best_stretches(segs, pause_break_s=math.inf)
    for length in (720, 1200):
        a, b = ruled[length], free[length]
        a_end = _window(segs, a)[-1]
        b_end = _window(segs, b)[-1]
        print(
            f"L={length}: ruled t {a.first_t_start}-{a_end['t_start'] + a_end['dt']} {a.distance_m:.3f} m; "
            f"pauses ignored t {b.first_t_start}-{b_end['t_start'] + b_end['dt']} {b.distance_m:.3f} m"
        )
        assert a != b
        assert b.distance_m > a.distance_m
        # The unbroken best crosses both of the run's pauses; the ruled best crosses neither.
        assert b.first_t_start < 252.0 and b_end["t_start"] > 353.0
    assert (round(ruled[720].first_t_start), round(free[720].first_t_start)) == (1269, 4)
    assert (round(ruled[1200].first_t_start), round(free[1200].first_t_start)) == (652, 4)


def test_the_search_imports_no_db_or_route():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    print("imports:", sorted(names))
    forbidden = {"sqlite3", "runcoach_api.db", "runcoach_api.main", "fastapi"}
    assert not {n for n in names if n in forbidden or any(n.startswith(f + ".") for f in forbidden)}
