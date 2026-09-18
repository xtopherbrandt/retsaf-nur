"""T085: ``GET /metrics/hrv`` -- the verdict for ``to``, with everything that produced it.

The route wires T083's ``build_series``, T084's ``judge`` and T092's resets
behind the UI<->engine contract's path (``contracts/openapi.yaml``,
``operationId: getHrvTrend``). It serves the **verdict** for ``to``; the
contract's per-day ``points[]`` is T091's (``test_hrv_trend_points.py``).

Everything is driven through ``TestClient`` against the autouse isolated
database. Readings are seeded through the **real** ``mapping -> classify ->
db.persist`` chain (``synthetic`` / ``classified`` from ``conftest.py``): a
Health Snapshot capture carries the exact ``rmssd_hrv`` the test chooses, so
the band the test computes by hand is independent of the endpoint; a
chest-strap capture goes through the beat-series classifier so the resolved
column seam is exercised end to end. The one row shape the classifier can no
longer produce -- a pre-amendment-window row (tier set, value null) -- is
persisted as a hand-built ``Session`` through ``db.persist``, never raw SQL.

The two things the wave-2/3 builders filed against this task are pinned here:
IDEA-045 (the route must read from ``to - 126d`` or the tier-change reset is
unreachable through the endpoint) and IDEA-046 (how an empty, inverted
``baseline.window`` is rendered). The ``from``/``to`` adversarial table at the
bottom is the parser's deliverable
(``adversarial-input-probes-are-a-task-deliverable.md``).
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import math
import os
import statistics
import subprocess
import sys
import tokenize
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from functools import cache
from itertools import pairwise, repeat
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo

import pytest
import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.config import AppConfig
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.metrics import hrv_trend
from runcoach_api.models import RRInterval, Session

STRAP = "chest_strap_raw"
SNAPSHOT = "health_snapshot"
PROFILE = "HRV Snapshot"
AUCKLAND = "Pacific/Auckland"

# The target date F005's canonical response example uses: baseline
# [2026-07-04, 2026-09-01], judged week [2026-09-02, 2026-09-08].
D = date(2026, 9, 8)


# ---------------------------------------------------------------------------
# configuration and seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def configure(isolated_data_dir, monkeypatch):
    """``configure(zone)`` -- re-install the isolated ``AppConfig`` with a
    different ``athlete_timezone`` and clear ``db._load_config_cached``, the
    ``declared_config`` pattern. Both halves are required: without the clear
    the route keeps reading the cached UTC config and the fixture is a
    silent no-op. The profile declaration is always present so the strap
    classifier route can fire."""

    def _configure(zone: str) -> AppConfig:
        config = AppConfig(
            host="127.0.0.1",
            port=8000,
            data_dir=isolated_data_dir,
            resting_hrv_profile_names=[PROFILE],
            athlete_timezone=zone,
        )
        monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: config)
        db_module._load_config_cached.cache_clear()
        return config

    return _configure


def at(day: date, hh: int = 6, mm: int = 0, zone: str = "UTC") -> datetime:
    """The aware UTC instant of local wall time ``hh:mm`` on ``day`` in ``zone``."""
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZoneInfo(zone)).astimezone(UTC)


def _strap_beats() -> list[RRInterval]:
    values = [800.0, 900.0, 850.0, 950.0, 870.0, 920.0]
    return [
        RRInterval(seq=i, rr_ms=values[i % len(values)], rr_source="chest_strap_ecg", is_artefact=False)
        for i in range(len(values))
    ]


class Seeder:
    """Builds sessions through the real classifier and persists them."""

    def __init__(self, synthetic, classified, persist_sessions) -> None:
        self._synthetic = synthetic
        self._classified = classified
        self._persist_sessions = persist_sessions
        self.sessions: list[Session] = []

    def snapshot(self, when: datetime, rmssd: float) -> Session:
        """A Health Snapshot capture carrying exactly ``rmssd`` (Tier 2)."""
        session = self._classified(self._synthetic(60, rmssd_hrv=rmssd, start_time=when))
        assert session.hrv_source_tier == SNAPSHOT and session.resting_rmssd_ms == rmssd
        return self._keep(session)

    def snapshots(self, dates: Iterable[date], values: Iterable[float]) -> list[Session]:
        """One snapshot per local day at 06:00 UTC, pairing ``dates`` with
        ``values`` as ``zip`` does -- ``repeat(v)`` seeds a flat series."""
        return [self.snapshot(at(day), value) for day, value in zip(dates, values)]

    def strap(self, when: datetime) -> Session:
        """A declared chest-strap capture whose reading is computed from beats (Tier 1)."""
        messages = self._synthetic(
            total_timer_time=150.0, avg_heart_rate=60, sport_profile_name=PROFILE, start_time=when
        )
        session = self._classified(messages, _strap_beats(), 1.0, [PROFILE])
        assert session.hrv_source_tier == STRAP and session.resting_rmssd_ms > 0
        return self._keep(session)

    def straps(self, dates: Iterable[date]) -> list[Session]:
        """One declared chest-strap capture per local day at 06:00 UTC."""
        return [self.strap(at(day)) for day in dates]

    def run(self, when: datetime) -> Session:
        """An ordinary run: no tier, no reading."""
        session = self._classified(self._synthetic(start_time=when))
        assert session.hrv_source_tier is None and session.resting_rmssd_ms is None
        return self._keep(session)

    def pre_amendment(self, when: datetime) -> Session:
        """A row from F004's pre-amendment window: tier set, reading never
        backfilled. The classifier cannot produce it any more, so it is a
        hand-built ``Session`` -- still persisted through ``db.persist``."""
        iso = when.isoformat()
        device = "garmin:legacy"
        session = Session(
            session_id=derive_session_id(device, iso),
            sport="running",
            source_vendor="garmin",
            start_time=iso,
            source_device=device,
            hrv_source_tier=STRAP,
            resting_rmssd_ms=None,
        )
        return self._keep(session)

    def _keep(self, session: Session) -> Session:
        self.sessions.append(session)
        return session

    def persist(self) -> None:
        """Write every kept session to the isolated store; a strap capture
        that resolved a reading carries its beats, nothing else does."""
        beats_by_id = {
            s.session_id: _strap_beats()
            for s in self.sessions
            if s.hrv_source_tier == STRAP and s.resting_rmssd_ms
        }
        self._persist_sessions(self.sessions, beats_by_id)


@pytest.fixture
def seeder(synthetic, classified, persist_sessions) -> Seeder:
    return Seeder(synthetic, classified, persist_sessions)


def days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


# The baseline most tests seed: the last 20 days of the baseline window,
# [D-26, D-7], enough to establish it (MIN_BASELINE_READINGS is 14).
BASELINE_20 = days(D - timedelta(days=26), D - timedelta(days=7))


def baseline_values(n: int) -> list[float]:
    """An ordinary athlete's dispersion: alternating 38/44 ms around 41,
    wide enough that the floor does not fire (T084)."""
    return [38.0 + 6.0 * (i % 2) for i in range(n)]


def expected_band(values: list[float]) -> tuple[float, float, float]:
    """Computed here, independently of the module, with the estimator the
    decision log fixed: sample SD of the log series."""
    logs = [math.log(v) for v in values]
    mean = statistics.fmean(logs)
    half = max(0.5 * statistics.stdev(logs), 0.01)
    return mean, mean - half, mean + half


def get(client: TestClient, **params):
    return client.get("/metrics/hrv", params={k: v for k, v in params.items() if v is not None})


# ---------------------------------------------------------------------------
# the first failing test: every input that produced the verdict is reported
# ---------------------------------------------------------------------------


def test_the_endpoint_reports_every_input_that_produced_the_verdict(configure, seeder) -> None:
    """A 20-reading snapshot baseline, a snapshot week with one duplicate
    capture, one real strap capture (off the baseline tier), one ordinary run
    (no tier) and one pre-amendment row -- persisted through the real path.
    The response must carry the verdict and everything needed to recompute
    it by hand (``research/00`` §1.6), and every exclusion names a reason.
    Red: the route does not exist (404)."""
    configure("UTC")
    values = baseline_values(20)
    seeder.snapshots(BASELINE_20, values)
    week = [40.0, 41.0, 42.0, 43.0]
    seeder.snapshots(days(D - timedelta(days=6), D - timedelta(days=3)), week)
    later = seeder.snapshot(at(D - timedelta(days=3), hh=9), 30.0)
    strap = seeder.strap(at(D - timedelta(days=2)))
    run = seeder.run(at(D - timedelta(days=1)))
    legacy = seeder.pre_amendment(at(D))
    seeder.persist()

    with TestClient(app) as client:
        response = get(client, to=D.isoformat())

    assert response.status_code == 200, response.text
    body = response.json()

    mean, lo, hi = expected_band(values)
    assert body["date"] == "2026-09-08"
    assert body["from"] == "2026-09-08"
    assert body["timezone"] == "UTC"
    assert body["verdict"] == "hrv_normal"
    assert body["ln_rmssd_7d_mean"] == pytest.approx(statistics.fmean(math.log(v) for v in week))
    assert body["below_by"] is None
    assert body["band"]["lo"] == pytest.approx(lo)
    assert body["band"]["hi"] == pytest.approx(hi)
    assert body["band"]["mean"] == pytest.approx(mean)
    assert body["band"]["half_width"] == pytest.approx((hi - lo) / 2)
    assert body["band"]["floored"] is False
    assert body["baseline"] == {
        "window": ["2026-07-04", "2026-09-01"],
        "n": 20,
        "tier": SNAPSHOT,
        "established": True,
        "reset_on": None,
        "reset_reason": None,
    }
    assert body["window"] == ["2026-09-02", "2026-09-08"]
    assert body["readings_in_window"] == 4
    assert [r["rmssd_ms"] for r in body["included"]] == week
    assert all(r["tier"] == SNAPSHOT for r in body["included"])
    assert body["thresholds"] == {
        "baseline_days": 60,
        "min_baseline_readings": 14,
        "min_window_readings": 3,
        "gap_reset_days": 21,
        "band_floor": 0.01,
        "swc_factor": 0.5,
    }
    reasons = {entry["session_id"]: entry["reason"] for entry in body["excluded"]}
    assert reasons == {
        later.session_id: "same_day_later_capture",
        strap.session_id: f"off_baseline_tier: {STRAP}",
        run.session_id: "null_tier",
        legacy.session_id: "pre_amendment_window",
    }
    assert all(entry["reason"] for entry in body["excluded"])
    assert all(entry["date"] for entry in body["excluded"])
    # The per-day ``points[]`` beside these blocks is T091's (test_hrv_trend_points.py).


# ---------------------------------------------------------------------------
# the parameters and their defaults
# ---------------------------------------------------------------------------


def test_to_defaults_to_the_athletes_local_today_and_from_to_to(configure, monkeypatch) -> None:
    """The clock is frozen at 2026-09-08T13:00Z -- still the 8th in UTC, but
    01:00 on the 9th in Auckland. ``to`` must be the Auckland date."""
    configure(AUCKLAND)
    monkeypatch.setattr(main_module, "_utcnow", lambda: datetime(2026, 9, 8, 13, 0, tzinfo=UTC))

    with TestClient(app) as client:
        response = get(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["date"] == "2026-09-09"
    assert body["from"] == "2026-09-09"
    assert body["timezone"] == AUCKLAND
    assert body["window"] == ["2026-09-03", "2026-09-09"]


def test_from_after_to_is_a_422_naming_both_parameters(configure) -> None:
    configure("UTC")
    with TestClient(app) as client:
        response = get(client, **{"from": "2026-09-09", "to": "2026-09-08"})

    assert response.status_code == 422
    detail = str(response.json()["detail"])
    assert "from" in detail and "to" in detail
    assert "2026-09-09" in detail and "2026-09-08" in detail


def test_from_defaults_to_to_and_an_explicit_from_is_echoed(configure) -> None:
    configure("UTC")
    with TestClient(app) as client:
        explicit = get(client, **{"from": "2026-09-02", "to": D.isoformat()}).json()
        defaulted = get(client, to=D.isoformat()).json()

    assert explicit["from"] == "2026-09-02" and explicit["date"] == "2026-09-08"
    assert defaulted["from"] == "2026-09-08" and defaulted["date"] == "2026-09-08"


def test_a_future_to_and_a_pre_history_to_are_both_unavailable_with_200(configure, seeder) -> None:
    """There is essentially no error path: a day the athlete has not reached,
    a day before any capture, and an empty database are all
    ``hrv_unavailable`` with a 200, never a 404."""
    configure("UTC")
    with TestClient(app) as client:
        empty = get(client, to=D.isoformat())
        assert empty.status_code == 200, empty.text
        assert empty.json()["verdict"] == "hrv_unavailable"
        assert empty.json()["band"] is None
        assert empty.json()["baseline"]["established"] is False
        assert empty.json()["included"] == [] and empty.json()["excluded"] == []
        # T137: no reading of any tier at all is the structural cause, not
        # the generic "no band".
        assert empty.json()["unavailable_reason"] == "no_tier_sustains_a_trend"

    seeder.snapshots(days(D - timedelta(days=26), D), baseline_values(27))
    seeder.persist()

    with TestClient(app) as client:
        future = get(client, to=(D + timedelta(days=400)).isoformat())
        before = get(client, to=(D - timedelta(days=100)).isoformat())

    assert future.status_code == 200 and before.status_code == 200
    assert future.json()["verdict"] == "hrv_unavailable"
    assert future.json()["readings_in_window"] == 0
    # T137: this row is D + 400 relative to the real clock (this test does not
    # freeze it), so it is unconditionally after today -- the route's own
    # override reports day_not_happened here, not the structural cause that
    # also happens to hold (every reading is outside_windows): a day that has
    # not happened is the one true reason no verdict is asserted about it.
    assert future.json()["unavailable_reason"] == "day_not_happened"
    assert before.json()["verdict"] == "hrv_unavailable"
    assert before.json()["baseline"]["established"] is False
    assert before.json()["baseline"]["n"] == 0
    assert before.json()["unavailable_reason"] == "no_tier_sustains_a_trend"


def test_a_to_a_few_days_ahead_with_a_full_window_asserts_no_verdict(configure, seeder, monkeypatch) -> None:
    """The clock is frozen so the athlete's local today is ``D``. Daily
    captures through ``D`` -- an established baseline and a suppressed final
    week -- and ``to`` one, two and four days ahead: each judged window still
    holds at least three readings and the baseline is intact, so the pure
    computation would say ``hrv_suppressed`` about a day that has not
    happened. F005: a future date asserts no verdict. The ``D + 400`` row
    above cannot see this -- every reading is ``outside_windows`` there and
    the verdict is unavailable for an unrelated reason (review M1: the tests
    exercised the branch beside the bug). Red: ``hrv_suppressed``."""
    configure("UTC")
    monkeypatch.setattr(main_module, "_utcnow", lambda: datetime(D.year, D.month, D.day, 12, 0, tzinfo=UTC))
    seeder.snapshots(BASELINE_20, baseline_values(20))
    seeder.snapshots(days(D - timedelta(days=6), D), repeat(25.0))
    seeder.persist()

    with TestClient(app) as client:
        today = get(client, to=D.isoformat()).json()
        ahead = {k: get(client, to=(D + timedelta(days=k)).isoformat()).json() for k in (1, 2, 4)}

    assert today["verdict"] == "hrv_suppressed" and today["below_by"] > 0
    assert today["unavailable_reason"] is None
    for k, body in ahead.items():
        assert body["date"] == (D + timedelta(days=k)).isoformat()
        # The window is full and the baseline established: the guard is the
        # clock, not thin data.
        assert body["readings_in_window"] >= 3 and body["baseline"]["established"] is True
        assert body["verdict"] == "hrv_unavailable", (k, body["verdict"])
        # T137: day_not_happened, not the fields that would otherwise have
        # explained a suppressed/normal verdict -- the pure rule never gets a
        # say once the day is in the future.
        assert body["unavailable_reason"] == "day_not_happened", (k, body["unavailable_reason"])
        assert body["below_by"] is None


def test_the_zone_is_read_from_config_per_request(configure, seeder) -> None:
    """F005's rewritten scenario 18: a capture at 2026-09-07T17:00Z is the
    7th in UTC and the 8th in Auckland. Changing ``athlete_timezone`` between
    two requests moves the reading's local day and the reported zone, and
    asserts nothing about the change -- no reset object, no verdict."""
    configure("UTC")
    seeder.snapshot(datetime(2026, 9, 7, 17, 0, tzinfo=UTC), 40.0)
    seeder.persist()

    with TestClient(app) as client:
        in_utc = get(client, to=D.isoformat()).json()
        configure(AUCKLAND)
        in_auckland = get(client, to=D.isoformat()).json()

    assert in_utc["timezone"] == "UTC"
    assert [r["date"] for r in in_utc["included"]] == ["2026-09-07"]
    assert in_auckland["timezone"] == AUCKLAND
    assert [r["date"] for r in in_auckland["included"]] == ["2026-09-08"]
    for body in (in_utc, in_auckland):
        assert body["baseline"]["reset_on"] is None
        assert body["baseline"]["reset_reason"] is None
        assert body["verdict"] == "hrv_unavailable"


# ---------------------------------------------------------------------------
# the verdict blocks
# ---------------------------------------------------------------------------


def test_below_by_is_lo_minus_mean_when_suppressed_and_null_otherwise(configure, seeder) -> None:
    configure("UTC")
    values = baseline_values(20)
    seeder.snapshots(BASELINE_20, values)
    suppressed_week = [25.0, 26.0, 24.0]
    seeder.snapshots(days(D - timedelta(days=6), D - timedelta(days=4)), suppressed_week)
    seeder.persist()

    with TestClient(app) as client:
        response = get(client, to=D.isoformat())

    body = response.json()
    _, lo, _ = expected_band(values)
    week_mean = statistics.fmean(math.log(v) for v in suppressed_week)
    assert body["verdict"] == "hrv_suppressed"
    assert body["below_by"] == pytest.approx(lo - week_mean)
    assert body["below_by"] > 0
    assert body["below_by"] == pytest.approx(body["band"]["lo"] - body["ln_rmssd_7d_mean"])


def test_included_covers_the_window_only_and_excluded_spans_the_baseline_too(configure, seeder) -> None:
    """``included[]`` is the readings that fed the 7-day mean and nothing
    else -- the baseline is summarised by ``baseline.n`` -- while
    ``excluded[]`` lists every non-contributing row across ``[to-66, to]``,
    including an off-tier strap capture deep inside the baseline."""
    configure("UTC")
    seeder.snapshots(BASELINE_20, baseline_values(20))
    seeder.snapshots(days(D - timedelta(days=6), D - timedelta(days=3)), repeat(41.0))
    # 07:00, not 06:00: every synthetic file shares one source_device, so a
    # strap capture at the snapshot's instant would be the same session.
    deep = seeder.strap(at(D - timedelta(days=20), hh=7))
    seeder.persist()

    with TestClient(app) as client:
        body = get(client, to=D.isoformat()).json()

    assert body["baseline"]["n"] == 20
    assert len(body["included"]) == 4
    assert all(date.fromisoformat(r["date"]) >= D - timedelta(days=6) for r in body["included"])
    assert body["excluded"] == [
        {"date": "2026-08-19", "session_id": deep.session_id, "reason": f"off_baseline_tier: {STRAP}"}
    ]


# ---------------------------------------------------------------------------
# IDEA-045: the read window reaches the previous baseline window
# ---------------------------------------------------------------------------


def test_the_route_reads_rows_from_126_days_before_to(configure, monkeypatch) -> None:
    """The lower bound handed to ``db.read_hrv_rows`` must cover local day
    ``to - 126`` in any zone (UTC+14 starts it 14h before midnight UTC), so
    it is ``to - 126d - 26h`` or earlier; the upper bound must cover local
    day ``to`` in UTC-12, i.e. ``(to + 1) + 26h`` or later."""
    configure("UTC")
    seen: list[tuple[str, str]] = []
    real = db_module.read_hrv_rows

    def spy(conn, start_iso, end_iso):
        seen.append((start_iso, end_iso))
        return real(conn, start_iso, end_iso)

    monkeypatch.setattr(main_module.db, "read_hrv_rows", spy)

    with TestClient(app) as client:
        assert get(client, to=D.isoformat()).status_code == 200

    (start_iso, end_iso) = seen[0]
    midnight = datetime(D.year, D.month, D.day, tzinfo=UTC)
    assert datetime.fromisoformat(start_iso) <= midnight - timedelta(days=126, hours=26)
    assert datetime.fromisoformat(end_iso) >= midnight + timedelta(days=1, hours=26)
    assert start_iso.endswith("+00:00") and end_iso.endswith("+00:00"), "never a Z suffix"


def test_a_sustained_tier_change_is_reachable_through_the_endpoint(configure, seeder) -> None:
    """IDEA-045's scenario end to end: an established snapshot era in
    ``[D-126, D-67]`` followed by a strap era in ``[D-66, D-7]``. With a
    ``to - 66d`` read the previous window is empty, reads as thin, and the
    reset silently never fires; with ``to - 126d`` it is ``tier_change``."""
    configure("UTC")
    seeder.snapshots(days(D - timedelta(days=126), D - timedelta(days=67)), repeat(40.0))
    seeder.straps(days(D - timedelta(days=66), D - timedelta(days=7)))
    seeder.straps(days(D - timedelta(days=6), D - timedelta(days=4)))
    seeder.persist()

    with TestClient(app) as client:
        body = get(client, to=D.isoformat()).json()

    assert body["baseline"]["tier"] == STRAP
    assert body["baseline"]["reset_reason"] == "tier_change"
    assert body["baseline"]["reset_on"] == (D - timedelta(days=66)).isoformat()
    assert body["baseline"]["window"] == [(D - timedelta(days=66)).isoformat(), "2026-09-01"]
    assert body["baseline"]["established"] is True
    # The rows before D-66 fed the rule but are not "inside [to-66, to]", so
    # the documented excluded[] span does not list them.
    assert body["excluded"] == []


# ---------------------------------------------------------------------------
# G13 (T096): a layoff longer than the read window, end to end
# ---------------------------------------------------------------------------


def test_a_layoff_longer_than_the_read_window_reports_the_coverage_gap_through_the_endpoint(
    configure, seeder, monkeypatch
) -> None:
    """The route's own read bound is exercised, not just ``build_series``:
    a 34-reading snapshot era ``[D-161, D-128]`` whose last capture (06:00
    UTC) precedes the lower bound of the rows the route reads (``to - 126d
    - 26h``, i.e. ``D-127`` 22:00 UTC) -- then silence, then a daily
    snapshot from ``D-40``. The row read is **not** widened (the first spy
    pins its lower bound exactly where IDEA-045 put it, and the rows it
    returns are the 41 from ``D-40``, none of the era); the store's
    earliest reading is a scalar beside it (the second spy sees the route
    ask for it and receive the ``D-161`` instant), and it is what tells
    this 88-day layoff from a new athlete. ``coverage_gap`` on ``D-40``,
    the baseline clipped there with its 34 readings, and nothing before
    ``D-66`` listed. At ``726b6db`` the response reported no reset at all;
    a route that reads the rows but drops the scalar reports none here
    either (the 2026-09-12 mutant this pin was rebuilt to kill)."""
    configure("UTC")
    resume_on = D - timedelta(days=40)
    first_ever = D - timedelta(days=161)
    seeder.snapshots(days(first_ever, D - timedelta(days=128)), repeat(60.0))
    seeder.snapshots(days(resume_on, D), repeat(40.0))
    seeder.persist()
    seen: list[tuple[str, str, int]] = []
    earliest_seen: list[str | None] = []
    real_rows = db_module.read_hrv_rows
    real_earliest = db_module.earliest_hrv_reading

    def spy_rows(conn, start_iso, end_iso):
        rows = real_rows(conn, start_iso, end_iso)
        seen.append((start_iso, end_iso, len(rows)))
        return rows

    def spy_earliest(conn, tiers):
        found = real_earliest(conn, tiers)
        earliest_seen.append(found)
        return found

    monkeypatch.setattr(main_module.db, "read_hrv_rows", spy_rows)
    monkeypatch.setattr(main_module.db, "earliest_hrv_reading", spy_earliest)

    with TestClient(app) as client:
        body = get(client, to=D.isoformat()).json()

    (start_iso, _end_iso, n_rows) = seen[0]
    midnight = datetime(D.year, D.month, D.day, tzinfo=UTC)
    assert datetime.fromisoformat(start_iso) == midnight - timedelta(days=126, hours=26), "the row read is not widened"
    assert datetime.fromisoformat(start_iso) > at(D - timedelta(days=128)), "the era's last capture is outside the rows"
    assert n_rows == 41, "only the resumed era is read"
    assert earliest_seen == [seeder.sessions[0].start_time], "the route asked the store for its earliest reading"
    assert datetime.fromisoformat(earliest_seen[0]) == at(first_ever)
    assert body["baseline"]["reset_reason"] == "coverage_gap"
    assert body["baseline"]["reset_on"] == resume_on.isoformat()
    assert body["baseline"]["window"] == [resume_on.isoformat(), (D - timedelta(days=7)).isoformat()]
    assert body["baseline"]["n"] == 34
    assert body["baseline"]["established"] is True
    assert body["baseline"]["tier"] == SNAPSHOT
    assert body["excluded"] == []


def test_a_new_athletes_first_capture_is_not_a_coverage_gap_through_the_endpoint(configure, seeder) -> None:
    """The discriminator's other side through the real path: ordinary runs
    and pre-amendment rows for a year before the first capture at ``D-40``
    are stored rows, not readings, so the store's earliest *reading* is
    the first capture itself and nothing resets. Perturbation: a scalar
    that counts any stored row reports ``coverage_gap`` on ``D-40`` here."""
    configure("UTC")
    resume_on = D - timedelta(days=40)
    for day in days(D - timedelta(days=400), D - timedelta(days=127))[::7]:
        seeder.run(at(day, 18))
        seeder.pre_amendment(at(day, 6))
    seeder.snapshots(days(resume_on, D), repeat(40.0))
    seeder.persist()

    with TestClient(app) as client:
        body = get(client, to=D.isoformat()).json()

    assert body["baseline"]["reset_reason"] is None
    assert body["baseline"]["reset_on"] is None
    assert body["baseline"]["window"] == [(D - timedelta(days=66)).isoformat(), (D - timedelta(days=7)).isoformat()]
    assert body["baseline"]["n"] == 34
    assert body["baseline"]["tier"] == SNAPSHOT


def test_the_stores_earliest_reading_screens_rows_the_way_the_exclusion_chain_does(
    configure, persist_sessions
) -> None:
    """``db.earliest_hrv_reading`` is the one scalar G13 adds beside
    ``read_hrv_rows``: the earliest ``start_time`` of a *reading*, screened
    in SQL the way ``hrv_trend._exclusion_reason`` screens rows in Python.
    A null tier (an ordinary run), a pre-amendment row (a tier, no value), a
    tier the enum does not name, and a value ``ln`` cannot take -- ``0``, a
    negative, ``+inf`` (SQLite stores ``nan`` as ``NULL``, so it is the
    pre-amendment shape) -- are stored rows, not readings, and none of them
    is the store's earliest reading; a very large finite value is one. With
    no reading at all the scalar is ``None``. Perturbation: dropping any one
    screen makes that junk row the earliest reading, and a new athlete's
    first capture 40 days later reads as the end of a layoff."""
    configure("UTC")

    def stored(day: date, tier: str | None, value: float | None, device: str) -> Session:
        iso = at(day).isoformat()
        return Session(
            session_id=derive_session_id(device, iso),
            sport="running",
            source_vendor="garmin",
            start_time=iso,
            source_device=device,
            hrv_source_tier=tier,
            resting_rmssd_ms=value,
        )

    junk = [
        stored(D - timedelta(days=300), None, None, "run"),
        stored(D - timedelta(days=290), STRAP, None, "pre-amendment"),
        stored(D - timedelta(days=280), "wrist_ppg", 40.0, "unknown-tier"),
        stored(D - timedelta(days=270), SNAPSHOT, 0.0, "zero"),
        stored(D - timedelta(days=260), SNAPSHOT, -5.0, "negative"),
        stored(D - timedelta(days=250), STRAP, math.inf, "inf"),
        stored(D - timedelta(days=240), STRAP, math.nan, "nan"),
    ]
    huge = stored(D - timedelta(days=200), SNAPSHOT, 1e300, "huge")
    first = stored(D - timedelta(days=40), SNAPSHOT, 40.0, "first")

    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        assert db_module.earliest_hrv_reading(conn, hrv_trend.TIER_FIDELITY) is None
        persist_sessions(junk + [first])
        assert db_module.earliest_hrv_reading(conn, hrv_trend.TIER_FIDELITY) == first.start_time
        persist_sessions([huge])
        assert db_module.earliest_hrv_reading(conn, hrv_trend.TIER_FIDELITY) == huge.start_time
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# T093: a trial-then-abandoned strap, end to end
# ---------------------------------------------------------------------------


def test_a_trial_then_abandoned_strap_does_not_blank_the_verdict_through_the_endpoint(configure, seeder) -> None:
    """The critic's series (``CRITIC-F005.md``) through ``db.persist`` and
    ``GET /metrics/hrv``: a daily Health Snapshot for 200 days ending
    ``D`` (Pacific/Auckland), genuinely suppressed at 25 ms on 2026-08-20 ..
    2026-09-02, and a chest strap used daily 2026-07-03 .. 2026-07-16 and
    never again. The strap sustains a baseline by count for every ``to``
    from 2026-07-23 to 2026-09-07 but covers no judged week, so the
    snapshot owns the verdict throughout: the suppression is reported on
    2026-09-02, no ``tier_change`` is reported on any day, and ``points[]``
    carries one continuous snapshot band across the range -- every day's
    band equal to the one computed here from the snapshot values alone."""
    configure(AUCKLAND)
    snapshot_days = days(D - timedelta(days=199), D)
    suppressed = days(date(2026, 8, 20), date(2026, 9, 2))
    values = {day: 25.0 if day in suppressed else 38.0 + 6.0 * (i % 2) for i, day in enumerate(snapshot_days)}
    for day in snapshot_days:
        seeder.snapshot(at(day, 7, zone=AUCKLAND), values[day])
    for day in days(date(2026, 7, 3), date(2026, 7, 16)):
        seeder.strap(at(day, 6, zone=AUCKLAND))  # a different instant from the day's snapshot
    seeder.persist()

    first, last = date(2026, 7, 23), D
    with TestClient(app) as client:
        on_suppressed_day = get(client, to="2026-09-02").json()
        response = get(client, **{"from": first.isoformat(), "to": last.isoformat()})
    assert response.status_code == 200, response.text
    body = response.json()

    assert on_suppressed_day["verdict"] == "hrv_suppressed"
    assert on_suppressed_day["below_by"] > 0
    assert on_suppressed_day["baseline"]["tier"] == SNAPSHOT
    assert on_suppressed_day["baseline"]["established"] is True
    assert on_suppressed_day["baseline"]["reset_reason"] is None
    assert on_suppressed_day["readings_in_window"] == 7
    strap_rows = [e for e in on_suppressed_day["excluded"] if e["reason"].startswith("off_baseline_tier")]
    assert len(strap_rows) == 14
    assert {e["reason"] for e in strap_rows} == {"off_baseline_tier: chest_strap_raw"}

    assert body["baseline"]["tier"] == SNAPSHOT
    assert body["baseline"]["reset_reason"] is None
    points = body["points"]
    assert [p["date"] for p in points] == [d.isoformat() for d in days(first, last)]
    for point in points:
        day = date.fromisoformat(point["date"])
        lo, hi = hrv_trend.baseline_window(day)
        _, expected_lo, expected_hi = expected_band([v for d, v in sorted(values.items()) if lo <= d <= hi])
        assert point["swc_low"] == pytest.approx(expected_lo, abs=1e-6), point
        assert point["swc_high"] == pytest.approx(expected_hi, abs=1e-6), point
    # Continuity: one snapshot band, drifting day by day as the baseline
    # slides, never stepping onto the strap's (ln 79 vs ln 41, about 0.66).
    steps = [abs(b["swc_low"] - a["swc_low"]) for a, b in pairwise(points)]
    assert max(steps) < 0.05, max(steps)


# ---------------------------------------------------------------------------
# T094: the reverse transition and a non-sliding reset_on, end to end
# ---------------------------------------------------------------------------


def test_the_reverse_transition_reports_its_reset_the_day_the_snapshot_owns_the_baseline(configure, seeder) -> None:
    """G3 through ``db.persist`` and ``GET /metrics/hrv``: a daily strap
    from ``to-126`` to ``T = D-60``, a daily snapshot from ``T+1``. At
    ``T+20`` the strap still holds the baseline with nothing to judge; at
    ``T+21`` the snapshot owns it with ``tier_change`` on ``T+1``, and at
    ``T+60`` the same ``reset_on`` is still reported. Under cf48c3a the
    reset arrived at ``T+54``."""
    configure("UTC")
    T = D - timedelta(days=60)
    seeder.straps(days(D - timedelta(days=126), T))
    seeder.snapshots(days(T + timedelta(days=1), D), repeat(40.0))
    seeder.persist()

    with TestClient(app) as client:
        before = get(client, to=(T + timedelta(days=20)).isoformat()).json()
        first = get(client, to=(T + timedelta(days=21)).isoformat()).json()
        later = get(client, to=D.isoformat()).json()

    assert before["baseline"]["tier"] == STRAP
    assert before["baseline"]["reset_reason"] is None and before["baseline"]["reset_on"] is None
    assert before["verdict"] == "hrv_unavailable" and before["readings_in_window"] == 0

    era_start = (T + timedelta(days=1)).isoformat()
    for body in (first, later):
        assert body["baseline"]["tier"] == SNAPSHOT
        assert body["baseline"]["reset_reason"] == "tier_change"
        assert body["baseline"]["reset_on"] == era_start
        assert body["baseline"]["established"] is True
    assert first["baseline"]["window"] == [era_start, (T + timedelta(days=14)).isoformat()]
    assert first["baseline"]["n"] == 14
    assert later["baseline"]["window"] == [era_start, (D - timedelta(days=7)).isoformat()]


def test_reset_on_does_not_slide_once_the_era_start_ages_past_the_window(configure, seeder) -> None:
    """G8 through the endpoint: a snapshot era to D-81 and a daily strap
    from D-80. At ``to = D-13`` and ``to = D-1`` the era start is older
    than ``to-66``; ``reset_on`` is D-80 on both, and ``window[0]`` is the
    clip ``max(to-66, reset_on)`` -- so ``reset_on`` may precede
    ``window[0]``. Under cf48c3a the two answers were D-79 and D-67."""
    configure("UTC")
    switch = D - timedelta(days=80)
    seeder.snapshots(days(D - timedelta(days=126), D - timedelta(days=81)), repeat(40.0))
    seeder.straps(days(switch, D))
    seeder.persist()

    with TestClient(app) as client:
        early = get(client, to=(D - timedelta(days=13)).isoformat()).json()
        late = get(client, to=(D - timedelta(days=1)).isoformat()).json()

    for body, to in ((early, D - timedelta(days=13)), (late, D - timedelta(days=1))):
        assert body["baseline"]["tier"] == STRAP, to
        assert body["baseline"]["reset_reason"] == "tier_change", to
        assert body["baseline"]["reset_on"] == switch.isoformat(), to
        assert body["baseline"]["window"] == [(to - timedelta(days=66)).isoformat(), (to - timedelta(days=7)).isoformat()], to
        assert body["baseline"]["reset_on"] < body["baseline"]["window"][0], to


# ---------------------------------------------------------------------------
# IDEA-046: an empty baseline after a late reset
# ---------------------------------------------------------------------------


def test_an_empty_baseline_window_after_a_late_reset_is_rendered_inverted(configure, seeder) -> None:
    """A gap ending inside the judged week: the clip ``[reset_on, D-7]`` is
    an empty interval. It is rendered exactly as the formula yields it --
    first after last -- with ``n`` 0, so a reader can verify the clip from
    ``reset_on`` and ``date``; the schema says so. The verdict is
    ``hrv_unavailable`` and the pre-gap readings are listed as
    ``before_reset: coverage_gap``."""
    configure("UTC")
    era = days(D - timedelta(days=66), D - timedelta(days=30))
    seeder.snapshots(era, repeat(40.0))
    seeder.snapshots(days(D - timedelta(days=2), D), repeat(40.0))
    seeder.persist()

    with TestClient(app) as client:
        body = get(client, to=D.isoformat()).json()

    resumed = (D - timedelta(days=2)).isoformat()
    assert body["baseline"]["reset_reason"] == "coverage_gap"
    assert body["baseline"]["reset_on"] == resumed
    assert body["baseline"]["window"] == [resumed, "2026-09-01"]
    assert body["baseline"]["window"][0] > body["baseline"]["window"][1]
    assert body["baseline"]["n"] == 0
    assert body["band"] is None
    assert body["verdict"] == "hrv_unavailable"
    assert body["readings_in_window"] == 3
    assert {e["reason"] for e in body["excluded"]} == {"before_reset: coverage_gap"}
    assert len(body["excluded"]) == len(era)


# ---------------------------------------------------------------------------
# the schema is the documentation
# ---------------------------------------------------------------------------


def test_the_schema_names_every_exclusion_reason_and_the_verdict_enum() -> None:
    """IDEA-041 / IDEA-046: the reasons T083 and T092 emit beyond the task's
    original five are in the schema description, read from the module's
    constants so the two cannot drift. The verdict is a closed enum."""
    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    reason = schemas["ExcludedReading"]["properties"]["reason"]["description"]
    for constant in (
        hrv_trend.REASON_PRE_AMENDMENT_WINDOW,
        hrv_trend.REASON_NULL_TIER,
        hrv_trend.REASON_UNKNOWN_TIER,
        hrv_trend.REASON_UNUSABLE_VALUE,
        hrv_trend.REASON_OFF_BASELINE_TIER,
        hrv_trend.REASON_SAME_DAY_LATER_CAPTURE,
        hrv_trend.REASON_OUTSIDE_WINDOWS,
        hrv_trend.REASON_BEFORE_RESET,
    ):
        assert constant in reason, constant
    assert set(schemas["HrvTrendResponse"]["properties"]["verdict"]["enum"]) == {
        "hrv_normal",
        "hrv_suppressed",
        "hrv_unavailable",
    }
    window = schemas["Baseline"]["properties"]["window"]["description"]
    assert "empty" in window and "n" in window, "IDEA-046's rendering decision is documented"


#: The checked-in target contract, the copy ``check_drift.py`` compares
#: endpoints against. Its ``reset_reason`` prose and ``schemas.Baseline``'s
#: are hand-synchronised: nothing in the tree compared them until T105.
CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"

# ``RESET_REASON_CLAIMS`` below declares the contract's live claim fragments.
# The retracted phrasings are *not* declared here: they live in
# ``tests/support/withdrawn_phrasings.py``, the one module the walk holds out
# for a *declaration* reason rather than a historical-record one (T114, gap
# G-C7-10; T119, gap G-C7-20 -- the note here used to call it the single file
# outside the scan, which the exclusion tables below have made untrue). This
# file is walked like any other, and the standing rule for it is the same as
# for every other scanned file -- do not quote a withdrawn phrasing anywhere
# in it. Notes that need to point at one name it by its index, or paraphrase
# it.

#: The sentences of the ``baseline.reset_reason`` description a client is
#: invited to key on, transcribed as the smallest fragment that carries each
#: claim rather than as the paragraph around it. One per decision a reader
#: could make differently if it vanished:
#:
#: 1. the mirror half T098 needed published (a null reason beside a clipped
#:    window is a correct state, not a bug to report);
#: 2. T103's surviving primary clause (the clip is unconditional under a
#:    reported ``tier_change``);
#: 3. the mechanism that makes the clip stop mattering, named with the
#:    expression that implements it;
#: 4-6. T105's correction as T109 re-derived it: the no-op stretch is
#:    conditional on the report outliving the day ``date-66`` reaches ``R``;
#:    that condition is *liveness*, which is rule 4's three conditions
#:    together and not clause (b) alone; and the inference a client must
#:    *not* draw.
#:
#: No entry here is a withdrawn phrasing, so this tuple is read by the file
#: scan like any other live line (T113, G-C7-6) -- as, since T114, is every
#: other line of this file.
#:
#: Authorship, corrected by T112 (review cycle 7, G-C7-2). T109's note here
#: said the entries were "re-derived from ``build_series``" -- which is the
#: wrong direction for a **contract** table: a claim derived from the
#: implementation it exists to constrain cannot detect an implementation
#: defect by construction (``contract-tables-need-an-independent-oracle``;
#: ``project-domain-and-spec-fidelity`` names ``research/00`` the authority).
#: The entries are **constrained by ``research/00`` §5.4 and F005** -- rule
#: 4's three conditions, the clip-versus-report split of decision log D4 --
#: and were *checked against* ``build_series``, which is a reproduction, not
#: a derivation. What T109 got right is that they are no longer transcribed
#: from the prose they pin: entry 5 used to be the (b)-alone gloss that is
#: now ``RESET_REASON_WITHDRAWN``'s entry 4, copied from the sentence it
#: pinned in the same pass that wrote it, and that sentence was false -- so
#: this tuple held a false equivalence in place and made removing it go red.
#:
#: This tuple can still only see the *words*. What makes entry 5 falsifiable
#: by the implementation is
#: ``test_hrv_trend_reset.test_clause_a_lapsing_nulls_the_report_while_clause
#: _b_and_the_week_half_still_hold`` (T112), which drives the (a)-lapses
#: series through ``build_series``; with the clause (a) gate deleted that pin
#: goes red and every assertion here stays green.
#:
#: Each entry below was reproduced against ``build_series`` at
#: ``D = R + 66`` on a daily switch series before it was kept: 1 and 2 on a
#: reported boundary with ``R > D-66`` (window clipped) and on the same
#: series with three old-tier days in ``[D-6, D]`` (null reason, same
#: clipped window); 3 at ``R+66`` and ``R+79`` (window un-clipped); 5 on the
#: three one-clause-fails series -- (a) fails with 11 strap days while the
#: snapshot still holds 60 in ``[D-126, D-67]``, (b) fails with 10 snapshot
#: days there, the week half fails on three stray days -- each null while
#: the other two conditions hold; 6 on T105's sparse-old-tier series, where
#: ``tier_change`` is reported on ``R+20..R+44`` and clipped on every one of
#: those days, 22 days before ``date-66`` reaches ``R``.
RESET_REASON_CLAIMS = (
    "a null here can sit beside a clipped `window`",
    "a reported `tier_change` is always clipped",
    "once the era's first day is date-66 or older the clip `max(date-66, R)` is a no-op",
    "that no-op stretch is conditional, not promised",
    "liveness is rule 4's three conditions together, not any one of them alone",
    "a client cannot infer from seeing a `tier_change` that an un-clipped `window` will follow",
)

def _load_module(name: str, path: Path) -> ModuleType:
    """Import a module from its path (see the note below on ``importlib``)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The withdrawn phrasings and the docstring idioms of the same claims are
#: declared in ``tests/support/withdrawn_phrasings.py`` (T114, gap G-C7-10).
#: That module is the one file the walk below holds out for a *declaration*
#: reason: it **is** the literals, so reading it would match them by
#: construction. What stops it being a hiding place is
#: ``test_the_unscanned_declaration_module_stays_a_declaration_table``, which
#: goes red if the module grows a comment, a top-level statement that is not
#: an import or an assignment, or a docstring longer than one line -- so there
#: is no prose in it for a claim to drift into.
#:
#: Everything else about the scan -- the roots, the file kinds, the two
#: exclusion tables and their reasons, the anchors -- lives in this file
#: instead, which the walk reads in full (T115, gap G-C7-18; T119, gap
#: G-C7-18).
#:
#: Why the declarations moved out. Until T114 they lived here, in the file
#: that scans for them, and every consequence of that self-reference had to be
#: engineered around:
#:
#: * the scan would have matched its own declarations, so the declaring
#:   literals were wrapped in marker comments and excised before scanning. Two
#:   generations of that fence were defeated by a marker pairing off with
#:   another copy of itself, the second time *by the commit that fixed the
#:   first* (T113 split one fenced region into two, after which deleting the
#:   first end marker left the fence balanced, the suite green and ~12 lines
#:   of live commentary silently unscanned);
#: * prose near the declarations could not quote a withdrawn phrasing without
#:   turning the scan red on itself, so glosses survived on markup asterisks
#:   or by index alone;
#: * the anchor proving the scan had read this file was itself one of the
#:   literals in that table, so a truncation dropped the live copy and passed
#:   on the configuration copy (measured 2026-09-14: truncating the suite
#:   after line 1000 left it green).
#:
#: Moving the declarations out removes all three at once. There is no fence in
#: the tree any more, nothing is excised from any scanned file, and this file
#: is read in full by the walk -- which
#: ``test_the_walk_reaches_every_file_an_anchor_speaks_for`` asserts by name
#: for this path, and whose anchor row would fail if the read stopped
#: reaching the tail.
#:
#: Loaded from its path because the workspace runs pytest with
#: ``--import-mode=importlib``, under which nothing in ``tests/`` is importable
#: by name.
_DECLARATIONS = _load_module(
    "withdrawn_phrasings", Path(__file__).parent / "support" / "withdrawn_phrasings.py"
)
RESET_REASON_WITHDRAWN = _DECLARATIONS.RESET_REASON_WITHDRAWN
RESET_REASON_WITHDRAWN_IDIOMS = _DECLARATIONS.RESET_REASON_WITHDRAWN_IDIOMS

#: What the three declarations are (T115, G-C7-18: this note used to live
#: beside them, in the module nothing scans).
#:
#: ``RESET_REASON_WITHDRAWN`` holds the phrasings withdrawn as false, which no
#: live copy may carry again, in the order they are declared:
#:
#: 1. T103's universal (a reported ``tier_change`` and an un-clipped window can
#:    never hold at the same time);
#: 2-3. T105's replacement universal, in the two spellings it was written in
#:    (the no-op stretch presented as something every report ends in, rather
#:    than as the conditional stretch it is);
#: 4-5. T109's two spellings of T105's false equivalence -- the gloss that
#:    equated report-liveness with clause (b), which lived in both contract
#:    copies, and the lifetime sentence that stated the same equivalence the
#:    other way round, which lived in the served copy alone and so was
#:    invisible to a pin that only compared the two copies' shared run.
#:
#: Entries 4 and 5 are false for the same reason: clause (b) is necessary for
#: the report, not sufficient. The one-shot ``! grep -q`` in those tasks' own
#: acceptance probes is the weak form ``sweep-the-claim-not-the-diff`` warns
#: about -- it never runs again. These do.
#:
#: ``RESET_REASON_WITHDRAWN_IDIOMS`` holds the same equivalence in the idioms
#: the *docstrings* use rather than the contract's. The two survivors T111
#: found are its entries 1 and 2 -- a form keyed on clause (b) failing, and a
#: form keyed on when the report ceases, neither of which any contract copy
#: would ever say -- so a tuple written against contract prose could not have
#: caught them even pointed at the right files.
#:
#: The third thing the scan needs -- *which files it reads* -- stopped being a
#: declaration in T119 (gap G-C7-18). It is the tree walk below, and the
#: anchors beside it are what prove the walk read something rather than
#: reporting all-clear over an empty match.


#: T114 (review cycle 7, G-C7-14). Eight live notes in this suite and in
#: ``hrv_trend.py`` address a withdrawn phrasing **by its index** -- "entry 4",
#: "entries 1 and 2 below", "``RESET_REASON_WITHDRAWN``'s first entry" -- and
#: until now nothing pinned either tuple's order or contents, so a reorder or
#: a mid-tuple insert silently re-pointed every one of those references at a
#: different claim, with no test able to notice.
#:
#: Each row is (tuple name, 1-based index, the first 12 hex digits of the
#: SHA-256 of the flattened entry, what that entry is). The digest is used
#: rather than the phrasing for the reason the fence used to exist: a literal
#: copy here would be a second copy of a withdrawn phrasing in a scanned file.
#: Unlike the fence, nothing has to stay balanced for this to work -- a wrong
#: digest is a failure, not a silent excision. Regenerate a row only when the
#: entry is *deliberately* changed, and fix the prose that names its index in
#: the same commit:
#:
#:     hashlib.sha256(_flat(entry).encode()).hexdigest()[:12]
WITHDRAWN_ORDER = (
    ("RESET_REASON_WITHDRAWN", 1, "68cdf4d5e54a", "T103's universal"),
    ("RESET_REASON_WITHDRAWN", 2, "13eab33ef6be", "T105's replacement, spelling 1"),
    ("RESET_REASON_WITHDRAWN", 3, "0cf46dd3cda0", "T105's replacement, spelling 2"),
    ("RESET_REASON_WITHDRAWN", 4, "66a6009ab905", "T109's (b)-alone gloss, contract idiom"),
    ("RESET_REASON_WITHDRAWN", 5, "4d68ca692f13", "T109's (b)-alone gloss, lifetime idiom"),
    ("RESET_REASON_WITHDRAWN_IDIOMS", 1, "d8f727d91f20", "T111's docstring survivor, (b)-fails form"),
    ("RESET_REASON_WITHDRAWN_IDIOMS", 2, "74d7925e934a", "T111's docstring survivor, stops-only-when form"),
    ("RESET_REASON_WITHDRAWN_IDIOMS", 3, "5f10a1615377", "T111's third docstring spelling"),
    ("VERDICT_WITHDRAWN", 1, "c547f180e777", "T116's asymmetric enumeration of unavailable"),
    ("VERDICT_WITHDRAWN", 2, "92bd3da2474a", "T116's suppression-only gloss, contract idiom"),
    ("VERDICT_WITHDRAWN", 3, "0900df973b19", "T116's leading suppression-only claim"),
    ("VERDICT_WITHDRAWN", 4, "9e09d905cfba", "T116's asymmetry in F005's @must Gherkin idiom"),
    ("WINDOW_WITHDRAWN", 1, "7bad332b1fca", "T107's identity on window[0], schema idiom"),
    ("WINDOW_WITHDRAWN", 2, "5d2f570ec80d", "T107's identity on window[0], YAML idiom"),
    ("ESTABLISHED_WITHDRAWN", 1, "9b001bd01d01", "T116's asymmetric established gloss"),
    ("THRESHOLDS_WITHDRAWN", 1, "be7083eef471", "IDEA-070's retracted promise, schema idiom"),
    ("THRESHOLDS_WITHDRAWN", 2, "285bbbe3b570", "IDEA-070's retracted promise, YAML idiom"),
)


#: The five HRV suites, as paths relative to the repo root. This is the scope
#: T117's measured green band for ``RECENCY_TOLERANCE_DAYS`` -- ``[18, 44]`` --
#: was taken over, and the scope is half of what that band means: the band is a
#: claim about *the rest of the suite*, since the tolerance's own pins are red
#: at every other value by construction.
SCOPED_HRV_SUITES = (
    "runcoach-api/tests/test_hrv_trend_band.py",
    "runcoach-api/tests/test_hrv_trend_endpoint.py",
    "runcoach-api/tests/test_hrv_trend_points.py",
    "runcoach-api/tests/test_hrv_trend_reset.py",
    "runcoach-api/tests/test_hrv_trend_series.py",
)

#: What ``pytest --collect-only`` returns over those five files **at this
#: commit**, re-measured as the last action before the commit that changes it.
#:
#: This constant exists so that no document has to carry the number (review
#: cycle 8, ``acfebae``). T121 published "the five suites collect 395" into three
#: normative documents to give the band its missing scope, and the very next
#: commit in T121's own fix batch added a test to one of those five suites and
#: made all three wrong -- silently, because nothing in the tree could see a
#: suite gain a test. The three sites now cite this pin by name and carry no
#: literal; the assertion below is what reddens when the corpus moves, and the
#: author who reddens it is the author who re-measures it.
SCOPED_SUITE_COLLECTED = 413  # re-measured 2026-09-17 (T138), as the last action before the
#                              # commit: +4. T138 added four pins to test_hrv_trend_reset.py (the
#                              # tier-change walk -- the silence, the reporting lag, and the two
#                              # dependencies of the figure) and touched no other test's identity,
#                              # so the net is four tests. Nothing publishes this literal; T132
#                              # left it at 409.

#: The collected tests the band's corpus **excludes**: the pins that assert the
#: tolerance's own value, directly or by holding its measured consequences, and
#: are therefore red at every ``N != RECENCY_TOLERANCE_DAYS`` by construction.
#: The relation -- corpus = collection minus these -- is the claim the band
#: depends on, and is what the three prose sites now state instead of a number.
BAND_CORPUS_EXCLUDES = (
    "test_rule_1s_recency_admits_a_candidate_up_to_the_tolerance_and_strikes_it_past_it",
    "test_the_seam_row_is_the_only_one_whose_red_onset_is_at_gap_reset_days",
)

#: The size of that corpus **on 2026-09-15, when the band was measured** (T121,
#: re-derived from its own table: 395 collected, less the one tolerance pin that
#: existed then). A dated fact, not a live one -- which is exactly why it is
#: here beside the live measurement rather than in a document on its own. The
#: assertion below reports how far the corpus has since grown, so the bracket
#: keeps meaning "green over those 394" rather than silently re-scoping itself
#: to a suite that has moved underneath it.
BAND_CORPUS_WHEN_MEASURED = 394


def test_the_scoped_suite_count_the_band_was_measured_over_is_pinned_not_published() -> None:
    """The count three normative documents used to publish, as an assertion.

    ``RECENCY_TOLERANCE_DAYS``' comment, ``research/00`` §5.4, F005's Negative
    Class row and the construction reference's constants table each scope the
    ``[18, 44]`` band to the tests of the five HRV suites that predate T117's
    tolerance pin. Until ``acfebae`` that scope was carried by a transcribed literal
    at every one of those sites and by no assertion anywhere, so when ``140dfff``
    added a test to ``test_hrv_trend_endpoint.py`` -- one commit after the
    batch that wrote the literal, in the same batch -- all four went stale and
    every gate in the tree stayed green. A number published in four documents
    and asserted in none is
    ``a-published-invariant-needs-a-test-that-can-break-it``'s exact shape.

    Collection is measured in a subprocess rather than counted from this
    session: ``--collect-only`` over the five paths is the same operation the
    band was measured with, it costs about a second, and reading it back from
    the running session would count whatever selection *this* run was given
    (``-k``, a single file, ``--lf``) instead of the suites' own size.

    What each assertion is for. The first pins the live collection, so any of
    the five gaining or losing a test is red here and nowhere else. The second
    pins the **relation** -- that the band's corpus is the collection less the
    tolerance's own pins -- by requiring each named pin to be collected exactly
    once; a renamed or deleted pin is red rather than silently shrinking the
    exclusion. The third keeps T121's dated bracket honest: the corpus may only
    have grown since it was measured, and by how much is reported rather than
    absorbed.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *SCOPED_HRV_SUITES],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,  # the returncode is asserted below, with the collection output in the message
    )
    assert result.returncode == 0, (
        f"collection over the five HRV suites failed (rc {result.returncode}): "
        f"{result.stdout[-2000:]} {result.stderr[-2000:]}"
    )
    node_ids = [line.strip() for line in result.stdout.splitlines() if "::" in line]

    assert len(node_ids) == SCOPED_SUITE_COLLECTED, (
        f"the five HRV suites now collect {len(node_ids)} tests, not the "
        f"{SCOPED_SUITE_COLLECTED} SCOPED_SUITE_COLLECTED pins. That is not a failure, it is "
        f"the notification T121's three published literals could not give: re-measure with "
        f"`uv run --package runcoach-api pytest --collect-only -q "
        f"runcoach-api/tests/test_hrv_trend_{{band,endpoint,points,reset,series}}.py`, update "
        f"this constant, and check that nothing has started publishing the number again"
    )

    for name in BAND_CORPUS_EXCLUDES:
        matched = [node for node in node_ids if f"::{name}" in node]
        assert len(matched) == 1, (
            f"{name} is collected {len(matched)} times, not once: the band's corpus is defined "
            f"as the collection less these pins, and that subtraction is now wrong"
        )

    corpus = len(node_ids) - len(BAND_CORPUS_EXCLUDES)
    assert corpus >= BAND_CORPUS_WHEN_MEASURED, (
        f"the band's corpus is {corpus} tests, below the {BAND_CORPUS_WHEN_MEASURED} T121 "
        f"measured [18, 44] over: the bracket is a claim about tests that no longer all exist"
    )


def _declared_phrasing_tuples() -> dict[str, tuple[str, ...]]:
    """Every phrasing tuple the declaration module declares, by name.

    One filter, read by both the order pin and the sweep-scope guard below, so
    the two tables T122 wrote to stop the sweep drifting from the declarations
    cannot themselves drift from each other (review cycle 8)."""
    return {
        name: value
        for name, value in vars(_DECLARATIONS).items()
        if not name.startswith("_")
        and isinstance(value, tuple)
        and value
        and all(isinstance(entry, str) for entry in value)
    }


def test_the_withdrawn_tuples_are_in_the_order_the_prose_names_them_by() -> None:
    """The index-by-index pin ``WITHDRAWN_ORDER`` describes.

    The digest loop is what catches a reorder, a mid-tuple insert, a reword or
    a deleted entry: each of those either re-points a pinned index at a
    different claim, so that index's digest no longer matches, or indexes past
    the end of the tuple and raises before the comparison.

    The two length assertions after it close the one change the digest loop
    cannot see: a ``WITHDRAWN_ORDER`` row deleted *together with* its tuple
    entry, which leaves every surviving digest correct while the scan has
    silently stopped guarding that phrasing (T115, G-C7-16). They also make an
    append red until ``WITHDRAWN_ORDER`` gains the row that pins the new entry
    -- an entry no row pins is one the index notes cannot name. The ``>=``
    that stood here before T115 could not fail at all: the pinned indices are
    contiguous ``1..N``, so a short tuple always raised ``IndexError`` in the
    loop above first.

    The dispatch below is read from ``vars(_DECLARATIONS)`` by the same filter
    ``test_every_declared_withdrawn_tuple_is_swept`` uses, rather than from a
    hand-kept literal of the tuple names (review cycle 8, ``acfebae``). T122 built
    that filter precisely so the sweep could not fall out of step with the
    declarations and then left this table, one screen away and in the same
    commit, keyed on three names written out by hand -- so a fifth tuple was
    swept, but its entries were pinned by nothing and every index note naming
    them was unguarded. Latent then, because the three names were complete;
    this pass adds two tuples, which is exactly the event that would have made
    it live."""
    tuples = _declared_phrasing_tuples()
    for name, index, digest, what in WITHDRAWN_ORDER:
        entry = tuples[name][index - 1]
        actual = hashlib.sha256(_flat(entry).encode()).hexdigest()[:12]
        assert actual == digest, (
            f"{name} entry {index} is no longer {what}: every note that names "
            f"that index now points at a different claim (got {actual})"
        )
    for name, tup in tuples.items():
        pinned = [row for row in WITHDRAWN_ORDER if row[0] == name]
        assert len(tup) == len(pinned), (
            f"{name} has {len(tup)} entries against {len(pinned)} pinned rows: "
            f"every entry is pinned by exactly one row, and every row pins an entry"
        )
    assert len(WITHDRAWN_ORDER) == 17, (
        f"WITHDRAWN_ORDER has {len(WITHDRAWN_ORDER)} rows, not 17: a row and its "
        f"tuple entry dropped together leave every remaining digest correct"
    )


def test_every_declared_withdrawn_tuple_is_swept() -> None:
    """A tuple can be declared and then left out of the walk, which is not a
    failure any assertion over the walk can see: the sweep reports all-clear
    over the phrasings it *was* given.

    That is the state ``VERDICT_WITHDRAWN`` was in from T116 until T122 -- read
    against the two contract copies only, while the ``reset_reason`` tuples
    were read against every walked file -- and it is the same shape as the
    ``WITHDRAWN_ORDER`` row T115 closed: a declaration nothing connects to the
    thing that uses it. This reads the declaration module's own namespace, so a
    fourth tuple is red until it is in ``WITHDRAWN_SWEPT``, and no one has to
    remember."""
    declared = _declared_phrasing_tuples()
    assert declared, "the declaration module declares no phrasing tuple at all: the walk reads nothing"
    for name, value in sorted(declared.items()):
        missing = [entry for entry in value if entry not in WITHDRAWN_SWEPT]
        assert not missing, (
            f"{name} is declared but not in WITHDRAWN_SWEPT, so the walk never looks for "
            f"{len(missing)} of its phrasings and every all-clear over them is vacuous: {missing}"
        )


# ---------------------------------------------------------------------------
# The scan: a walk over two trees, and the tables that say what is held out.
# ---------------------------------------------------------------------------

#: T119 (review cycle 7, gap G-C7-18). Until now the files the scan read were
#: a seven-path allowlist, and its completeness was a dated manual measurement
#: that nothing asserted. An allowlist is one level weaker than the string
#: sweep ``sweep-the-claim-not-the-diff`` warns about: a string sweep passes as
#: soon as the sentence is reworded, an allowlist passes as soon as the
#: sentence **moves file**. Two of the seven also lived under the gitignored
#: ``.shipyard`` breadcrumb and were skipped when it was absent, so a checkout
#: without it scanned five files and read the two normative documents nowhere
#: -- the two documents whose drift from each other produced G-C6-4, G-C6-7
#: and half of cycle 7's findings.
#:
#: What replaces it is the complement: walk both trees, and name what is held
#: out. No file is added to the scan by hand any more; a file leaves it only
#: through one of the three tables below, each entry of which states its
#: reason.
SCAN_SUFFIXES = (".py", ".md", ".yaml")

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The two roots. The first is the committed tree. The second is the Shipyard
#: data dir, reached through the ``.shipyard`` junction, which is gitignored
#: and machine-local -- so **in a checkout without it the walk is the first
#: root alone**, and that is a statement two assertions hold up rather than a
#: sentence: ``test_the_walk_reads_whole_trees_and_not_an_empty_one`` floors
#: the two roots separately and requires the first to exist, and
#: ``test_the_walk_reaches_every_file_an_anchor_speaks_for`` *fails* rather
#: than skips when a committed anchored file is not reached. The two normative
#: F005 documents are under the second root, so they are scanned wherever the
#: breadcrumb is and nowhere else; moving them into the committed tree is the
#: only thing that would change that, and it is not this task's to do.
SCAN_ROOTS = (_REPO_ROOT, _REPO_ROOT / ".shipyard")

#: Floors on how many files each root must yield, by root index. Measured
#: 2026-09-15 on this tree: **125** files under the committed root (127 found,
#: two held out) and **139** under the breadcrumb (265 found, 126 held out as
#: history) -- so what a checkout without the breadcrumb scans is 125 files,
#: against the five the allowlist reached there. Those two numbers are a
#: measurement of one tree on one date. The floors below are the invariant,
#: and they sit well under the measurement on purpose: their job is not to pin
#: a count -- ordinary growth and ordinary deletion must not trip them -- but
#: to catch the one failure a tree walk has that an allowlist does not, a walk
#: that has stopped descending and is reporting all-clear over nothing.
SCAN_ROOT_FLOORS = (100, 100)

#: Machinery, not prose. Matched on a **directory name** at any depth, so this
#: table cannot grow into a list of individual files.
SCAN_EXCLUDED_DIR_NAMES = (
    (".git", "an object store, not text anyone writes"),
    (".venv", "the installed dependency tree; none of it is this project's prose"),
    ("__pycache__", "compiled bytecode"),
    (".pytest_cache", "run state"),
    (".ruff_cache", "run state"),
    (".hypothesis", "run state"),
    ("node_modules", "vendored dependencies"),
    (".shipyard", "walked as its own root, so this stops the data dir being visited twice"),
)

#: Detached mutation-testing worktrees (``.mut-T108`` and friends) are a
#: second copy of the tree at some earlier commit, so every hit in one is a
#: duplicate of a hit already counted -- or a historical one.
SCAN_EXCLUDED_DIR_PREFIX = ".mut-"

#: Historical record. ``sweep-the-claim-not-the-diff`` step 3 says a completed
#: task file, a raw transcript and a dated verdict legitimately quote a
#: withdrawn phrasing *as the thing that was withdrawn*, and are to be left
#: alone. Each row is ``(root index, a directory prefix or a file name, why)``.
#:
#: **Two of the three rows are directory prefixes and one is a single named
#: file, and that is the honest description** (corrected 2026-09-15, T122:
#: this note used to say "a pattern, never an individual file", which the row
#: spec beside it and row 0 both contradict). A directory prefix holds out a
#: *class* of documents -- every task file, every verdict -- and grows only
#: when the project grows a new class. ``CHANGELOG.md`` holds out one file by
#: name, and what keeps the table from becoming an allowlist is therefore not
#: its shape but
#: ``test_every_historical_record_exclusion_still_shelters_a_withdrawn_phrasing``
#: plus the rule that a row must name a document class that is *by its nature*
#: a dated record. A second named file would be the fence pattern returning,
#: and should be refused on that ground rather than on the shape of the entry.
#:
#: **``CHANGELOG.md`` is the row where that rule is weakest, and the blind
#: spot is stated rather than argued away.** Its retraction entries do quote
#: each withdrawn phrasing as the thing being retracted, which is why the row
#: exists. But the CHANGELOG is also where this project writes **new live
#: normative prose** -- T116's entry is ~1,400 words of it and T117's was
#: written into the same list by T120 -- and nothing inside the file
#: distinguishes the live text from the historical: no section marker, no
#: per-entry convention, nothing the walk could key on. So a withdrawn
#: phrasing re-emitted in a *new* CHANGELOG entry is invisible to this scan,
#: by construction and not by accident. Nothing distinguishes them today; the
#: cheapest thing that would is a convention that quarantines quoted
#: retractions into a marked block, and that is a change to how the CHANGELOG
#: is written rather than to this table.
#:
#: What keeps it from growing is
#: ``test_every_historical_record_exclusion_still_shelters_a_withdrawn_phrasing``:
#: a row that shelters nothing is dead weight and must be deleted rather than
#: kept in case it is needed. ``.subagent-returns/`` needs no row -- those
#: transcripts are ``.txt`` and were never in ``SCAN_SUFFIXES``.
SCAN_EXCLUDED_HISTORY = (
    (0, "CHANGELOG.md", "its retraction entries quote each phrasing as the thing being retracted"),
    (1, "spec/tasks/", "a task file is the dated record of one task's decision, the withdrawal included"),
    (1, "verify/", "a review verdict quotes the phrasing it found, at the date it found it"),
)

#: The one exclusion that is not history. ``withdrawn_phrasings.py`` holds the
#: literals the scan searches for, so reading it would match every one of them
#: by construction. It is kept honest by
#: ``test_the_unscanned_declaration_module_stays_a_declaration_table`` rather
#: than by trust.
SCAN_EXCLUDED_DECLARATIONS = (
    0,
    "runcoach-api/tests/support/withdrawn_phrasings.py",
    "it declares the phrasings the scan searches for; every literal in it is a declaration",
)


def _history_exclusion(root_index: int, rel: str) -> str | None:
    """The ``SCAN_EXCLUDED_HISTORY`` pattern that holds ``rel`` out, or None."""
    for index, pattern, _reason in SCAN_EXCLUDED_HISTORY:
        if index == root_index and (rel == pattern or rel.startswith(pattern)):
            return pattern
    return None


@cache
def _candidate_files(root_index: int) -> tuple[Path, ...]:
    """Every ``SCAN_SUFFIXES`` file under a root, before the path exclusions.

    Directory pruning happens here (``os.walk`` rather than ``rglob``) because
    ``.venv`` alone holds tens of thousands of ``.py`` files, and a walk that
    descends into it and filters afterwards is the difference between a scan
    that runs in every suite run and one that is quietly disabled for being
    slow.
    """
    root = SCAN_ROOTS[root_index]
    pruned = {name for name, _reason in SCAN_EXCLUDED_DIR_NAMES}
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in pruned and not d.startswith(SCAN_EXCLUDED_DIR_PREFIX)
        ]
        for name in filenames:
            if Path(name).suffix.lower() in SCAN_SUFFIXES:
                found.append(Path(dirpath) / name)
    return tuple(sorted(found))


@cache
def _scanned_files(root_index: int) -> tuple[Path, ...]:
    """The files the scan actually reads under one root."""
    root = SCAN_ROOTS[root_index]
    declaring_root, declaring_rel, _why = SCAN_EXCLUDED_DECLARATIONS
    kept: list[Path] = []
    for path in _candidate_files(root_index):
        rel = path.relative_to(root).as_posix()
        if _history_exclusion(root_index, rel) is not None:
            continue
        if root_index == declaring_root and rel == declaring_rel:
            continue
        kept.append(path)
    return tuple(kept)


def _walked_roots() -> tuple[int, ...]:
    """The indices of the roots present on this machine."""
    return tuple(i for i, root in enumerate(SCAN_ROOTS) if root.exists())


def _all_scanned_files() -> tuple[Path, ...]:
    return tuple(path for i in _walked_roots() for path in _scanned_files(i))


#: The positive controls, one per file whose live prose this feature's
#: corrections had to reach. These are no longer the scan's *input* -- the
#: walk decides that -- they are its evidence that it read something. Each row
#: is ``(path, a live phrase that file must contain, is it committed)``.
#:
#: A negative scan over an empty read is green, which is the shape T113 (gap
#: G-C7-6) found and the shape a tree walk reproduces one level out if the
#: walk silently matches nothing. What an anchor has to be is asserted rather
#: than described:
#: ``test_the_withdrawn_reset_reason_phrasings_are_gone_from_every_live_copy``
#: fails if an anchor is absent from its file, if it occurs more than once
#: there, or if it starts before the 98% mark of that file's flattened text
#: (T115, gap G-C7-17). Measured 2026-09-15 on the layout of that date, the
#: seven anchors start past 99% of their files and each occurs exactly once --
#: that is a measurement of one layout on one date and not an invariant, which
#: is why the two assertions exist. The invariant is the 98% floor with the
#: uniqueness, and what trips it is a second occurrence of an anchor phrase
#: earlier in its file, or a truncation dropping the tail past an anchor. An
#: edit confined to the last two percent after an anchor is not something
#: these controls can see.
#:
#: The last two rows are ``False`` for *committed*: they live under the
#: gitignored breadcrumb and are absent from a fresh checkout, where their
#: cases skip loudly. Every other row is required to exist and to be reached.
#:
#: The table itself is declared in ``withdrawn_phrasings.py`` with the other
#: literals, and for the same reason: an anchor is a verbatim copy of a live
#: phrase, and one of the seven is a phrase in *this* file -- written here it
#: would be a second occurrence of that anchor and the uniqueness assertion
#: would fail on it (T119). The reasons stay here, where the walk reads them.
#: Re-anchored for ``test_hrv_trend_reset.py`` on 2026-09-17 (T138): that module
#: gained a tier-change walk section after its old anchor ("nothing else separates
#: the two runs"), which then sat at 91% of the flattened text and no longer proved
#: the tail was read. An anchor names its file's **last** live line by construction,
#: so appending to a scanned file means re-anchoring it in the same commit.
WITHDRAWN_SCAN_ANCHORS = _DECLARATIONS.WITHDRAWN_SCAN_ANCHORS


def test_the_anchor_table_still_speaks_for_every_file_it_was_built_for() -> None:
    """A dropped row takes its parametrised case with it, so the remaining
    cases still pass and the count in the summary is the only trace. Pinned by
    name (T114): the parametrisation cannot notice its own absence. Under the
    walk a dropped row no longer stops the file being *scanned* -- the walk
    still reaches it -- it stops the file being proved *read*, which is the
    same false all-clear in slower motion."""
    assert [path.name for path, _anchor, _committed in WITHDRAWN_SCAN_ANCHORS] == [
        "hrv_trend.py",
        "schemas.py",
        "openapi.yaml",
        "test_hrv_trend_endpoint.py",
        "test_hrv_trend_reset.py",
        "F005-resting-hrv-trend.md",
        "F005-trend-construction.md",
    ]


def test_the_walk_reads_whole_trees_and_not_an_empty_one() -> None:
    """A walk that matches nothing reports all-clear over nothing.

    That is the allowlist's own failure mode one level out, and it is the
    thing this replacement had to be built not to do: a mistyped suffix, a
    root that stopped resolving, a prune list that swallowed the tree, any of
    them leaves every negative assertion below vacuously true. The floors are
    what make that red."""
    assert SCAN_ROOTS[0].exists(), (
        f"the committed tree is not at {SCAN_ROOTS[0]}: the walk has no root and every "
        f"all-clear below would be a report over nothing"
    )
    # T122: the loop below is over the *floors*, so a third root added without
    # a floor row is walked and never floored, and a truncated floors table
    # silently stops flooring the roots past its end -- the same shape T115
    # closed for WITHDRAWN_ORDER. One root, one floor.
    assert len(SCAN_ROOT_FLOORS) == len(SCAN_ROOTS), (
        f"{len(SCAN_ROOT_FLOORS)} floors against {len(SCAN_ROOTS)} roots: a root with no floor "
        f"row is walked with nothing checking that the walk descended into it"
    )
    for index, floor in enumerate(SCAN_ROOT_FLOORS):
        if index not in _walked_roots():
            # Root 0 is the committed tree and is required above; every other
            # root is machine-local and may be absent. Written as "not the
            # committed root" rather than "== 1" so a third root does not
            # make a legitimately absent breadcrumb fail here (T122).
            assert index != 0, f"root {SCAN_ROOTS[index]} is the committed tree and must exist"
            continue
        count = len(_scanned_files(index))
        assert count >= floor, (
            f"{SCAN_ROOTS[index]} yielded {count} files, under the floor of {floor}: the walk "
            f"has stopped descending, so every phrasing assertion over it is vacuous"
        )


def test_the_walk_reaches_every_file_an_anchor_speaks_for() -> None:
    """The anchors prove their files were *read*; this proves the walk still
    *reaches* them. Without it an exclusion pattern widened by one character
    could drop a live file out of the walk while its anchor case went on
    passing -- the anchor test opens the file directly.

    The committed rows fail rather than skip when absent, which is what turns
    "in a checkout without the breadcrumb the walk is the committed tree
    alone" from a sentence into a checked claim: five of the seven anchored
    files are reached on every machine, and the assertion below is what says
    so."""
    reached = set(_all_scanned_files())
    for path, _anchor, committed in WITHDRAWN_SCAN_ANCHORS:
        if not committed and not path.exists():
            continue
        assert path.exists(), f"{path} is committed and must be in every checkout"
        assert path in reached, (
            f"{path} is not in the walk: an exclusion pattern, a pruned directory or a "
            f"suffix has taken a file with live prose out of the scan"
        )


def test_every_historical_record_exclusion_still_shelters_a_withdrawn_phrasing() -> None:
    """The exclusion table is the new allowlist if it is allowed to grow.

    Every historical-record row must currently hold out at least one real file
    that really does carry a withdrawn phrasing. A row that shelters nothing
    is not protecting history, it is widening the blind spot on the chance
    that it might one day be needed -- delete it and add it back when a hit
    appears. This is the assertion that keeps the table a short list of
    patterns rather than the seven-path allowlist in a new shape.

    A row under an absent root is skipped, not failed: its evidence is not on
    this machine to look at.

    What this cannot check is *scope within* a sheltered file: the
    ``CHANGELOG.md`` row shelters real retraction quotes and, with them, every
    live entry in the same file (T122, stated in the table's own note)."""
    withdrawn = WITHDRAWN_SWEPT
    checked = 0
    for root_index, pattern, reason in SCAN_EXCLUDED_HISTORY:
        if root_index not in _walked_roots():
            continue
        checked += 1
        root = SCAN_ROOTS[root_index]
        sheltered = [
            path
            for path in _candidate_files(root_index)
            if _history_exclusion(root_index, path.relative_to(root).as_posix()) == pattern
            and any(_flat(phrase) in _scannable(path) for phrase in withdrawn)
        ]
        assert sheltered, (
            f"the exclusion {pattern!r} ({reason}) shelters no file carrying a withdrawn "
            f"phrasing: it is widening the blind spot for nothing and should be deleted"
        )
    assert checked, "no exclusion row was checkable: both roots' history is out of reach"


def test_the_unscanned_declaration_module_stays_a_declaration_table() -> None:
    """The declaration module is the only file held out for a non-historical
    reason, so it is the only place prose could sit unread -- which is exactly
    what T115 was closing when it moved seventy lines of gloss out of it, and
    gloss is what has twice on this feature drifted into a literal copy of a
    withdrawn phrasing.

    Until now, that the module stays a bare declaration table was a sentence
    nothing enforced, which is cycle 7's one repeated defect shape. This is
    the assertion: imports, assignments and a single-line docstring are all it
    may hold, and it may hold no comments at all."""
    root_index, rel, _reason = SCAN_EXCLUDED_DECLARATIONS
    path = SCAN_ROOTS[root_index] / rel
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        docstring = isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        assert docstring or isinstance(node, ast.Import | ast.ImportFrom | ast.Assign | ast.AnnAssign), (
            f"{rel} line {node.lineno} is a {type(node).__name__}: this module is held out of "
            f"the scan, so anything in it beyond imports, assignments and its docstring is "
            f"text nothing reads"
        )
    comments = [
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    ]
    assert comments == [], (
        f"{rel} has grown {len(comments)} comment(s): prose in the one unscanned module is "
        f"prose no scan can see -- put it beside the scan instead: {comments[:3]}"
    )
    doc = ast.get_docstring(tree) or ""
    assert "\n" not in doc and len(doc) <= 120, (
        f"{rel}'s docstring runs to {len(doc)} characters: it is the one piece of prose in an "
        f"unscanned file and stays one short line"
    )


def _scannable(path: Path) -> str:
    """The file's whole text, whitespace-flattened. Nothing is excised.

    Flattening is the whole point: ``hrv_trend.py`` wrapped a retracted
    sentence across a line break and a line-oriented ``grep`` for it returned
    nothing, which is exactly the false all-clear
    ``sweep-the-claim-not-the-diff`` warns about.

    T114 (gap G-C7-10) removed the excision this function used to do. Two
    generations of fence lived here -- a marker pair whose literals had to be
    assembled rather than written, because a literal copy of a marker is
    another marker and pairs off with a real one. T113 replaced a
    ``if begin in text`` form that silently dropped everything to EOF with a
    balance-asserting loop, and split one fenced region into two in the same
    commit; deleting the *first* of the two end markers then left the loop
    balanced, ~12 lines of live commentary unscanned and the suite green.
    Guarding a fence is what failed, twice. There is no fence now, and that no
    file the walk reaches carries a withdrawn phrasing is not a claim made
    here but the assertion below, run over every file the walk reaches.
    """
    return _flat(path.read_text(encoding="utf-8"))


def test_the_withdrawn_phrasings_are_gone_from_every_file_the_walk_reaches() -> None:
    """T111 (review cycle 7, gap G-C7-1) read ``RESET_REASON_WITHDRAWN``
    against the two description strings only, so the four sites that actually
    carried the withdrawn equivalence -- a module docstring, a test docstring
    and two spec documents -- were invisible to it by construction. T111's fix
    named those files; T119's stops naming files at all, because an allowlist
    passes as soon as the sentence moves file.

    Every offender is collected before the assertion so one run names all of
    them: a phrasing that has come back has usually come back in more than one
    place, and reporting the first hit turns one sweep into several."""
    offenders = [
        f"{path}: {phrase}"
        for path in _all_scanned_files()
        for phrase in WITHDRAWN_SWEPT
        if _flat(phrase) in _scannable(path)
    ]
    assert not offenders, "withdrawn as false (T103/T105/T109/T116), back in: " + "; ".join(offenders)


@pytest.mark.parametrize(
    ("path", "anchor", "committed"),
    WITHDRAWN_SCAN_ANCHORS,
    ids=[path.name for path, _anchor, _committed in WITHDRAWN_SCAN_ANCHORS],
)
def test_the_withdrawn_reset_reason_phrasings_are_gone_from_every_live_copy(
    path: Path, anchor: str, committed: bool
) -> None:
    """The positive half of the scan, one case per file whose live prose this
    feature's corrections had to reach: the anchor says this file's text was
    read to its tail, so the negative sweep above is a report over something
    rather than over an empty string. The phrasing check is repeated here
    because it reports per file, and because it is what fails first if a
    correction is reverted in one of the seven files that have carried one."""
    if not path.exists():
        assert not committed, f"{path} is committed and must be in every checkout"
        pytest.skip(f"{path} is absent (the .shipyard breadcrumb is machine-local and gitignored)")
    text = _scannable(path)
    flat_anchor = _flat(anchor)
    assert flat_anchor in text, (
        f"{path.name}: the scan did not read its live anchor, so every all-clear below "
        f"would be a report over text nothing read: {anchor}"
    )
    assert text.count(flat_anchor) == 1, (
        f"{path.name}: the anchor occurs {text.count(flat_anchor)} times, so a truncation "
        f"dropping the tail past the last one still finds an earlier copy: {anchor}"
    )
    assert text.find(flat_anchor) / len(text) > 0.98, (
        f"{path.name}: the anchor starts at "
        f"{text.find(flat_anchor) / len(text):.1%} of the flattened text, not past 98%, so "
        f"a truncation of the tail can drop live prose and still leave the anchor: {anchor}"
    )
    for withdrawn in WITHDRAWN_SWEPT:
        assert _flat(withdrawn) not in text, (
            f"withdrawn as false (T103/T105/T109/T116), back in {path.name}: {withdrawn}"
        )


#: Where the two copies must agree word for word. The served description is
#: the longer one (it also carries the lifetimes and "a timezone change is
#: never a reset"), so equality is not available; what T103 and T105 both
#: required is that this shared run be *the same words at both sites*, and
#: that is what is asserted.
RESET_REASON_SHARED_ANCHOR = "baseline clip is decided by the era boundary alone"


#: Quote characters are removed, not turned into spaces: an implicitly
#: concatenated Python literal splits either inside a word or between two
#: words that already carry their space, and in both cases the quotes are the
#: only thing standing between them.
_QUOTE_CHARS = str.maketrans("", "", "\"'")

#: Typography, folded to the ASCII the same claim is written in on the other
#: side (review cycle 8, ``acfebae``). ``contracts/openapi.yaml`` is prose in a
#: YAML document and spells its operators and dashes typographically; ``schemas.py``
#: is Python source and spells them in ASCII. ``Baseline.established`` publishes
#: the same four claims in both copies and differs **only** here -- ``n >=``
#: against ``n >=``, ``--`` against ``--`` -- so without this fold the
#: shared-run assertion below compares two spellings of one sentence and fails
#: on the typeface. This is the same move T113 made for quote characters, and
#: it is preferred over re-typing one copy into the other's conventions, which
#: would be a cosmetic edit to a published contract. No declared phrasing
#: contains any of these characters, so no digest in ``WITHDRAWN_ORDER`` and no
#: sweep result moves with it.
_TYPOGRAPHY = {"≥": ">=", "≤": "<=", "—": "--", "–": "--", "−": "-"}


def _flat(text: str) -> str:
    """Folded YAML and an implicitly concatenated Python literal wrap at
    different columns, so compare on whitespace-normalised lowercase with
    quote characters removed.

    Quote removal is T113, gap G-C7-8: ``schemas.py`` is built entirely of
    implicitly concatenated literals, and a sentence split at a literal
    boundary flattens to ``... is no longer " "sustained by ...`` -- the
    quotes still sitting between the words, so whitespace normalisation
    alone left the substring check blind to exactly the file whose wrapping
    is least like prose. ``RESET_REASON_WITHDRAWN`` is separately checked
    against the *rendered* description, which closed that hole for those five
    fragments; ``RESET_REASON_WITHDRAWN_IDIOMS`` is read by the file scan
    alone and had no such cover.
    """
    folded = text.translate(_QUOTE_CHARS)
    for symbol, ascii_form in _TYPOGRAPHY.items():
        folded = folded.replace(symbol, ascii_form)
    return " ".join(folded.split()).lower()


def test_the_two_copies_of_the_reset_reason_contract_publish_the_same_claims() -> None:
    """G-C6-2 (review cycle 6). ``contracts/openapi.yaml`` and
    ``schemas.Baseline.reset_reason`` publish the same contract to two
    audiences and were kept in step by hand. ``check_drift.py`` compares
    path, method, 2xx presence, multipart properties and required query
    parameters -- never description prose -- so a false sentence in either
    copy, or a correction landed in only one, passes every gate in the
    tree. T103 published a false universal here and T105 replaced it with
    a second one; each was caught by a ``! grep -q`` inside its own
    acceptance probe, which by construction never runs again.

    This is ``PUBLISHED_REASONS``' pattern applied to prose that is not an
    enum: pin the **claim**, not the paragraph. ``RESET_REASON_CLAIMS``
    lists the six sentences a client could act on, each as the shortest
    fragment that carries it, so a legitimate rewording of the surrounding
    text stays green while dropping a claim from either copy goes red.
    ``RESET_REASON_WITHDRAWN`` holds the **five** phrasings retracted as
    false -- T103's universal, T105's replacement in its two spellings, and
    T109's two spellings of the (b)-alone equivalence (T114, G-C7-15: this
    line said "the two" from T103's day and was never updated as T105 and
    T109 appended to the tuple).
    ``RESET_REASON_SHARED_ANCHOR`` starts the run the two sites are
    required to state identically, and the run is compared across them, so
    a correction applied to one copy alone fails here.

    Authorship, per ``contract-tables-need-an-independent-oracle`` -- and
    the thing this pin got wrong (T109, review cycle 6 G-C6-6). The
    fragments were originally transcribed from the contract prose in the
    *same pass* that wrote it, so entry 5 inherited that pass's false
    equivalence and this test then held it in place: removing the false
    clause went red, and the check built to stop false prose recurring had
    become the reason it could not be withdrawn. An oracle that transcribes
    prose inherits whatever is wrong with the prose and promotes it to an
    enforced invariant. The pattern is right; the authorship was not, which
    is G-C5-7's no-independent-oracle shape again. The entries are
    constrained by ``research/00`` §5.4 and F005 rather than by the sentence
    they pin, and each was reproduced against ``build_series`` -- the runs
    are listed above ``RESET_REASON_CLAIMS`` (T109; the stated authorship
    corrected by T112, which found it claiming the entries were *derived
    from* the implementation they exist to constrain).

    What this check is and is not. It constrains two independently edited
    artifacts jointly and encodes the **negative** claims, so a false
    sentence cannot return to either copy quietly. It remains a check on
    *words*: no assertion here can fail because ``build_series`` changed.
    The behavioural half of entry 5 is
    ``test_hrv_trend_reset.test_clause_a_lapsing_nulls_the_report_while_
    clause_b_and_the_week_half_still_hold`` (T112, G-C7-2) -- verified red
    against a ``build_series`` that reports ``tier_change`` on a lapsed
    clause (a), while every assertion in this test stayed green.
    """
    target = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    contract = _flat(
        target["components"]["schemas"]["HrvTrend"]["properties"]["baseline"]["properties"][
            "reset_reason"
        ]["description"]
    )
    served = _flat(app.openapi()["components"]["schemas"]["Baseline"]["properties"]["reset_reason"]["description"])

    for claim in RESET_REASON_CLAIMS:
        flat = _flat(claim)
        assert flat in contract, f"contracts/openapi.yaml no longer publishes: {claim}"
        assert flat in served, f"schemas.Baseline.reset_reason no longer publishes: {claim}"
    for withdrawn in RESET_REASON_WITHDRAWN:
        assert _flat(withdrawn) not in contract, f"withdrawn as false, back in the contract: {withdrawn}"
        assert _flat(withdrawn) not in served, f"withdrawn as false, back in the schema: {withdrawn}"

    start = contract.find(_flat(RESET_REASON_SHARED_ANCHOR))
    assert start != -1, "the contract's shared run no longer starts where the anchor says"
    shared = contract[start:]
    assert shared in served, (
        "the two copies have stopped stating the shared run in the same words; "
        "T103 and T105 both required them to. The contract says: " + shared
    )


#: The same treatment for ``baseline.window``, added by T107 (review cycle
#: 6). Until then the two copies of the *window* contract were compared by
#: nothing -- the pin above reads ``reset_reason`` only -- and they had in
#: fact drifted: ``schemas.Baseline.window`` carried the identity now declared
#: as ``WINDOW_WITHDRAWN``'s entry 1, which stated ``R`` and a
#: ``coverage_gap``'s ``reset_on`` to be the same day. T107 falsified it (the
#: era boundary can clip later than the resumption, so ``window[0]`` may lie
#: after a ``coverage_gap``'s ``reset_on``), while ``contracts/openapi.yaml``
#: said nothing about ``coverage_gap`` at all. One copy false, the other
#: silent, both green.
WINDOW_CLAIMS = (
    "r is not reset_on",
    "a tier-change era boundary clips this window whether or not the change is reported",
    "the two clips compose as the later of their first days",
    "may lie after reset_on",
    "clients must not assume window[0]",
)

#: Withdrawn by T107 as false, in each copy's own idiom (the schema copy
#: marks up identifiers, the YAML copy does not), so the claim cannot come
#: back at either site in the spelling that site would use.
#:
#: Review cycle 8 (``acfebae``) moved this tuple into ``withdrawn_phrasings.py``
#: and into ``WITHDRAWN_SWEPT``. Until then it was a live **fourth** withdrawn
#: tuple declared in this module and read against the two ``window``
#: description copies alone -- verbatim the scope gap T122 closed for
#: ``VERDICT_WITHDRAWN`` one screen below, sitting one screen above the guard
#: T122 added to make a fourth tuple impossible, which enumerates
#: ``vars(_DECLARATIONS)`` and so could not see a tuple declared here.
#:
#: Both entries are **disambiguated the way ``VERDICT_WITHDRAWN`` entry 1 was**
#: and for the same reason: swept over all 265 walked files the bare fragment
#: matches a review document that quotes it *as the phrasing it found*. Each
#: entry therefore carries the clause it followed in the copy it was withdrawn
#: from -- ``... before_reset: tier_change``) -- which both live copies still
#: state, so a restoration of the false sentence in its own position matches,
#: while a quotation of the fragment alone does not.
WINDOW_WITHDRAWN = _DECLARATIONS.WINDOW_WITHDRAWN


def test_the_two_copies_of_the_window_contract_publish_the_same_claims() -> None:
    """T107 (review cycle 6), the ``reset_reason`` pin above applied to
    ``baseline.window``, whose two copies nothing compared.

    The gap it closes is the one that let T107's sixth site survive a
    claim-sweep: ``schemas.Baseline.window`` published ``WINDOW_WITHDRAWN``'s
    entry 1 -- the same claim as "a `coverage_gap`'s ``reset_on`` never
    precedes ``window[0]``", stated as an identity on ``window[0]``
    rather than as a "never", and false in exactly the case T107 creates
    (``test_the_era_clip_does_not_replace_the_gaps_when_the_gap_is_later``
    publishes ``reset_on`` 2026-08-15 under a window opening 2026-08-15
    while the era boundary sits at 2026-06-25; on T107's other pin the
    reported ``reset_on`` 2026-07-29 precedes ``window[0]`` 2026-08-13).
    It was served to clients through ``app.openapi()`` and contradicted
    ``Baseline.reset_on`` three fields below. A phrase-shaped sweep could
    not see it; a claim-shaped pin can, and ``WINDOW_WITHDRAWN`` is that
    pin.

    *If the correction had been applied to one copy only* -- which is what
    happened to the claim itself, and what ``check_drift.py`` cannot see,
    comparing paths and parameters but never prose -- the shared-claim
    loop goes red on whichever copy dropped it.
    """
    target = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    contract = _flat(
        target["components"]["schemas"]["HrvTrend"]["properties"]["baseline"]["properties"]["window"][
            "description"
        ]
    )
    served = _flat(app.openapi()["components"]["schemas"]["Baseline"]["properties"]["window"]["description"])

    for claim in WINDOW_CLAIMS:
        flat = _flat(claim)
        assert flat in contract, f"contracts/openapi.yaml no longer publishes: {claim}"
        assert flat in served, f"schemas.Baseline.window no longer publishes: {claim}"
    for withdrawn in WINDOW_WITHDRAWN:
        assert _flat(withdrawn) not in contract, f"withdrawn as false, back in the contract: {withdrawn}"
        assert _flat(withdrawn) not in served, f"withdrawn as false, back in the schema: {withdrawn}"


#: The same treatment for ``verdict``, added by T116 (review cycle 7,
#: [[IDEA-062]]). Until T116 the two copies published an **asymmetric** rule:
#: the suppression was withheld on an unestablished baseline and the
#: ``hrv_normal`` was not, so a client reading either copy was told that
#: ``hrv_normal`` carries no claim about ``baseline.established`` -- which was
#: true, and was the defect. The claims below are the symmetric rule, and
#: ``VERDICT_WITHDRAWN`` holds the asymmetric one in each copy's own idiom so
#: it cannot come back at either site quietly.
#:
#: Authorship (``contract-tables-need-an-independent-oracle``, and T109's
#: correction of this pattern): these fragments are **not** transcriptions of
#: the paragraph. They are the four things a client can act on, taken from the
#: user decision of 2026-09-15 and from ``research/00`` §1.7, and each is
#: reproduced against ``judge`` by a named behavioural pin rather than by this
#: file: entry 1 and entry 3 by
#: ``test_hrv_trend_band.test_a_thin_baseline_inside_the_band_is_unavailable_not_normal``
#: and ``..._above_the_band_is_unavailable_too``, entry 2 by
#: ``test_hrv_trend_band.test_the_establishment_gate_flips_normal_at_exactly_fourteen_readings``,
#: entry 4 by the same tests' ``result.band is not None`` assertions. This
#: test constrains the **words**; nothing here fails because ``judge``
#: changed, which is the division T112 named.
VERDICT_CLAIMS = (
    "hrv_normal and hrv_suppressed both assert an established baseline",
    "inside or above the band is hrv_normal",
    "any week judged against an unestablished baseline (below, inside or above the band alike",
    "the band is still reported whenever the baseline can build one, established or not",
)

#: Withdrawn by T116 as the asymmetry itself, in each copy's own spelling
#: (the YAML copy parenthesised the section number, the schema copy did not).
#:
#: T122 (review cycle 7) moved this tuple into ``withdrawn_phrasings.py`` and
#: into the walk. Until then it was read against the **two contract copies
#: only**, while ``RESET_REASON_WITHDRAWN`` was read against all 265 walked
#: files -- so a restored asymmetric sentence in ``research/00`` §5.4, spec
#: §3.7.3, the construction reference or any docstring was invisible to it *by
#: construction*, which is precisely the gap T111 found for ``reset_reason``
#: and T119 then closed by replacing the allowlist with a walk. Measured
#: before the move: no live copy existed anywhere in the 265, so this is a
#: scope gap and not an escaped phrasing.
#:
#: Entry 1 is **not** the bare fragment the contract carried. "A below-band
#: week on an unestablished baseline" is also the still-true title of
#: [[IDEA-043]]/T084 and appears in a live docstring in this file describing
#: what T116 withdrew, so swept over 265 files it reddens on true text. What
#: T116 withdrew is the **enumeration** -- unavailable listed that cell and
#: not its two neighbours -- so entry 1 carries the neighbour it was listed
#: beside, which no true sentence pairs it with.
VERDICT_WITHDRAWN = _DECLARATIONS.VERDICT_WITHDRAWN

#: The same treatment for ``baseline.established``, added by review cycle 8
#: (``acfebae``). T120 rewrote this description in both copies -- from the
#: asymmetric gloss now declared as ``ESTABLISHED_WITHDRAWN``'s entry 1, which
#: named the suppression alone, to the symmetric rule
#: T116 implemented -- and added **no test**, which is the one thing review
#: cycle 8 iteration 1's must-fix on this field asked for. Measured at source
#: before this tuple was written: no ``ESTABLISHED_*`` name existed anywhere in
#: the tree, ``check_drift.py`` compares path, method, 2xx presence and required
#: parameters and never description prose, and the retracted sentence was in no
#: withdrawn tuple -- so every failure mode that must-fix named (the correction
#: landed in one copy only; the pre-T116 sentence restored in either) passed
#: every gate in the tree.
#:
#: Authorship (``contract-tables-need-an-independent-oracle``): these are not
#: transcriptions of the paragraph. They are the three things a client reading
#: ``established`` can act on -- what the flag *is*, that **both** verdicts and
#: not just the suppression are withheld beneath it, and what is emitted
#: instead -- taken from the 2026-09-15 user decision and ``research/00`` §1.7,
#: the same source ``VERDICT_CLAIMS`` is constrained by. Each is reproduced
#: against ``judge`` by a named behavioural pin rather than by this file:
#: ``test_hrv_trend_band.test_the_establishment_gate_flips_normal_at_exactly_fourteen_readings``
#: for the 14-reading boundary and for ``hrv_normal`` being withheld, and
#: ``test_hrv_trend_band.test_a_thin_baseline_inside_the_band_is_unavailable_not_normal``
#: for ``hrv_unavailable`` being what is emitted in its place.
ESTABLISHED_CLAIMS = (
    "n >= min_baseline_readings",
    "below it both verdicts are withheld",
    "hrv_suppressed and hrv_normal alike",
    "hrv_unavailable is the only verdict emitted",
)

#: Withdrawn by T116 and retracted from both copies by T120: the asymmetric
#: gloss that named the suppression alone. **One** spelling, not two as the
#: other pairs need, because the two copies carried that sentence identically
#: -- they differed only in how they spelled the comparison in the clause
#: before it. Read back from ``f0ce19f^`` rather than from recollection; the
#: sentence itself is not quoted here, this file being walked (see the note
#: above ``RESET_REASON_CLAIMS``).
ESTABLISHED_WITHDRAWN = _DECLARATIONS.ESTABLISHED_WITHDRAWN

#: The run the two copies must state identically, from this anchor to the end.
#: It is what makes a correction landed in one copy alone red, and it is only
#: comparable at all because ``_flat`` folds the typography the two copies
#: differ in -- see ``_TYPOGRAPHY``.
ESTABLISHED_SHARED_ANCHOR = "below it both verdicts are withheld"


#: The same treatment for ``thresholds``, added by review cycle 8 iteration 3
#: ([[IDEA-070]], user decision 2026-09-15). Both copies described the block as
#: "the heuristic constants the verdict was computed with" while
#: ``RECENCY_TOLERANCE_DAYS`` -- which decides whose band the verdict is
#: computed against -- is neither in the block nor anywhere in the response.
#: The decision narrowed the description rather than adding a seventh key, so
#: the retracted promise is a two-copy claim like the four above and is pinned
#: the same way.
#:
#: Iteration 3's narrowed description was itself false and this tuple was four
#: contiguous fragments of it, so the false sentence was pinned in place (review
#: cycle 8 iteration 4, R2). It claimed tier resolution was "not in this block":
#: ``resolve_baseline_tier`` applies ``MIN_BASELINE_READINGS`` and
#: ``MIN_WINDOW_READINGS`` in its own body, and is handed counts taken over the
#: ``BASELINE_DAYS`` window after the ``GAP_RESET_DAYS`` clip -- four of the six
#: keys. The tuple was re-derived from the code rather than reworded.
#:
#: Authorship (``contract-tables-need-an-independent-oracle``): the claims are
#: not a transcription of the sentence, and the thing that makes that true is
#: ``test_the_thresholds_description_names_the_tier_constant_the_block_omits``
#: below, whose oracle is the tier rule's own source -- ``resolve_baseline_tier``
#: and the module-level helpers it calls, since T125 factored ``_recency_struck``
#: out of it: it reads the constants that rule applies, subtracts the published
#: keys, and requires the remainder to be named in both descriptions. So claim 2 is re-derivable
#: from the tree and reddens on a tier rule that starts applying a second
#: unpublished constant, which no reading of the sentence could do. Claim 3 is
#: reproduced against ``build_series`` by
#: ``test_hrv_trend_series.test_stale_candidacy_the_july_trial_no_longer_owns_the_week_on_the_july_band``
#: -- the six alone make the July strap a candidate that covers the week, and
#: the reported tier is the snapshot. The block's six keys and their values are
#: pinned by ``test_the_endpoint_reports_every_input_that_produced_the_verdict``
#: above and ``test_hrv_trend_band.test_the_thresholds_the_response_echoes_are_the_constants_the_verdict_uses``.
THRESHOLDS_CLAIMS = (
    "the band, verdict and tier-resolution constants",
    "recency_tolerance_days, which this response does not echo",
    "derive a baseline.tier that disagrees with the reported one",
)

#: Retracted by [[IDEA-070]] in each copy's own spelling, and **positional**:
#: the bare promise is quoted as the thing being retracted by IDEA-070 itself,
#: by this cycle's review documents and by the review cursor, all of which the
#: walk reads, so each entry carries the citation that followed it in the copy
#: it was withdrawn from -- ``(spec 03`` in the schema, ``(spec/03`` in the
#: YAML. See ``POSITIONAL_WITHDRAWN``.
THRESHOLDS_WITHDRAWN = _DECLARATIONS.THRESHOLDS_WITHDRAWN

#: The run the two copies must state identically, from this anchor to the end.
#: They differ only in the citation that ends the first sentence, so the anchor
#: is the correction itself.
THRESHOLDS_SHARED_ANCHOR = "they are not all of tier resolution"


#: Every withdrawn phrasing the walk reads, in one name so a declared tuple
#: cannot be left out of the sweep -- which is the shape ``VERDICT_WITHDRAWN``
#: was in until T122, ``WINDOW_WITHDRAWN`` until ``acfebae``, and the same shape T115
#: closed for ``WITHDRAWN_ORDER``. ``test_every_declared_withdrawn_tuple_is_swept``
#: is what holds it: it reads the declaration module's own namespace rather
#: than this line, so the guard fires on a tuple nobody remembered -- which is
#: what it did for ``ESTABLISHED_WITHDRAWN`` and ``WINDOW_WITHDRAWN`` when
#: ``acfebae`` declared them and left this line alone.
WITHDRAWN_SWEPT = (
    RESET_REASON_WITHDRAWN
    + RESET_REASON_WITHDRAWN_IDIOMS
    + VERDICT_WITHDRAWN
    + WINDOW_WITHDRAWN
    + ESTABLISHED_WITHDRAWN
    + THRESHOLDS_WITHDRAWN
)

#: Being in ``WITHDRAWN_SWEPT`` is not the same as reaching the whole walk.
#: These tuples' entries each carry the clause that followed them in the copy
#: they were withdrawn from, because the bare fragment is also quoted -- as the
#: thing being retracted -- by documents the walk reads. So they match a
#: restoration *in place* and nothing else: their reach is the two contract
#: copies, not the walked tree. ``WINDOW_WITHDRAWN``'s entries also carry the
#: pre-T107 comma-less spelling, and what that buys is narrower than "a revert
#: is caught" (measured 2026-09-15 by flattening each revision): entry 1 fires
#: on a byte-exact revert of ``schemas.py`` to ``7103fd7^``; entry 2 fires on
#: nothing there, the YAML copy never having carried the claim at all (see that
#: tuple's own note); and both live copies now write the comma, so a revert made
#: by editing today's sentence in place matches neither.
POSITIONAL_WITHDRAWN = {
    "WINDOW_WITHDRAWN": ("tier_change`).", "tier_change)."),
    "THRESHOLDS_WITHDRAWN": ("(spec 03", "(spec/03"),
}


def test_the_positional_withdrawn_entries_are_the_ones_that_declare_it() -> None:
    """Review cycle 8, S-B: ``WINDOW_WITHDRAWN`` was moved into the walk and
    gained no reach by it, because both of its entries carry a disambiguating
    clause that only the two contract copies contain. That is a property of the
    entries, so it is asserted on them rather than written above them.

    Both directions matter. An entry in a named tuple that has lost its clause
    is a phrasing that now sweeps the whole tree and will redden on the
    documents that quote it; an entry outside the table that has acquired one
    is a tuple whose reach has silently narrowed to one sentence's position --
    which is the state ``WINDOW_WITHDRAWN`` was in, unstated, from T107 until
    now."""
    declared = _declared_phrasing_tuples()
    markers = tuple(marker for group in POSITIONAL_WITHDRAWN.values() for marker in group)
    for name, expected in POSITIONAL_WITHDRAWN.items():
        assert name in declared, f"{name} is in POSITIONAL_WITHDRAWN but is not a declared tuple"
        for entry in declared[name]:
            assert any(marker in entry for marker in expected), (
                f"{name} entry {entry!r} no longer carries the clause that scopes it to the copy "
                f"it was withdrawn from: swept bare it reddens on every document quoting it"
            )
    for name, value in declared.items():
        if name in POSITIONAL_WITHDRAWN:
            continue
        for entry in value:
            assert not any(marker in entry for marker in markers), (
                f"{name} entry {entry!r} carries a positional clause while {name} is not in "
                f"POSITIONAL_WITHDRAWN: its reach is one sentence's position, not the walk"
            )

#: The run the two copies must state identically, from this anchor to the end.
VERDICT_SHARED_ANCHOR = "hrv_normal and hrv_suppressed both assert an established baseline"


def test_the_two_copies_of_the_verdict_contract_publish_the_same_claims() -> None:
    """T116 (review cycle 7, [[IDEA-062]]), the ``reset_reason`` and
    ``window`` pins above applied to ``verdict`` -- the third pair of
    hand-synchronised description copies in this contract, and the one that
    published a rule ``judge`` has now stopped following.

    What makes it necessary rather than decorative: ``check_drift.py``
    compares path, method, 2xx codes and required query parameters, never
    prose, so had T116 corrected only ``schemas.py`` the contract in
    ``contracts/openapi.yaml`` would have gone on telling clients that a
    below-band week on an unestablished baseline is the *only* thin-baseline
    cell that reads unavailable, and every gate in the tree would have
    stayed green. The shared-run assertion is what makes a one-copy
    correction red; ``VERDICT_WITHDRAWN`` is what makes a *reverted* one red.

    The distinct failure modes this goes red on, each run to confirm it:
    (1) a claim dropped from either copy; (2) the old asymmetric sentence
    restored in either copy; (3) the correction landed in one copy only,
    which the shared run catches even when both copies still carry every
    claim.
    """
    target = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    contract = _flat(target["components"]["schemas"]["HrvTrend"]["properties"]["verdict"]["description"])
    served = _flat(app.openapi()["components"]["schemas"]["HrvTrendResponse"]["properties"]["verdict"]["description"])

    for claim in VERDICT_CLAIMS:
        flat = _flat(claim)
        assert flat in contract, f"contracts/openapi.yaml no longer publishes: {claim}"
        assert flat in served, f"schemas.HrvTrendResponse.verdict no longer publishes: {claim}"
    for withdrawn in VERDICT_WITHDRAWN:
        assert _flat(withdrawn) not in contract, f"withdrawn as false, back in the contract: {withdrawn}"
        assert _flat(withdrawn) not in served, f"withdrawn as false, back in the schema: {withdrawn}"

    start = contract.find(_flat(VERDICT_SHARED_ANCHOR))
    assert start != -1, "the contract's shared run no longer starts where the anchor says"
    shared = contract[start:]
    assert shared in served, (
        "the two copies have stopped stating the verdict rule in the same words. The contract says: " + shared
    )


def test_the_two_copies_of_the_established_contract_publish_the_same_claims() -> None:
    """Review cycle 8 (``acfebae``), the fourth of these pins and the one T120 owed.

    ``baseline.established`` is the flag the whole symmetric rule is keyed on,
    and it is published twice -- in ``contracts/openapi.yaml`` and in
    ``schemas.Baseline.established`` -- and hand-synchronised, like the three
    pairs above. T120 corrected both copies from the asymmetric sentence to the
    symmetric one in a commit that added no assertion, so from T120 until now
    the correction was held in place by nothing: ``check_drift.py`` reads path,
    method, 2xx presence and required parameters and never prose.

    The distinct failure modes, each run to confirm this goes red on it:
    (1) a claim dropped from either copy (the claim loop); (2) the pre-T116
    sentence restored in either copy (the withdrawn loop -- and, since ``acfebae`` put
    ``ESTABLISHED_WITHDRAWN`` into ``WITHDRAWN_SWEPT``, restored in *any* of the
    265 walked files, which is the half the two-copy loop cannot reach);
    (3) the correction landed in one copy only, which the shared run catches
    even where both copies still carry every claim.

    **The two copies are not byte-identical and the review asked which fix was
    chosen.** They differ only in typography -- the YAML spells the comparison
    and the dashes typographically, ``schemas.py`` in ASCII -- and the fold
    happens in ``_flat`` (see ``_TYPOGRAPHY``) rather than by re-typing a
    published contract to match the other's conventions. Retyping would be a
    cosmetic edit to a served description; folding is what ``_flat`` already
    does for wrapping and for quote characters, and it is checked not to move
    any ``WITHDRAWN_ORDER`` digest.
    """
    target = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    contract = _flat(
        target["components"]["schemas"]["HrvTrend"]["properties"]["baseline"]["properties"][
            "established"
        ]["description"]
    )
    served = _flat(app.openapi()["components"]["schemas"]["Baseline"]["properties"]["established"]["description"])

    for claim in ESTABLISHED_CLAIMS:
        flat = _flat(claim)
        assert flat in contract, f"contracts/openapi.yaml no longer publishes: {claim}"
        assert flat in served, f"schemas.Baseline.established no longer publishes: {claim}"
    for withdrawn in ESTABLISHED_WITHDRAWN:
        assert _flat(withdrawn) not in contract, f"withdrawn as false, back in the contract: {withdrawn}"
        assert _flat(withdrawn) not in served, f"withdrawn as false, back in the schema: {withdrawn}"

    start = contract.find(_flat(ESTABLISHED_SHARED_ANCHOR))
    assert start != -1, "the contract's shared run no longer starts where the anchor says"
    shared = contract[start:]
    assert shared in served, (
        "the two copies of the established rule have stopped stating the shared run in the "
        "same words; T120 corrected both and pinned neither. The contract says: " + shared
    )


def test_the_two_copies_of_the_thresholds_contract_publish_the_same_claims() -> None:
    """[[IDEA-070]], user decision 2026-09-15: the fifth of these pins, and the
    one the narrowed promise is shipped with rather than after.

    What the block promised was "the heuristic constants the verdict was
    computed with". ``RECENCY_TOLERANCE_DAYS`` decides which tier's band the
    verdict is computed against, is not one of the six keys, and is echoed
    nowhere else in the response -- so the promise was false from T117 on, at
    both copies, and nothing in the tree could say so: ``check_drift.py`` reads
    path, method, 2xx presence and required parameters, never prose.

    The decision was to narrow the description and not to publish the constant,
    so the six keys and the contract shape are unchanged and the assertions
    here are about the words alone. The failure modes, each run to confirm this
    goes red on it: (1) a claim dropped from either copy; (2) the retracted
    promise back in either copy -- or, through ``WITHDRAWN_SWEPT``, anywhere
    else the walk reads it in the spelling its own copy used; (3) the
    correction landed in one copy only, which the shared run catches while both
    copies still carry every claim.
    """
    target = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    contract = _flat(target["components"]["schemas"]["HrvTrend"]["properties"]["thresholds"]["description"])
    served = _flat(app.openapi()["components"]["schemas"]["Thresholds"]["description"])

    for claim in THRESHOLDS_CLAIMS:
        flat = _flat(claim)
        assert flat in contract, f"contracts/openapi.yaml no longer publishes: {claim}"
        assert flat in served, f"schemas.Thresholds no longer publishes: {claim}"
    for withdrawn in THRESHOLDS_WITHDRAWN:
        assert _flat(withdrawn) not in contract, f"withdrawn as false, back in the contract: {withdrawn}"
        assert _flat(withdrawn) not in served, f"withdrawn as false, back in the schema: {withdrawn}"

    start = contract.find(_flat(THRESHOLDS_SHARED_ANCHOR))
    assert start != -1, "the contract's shared run no longer starts where the anchor says"
    shared = contract[start:]
    assert shared in served, (
        "the two copies of the thresholds description have stopped naming the same cost in the "
        "same words. The contract says: " + shared
    )


#: Where the tier rule starts. The oracle below walks out from here.
TIER_RULE_ENTRY = "resolve_baseline_tier"


def _tier_rule_source() -> dict[str, ast.FunctionDef]:
    """``resolve_baseline_tier`` and every module-level function it reaches,
    by name, parsed out of ``hrv_trend``'s source.

    **Why the call graph and not one function body** (T125, 2026-09-16). Until
    T125 this oracle read ``resolve_baseline_tier``'s body alone, and T125
    factored rule 1's recency gate into ``_recency_struck`` so that
    ``build_series`` could ask which tiers it struck. The rule did not change
    -- the same gate, applying the same constant, called from the same line --
    but ``RECENCY_TOLERANCE_DAYS`` left the body, the oracle's remainder
    emptied and it went red. A test that reddens when a rule is *refactored*
    and not when it is *changed* is measuring the shape of the source, so the
    oracle follows the rule into the helpers it calls instead. What it must not
    become is "the name is mentioned somewhere": the walk is closed over
    module-level functions reachable from ``resolve_baseline_tier`` by a direct
    call, so a constant applied anywhere else in the module -- ``judge``,
    ``build_series``, the reset rules -- is still outside it, and the failure
    modes the test's docstring lists are all still live.
    """
    tree = ast.parse(Path(hrv_trend.__file__).read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    reached: dict[str, ast.FunctionDef] = {}
    pending = [TIER_RULE_ENTRY]
    while pending:
        name = pending.pop()
        if name in reached or name not in functions:
            continue
        reached[name] = functions[name]
        pending += [
            node.func.id
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions
        ]
    return reached


def _tier_resolution_constants() -> set[str]:
    """Every module constant the tier rule applies -- in
    ``resolve_baseline_tier``'s own body or in a helper it calls -- read out of
    the source instead of listed here.

    Listing them would make this file a second transcription of the thing it is
    supposed to be an oracle for. Parsed rather than grepped so a name inside a
    docstring or a comment -- and these functions' docstrings name several --
    cannot be mistaken for one the code applies.
    """
    return {
        node.id
        for body in _tier_rule_source().values()
        for node in ast.walk(body)
        if isinstance(node, ast.Name)
        and node.id.isupper()
        and isinstance(getattr(hrv_trend, node.id, None), int | float)
    }


def test_the_thresholds_description_names_the_tier_constant_the_block_omits() -> None:
    """Review cycle 8 iteration 4, F1. The independent oracle
    ``THRESHOLDS_CLAIMS`` claimed to have and did not.

    Iteration 3 published "tier resolution is not in this block: it applies a
    constant this response does not echo" into both copies and pinned it with
    four verbatim fragments of itself. The first half is false --
    ``resolve_baseline_tier`` applies ``MIN_BASELINE_READINGS`` and
    ``MIN_WINDOW_READINGS`` directly and its counts come from the
    ``BASELINE_DAYS`` window after the ``GAP_RESET_DAYS`` clip -- and no
    assertion in the tree could say so, because every assertion about it was
    quoting it.

    So the claim is checked against the code: the constants the tier rule
    applies, less the keys the block publishes, must be exactly the constant
    both descriptions name. That is one assertion with three failure modes, each
    run red before this was committed: the description stops naming
    ``recency_tolerance_days`` (the state this replaces); the tier rule starts
    applying a second constant the response does not echo; and the block gains
    ``recency_tolerance_days`` as a key, which the decision refused -- the
    remainder then empties and this is red, so the shape is held from this side
    too.

    **T125 (2026-09-16) moved the constant and this followed it.** Form 2
    factored rule 1's gate into ``hrv_trend._recency_struck`` so the struck set
    could be read at the verdict; ``RECENCY_TOLERANCE_DAYS`` left
    ``resolve_baseline_tier``'s body and this went red on a rule that had not
    changed. The oracle now walks the tier rule's **call graph**
    (``_tier_rule_source``) rather than one function body -- it follows the
    constant into wherever the rule keeps it, and it is not weakened to "the
    module mentions a constant somewhere": the walk is closed over module-level
    functions reachable from ``resolve_baseline_tier`` by a direct call, so
    ``judge``'s and ``build_series``'s constants are still outside it. All
    three failure modes were re-demonstrated against the widened oracle on
    2026-09-16, one at a time, each reverted: a seventh ``thresholds`` key
    (``recency_tolerance_days`` published in both copies) -- red, remainder
    empty; a second unpublished constant entering the tier rule
    (``WINDOW_DAYS`` applied in ``_recency_struck`` -- deliberately in the
    **factored helper**, where the pre-T125 oracle could not have seen it) --
    red, remainder ``['recency_tolerance_days', 'window_days']``; and the name struck
    from the served description -- red on ``schemas.Thresholds`` -- and from the
    YAML copy -- red on ``contracts/openapi.yaml``.
    """
    applied = {name.lower() for name in _tier_resolution_constants()}
    assert applied, "no module constant was read out of the tier rule's source"
    reached = _tier_rule_source()
    assert TIER_RULE_ENTRY in reached, (
        f"{TIER_RULE_ENTRY} is no longer a module-level function of hrv_trend: this oracle "
        f"walks out from it and has nothing to walk"
    )

    target = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    published = target["components"]["schemas"]["HrvTrend"]["properties"]["thresholds"]
    served = app.openapi()["components"]["schemas"]["Thresholds"]

    for label, block in (("contracts/openapi.yaml", published), ("schemas.Thresholds", served)):
        keys = set(block["properties"])
        assert applied & keys, (
            f"{label}: the description calls these the band, verdict and tier-resolution "
            f"constants, but the tier rule applies none of the published keys {sorted(keys)}"
        )
        omitted = applied - keys
        assert omitted == {"recency_tolerance_days"}, (
            f"{label}: the tier rule ({', '.join(sorted(reached))}) applies {sorted(applied)} "
            f"and the block publishes {sorted(keys)}, so the constants it applies and does not "
            f"echo are {sorted(omitted)} -- not the one the description names"
        )
        description = _flat(block["description"])
        for name in omitted:
            assert name in description, (
                f"{label} applies {name} to resolve baseline.tier and neither publishes it "
                f"nor names it: the block's description is a false account of what it carries"
            )


def _row(day: date, hh: int, tier: str | None, value: float | None, session_id: str) -> dict:
    """One stored row as ``db.hrv_rows`` hands it to ``build_series``, in the
    UTC zone the rendering pins below configure."""
    return {
        "session_id": session_id,
        "start_time": at(day, hh).isoformat(),
        "resting_rmssd_ms": value,
        "hrv_source_tier": tier,
    }


def _rendered(rows: list[dict], to: date = D) -> dict:
    """``rows`` through the whole read path the route uses -- ``build_series``,
    ``judge``, ``_trend_response`` -- as the JSON body a client receives. The
    seam that matters here is ``_trend_response``'s trim: a reason the module
    emits is only *published* if it survives into this dict."""
    series = hrv_trend.build_series(rows, ZoneInfo("UTC"), to)
    response = main_module._trend_response(to - timedelta(days=126), [], series, hrv_trend.judge(series))
    return json.loads(response.model_dump_json(by_alias=True))


def _era_with_trial_rows() -> list[dict]:
    """The shared fixture of the two rendering pins below: a snapshot era, a
    ten-day strap trial inside it, and a genuine switch to a daily strap at
    ``D-39``. ``trial-*`` are the rows the era clip drops."""
    return (
        [_row(day, 6, SNAPSHOT, 40.0, f"snap-{day}") for day in days(D - timedelta(days=126), D - timedelta(days=40))]
        + [_row(day, 7, STRAP, 25.0, f"trial-{day}") for day in days(D - timedelta(days=60), D - timedelta(days=51))]
        + [_row(day, 7, STRAP, 40.0, f"strap-{day}") for day in days(D - timedelta(days=39), D)]
    )

#: Every value ``contracts/openapi.yaml``'s ``excluded[].reason`` prose
#: publishes as receivable, transcribed from that prose rather than read from
#: the module, so the two are independent oracles. The parameterised members
#: are spelled with the arguments the series below produce.
PUBLISHED_REASONS = {
    "pre_amendment_window",
    "null_tier",
    "unknown_tier: wrist_ppg_guess",
    "unusable_value: 0.0",
    f"off_baseline_tier: {SNAPSHOT}",
    "same_day_later_capture",
    "before_reset: tier_change",
    "before_reset: coverage_gap",
}


def test_every_published_exclusion_reason_is_observed_in_a_rendered_response() -> None:
    """T101's sweep, as an assertion. The test above checks each reason is
    *named* in the schema; this one checks each is *emittable* -- that some
    real series renders a body carrying it. A published value no code path
    can emit is a claim no gate catches (``check_drift.py`` compares
    endpoints, not enum prose), and ``before_reset: tier_change`` was exactly
    that until T098 made the era clip unconditional (review cycle 4, G-C4-3).

    Two series, one per reason, because each is clearest where it is the
    only reset in play. The two resets **can** co-occur (T107, review cycle
    6 G-C6-5: the gap takes precedence over the *report* alone, and the era
    clip runs whether or not it fired) -- the two task notes that recorded
    "the two resets cannot co-occur" are what left the co-occurring case
    without a fixture for three cycles; it is pinned in
    ``test_hrv_trend_reset.py`` rather than here, because the exclusion
    chain is what this test is about:

    * a snapshot era with a ten-day strap trial inside it and a genuine
      switch to a daily strap, plus one row for each screen of the exclusion
      chain -- seven of the eight;
    * a snapshot era, a 27-day silence and a resumption -- the eighth.

    Their union is asserted **equal** to the published set, so the diff is
    pinned both ways: a published member no series can produce fails here,
    and a reason the module learns to emit without the contract learning it
    fails here too. ``outside_windows`` is the one module constant that is
    deliberately not in the set -- ``_trend_response`` trims it -- and the
    last assertion is that it never reaches a body.
    """
    era = _rendered(
        _era_with_trial_rows()
        + [
            _row(D - timedelta(days=20), 9, None, 40.0, "no-tier"),
            _row(D - timedelta(days=19), 9, "wrist_ppg_guess", 40.0, "odd-tier"),
            _row(D - timedelta(days=18), 9, STRAP, 0.0, "bad-value"),
            _row(D - timedelta(days=17), 9, STRAP, None, "pre-amendment"),
            _row(D - timedelta(days=10), 9, STRAP, 41.0, "later-same-day"),
        ]
    )
    gap = _rendered(
        [_row(day, 6, SNAPSHOT, 40.0, f"snap-{day}") for day in days(D - timedelta(days=66), D - timedelta(days=30))]
        + [_row(day, 6, SNAPSHOT, 40.0, f"back-{day}") for day in days(D - timedelta(days=2), D)]
    )

    assert era["baseline"]["reset_reason"] == "tier_change"
    assert gap["baseline"]["reset_reason"] == "coverage_gap"
    observed = {entry["reason"] for entry in era["excluded"]} | {entry["reason"] for entry in gap["excluded"]}
    assert observed == PUBLISHED_REASONS
    assert hrv_trend.REASON_OUTSIDE_WINDOWS not in observed


def test_the_tier_change_clip_is_listed_in_a_rendered_body_whether_or_not_it_is_reported() -> None:
    """G-C4-3's own pin, at the rendering seam. ``test_hrv_trend_reset.py``'s
    ``test_the_clipped_readings_are_listed_before_reset_tier_change`` pins the
    listing and the disjoint-and-exhaustive invariant on ``HrvSeries``; this
    pins that it survives ``_trend_response``'s trim into the body a client
    reads, on **both** sides of D4a's split -- a reported ``tier_change`` and
    a withdrawn one, where the clip happens all the same.

    Three stray snapshot captures inside the judged week are what withdraws
    the report -- rule 4(c)'s week half calls the old tier corroboration
    until it covers ``MIN_WINDOW_READINGS`` distinct days of ``[D-6, D]``;
    the two strays of the reported case sit a month back and clear it. The
    ten clipped trial rows are listed either way, and ``included`` never
    holds one."""
    def rows(strays: tuple[int, ...]) -> list[dict]:
        return _era_with_trial_rows() + [
            _row(D - timedelta(days=n), 8, SNAPSHOT, 40.0, f"stray-{n}") for n in strays
        ]

    trial_ids = {f"trial-{day}" for day in days(D - timedelta(days=60), D - timedelta(days=51))}
    reported = _rendered(rows((30, 29)))
    withdrawn = _rendered(rows((4, 3, 2)))

    assert reported["baseline"]["reset_reason"] == "tier_change"
    assert withdrawn["baseline"]["reset_reason"] is None
    for body, name in ((reported, "reported"), (withdrawn, "withdrawn")):
        listed = {
            entry["session_id"] for entry in body["excluded"] if entry["reason"] == "before_reset: tier_change"
        }
        assert listed == trial_ids, name
        assert trial_ids.isdisjoint({entry["session_id"] for entry in body["included"]}), name


@pytest.mark.parametrize(
    ("era_age", "window_first", "n"),
    [
        pytest.param(60, D - timedelta(days=60), 54, id="era-younger-than-the-window-clip-binds"),
        pytest.param(66, D - timedelta(days=66), 60, id="era-first-day-is-date-66-clip-is-a-no-op"),
        pytest.param(70, D - timedelta(days=66), 60, id="era-older-than-the-window-clip-is-a-no-op"),
    ],
)
def test_a_reported_tier_change_sits_beside_the_unclipped_window_once_the_era_is_older_than_it(
    era_age: int, window_first: date, n: int
) -> None:
    """T103 (review cycle 5, G-C5-5). Until T103 ``contracts/openapi.yaml``
    and ``schemas.Baseline.reset_reason`` published the universal now held
    as ``RESET_REASON_WITHDRAWN``'s first entry (a reported ``tier_change``
    and an un-clipped ``window``, asserted never to co-occur; quoted there
    once, not restated here, so the tuple stays the single copy). The clip is
    ``[max(date-66, R), date-7]`` (``build_series``, D4a), so once the era's
    first day ``R`` is ``date-66`` or older the ``max`` yields ``date-66``
    and the reported window is byte-identical to ``baseline_window(date)``
    -- while ``tier_change`` is still reported, because on those rows the
    report is still live: all three of rule 4's conditions hold together
    (the strap sustains the baseline window, the previous window
    ``[date-126, date-67]`` is still sustained by the snapshot, and the
    eras do not interleave). Clause (b) alone is necessary, not
    sufficient, so the ``S+80`` day below bounds the report rather than
    defining it (T109; corrected here by T111, which found this docstring
    still stating the withdrawn equivalence ~250 lines below the tuple
    T109 fixed). Reproduced on ``test_hrv_trend_reset.py``'s
    ``S+80`` row before this pin was written: ``reset_reason
    tier_change``, ``reset_on 2026-05-02``, ``baseline_window (2026-05-15,
    2026-07-13)``, ``baseline_window(2026-07-20) (2026-05-15,
    2026-07-13)``, equal.

    The contract's claim is about the **response**, so this pins the
    pairing through ``_trend_response``: a daily snapshot era, then a daily
    strap from ``D-era_age``. On ``D`` the strap sustains ``[D-66, D-7]``,
    the snapshot still sustains the previous window (the strap holds at
    most 4 days there), no capture lies across the boundary, and the
    switch is reported on every row. *Under the withdrawn clause* the
    second and third rows could not both hold: with ``tier_change``
    reported, ``window`` would have to differ from ``baseline_window(D)``,
    so the ``window`` assertion on those two rows is what goes red --
    ``reset_on`` names the era's first day beneath ``window[0]`` all the
    same. The first row is the edge from the other side: a younger era is
    clipped at its own first day, so the pin discriminates the two states
    rather than describing one.

    T105 (review cycle 6, G-C6-3) removed a trailing assertion that no
    ``before_reset`` row reached ``excluded``. It could not fail on any of
    the three rows -- the clip lists only readings of the **resolved**
    tier and this fixture seeds no strap reading before the boundary, so
    the list is empty even on ``era_age=60`` where the clip genuinely
    binds and drops ``[D-66, D-61]`` -- and its stated justification ("the
    clip removed nothing") was false of that row.
    ``test_the_tier_change_clip_is_listed_in_a_rendered_body_whether_or_not_it_is_reported``
    pins the listing behaviour on a fixture that can produce it."""
    era_first_day = D - timedelta(days=era_age)
    day_before_era = era_first_day - timedelta(days=1)
    body = _rendered(
        [_row(day, 6, SNAPSHOT, 40.0, f"snap-{day}") for day in days(D - timedelta(days=126), day_before_era)]
        + [_row(day, 7, STRAP, 40.0, f"strap-{day}") for day in days(era_first_day, D)]
    )
    unclipped = [d.isoformat() for d in hrv_trend.baseline_window(D)]

    assert body["baseline"]["reset_reason"] == "tier_change"
    assert body["baseline"]["reset_on"] == era_first_day.isoformat()
    assert body["baseline"]["window"] == [window_first.isoformat(), (D - timedelta(days=7)).isoformat()]
    assert (body["baseline"]["window"] == unclipped) is (era_age >= 66)
    assert body["baseline"]["reset_on"] <= body["baseline"]["window"][0]
    assert body["baseline"]["n"] == n and body["baseline"]["established"] is True
    assert body["baseline"]["tier"] == STRAP


def test_the_query_parameters_are_from_and_to_and_both_optional() -> None:
    """``from`` is a Python keyword, so the parameter is aliased; the alias
    is what ``/openapi.json`` -- and T091's contract pin -- must show."""
    params = {p["name"]: p for p in app.openapi()["paths"]["/metrics/hrv"]["get"]["parameters"]}
    assert set(params) == {"from", "to"}
    assert all(p["in"] == "query" and not p.get("required", False) for p in params.values())


# ---------------------------------------------------------------------------
# the from/to adversarial table (the parser's deliverable)
#
# Authored from the task's list before the route was written. Each row is what
# was sent and what came back; the reasons are in the task file's table.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["", "yesterday", "2026-13-01", "2026-09-0", "2026/09/08", "2026-02-29", "20260908", "2026-09-08T06:00"],
)
def test_a_malformed_date_is_pydantics_422_not_a_hand_parse(configure, value: str) -> None:
    configure("UTC")
    with TestClient(app) as client:
        response = get(client, to=value)

    assert response.status_code == 422, response.text
    assert any(err["loc"] == ["query", "to"] for err in response.json()["detail"])


@pytest.mark.parametrize(
    ("from_", "to", "zone"),
    [
        ("2028-02-29", "2028-02-29", "UTC"),  # a real leap day
        ("2026-09-27", "2026-09-27", AUCKLAND),  # Auckland's spring-forward day
        ("2026-04-05", "2026-04-05", AUCKLAND),  # Auckland's fall-back day
        ("2026-09-08", "2026-09-08", "UTC"),  # both parameters equal
        ("2025-09-08", "2026-09-08", "UTC"),  # from before any history (a year back; T091 caps at 366 days)
        ("0001-05-07", "0001-05-07", "UTC"),  # to - 126 is the calendar's origin: the read bound clamps
        ("9999-12-31", "9999-12-31", "UTC"),  # to at the calendar's end: the padded read bound clamps
        ("2025-09-07", "2026-09-08", "UTC"),  # a 366-day range; 367 is T091's 422 (test_hrv_trend_points.py)
    ],
)
def test_well_formed_ranges_parse_and_answer_200(configure, from_: str, to: str, zone: str) -> None:
    configure(zone)
    with TestClient(app) as client:
        response = get(client, **{"from": from_, "to": to})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["from"] == from_ and body["date"] == to
    assert body["verdict"] == "hrv_unavailable"


def test_a_to_inside_the_calendars_first_126_days_is_a_422_not_a_500(configure) -> None:
    """The far-future row above clamps because only the *read bound* overflows;
    a ``to`` within 126 days of ``date.min`` overflows the module's own window
    arithmetic, which is the parameter's problem and is named as such."""
    configure("UTC")
    with TestClient(app) as client:
        response = get(client, to="0001-01-31")

    assert response.status_code == 422, response.text
    assert "to" in str(response.json()["detail"]) and "0001-01-31" in str(response.json()["detail"])
