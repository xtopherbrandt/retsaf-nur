"""GAP on a one-direction split of a real hilly run, against an oracle restated here.

The corpus ratio rows (``test_session_features_real_fixtures.py``) are whole runs, and every one is
a loop: it starts and ends at about the same altitude, so the distance-weighted mean grade is near
zero and the linear (uphill/downhill asymmetry) term of Minetti's cost cancels. Changing that
coefficient from 19.5 to 21.5 moves every whole-run ratio by at most 0.0006, inside the +-0.005 band.
The pure cost curve pins the coefficient (``test_gap_cost_curve.py``), and so do the synthetic ramps
of the probe table; this module pins it on real running data.

**The split.** ``hilly_run_8k_fr945.fit`` (provenance row: real run, run proof ``yes``) is decoded
with fitdecode, and the test takes the contiguous span of records with the largest net rise in the
watch's own altitude: from the lowest record before the highest point it reaches, found in one
scan. No pause falls inside it, and the span itself is computed here, not copied. Its records go
through the real upload path with only the decoder replaced (as the probe table does, because the
repository has no FIT encoder): mapping, the gates, persistence and the reader all run. No
position field is carried: the stand-in records hold time, distance, speed, altitude, HR and
cadence only.

**The oracle.** It reads the stored, gated records the route serves (``GET /sessions/{id}``, the
inputs the F013 reference section 1 names) and applies the reference sections 2-5, restated here:
counted segments are 0 < dt <= 5 s; a segment contributes ``max(dd, 0)`` (0 when a distance is
absent); ``s`` is the running sum; a segment's window is every record with ``s`` in
``[m - 25, m + 25]`` around its midpoint ``m`` with altitude present, and its grade is
``(altitude[l] - altitude[f]) / (s[l] - s[f])`` over the first and last such records (no grade, so
g = 1, with fewer than two or ``s[l] <= s[f]``); the grade is clamped to +-0.45; g is the published
polynomial over 3.6, never ``metrics.gap``. The served ``avg_pace / gap_avg_pace`` must equal the
distance-weighted mean g to 1e-6. The test also asserts that the split really is one direction:
its distance-weighted mean grade is at least 0.02, so a 2.0 change in the linear coefficient moves
the ratio by at least 0.011, far outside the tolerance.

The oracle's clamp, its ``0 < dt <= 5 s`` bound and its ``max(dd, 0)`` are inert on this split (it
holds no pause, no grade that reaches the clamp and no falling distance), so this module does not
pin those production rules: the clamp is pinned by ``test_gap_cost_curve.py`` and row 19 of
``test_session_features_probe_table.py``, the dt bound by ``test_segment_dt_boundaries`` in
``test_session_time_base.py`` and probe row 5, and the falling distance by
``test_a_regressing_distance_contributes_zero_and_flags_the_session`` in ``test_session_time_base.py``.
"""

from __future__ import annotations

from pathlib import Path

import fitdecode
import pytest
from fastapi.testclient import TestClient
from runcoach_api.ingestion import pipeline
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
SPLIT_FIXTURE = "hilly_run_8k_fr945.fit"

# Reference section 2 and 3 constants, restated (never imported).
MAX_COUNTED_DT_S = 5.0
HALF_WINDOW_M = 25.0
GRADE_DOMAIN = 0.45

RATIO_TOLERANCE = 1e-6
MIN_MEAN_GRADE = 0.02  # the split's distance-weighted mean grade: one direction, not a loop
MIN_RISE_M = 50.0


def _published_cost(i: float, linear: float = 19.5) -> float:
    """Minetti's cost polynomial, J/(kg*m), from the published coefficients (F013 reference section 4)."""
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + linear * i + 3.6


def _g(i: float, linear: float = 19.5) -> float:
    return _published_cost(i, linear) / 3.6


class _UploadMsg:
    """A ``fitdecode.FitDataMessage`` stand-in with the members the ingestion path reads."""

    def __init__(self, name: str, values: dict) -> None:
        self.name = name
        self._values = values
        self.fields: list = []

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)

    def has_field(self, name) -> bool:
        return name in self._values


RECORD_FIELDS = ("timestamp", "distance", "enhanced_speed", "enhanced_altitude", "heart_rate", "cadence")


def _decoded_records(name: str) -> list[dict]:
    rows = []
    with fitdecode.FitReader(str(FIXTURES / name)) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "record":
                rows.append({f: frame.get_value(f, fallback=None) for f in RECORD_FIELDS})
    return rows


def _largest_rise(rows: list[dict]) -> tuple[int, int, float]:
    """``(first, last, rise)``: the record span with the largest net altitude rise, low point first."""
    best = (0, 0, 0.0)
    low = None
    for j, row in enumerate(rows):
        altitude = row["enhanced_altitude"]
        if altitude is None:
            continue
        if low is None or altitude < rows[low]["enhanced_altitude"]:
            low = j
        rise = altitude - rows[low]["enhanced_altitude"]
        if rise > best[2]:
            best = (low, j, rise)
    return best


def _oracle(records: list[dict], linear: float = 19.5) -> tuple[float, float, float]:
    """``(ratio, mean_grade, coverage)`` over the served records, from reference sections 2-5."""
    s = [0.0]
    segments = []  # (k, contribution)
    for k in range(len(records) - 1):
        a, b = records[k], records[k + 1]
        dt = b["t"] - a["t"]
        contribution = 0.0
        if 0 < dt <= MAX_COUNTED_DT_S:
            if a["distance"] is not None and b["distance"] is not None:
                contribution = max(b["distance"] - a["distance"], 0.0)
            segments.append((k, contribution))
        s.append(s[-1] + contribution)
    total = sum(c for _, c in segments)
    assert total > 0, "the split has no distance"

    weighted_g = weighted_grade = graded = 0.0
    for k, contribution in segments:
        m = (s[k] + s[k + 1]) / 2
        window = [j for j in range(len(records))
                  if m - HALF_WINDOW_M <= s[j] <= m + HALF_WINDOW_M and records[j]["altitude"] is not None]
        g = 1.0
        if len(window) >= 2 and s[window[-1]] > s[window[0]]:
            f, last = window[0], window[-1]
            grade = (records[last]["altitude"] - records[f]["altitude"]) / (s[last] - s[f])
            grade = max(-GRADE_DOMAIN, min(GRADE_DOMAIN, grade))
            g = _g(grade, linear)
            weighted_grade += contribution * grade
            graded += contribution
        weighted_g += contribution * g
    return weighted_g / total, weighted_grade / total, graded / total


def test_gap_on_an_uphill_split_of_a_real_run_matches_the_restated_oracle(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _decoded_records(SPLIT_FIXTURE)
    first, last, rise = _largest_rise(rows)
    split = rows[first:last + 1]
    assert rise >= MIN_RISE_M, f"{SPLIT_FIXTURE}: the largest rise is only {rise:.1f} m"

    start = split[0]["timestamp"]
    messages = [_UploadMsg("session", {"sport": "running", "start_time": start})]
    messages += [_UploadMsg("record", {k: v for k, v in row.items() if v is not None}) for row in split]
    monkeypatch.setattr(pipeline.fit_parser, "decode", lambda _raw: messages)

    with TestClient(app) as client:
        created = client.post("/sessions", files={"file": ("split.fit", b"irrelevant", "application/octet-stream")})
        assert created.status_code == 201, created.text
        session_id = created.json()["session_id"]
        detail = client.get(f"/sessions/{session_id}")
        body = client.get(f"/sessions/{session_id}/features")
    assert detail.status_code == 200, detail.text
    assert body.status_code == 200, body.text
    features = body.json()["features"]

    records = sorted(detail.json()["records"], key=lambda r: r["t"])
    expected, mean_grade, coverage = _oracle(records)
    shifted, _, _ = _oracle(records, linear=21.5)
    served = features["avg_pace_s_per_km"]["value"] / features["gap_avg_pace_s_per_km"]["value"]
    print(
        f"[slice compared] {SPLIT_FIXTURE} records {first}..{last} ({len(split)} records, rise {rise:.1f} m): "
        f"ratio served={served:.6f} oracle={expected:.6f}; mean grade {mean_grade:.4f}; "
        f"coverage oracle={coverage:.4f} served={body.json()['gap_coverage']}; "
        f"oracle with the linear coefficient at 21.5 would be {shifted:.6f}"
    )
    assert mean_grade >= MIN_MEAN_GRADE, f"the split is not one direction: mean grade {mean_grade:.4f}"
    assert abs(shifted - expected) > 100 * RATIO_TOLERANCE
    assert body.json()["gap_coverage"] == pytest.approx(coverage, abs=1e-9)
    assert abs(served - expected) <= RATIO_TOLERANCE, (
        f"{SPLIT_FIXTURE} uphill split: served ratio {served:.6f} misses the restated oracle {expected:.6f}"
    )
