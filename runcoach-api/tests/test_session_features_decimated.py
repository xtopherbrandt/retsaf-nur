"""Decimated real runs through the upload path (IDEA-126's acceptance; tolerances ruled 2026-10-10).

A real 1 Hz run is decimated **in its FIT bytes, before upload**, with
``tests/support/fit_patch.py``, so the sparse copy goes through the real
quality gate, the record store and ``GET /sessions/{id}/features`` exactly as
a watch file would. Every case compares the decimated copy with the same
fixture uploaded at 1 Hz **in the same test**; nothing here is a pinned number.
The decimated copy keeps its ``session`` message, so it has the 1 Hz upload's
``source_device`` and ``start_time`` and the same session id: the 1 Hz body is
read first, that session is deleted, and only then is the sparse copy
uploaded (``test_session_load_time_basis.py`` does the same).

**The ruled tolerances**, at 6 to 8 s spacing: ``duration_s`` and
``distance_m`` within 1 % of the 1 Hz figures; GAP/raw (here
``avg_pace_s_per_km / gap_avg_pace_s_per_km``, the grade-adjusted speed over
the raw speed) within 0.01 of the 1 Hz ratio. The sparse copy carries the
``moving_gap`` flag, because every gap over 5 s crossed at running pace is a
counted moving gap; the 1 Hz upload does not.

- **Uniform and seeded random spacing.** ``sample_run`` and
  ``hilly_run_8k_fr945`` (provenance table in ``fixtures/README.md``, run
  proof ``yes``, both FR945) at every 6th, 7th and 8th record and at a
  seeded random 6 to 8 s spacing with seeds 1, 2 and 3.
- **Injected moving dropouts.** 11 s and 30 s of records removed while the
  runner moved, placed on the **steepest moving stretch** of each fixture
  (the window of that length with the largest absolute altitude change per
  metre among the windows covered at 2 m/s or more; on ``hilly_run_8k`` that
  is its steepest hill). Duration and distance within 1 %, GAP/raw within
  0.01, ``moving_gap`` present.

Each case prints the slice it compared: fixture, spacing, seed, duration and
distance at 1 Hz and decimated with the percentage change, GAP/raw at both
with its delta, and the flags served. The acceptance probe runs with ``-s``
and greps those lines.

**Held axes** (the sweep varies spacing, seed and fixture and nothing else):
one device (FR945, both fixtures); the gate's 3-sample altitude smoothing;
the +-25 m grade window; decimation applied before the gate, never to stored
rows; fixed seeds. A result here says nothing about another device, another
smoothing or a grade window of another width.

**Decimated copies are constructed, not smart-recording proof.** Both
fixtures were written at 1 Hz (``hilly_run_8k_fr945`` with Smart recording
selected); a watch that really records sparsely may place its records
differently (on a change of pace, heading or heart rate rather than at a
spacing). A real smart-recorded run is owned by IDEA-129; when one lands,
its own case belongs here beside these. No provenance row is added and no
fixture file changes: every decimated copy lives in memory.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import fitdecode
import pytest
from fastapi.testclient import TestClient
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"

RUN_FIXTURES = ("sample_run.fit", "hilly_run_8k_fr945.fit")
UNIFORM_SPACINGS_S = (6, 7, 8)
RANDOM_SPACING_S = (6, 8)
SEEDS = (1, 2, 3)
DROPOUT_LENGTHS_S = (11, 30)

# The ruled tolerances (IDEA-126, 2026-10-10).
DURATION_TOLERANCE = 0.01  # 1 % of the 1 Hz duration_s
DISTANCE_TOLERANCE = 0.01  # 1 % of the 1 Hz distance_m
GAP_RATIO_TOLERANCE = 0.01  # absolute, on GAP/raw

MOVING_GAP_FLAG = "moving_gap"
RUNNING_SPEED_M_S = 2.0  # a window covered slower than this is not "at running pace"
RECORD = 20


def _load_support(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")


def _raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _upload(client: TestClient, name: str, raw: bytes) -> str:
    response = client.post("/sessions", files={"file": (name, raw)})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _features(client: TestClient, session_id: str) -> dict:
    response = client.get(f"/sessions/{session_id}/features")
    assert response.status_code == 200, response.text
    return response.json()


def _delete(client: TestClient, session_id: str) -> None:
    assert client.delete(f"/sessions/{session_id}").status_code == 204


def _value(body: dict, name: str) -> float:
    feature = body["features"][name]
    assert feature["unavailable"] is None, (name, feature)
    assert feature["value"] is not None, (name, feature)
    return feature["value"]


def _gap_ratio(body: dict) -> float:
    """GAP/raw: the grade-adjusted speed over the raw speed, as a ratio of the two paces."""
    return _value(body, "avg_pace_s_per_km") / _value(body, "gap_avg_pace_s_per_km")


def _one_hz_then_sparse(client: TestClient, name: str, sparse: bytes) -> tuple[dict, dict, str]:
    """``(1 Hz body, decimated body, decimated session id)``; the 1 Hz session is deleted in between."""
    base_id = _upload(client, name, _raw(name))
    base = _features(client, base_id)
    _delete(client, base_id)
    sparse_id = _upload(client, name, sparse)
    return base, _features(client, sparse_id), sparse_id


def _compare(fixture: str, spacing: str, seed: str, base: dict, sparse: dict) -> None:
    """Print the slice, then hold the decimated body to the ruled tolerances."""
    d0, d1 = _value(base, "duration_s"), _value(sparse, "duration_s")
    m0, m1 = _value(base, "distance_m"), _value(sparse, "distance_m")
    r0, r1 = _gap_ratio(base), _gap_ratio(sparse)
    duration_pct = 100.0 * (d1 - d0) / d0
    distance_pct = 100.0 * (m1 - m0) / m0
    print(
        f"  fixture={fixture} spacing={spacing} seed={seed} "
        f"duration {d0:.1f} -> {d1:.1f} s ({duration_pct:+.3f} %) "
        f"distance {m0:.1f} -> {m1:.1f} m ({distance_pct:+.3f} %) "
        f"GAP/raw ratio {r0:.4f} -> {r1:.4f} (delta {r1 - r0:+.4f}) flags {sparse['flags']}"
    )
    case = (fixture, spacing, seed)
    assert abs(d1 - d0) <= DURATION_TOLERANCE * d0, (case, "duration_s", d0, d1)
    assert abs(m1 - m0) <= DISTANCE_TOLERANCE * m0, (case, "distance_m", m0, m1)
    assert abs(r1 - r0) <= GAP_RATIO_TOLERANCE, (case, "GAP/raw", r0, r1)
    assert MOVING_GAP_FLAG in sparse["flags"], case
    assert MOVING_GAP_FLAG not in base["flags"], case


@pytest.mark.parametrize("fixture", RUN_FIXTURES, ids=[n.removesuffix(".fit") for n in RUN_FIXTURES])
def test_runs_decimated_to_six_to_eight_seconds_stay_within_tolerance(fixture: str) -> None:
    raw = _raw(fixture)
    cases: list[tuple[str, str, bytes]] = [
        (f"uniform-{k}s", "-", fit_patch.decimate_records(raw, k)) for k in UNIFORM_SPACINGS_S
    ] + [
        (f"random-{RANDOM_SPACING_S[0]}-{RANDOM_SPACING_S[1]}s", str(seed), fit_patch.decimate_records_randomly(raw, *RANDOM_SPACING_S, seed))
        for seed in SEEDS
    ]
    print()
    with TestClient(app) as client:
        for spacing, seed, sparse in cases:
            base, body, sparse_id = _one_hz_then_sparse(client, fixture, sparse)
            _compare(fixture.removesuffix(".fit"), spacing, seed, base, body)
            _delete(client, sparse_id)


# ---------------------------------------------------------------------------
# injected moving dropouts
# ---------------------------------------------------------------------------


def _record_track(raw: bytes) -> list[tuple[int, float, float]]:
    """``(seconds after the first record, distance m, altitude m)`` of every record with all three."""
    track: list[tuple[int, float, float]] = []
    first: int | None = None
    with fitdecode.FitReader(io.BytesIO(raw), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if not (isinstance(frame, fitdecode.FitDataMessage) and frame.global_mesg_num == RECORD):
                continue
            stamp = frame.get_raw_value("timestamp")
            if first is None:
                first = stamp
            distance = frame.get_value("distance", fallback=None)
            altitude = frame.get_value("enhanced_altitude", fallback=None)
            if altitude is None:
                altitude = frame.get_value("altitude", fallback=None)
            if distance is None or altitude is None:
                continue
            track.append((stamp - first, float(distance), float(altitude)))
    return track


def _steepest_moving_window(raw: bytes, gap_s: int) -> tuple[int, float, float]:
    """``(start s after the first record, speed m/s, grade)`` of the steepest window of ``gap_s`` run at pace.

    The dropped records are ``[start, start + gap_s - 1)``: the record at
    ``start - 1`` and the one at ``start + gap_s - 1`` survive, ``gap_s``
    seconds apart on a 1 Hz file. The window is judged over those two
    records; a window is a candidate when it was covered at
    ``RUNNING_SPEED_M_S`` or more.
    """
    by_offset = {offset: (distance, altitude) for offset, distance, altitude in _record_track(raw)}
    best: tuple[float, int, float, float] | None = None
    for start_minus_one, (d0, a0) in by_offset.items():
        end = start_minus_one + gap_s
        if end not in by_offset:
            continue
        d1, a1 = by_offset[end]
        dd = d1 - d0
        if dd / gap_s < RUNNING_SPEED_M_S:
            continue
        grade = (a1 - a0) / dd
        if best is None or abs(grade) > best[0]:
            best = (abs(grade), start_minus_one + 1, dd / gap_s, grade)
    assert best is not None, f"no window of {gap_s} s run at {RUNNING_SPEED_M_S} m/s or more"
    _, start, speed, grade = best
    return start, speed, grade


@pytest.mark.parametrize("fixture", RUN_FIXTURES, ids=[n.removesuffix(".fit") for n in RUN_FIXTURES])
def test_injected_moving_dropouts_are_counted(fixture: str) -> None:
    raw = _raw(fixture)
    print()
    with TestClient(app) as client:
        for gap_s in DROPOUT_LENGTHS_S:
            start, speed, grade = _steepest_moving_window(raw, gap_s)
            sparse = fit_patch.drop_records(raw, start, start + gap_s - 1)
            print(
                f"  fixture={fixture.removesuffix('.fit')} dropout {gap_s} s: records [{start}, {start + gap_s - 1}) "
                f"after the first record dropped; the gap is run at {speed:.2f} m/s on a grade of {grade:+.3f}"
            )
            base, body, sparse_id = _one_hz_then_sparse(client, fixture, sparse)
            _compare(fixture.removesuffix(".fit"), f"dropout-{gap_s}s", "-", base, body)
            _delete(client, sparse_id)
