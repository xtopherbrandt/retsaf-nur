"""Which runs critical speed reads, the stretches it serves and the rules' edges, end to end
through ``GET /state`` (F033 AC1, AC2 and AC4; reference sections 2, 3, 4 and 7).

Synthetic sessions are stored with ``db.persist`` and 1 Hz flat records (altitude constant, so
F013's GAP speed is the speed run), as ``test_riegel_b_qualifying.py`` stores its races. A run is
built from constant-speed pieces: a ``pause`` piece moves the clock with no distance (a stationary
gap, which F013 does not count), a ``moving`` piece is one gap of that many seconds over which the
distance advances (F013 counts it with ``moving_gap`` true). Every saved load is replaced through
``support/session_loads.py``. The clock is frozen through ``main._utcnow``; the autouse isolated
store's ``athlete_timezone`` is UTC.

F013 values the records cannot give on demand (its ``distance_regressed``, ``smart_recording`` and
``grade_clamped`` flags on a flat run, a computation that fails) are set on the real computation's
result by wrapping ``db._cs_block`` for the named sessions only; every other value is F013's own.
The sessions so patched are untagged, so no threshold or Riegel read reads them first.

Real files, with their rows of the provenance table in ``fixtures/README.md``:

| file | kind | notes | mode | devices | positions | run proof |
|---|---|---|---|---|---|---|
| wrist_ppg_run | real run | HR from the chest strap (user, 2026-10-04); the name is historical | 1 Hz, 81 s and 11 s pauses | FR945 LTE; HRM-Pro Plus | yes | yes |
| strap_run_hrv | real run | strap HRV | 1 Hz, 229 s pause | FR945 LTE; HRM-Pro Plus | yes | yes |

The expected stretches of a real file come from a window search written in this file
(``_oracle_windows``) over F013's ``segment_rows`` stream: only each segment's GAP price is F013's,
the windows, the pause rule and the scaling are restated here, and ``gap_stretches`` is not
imported. The expected fits come from ``support/cs_oracle.py``, which imports nothing from
``runcoach_api``. The reference section 1 constants are typed in from the reference.

The contract table of ``test_each_candidate_row_lands_on_its_reason`` was written from the
reference section 2 list of reasons, in its order, before the estimator's screen was read.
"""

from __future__ import annotations

import bisect
import importlib.util
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.metrics import session_features
from runcoach_api.models import Context, Record, Session, SessionLap, SessionWorkoutStep

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
NAMES = ["functional_threshold_pace", "riegel_exponent", "critical_speed"]
LISTED = "Progression Run"

# F033 reference section 1, typed in from the reference.
LENGTHS = (120, 180, 300, 480, 720, 1200)
PAUSE_BREAK_S = 10.0
POPULATION_B = 1.06


def _load_support(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


session_loads = _load_support("session_loads")
cs_oracle = _load_support("cs_oracle")
fit_patch = _load_support("fit_patch")


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _sept(day: int) -> datetime:
    return datetime(2026, 9, day, 7, 0, tzinfo=UTC)


def _pieces(*parts: tuple) -> list[Record]:
    """Records from ``("run", seconds, m_per_s)``, ``("pause", seconds)`` and
    ``("moving", seconds, m_per_s)`` parts: a run piece is one record per second; a pause or a
    moving gap is one jump of the clock to the next record (no distance, or ``seconds * speed``)."""
    t, distance = 0.0, 0.0
    records = [Record(t=0.0, distance=0.0, speed=0.0, altitude=100.0, heart_rate=150.0)]
    for part in parts:
        kind, seconds = part[0], float(part[1])
        if kind == "run":
            speed = float(part[2])
            for _ in range(int(seconds)):
                t += 1.0
                distance += speed
                records.append(Record(t=t, distance=distance, speed=speed, altitude=100.0, heart_rate=150.0))
        elif kind == "pause":
            t += seconds
            records.append(Record(t=t, distance=distance, speed=0.0, altitude=100.0, heart_rate=150.0))
        elif kind == "moving":
            speed = float(part[2])
            t += seconds
            distance += seconds * speed
            records.append(Record(t=t, distance=distance, speed=speed, altitude=100.0, heart_rate=150.0))
        else:
            raise AssertionError(kind)
    return records


def _flat(time_s: float, pace: float) -> list[Record]:
    """``time_s`` seconds at ``pace`` s/km."""
    return _pieces(("run", time_s, 1000.0 / pace))


def _named_plan(duration_s: float, name: str) -> tuple[list[SessionLap], list[SessionWorkoutStep]]:
    """One active lap over the whole run on a one-step plan named ``name``."""
    laps = [
        SessionLap(
            lap_index=0,
            start_t=0.0,
            end_t=duration_s,
            timer_s=duration_s,
            intensity="active",
            wkt_step_index=0,
        )
    ]
    steps = [
        SessionWorkoutStep(
            step_index=0, workout_name=name, intensity="active", duration_type="time", target_type="speed"
        )
    ]
    return laps, steps


def _store(
    start: datetime,
    records: list[Record],
    *,
    sport: str = "running",
    tag: str | None = None,
    flags: list[str] | None = None,
    name: str | None = None,
    workout_name: str | None = None,
) -> str:
    """A stored session over ``records``; its timer and summary duration are the last record's
    ``t``, its distance the last record's distance."""
    duration_s = records[-1].t
    start_time = start.isoformat()
    session = Session(
        session_id=derive_session_id("fr945", start_time),
        sport=sport,
        source_vendor="garmin",
        start_time=start_time,
        source_device="fr945",
        activity_tag=tag,
        timer_time_s=duration_s,
        quality_flags=list(flags or []),
        summary={"duration_s": duration_s, "distance_m": records[-1].distance},
        context=Context(),
        name=name,
    )
    laps = _named_plan(duration_s, workout_name) if workout_name is not None else None
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        db_module.persist(conn, session, records, [], {}, laps=laps)
    finally:
        conn.close()
    return session.session_id


def _sql(statement: str, *params) -> None:
    conn = db_module.get_connection()
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def _plant_every_load(value: float = 50.0) -> None:
    conn = db_module.get_connection()
    try:
        for (session_id,) in conn.execute("SELECT session_id FROM sessions").fetchall():
            session_loads.plant_load(conn, session_id, value=value)
    finally:
        conn.close()


def _body(client: TestClient, as_of: str | None = None) -> dict:
    response = client.get("/state", params={} if as_of is None else {"as_of": as_of})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [d["name"] for d in body["determinants"]] == NAMES
    return body


def _cs(client: TestClient, as_of: str | None = None) -> dict:
    return _body(client, as_of)["determinants"][2]


def _print_entry(entry: dict) -> None:
    print(f"  unavailable {entry['unavailable']}; excess {entry['far_off_excess_s_per_km']}")
    for s in entry["stretches"]:
        print(
            f"  {s['length_s']:>5} s {s['distance_m']!r:>22} m pace {s['pace_s_per_km']!r} "
            f"{s['session_id'][:10]} {s['start_time']} drop={s['drop_reason']} by={s['beaten_by_s']}"
        )
    for e in entry["excluded"]:
        print(f"  excluded {e['session_id'][:10]} {e['start_time']} {e['reason']} {e['source']}")


def _patch_blocks(monkeypatch: pytest.MonkeyPatch, flags: dict[str, list[str] | None]) -> None:
    """``db._cs_block`` computes F013's flags and stream as built; a session named in ``flags`` has
    its flags replaced (``None`` makes it a computation that failed)."""
    real = db_module._cs_block

    def patched(conn, session_id, features_by_session):
        block = real(conn, session_id, features_by_session)
        if session_id not in flags or block is None:
            return block
        if flags[session_id] is None:
            return None
        return list(flags[session_id]), block[1]

    monkeypatch.setattr(db_module, "_cs_block", patched)


def _counted_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    reads: list[str] = []
    real = db_module.read_session_feature_inputs

    def counted(conn, session_id):
        reads.append(session_id)
        return real(conn, session_id)

    monkeypatch.setattr(db_module, "read_session_feature_inputs", counted)
    return reads


# Six flat runs, (start, pace s/km, time s): each one's best at its length is the only choice there,
# from the longest length down (one run per length), and each shorter stretch is faster than every
# longer one, so all six are kept.
CURVE = (
    (_sept(1), 260.0, 1300.0),  # 1200 s
    (_sept(3), 245.0, 800.0),  # 720 s
    (_sept(5), 235.0, 500.0),  # 480 s
    (_sept(7), 225.0, 320.0),  # 300 s
    (_sept(9), 215.0, 200.0),  # 180 s
    (_sept(11), 205.0, 130.0),  # 120 s
)


def _curve(*runs: tuple[datetime, float, float]) -> list[str]:
    return [_store(start, _flat(time_s, pace)) for start, pace, time_s in runs]


# ---------------------------------------------------------------------------
# AC1: the candidates (reference section 2)
# ---------------------------------------------------------------------------

# One row per reference section 2 condition, in its order, then the order pins: (name, start,
# how it is stored, what F013 gives (``"keep"``: as computed; ``None``: a failed computation),
# the served reason and source; ``"used"`` for a candidate, ``"unlisted"`` for no listing).
CANDIDATE_ROWS: list[tuple[str, datetime, dict, object, object, str | None]] = [
    ("cycling", _sept(12), {"sport": "cycling"}, "keep", "not_running", None),
    (
        "health_snapshot tag on running",
        _sept(13),
        {"tag": "health_snapshot"},
        "keep",
        "declared_capture",
        None,
    ),
    (
        "stored flags not JSON",
        _sept(14),
        {"raw_flags": "{{not json"},
        "keep",
        "quality_flags_unreadable",
        None,
    ),
    (
        "stored distance_regressed",
        _sept(15),
        {"flags": ["distance_regressed"]},
        "keep",
        "distance_regressed",
        "stored",
    ),
    (
        "stored smart_recording",
        _sept(16),
        {"flags": ["smart_recording"]},
        "keep",
        "smart_recording",
        "stored",
    ),
    (
        "stored both: distance_regressed first",
        _sept(17),
        {"flags": ["smart_recording", "distance_regressed"]},
        "keep",
        "distance_regressed",
        "stored",
    ),
    (
        "stored flag before an F013 flag",
        _sept(18),
        {"flags": ["smart_recording"]},
        ["distance_regressed"],
        "smart_recording",
        "stored",
    ),
    ("features did not compute", _sept(19), {}, None, "features_unavailable", None),
    ("F013 distance_regressed", _sept(20), {}, ["distance_regressed"], "distance_regressed", "f013"),
    ("F013 smart_recording", _sept(21), {}, ["smart_recording"], "smart_recording", "f013"),
    ("F013 grade_clamped", _sept(22), {}, ["grade_clamped"], "grade_clamped", "f013"),
    (
        "F013 smart_recording before grade_clamped",
        _sept(23),
        {},
        ["grade_clamped", "smart_recording"],
        "smart_recording",
        "f013",
    ),
    ("cycling before the window", NOW - timedelta(days=120), {"sport": "cycling"}, "keep", "unlisted", None),
]


def test_each_candidate_row_lands_on_its_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """One session per reference section 2 row beside the ``CURVE`` runs, the 1200 s one 89.99 days
    before the instant (a candidate, used) and a faster 1300 s run exactly 90.00 days before it
    (outside: neither used nor listed, though it would take the 1200 s length). Each row is listed
    with its reason and source, in start order; the old cycling session is not listed; the six
    ``CURVE`` runs are the six stretches; the response is a 200."""
    with TestClient(app) as client:
        curve = _curve((NOW - timedelta(days=89.99), 260.0, 1300.0), *CURVE[1:])
        at_90 = _store(NOW - timedelta(days=90), _flat(1300.0, 200.0))
        ids: list[str] = []
        f013: dict[str, list[str] | None] = {}
        for _name, start, stored, computed, _reason, _source in CANDIDATE_ROWS:
            session_id = _store(
                start,
                _flat(400.0, 300.0),
                sport=stored.get("sport", "running"),
                tag=stored.get("tag"),
                flags=stored.get("flags"),
            )
            if "raw_flags" in stored:
                _sql(
                    "UPDATE sessions SET quality_flags = ? WHERE session_id = ?",
                    stored["raw_flags"],
                    session_id,
                )
            if computed != "keep":
                f013[session_id] = computed  # type: ignore[assignment]
            ids.append(session_id)
        _patch_blocks(monkeypatch, f013)
        _plant_every_load()
        entry = _cs(client)
    _print_entry(entry)
    expected = [
        (session_id, reason, source)
        for session_id, (_n, _s, _st, _c, reason, source) in zip(ids, CANDIDATE_ROWS, strict=True)
        if reason != "unlisted"
    ]
    served = [(e["session_id"], e["reason"], e["source"]) for e in entry["excluded"]]
    for (name, *_rest), session_id in zip(CANDIDATE_ROWS, ids, strict=True):
        print(f"  {name}: {[r for s, r, _src in served if s == session_id]}")
    assert served == expected
    listed = {s["session_id"] for s in entry["stretches"]} | {e["session_id"] for e in entry["excluded"]}
    assert ids[-1] not in listed
    assert at_90 not in listed
    assert entry["unavailable"] is None
    assert {s["length_s"]: s["session_id"] for s in entry["stretches"]} == dict(
        zip((1200, 720, 480, 300, 180, 120), curve, strict=True)
    )


def test_stored_flags_exclude_before_any_record_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sessions the stored values decide (each stored flag, unreadable flags, a declared-capture
    tag, a cycling session in the window, a clean run 91 days before the instant) never have their
    records read: ``read_session_feature_inputs`` is called for the candidates only, once each.
    None is tagged ``race`` or ``threshold_workout``, so no threshold or Riegel read reads them
    either. The window and the sport rows pin the predicate: one that ignored either would read a
    session the estimator then excludes or leaves out, and the response would not change."""
    with TestClient(app) as client:
        curve = _curve(*CURVE)
        decided = {
            "distance_regressed": _store(_sept(12), _flat(400.0, 300.0), flags=["distance_regressed"]),
            "smart_recording": _store(_sept(13), _flat(400.0, 300.0), flags=["smart_recording"]),
            "quality_flags_unreadable": _store(_sept(14), _flat(400.0, 300.0)),
            "declared_capture": _store(_sept(15), _flat(400.0, 300.0), tag="resting_hrv_check"),
            "not_running": _store(_sept(16), _flat(400.0, 300.0), sport="cycling"),
        }
        _sql(
            "UPDATE sessions SET quality_flags = ? WHERE session_id = ?",
            "[1",
            decided["quality_flags_unreadable"],
        )
        old = _store(NOW - timedelta(days=91), _flat(1300.0, 200.0))
        _plant_every_load()
        reads = _counted_reads(monkeypatch)  # from the state read on: storing a session reads it too
        entry = _cs(client)
    _print_entry(entry)
    print(f"  reads {[r[:10] for r in reads]}; old {old[:10]}; cycling {decided['not_running'][:10]}")
    assert sorted(reads) == sorted(curve)
    assert old not in reads and decided["not_running"] not in reads
    served = {e["session_id"]: e["reason"] for e in entry["excluded"]}
    assert served == {session_id: reason for reason, session_id in decided.items()}
    assert old not in {s["session_id"] for s in entry["stretches"]}


def test_runs_before_the_window_are_not_listed() -> None:
    """At now, runs 90.5 and 200 days before the instant (a fast clean run, a flagged run and a
    cycling session) are neither used nor listed; at an ``as_of`` 60 days earlier the same runs are
    inside the window and are used or listed, so they are left out by the window alone."""
    with TestClient(app) as client:
        _curve(*CURVE)
        fast = _store(NOW - timedelta(days=90.5), _flat(1300.0, 200.0))
        flagged = _store(NOW - timedelta(days=90.6), _flat(400.0, 300.0), flags=["distance_regressed"])
        cycling = _store(NOW - timedelta(days=200), _flat(400.0, 300.0), sport="cycling")
        _plant_every_load()
        now = _cs(client)
        past = _cs(client, (NOW - timedelta(days=60)).isoformat())
    _print_entry(now)
    _print_entry(past)
    old = {fast, flagged, cycling}
    listed_now = {s["session_id"] for s in now["stretches"]} | {e["session_id"] for e in now["excluded"]}
    assert not old & listed_now
    assert now["excluded"] == []
    assert {s["length_s"]: s["session_id"] for s in past["stretches"]}[1200] == fast
    assert [(e["session_id"], e["reason"]) for e in past["excluded"]] == [(flagged, "distance_regressed")]
    assert cycling not in {e["session_id"] for e in past["excluded"]}  # 140 days before that instant


# ---------------------------------------------------------------------------
# AC2: the stretches of the real files (reference sections 3 and 7)
# ---------------------------------------------------------------------------


def _segments(session_id: str) -> list[dict]:
    conn = db_module.get_connection()
    try:
        session, records = db_module.read_session_feature_inputs(conn, session_id)  # type: ignore[misc]
    finally:
        conn.close()
    return session_features.segment_rows(session, records)


def _oracle_windows(
    segments: list[dict], length: float, *, pauses: bool
) -> list[tuple[float, float, float, int]]:
    """Every window of consecutive segments whose summed ``dt`` first reaches ``length`` and that
    holds no moving gap (and, with ``pauses``, spans no pause over ``PAUSE_BREAK_S``):
    ``(scaled distance, raw distance, counted time, first index)``."""
    counted, metres, moving, breaks = [0.0], [0.0], [0], [0]
    for k, seg in enumerate(segments):
        counted.append(counted[-1] + seg["dt"])
        metres.append(metres[-1] + seg["v_actual"] * seg["g"] * seg["dt"])
        moving.append(moving[-1] + bool(seg["moving_gap"]))
        prev = segments[k - 1] if k else None
        # a break sits on the later segment of a pair whose gap is over the limit
        breaks.append(
            breaks[-1]
            + (prev is not None and seg["t_start"] - (prev["t_start"] + prev["dt"]) > PAUSE_BREAK_S)
        )
    out = []
    for i in range(len(segments)):
        e = bisect.bisect_left(counted, counted[i] + length)  # the first prefix reaching the length
        if e > len(segments):
            break
        if moving[e] - moving[i]:
            continue
        if pauses and breaks[e] - breaks[i + 1]:
            continue
        raw, time_s = metres[e] - metres[i], counted[e] - counted[i]
        out.append((raw * length / time_s, raw, time_s, i))
    return out


def _pauses_crossed(segments: list[dict], first: int, length: float) -> list[float]:
    counted = 0.0
    crossed = []
    k = first
    while counted < length:
        if k > first:
            gap = segments[k]["t_start"] - (segments[k - 1]["t_start"] + segments[k - 1]["dt"])
            if gap > PAUSE_BREAK_S:
                crossed.append(gap)
        counted += segments[k]["dt"]
        k += 1
    return crossed


@pytest.mark.parametrize("fixture", ["strap_run_hrv", "wrist_ppg_run"])
def test_fixture_runs_serve_stretches_that_cross_no_pause(fixture: str) -> None:
    """Six copies of the file, each moved one day later (``fit_patch.shift_timestamps``), so each
    length is served from one copy (ties go to the later start). Each served stretch is the best
    window of this file's own search with the pause rule, to 1e-9, and every window that gives the
    served distance crosses no pause over 10 s. The 1200 s stretch's GAP distance and counted time
    are recomputed from its window. On ``wrist_ppg_run`` the best 720 s and 1200 s windows without
    the pause rule cross a pause and are faster, so a search that ignored pauses serves another
    figure there."""
    raw = (FIXTURES / f"{fixture}.fit").read_bytes()
    with TestClient(app) as client:
        ids = []
        for day in range(6):
            uploaded = client.post(
                "/sessions", files={"file": (f"{fixture}.fit", fit_patch.shift_timestamps(raw, day * 86400))}
            )
            assert uploaded.status_code == 201, uploaded.text
            ids.append(uploaded.json()["session_id"])
        starts = [datetime.fromisoformat(client.get(f"/sessions/{s}").json()["start_time"]) for s in ids]
        _plant_every_load()
        entry = _cs(client, (max(starts) + timedelta(days=1)).isoformat())
    _print_entry(entry)
    assert entry["excluded"] == []
    served = {s["length_s"]: s for s in entry["stretches"]}
    assert set(served) <= set(LENGTHS) and 1200 in served
    for length, stretch in sorted(served.items()):
        segments = _segments(stretch["session_id"])
        eligible = _oracle_windows(segments, length, pauses=True)
        best = max(eligible)
        loose = max(_oracle_windows(segments, length, pauses=False))
        matching = [w for w in eligible if w[0] == pytest.approx(stretch["distance_m"], rel=1e-9)]
        print(
            f"  {length} s served {stretch['distance_m']!r}; oracle {best[0]!r} (counted {best[2]} s, first t "
            f"{segments[best[3]]['t_start']}); ignoring pauses {loose[0]!r} crossing "
            f"{_pauses_crossed(segments, loose[3], length)}"
        )
        assert stretch["distance_m"] == pytest.approx(best[0], rel=1e-9)
        assert matching and all(_pauses_crossed(segments, w[3], length) == [] for w in matching)
        if fixture == "wrist_ppg_run" and length in (720, 1200):
            assert loose[0] > best[0] * (1 + 1e-6) and _pauses_crossed(segments, loose[3], length)
    segments = _segments(served[1200]["session_id"])
    _scaled, raw_m, counted_s, first = max(_oracle_windows(segments, 1200, pauses=True))
    window, total = [], 0.0
    for seg in segments[first:]:
        if total >= 1200:
            break
        window.append(seg)
        total += seg["dt"]
    print(f"  1200 s window: {len(window)} segments, {total} s counted, {raw_m!r} m GAP")
    assert total == pytest.approx(counted_s, abs=1e-9) and total >= 1200
    assert sum(seg["v_actual"] * seg["g"] * seg["dt"] for seg in window) == pytest.approx(raw_m, rel=1e-12)
    assert served[1200]["distance_m"] == pytest.approx(raw_m * 1200 / total, rel=1e-9)


# ---------------------------------------------------------------------------
# AC2: synthetic windows (moving gap, the 10 s pause edge)
# ---------------------------------------------------------------------------

FAST = 1000.0 / 200.0  # 200 s/km
SLOW = 1000.0 / 280.0  # 280 s/km
JOINS = {
    # The fastest 300 s (150 + 150 s fast) is joined by an 8 s moving gap: not eligible.
    "moving_gap": (("run", 150, FAST), ("moving", 8, FAST), ("run", 150, FAST)),
    # Joined by a 10.0 s pause: spanned, its time not counted, so the fast 300 s is the best.
    "pause_10.0": (("run", 150, FAST), ("pause", 10.0), ("run", 150, FAST)),
    # Joined by a 10.1 s pause: a break.
    "pause_10.1": (("run", 150, FAST), ("pause", 10.1), ("run", 150, FAST)),
}


@pytest.mark.parametrize(
    ("join", "best_speed"), [("moving_gap", SLOW), ("pause_10.0", FAST), ("pause_10.1", SLOW)]
)
def test_a_moving_gap_run_serves_its_next_best_window(join: str, best_speed: float) -> None:
    """One run: two 150 s fast pieces joined by ``join``, a 60 s stop, then 320 s slow. Its fastest
    300 s crosses the join; across a moving gap or a 10.1 s pause it is not eligible and the slow
    piece's 300 s is served, across a 10.0 s pause it is served. No window reaches 480 s without a
    break, so the run, shorter there, has no stretch at 480 s or longer and is used at 300 s."""
    records = _pieces(*JOINS[join], ("pause", 60), ("run", 320, SLOW))
    with TestClient(app) as client:
        run = _store(_sept(20), records)
        _plant_every_load()
        entry = _cs(client)
    _print_entry(entry)
    assert entry["excluded"] == []
    assert entry["unavailable"] == "too_few_stretches"
    [stretch] = entry["stretches"]
    assert (stretch["length_s"], stretch["session_id"]) == (300, run)
    assert stretch["distance_m"] == pytest.approx(300 * best_speed, rel=1e-9)


# ---------------------------------------------------------------------------
# AC4: each rule edge through the route (reference section 4)
# ---------------------------------------------------------------------------

E = (POPULATION_B - 1) / POPULATION_B


def _pace_for_excess(excess: float, p_prev: float = 245.0) -> float:
    """The 1200 s pace whose far-off excess over a 720 s stretch at ``p_prev`` is ``excess``."""
    return p_prev * (1200 / 720) ** E + excess


def _seed_curve(*runs: tuple[datetime, float, float]) -> Callable[..., list[str]]:
    return lambda _declare: _curve(*runs)


def _seed_race(time_s: float, *, listed: bool = False) -> Callable[..., list[str]]:
    """``CURVE`` with its 1200 s run replaced by a Race-tagged one of ``time_s`` (named ``LISTED``
    and the name listed when ``listed``)."""

    def seed(declare) -> list[str]:
        if listed:
            declare(threshold_workout_names=[LISTED])
        race = _store(_sept(1), _flat(time_s, 260.0), tag="race", workout_name=LISTED if listed else None)
        return [race, *_curve(*CURVE[1:])]

    return seed


# (id, seed, unavailable, the served lengths, label, extra checks)
EDGES = [
    (
        "beaten_by_longer",
        _seed_curve(*CURVE[:3], (_sept(7), 240.0, 320.0), *CURVE[4:]),  # 300 s at 240, 480 s at 235
        None,
        (120, 180, 300, 480, 720, 1200),
        "low",
        {"dropped": {300: 480}},
    ),
    (
        "excess_14.99",
        _seed_curve((_sept(1), _pace_for_excess(14.99), 1300.0), *CURVE[1:]),
        None,
        LENGTHS,
        "low",
        {"excess": 14.99},
    ),
    (
        "excess_15.01",
        _seed_curve((_sept(1), _pace_for_excess(15.01), 1300.0), *CURVE[1:]),
        "longest_far_off",
        LENGTHS,
        None,
        {"excess": 15.01},
    ),
    ("shortest_300", _seed_curve(*CURVE[:4]), None, (300, 480, 720, 1200), "low", {}),
    ("shortest_480", _seed_curve(*CURVE[:3]), "spread_too_narrow", (480, 720, 1200), None, {}),
    ("longest_720", _seed_curve(*CURVE[1:]), None, (120, 180, 300, 480, 720), "low", {}),
    ("longest_480", _seed_curve(*CURVE[2:]), "spread_too_narrow", (120, 180, 300, 480), None, {}),
    ("two_kept", _seed_curve(*CURVE[:2]), "too_few_stretches", (720, 1200), None, {}),
    ("race_1800", _seed_race(1800.0), None, LENGTHS, "medium", {}),
    ("race_1801", _seed_race(1801.0), None, LENGTHS, "low", {}),
    ("race_listed_name", _seed_race(1800.0, listed=True), None, LENGTHS, "low", {}),
    (
        "d_prime_zero",
        _seed_curve(*((start, 250.0, time_s) for start, _pace, time_s in CURVE)),  # 4 m/s at every length
        None,
        LENGTHS,
        "low",
        {"d_prime_zero": True},
    ),
]


@pytest.mark.parametrize(
    ("seed", "unavailable", "lengths", "label", "extra"), [e[1:] for e in EDGES], ids=[e[0] for e in EDGES]
)
def test_each_rule_edge_serves_through_the_route(
    declared_config, seed, unavailable, lengths, label, extra
) -> None:
    """Each edge of reference section 4 through ``GET /state``: the unavailable reason (or none),
    the lengths served (an unavailable entry still serves its stretches), the label, and for a
    served fit every figure is the independent oracle's fit of the served kept stretches. A dropped
    stretch carries ``beaten_by_longer`` and the length that beat it; the far-off excess is served
    either side of 15; a fit whose kept stretches all run at one speed has D' 0 and is flagged
    ``d_prime_not_positive`` (with a speed that never rises with length, D' is never below 0)."""
    with TestClient(app) as client:
        seed(declared_config)
        _plant_every_load()
        entry = _cs(client)
    _print_entry(entry)
    assert entry["unavailable"] == unavailable
    assert [s["length_s"] for s in entry["stretches"]] == sorted(lengths)
    dropped = extra.get("dropped", {})
    for s in entry["stretches"]:
        if s["length_s"] in dropped:
            assert (s["drop_reason"], s["beaten_by_s"]) == ("beaten_by_longer", dropped[s["length_s"]])
        else:
            assert (s["drop_reason"], s["beaten_by_s"]) == (None, None)
    kept = sorted(
        (float(s["length_s"]), s["distance_m"]) for s in entry["stretches"] if s["drop_reason"] is None
    )
    if "excess" in extra:
        print(
            f"  excess served {entry['far_off_excess_s_per_km']!r}; oracle {cs_oracle.far_off_excess(kept[-2], kept[-1])!r}"
        )
        assert entry["far_off_excess_s_per_km"] == pytest.approx(extra["excess"], abs=1e-6)
        assert entry["far_off_excess_s_per_km"] == pytest.approx(
            cs_oracle.far_off_excess(kept[-2], kept[-1]), rel=1e-9
        )
    if unavailable is not None:
        assert entry["estimate"] is None and entry["d_prime"] is None
        return
    estimate, d_prime = entry["estimate"], entry["d_prime"]
    print(f"  served {estimate}; D' {d_prime}; flag {entry['d_prime_not_positive']}")
    assert estimate["confidence"] == label
    if extra.get("d_prime_zero"):
        assert d_prime == {"value_m": 0.0, "low_m": 0.0, "high_m": 0.0}
        assert entry["d_prime_not_positive"] is True
        assert estimate["value"] == estimate["interval"]["low"] == estimate["interval"]["high"] == 250.0
        return
    line = cs_oracle.line(kept)
    print(f"  oracle CS {line.pace} [{line.pace_low}, {line.pace_high}]; D' {line.d_prime}")
    assert entry["d_prime_not_positive"] is False
    assert estimate["value"] == pytest.approx(line.pace, rel=1e-9)
    assert estimate["interval"]["low"] == pytest.approx(line.pace_low, rel=1e-9)
    assert estimate["interval"]["high"] == pytest.approx(line.pace_high, rel=1e-9)
    assert d_prime["value_m"] == pytest.approx(line.d_prime, rel=1e-9)


def test_no_candidate_runs_is_unavailable() -> None:
    """A store whose sessions are all excluded (a cycling session, a flagged run) or outside the
    window serves ``no_candidate_runs`` with no stretch, the exclusions listed."""
    with TestClient(app) as client:
        cycling = _store(_sept(10), _flat(1300.0, 260.0), sport="cycling")
        flagged = _store(_sept(11), _flat(1300.0, 260.0), flags=["smart_recording"])
        _store(NOW - timedelta(days=100), _flat(1300.0, 260.0))
        _plant_every_load()
        entry = _cs(client)
        empty = _cs(client, "2020-01-01")
    _print_entry(entry)
    for served in (entry, empty):
        assert served["unavailable"] == "no_candidate_runs"
        assert served["stretches"] == [] and served["estimate"] is None and served["d_prime"] is None
    assert [(e["session_id"], e["reason"]) for e in entry["excluded"]] == [
        (cycling, "not_running"),
        (flagged, "smart_recording"),
    ]
    assert empty["excluded"] == []


# ---------------------------------------------------------------------------
# A tagged race whose stored flags nest past the JSON decoder's depth
# ---------------------------------------------------------------------------


def test_a_tagged_race_with_nested_json_flags_is_excluded_not_a_500() -> None:
    """A Race-tagged running session of 40 minutes (inside the threshold band, so the threshold read
    decodes its flags) whose ``quality_flags`` is ``[`` repeated 100000 times: ``GET /state`` is a
    200, and the race is excluded ``quality_flags_unreadable`` by the threshold pace, Riegel and
    critical-speed entries alike."""
    with TestClient(app) as client:
        race = _store(_sept(20), _flat(2400.0, 260.0), tag="race")
        _sql("UPDATE sessions SET quality_flags = ? WHERE session_id = ?", "[" * 100000, race)
        _plant_every_load()
        response = client.get("/state")
    print(f"  status {response.status_code} {response.text[:200]}")
    assert response.status_code == 200, response.text
    threshold, riegel, cs = response.json()["determinants"]
    for determinant in (threshold, riegel, cs):
        reasons = [(e["session_id"], e["reason"]) for e in determinant["excluded"]]
        print(f"  {determinant['name']}: {reasons}")
        assert reasons == [(race, "quality_flags_unreadable")]
