"""A moving dropout inside a work rep of a real file, read through the real segment rows and
``GET /state`` (F027 reference section 4 orders 8 to 11 over F013's counted segments; the
moving-gap rule of IDEA-126).

``test_threshold_workout_active_block.py`` pins the dropout at the pure seam with hand-written
segment rows. Here the gap is cut into the bytes of ``sample_run.fit`` (run proof ``yes``,
tests/fixtures/README.md) in memory with ``fit_patch.drop_records``, never written under
``tests/fixtures``: the record messages 2030 s to 2036 s after the first record are dropped, so
the records at 2029 s and 2037 s become neighbours, 8 s apart, inside lap 6 (step 2's middle
lap, [1824, 2134)), on a downhill stretch (28.10 m of device distance, 3.5 m/s, grade -8 %). The patched file is uploaded with ``POST /sessions``, tagged
``threshold_workout`` through ``PUT /race-tags`` from its own stored summary, and the served
workout is read from ``GET /state``.

**The oracle is independent of the estimator** (rule contract-tables-need-an-independent-oracle):
the counted segments are rebuilt in this file from consecutive pairs of the patched file's
canonical records, by the rule as IDEA-126 states it, written out in ``_hand_segments``; the
work windows are the lap bounds written by hand from the decoded laps; the time, metres and GAP
pace over the windows are summed here. Only each segment's grade price ``g`` is taken from
F013's ``session_features.segment_rows`` (the Minetti pricing of a smoothed grade is F013's
rule, not the estimator's), after this file has checked that stream against the hand segments
one row at a time. ``threshold_pace`` is not imported.

The gap is cut in a **step-2** lap on purpose: step 1 reads about 331.38 s/km and step 2 about
301.47, a ratio of 1.0992 against the 1.10 band, so slowing step 1 could drop it from the count
and change what is being measured. The stretch matters too: a gap is priced at one smoothed
grade where the seven segments it replaces were priced one by one, and on this file's uphill
stretches that reads step 2 faster, not slower (a cut at [1900, 1907) s read the ratio
1.100013 and would have dropped step 1); on the downhill stretch chosen it reads 1.0981. The
test prints the ratio and asserts it stays under 1.10, so a drift of the file or the pricing
that changes which reps count fails here rather than passing as a pace.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import main as main_module
from runcoach_api.ingestion import fit_parser, mapping, quality_gates
from runcoach_api.main import app
from runcoach_api.metrics import session_features as SF

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
WORKOUT = "threshold_workout"
NAMES = ["Progression Run"]  # listed as the other threshold tests list it; this file admits by tag

# The moving-gap rule of IDEA-126, written out here: a segment of 0 < dt <= 5 s is sampled and
# counted whatever its distances say (its metres are max(dd, 0)); a gap over 5 s is counted, in
# full, only when both distances are present and dd / dt is at least 0.5 m/s; anything else
# contributes nothing.
SAMPLED_MAX_DT_S = 5.0
MOVING_GAP_MIN_SPEED_M_S = 0.5

# The records dropped: [2030, 2037) s after the first record, 7 records of a 1 Hz file, so the
# remaining neighbours 2029 s and 2037 s span one 8 s gap.
DROP_START_S, DROP_END_S = 2030.0, 2037.0
GAP_T_START, GAP_DT = 2029.0, 8.0

# Written by hand from the decoded laps (whole-second stamps): laps 1-4 name work step 1, laps 5-7
# work step 2; their timer_s sum to 2100 s. Lap 6, [1824, 2134), holds the gap.
STEP_1_WINDOWS = ((300.0, 678.0), (679.0, 1034.0), (1035.0, 1335.0), (1336.0, 1500.0))
STEP_2_WINDOWS = ((1501.0, 1823.0), (1824.0, 2134.0), (2135.0, 2400.0))
ACTIVE_S = 2100.0
BAND = 1.10


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


def _load_support(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")


# ---------------------------------------------------------------------------
# The hand oracle
# ---------------------------------------------------------------------------


def _canonical(raw: bytes) -> tuple[list[dict], list[dict], list[dict]]:
    """``(laps, steps, records)`` of ``raw`` as the ingest maps them, each as dicts."""
    messages = fit_parser.decode(raw)
    laps, steps = mapping.to_laps(messages)
    session, records = mapping.to_canonical(messages)
    quality_gates.apply(session, records)
    return (
        [dataclasses.asdict(lap) for lap in laps],
        [dataclasses.asdict(step) for step in steps],
        [dataclasses.asdict(record) for record in records],
    )


def _hand_segments(records: list[dict]) -> list[dict]:
    """The counted segments of ``records`` by the moving-gap rule written above, one per
    consecutive pair, keyed by the pair's first ``t``: ``t_start, dt, contributed_m, moving_gap``."""
    out: list[dict] = []
    for a, b in pairwise(records):
        dt = b["t"] - a["t"]
        d0, d1 = a["distance"], b["distance"]
        dd = None if d0 is None or d1 is None else d1 - d0
        if 0.0 < dt <= SAMPLED_MAX_DT_S:
            metres = 0.0 if dd is None else max(dd, 0.0)
            out.append({"t_start": a["t"], "dt": dt, "contributed_m": metres, "moving_gap": False})
        elif dt > SAMPLED_MAX_DT_S and dd is not None and dd / dt >= MOVING_GAP_MIN_SPEED_M_S:
            out.append({"t_start": a["t"], "dt": dt, "contributed_m": dd, "moving_gap": True})
    return out


def _in_windows(segments: list[dict], windows: tuple[tuple[float, float], ...]) -> list[dict]:
    """The segments whose ``t_start`` lies in one of ``windows``, ``[start, end)``."""
    return [seg for seg in segments if any(start <= seg["t_start"] < end for start, end in windows)]


def _hand_pace(segments: list[dict], g_at: dict[float, float]) -> tuple[float, float, float]:
    """``(T, GAP metres, pace)`` over ``segments``: ``1000 * sum(dt) / sum(contributed_m * g)``,
    with ``g`` looked up by ``t_start`` in ``g_at``."""
    seconds = sum(seg["dt"] for seg in segments)
    gap_metres = sum(seg["contributed_m"] * g_at[seg["t_start"]] for seg in segments)
    return seconds, gap_metres, 1000.0 * seconds / gap_metres


def _around_gap(segments: list[dict]) -> list[dict]:
    """The segments starting within 3 s either side of the gap, for a failure message."""
    return [seg for seg in segments if GAP_T_START - 3.0 <= seg["t_start"] <= GAP_T_START + GAP_DT + 3.0]


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


def _upload_and_tag(client: TestClient, raw: bytes) -> str:
    """Upload ``raw`` as ``sample_run.fit``, tag the stored session ``threshold_workout`` from its
    own stored start time and summary, and return its session id."""
    uploaded = client.post("/sessions", files={"file": ("sample_run.fit", raw)})
    assert uploaded.status_code == 201, uploaded.text
    session_id = uploaded.json()["session_id"]
    detail = client.get(f"/sessions/{session_id}")
    assert detail.status_code == 200, detail.text
    start = datetime.fromisoformat(detail.json()["start_time"]).astimezone(UTC)
    summary = detail.json()["summary"]
    tagged = client.put(
        "/race-tags",
        json={
            "items": [
                {
                    "connect_activity_id": 24557261242,
                    "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "distance_m": summary["distance_m"],
                    "duration_s": summary["duration_s"],
                    "tag": WORKOUT,
                }
            ]
        },
    )
    assert tagged.status_code == 200, tagged.text
    assert [(i["outcome"], i["session_id"]) for i in tagged.json()["items"]] == [("tagged", session_id)]
    return session_id


def _served_workout(client: TestClient, session_id: str) -> dict:
    response = client.get("/state")
    assert response.status_code == 200, response.text
    [determinant] = [d for d in response.json()["determinants"] if d["name"] == "functional_threshold_pace"]
    print(f"  efforts {determinant['efforts']}\n  excluded {determinant['excluded']}")
    assert determinant["excluded"] == []
    [effort] = determinant["efforts"]
    assert effort["session_id"] == session_id
    return effort


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


def test_a_dropout_inside_a_work_rep_reads_the_hand_computed_pace(declared_config) -> None:
    """Seven records cut from lap 6 of ``sample_run.fit`` leave one 8 s moving gap (28.10 m of
    device distance, 3.5 m/s). Uploaded and tagged, the served workout's GAP pace is the hand
    pace over the hand segments of the remaining records: the gap counts in the window it starts
    in, in full, for its 8 s and its metres, so the windows' counted time is the unpatched file's
    to the second, while ``A`` (``duration_s``) stays the laps' 2100 s, untouched by the records.
    The step ratio is printed and stays under the band, so both reps count as before."""
    declared_config(threshold_workout_names=NAMES)
    raw = (FIXTURES / "sample_run.fit").read_bytes()
    patched = fit_patch.drop_records(raw, DROP_START_S, DROP_END_S)
    assert patched != raw

    # The hand side: the windows are the decoded laps' bounds, the gap is where it was cut.
    laps, _steps, records = _canonical(patched)
    whole_laps, _whole_steps, whole_records = _canonical(raw)
    assert [(lap["start_t"], lap["end_t"]) for lap in laps[1:8]] == [*STEP_1_WINDOWS, *STEP_2_WINDOWS]
    assert [lap["wkt_step_index"] for lap in laps[1:8]] == [1, 1, 1, 1, 2, 2, 2]
    assert sum(lap["timer_s"] for lap in laps[1:8]) == pytest.approx(ACTIVE_S, abs=1e-9)
    assert laps == whole_laps, "dropping record messages must leave the lap messages as they were"
    assert len(records) == len(whole_records) - 7
    times = [record["t"] for record in records]
    assert times[times.index(GAP_T_START) + 1] == GAP_T_START + GAP_DT
    assert {whole_record["t"] for whole_record in whole_records} - set(times) == set(range(2030, 2037))

    segments = _hand_segments(records)
    [gap] = [seg for seg in segments if seg["moving_gap"]]
    assert (gap["t_start"], gap["dt"]) == (GAP_T_START, GAP_DT)
    assert gap["contributed_m"] / gap["dt"] >= MOVING_GAP_MIN_SPEED_M_S
    assert STEP_2_WINDOWS[1][0] <= gap["t_start"] < STEP_2_WINDOWS[1][1]
    print(f"  hand gap {gap}")

    # F013's stream of the same records must carry the hand segments row for row before its g is
    # borrowed: the gap is one counted row of dt 8 with its metres, flagged moving_gap, and no
    # row of the stream lies inside the hole.
    session = {"session_id": "s", "sport": "running", "quality_flags": [], "context": {}}
    stream = SF.segment_rows(session, records)
    by_start = {row["t_start"]: row for row in stream}
    detail = f"hand segments near the gap {_around_gap(segments)}\n  stream rows near the gap {_around_gap(stream)}"
    assert sorted(by_start) == [seg["t_start"] for seg in segments], detail
    for seg in segments:
        row = by_start[seg["t_start"]]
        assert (row["dt"], row["moving_gap"]) == (seg["dt"], seg["moving_gap"]), detail
        assert row["contributed_m"] == pytest.approx(seg["contributed_m"], abs=1e-9), detail
    g_at = {start: row["g"] for start, row in by_start.items()}
    print(f"  stream gap row {by_start[GAP_T_START]}")

    step_1 = _hand_pace(_in_windows(segments, STEP_1_WINDOWS), g_at)
    step_2 = _hand_pace(_in_windows(segments, STEP_2_WINDOWS), g_at)
    ratio = max(step_1[2], step_2[2]) / min(step_1[2], step_2[2])
    print(f"  step 1 (T, GAP m, pace) {step_1}; step 2 {step_2}; step ratio {ratio:.6f} against {BAND}")
    assert ratio < BAND, f"step ratio {ratio} would change which reps count; step 1 {step_1}, step 2 {step_2}"
    counted = _in_windows(segments, (*STEP_1_WINDOWS, *STEP_2_WINDOWS))
    seconds, gap_metres, hand_pace = _hand_pace(counted, g_at)

    # The gap's seconds and metres are counted in the window it starts in, in full: the windows'
    # time and device metres are the unpatched file's.
    whole_counted = _in_windows(_hand_segments(whole_records), (*STEP_1_WINDOWS, *STEP_2_WINDOWS))
    assert seconds == pytest.approx(sum(seg["dt"] for seg in whole_counted), abs=1e-9)
    assert sum(seg["contributed_m"] for seg in counted) == pytest.approx(
        sum(seg["contributed_m"] for seg in whole_counted), abs=1e-6
    )
    assert any(seg["moving_gap"] for seg in counted)
    assert len(counted) == len(whole_counted) - 7

    # The route side.
    with TestClient(app) as client:
        session_id = _upload_and_tag(client, patched)
        effort = _served_workout(client, session_id)
    print(
        f"  hand T {seconds}, GAP m {gap_metres:.3f}, pace {hand_pace:.6f}; served {effort['gap_pace_s_per_km']}"
    )
    assert effort["gap_pace_s_per_km"] == pytest.approx(hand_pace, rel=1e-9), (
        f"served {effort['gap_pace_s_per_km']} over {effort['duration_s']} s against the hand pace {hand_pace} "
        f"(T {seconds}, GAP m {gap_metres}); step ratio {ratio:.6f}; {detail}"
    )
    # A is the laps' timer_s, which the dropped records do not touch; both reps still count.
    assert effort["duration_s"] == pytest.approx(ACTIVE_S, abs=1e-9)
    assert (effort["kind"], effort["admitted_by"], effort["active_blocks"]) == (WORKOUT, ["tag"], 1)
