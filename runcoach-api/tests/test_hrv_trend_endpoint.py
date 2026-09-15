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

import hashlib
import importlib.util
import json
import math
import statistics
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
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

    seeder.snapshots(days(D - timedelta(days=26), D), baseline_values(27))
    seeder.persist()

    with TestClient(app) as client:
        future = get(client, to=(D + timedelta(days=400)).isoformat())
        before = get(client, to=(D - timedelta(days=100)).isoformat())

    assert future.status_code == 200 and before.status_code == 200
    assert future.json()["verdict"] == "hrv_unavailable"
    assert future.json()["readings_in_window"] == 0
    assert before.json()["verdict"] == "hrv_unavailable"
    assert before.json()["baseline"]["established"] is False
    assert before.json()["baseline"]["n"] == 0


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
    for k, body in ahead.items():
        assert body["date"] == (D + timedelta(days=k)).isoformat()
        # The window is full and the baseline established: the guard is the
        # clock, not thin data.
        assert body["readings_in_window"] >= 3 and body["baseline"]["established"] is True
        assert body["verdict"] == "hrv_unavailable", (k, body["verdict"])
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
# ``tests/support/withdrawn_phrasings.py``, which is the one file outside the
# scan corpus (T114, gap G-C7-10). This file is therefore scanned in full, and
# the standing rule for it is the same as for every other scanned file -- do
# not quote a withdrawn phrasing anywhere in it. Notes that need to point at
# one name it by its index, or paraphrase it.

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


#: The withdrawn phrasings, the docstring idioms of the same claims, and the
#: files scanned for them live in ``tests/support/withdrawn_phrasings.py``
#: (T114, gap G-C7-10). They used to live here, in the file that scans for
#: them, and the fence that stopped the scan matching its own declarations was
#: defeated twice by a marker pairing off with another copy of itself -- the
#: second time by the commit that fixed the first. Moving the declarations out
#: deletes the fence, the excision and that whole class of defect: this file is
#: now scanned in full, like every other file in the table.
#:
#: Loaded from its path because the workspace runs pytest with
#: ``--import-mode=importlib``, under which nothing in ``tests/`` is importable
#: by name.
_DECLARATIONS = _load_module(
    "withdrawn_phrasings", Path(__file__).parent / "support" / "withdrawn_phrasings.py"
)
RESET_REASON_WITHDRAWN = _DECLARATIONS.RESET_REASON_WITHDRAWN
RESET_REASON_WITHDRAWN_IDIOMS = _DECLARATIONS.RESET_REASON_WITHDRAWN_IDIOMS
WITHDRAWN_SCAN_FILES = _DECLARATIONS.WITHDRAWN_SCAN_FILES


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
)


def test_the_withdrawn_tuples_are_in_the_order_the_prose_names_them_by() -> None:
    """The index-by-index pin ``WITHDRAWN_ORDER`` describes. A reorder, a
    mid-tuple insert, a deletion or a reworded entry all go red here, and the
    length check makes an *append* the only change that passes silently --
    which is the only one that leaves every existing index pointing where the
    prose says it points."""
    tuples = {
        "RESET_REASON_WITHDRAWN": RESET_REASON_WITHDRAWN,
        "RESET_REASON_WITHDRAWN_IDIOMS": RESET_REASON_WITHDRAWN_IDIOMS,
    }
    for name, index, digest, what in WITHDRAWN_ORDER:
        entry = tuples[name][index - 1]
        actual = hashlib.sha256(_flat(entry).encode()).hexdigest()[:12]
        assert actual == digest, (
            f"{name} entry {index} is no longer {what}: every note that names "
            f"that index now points at a different claim (got {actual})"
        )
    for name, tup in tuples.items():
        pinned = [row for row in WITHDRAWN_ORDER if row[0] == name]
        assert len(tup) >= len(pinned), f"{name} lost an entry the prose names by index"


def test_the_scan_corpus_still_holds_every_file_it_was_built_for() -> None:
    """A dropped row in ``WITHDRAWN_SCAN_FILES`` stops a file being scanned and
    takes its parametrised case with it, so the remaining cases still pass and
    the count in the summary is the only trace. Pinned by name (T114): the
    parametrisation cannot notice its own absence."""
    assert [path.name for path, _ in WITHDRAWN_SCAN_FILES] == [
        "hrv_trend.py",
        "schemas.py",
        "openapi.yaml",
        "test_hrv_trend_endpoint.py",
        "test_hrv_trend_reset.py",
        "F005-resting-hrv-trend.md",
        "F005-trend-construction.md",
    ]


def _scannable(path: Path) -> str:
    """The file's whole text, whitespace-flattened. Nothing is excised.

    Flattening is the whole point: ``hrv_trend.py`` wrapped "is no longer
    sustained by / the old tier" across a line break and a line-oriented
    ``grep`` for the sentence returned nothing, which is exactly the false
    all-clear ``sweep-the-claim-not-the-diff`` warns about.

    T114 (gap G-C7-10) removed the excision this function used to do. Two
    generations of fence lived here -- a marker pair whose literals had to be
    assembled rather than written, because a literal copy of a marker is
    another marker and pairs off with a real one. T113 replaced a
    ``if begin in text`` form that silently dropped everything to EOF with a
    balance-asserting loop, and split one fenced region into two in the same
    commit; deleting the *first* of the two end markers then left the loop
    balanced, ~12 lines of live commentary unscanned and the suite green.
    Guarding a fence is what failed, twice. There is no fence now, and no
    file in the scan corpus contains a withdrawn phrasing to hide from it.
    """
    return _flat(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(("path", "anchor"), WITHDRAWN_SCAN_FILES, ids=[path.name for path, _ in WITHDRAWN_SCAN_FILES])
def test_the_withdrawn_reset_reason_phrasings_are_gone_from_every_live_copy(path: Path, anchor: str) -> None:
    """T111 (review cycle 7, G-C7-1). ``RESET_REASON_WITHDRAWN`` was read
    against the two description strings only, so the four sites that
    actually carried the withdrawn equivalence -- a module docstring, a
    test docstring and two spec documents -- were invisible to it by
    construction. This reads the same tuple against every file that
    carries a live copy of the ``tier_change`` lifetime, so a recurrence
    goes red here instead of waiting for a review scanner.
    """
    if not path.exists():
        pytest.skip(f"{path} is absent (the .shipyard breadcrumb is machine-local and gitignored)")
    text = _scannable(path)
    assert _flat(anchor) in text, (
        f"{path.name}: the scan did not read its live anchor, so every all-clear below "
        f"would be a report over text nothing read: {anchor}"
    )
    for withdrawn in RESET_REASON_WITHDRAWN + RESET_REASON_WITHDRAWN_IDIOMS:
        assert _flat(withdrawn) not in text, f"withdrawn as false (T103/T105/T109), back in {path.name}: {withdrawn}"


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
    return " ".join(text.translate(_QUOTE_CHARS).split()).lower()


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
#: fact drifted: ``schemas.Baseline.window`` carried "For `coverage_gap` R
#: is reset_on", which T107 falsified (the era boundary can clip later than
#: the resumption, so ``window[0]`` may lie after a ``coverage_gap``'s
#: ``reset_on``), while ``contracts/openapi.yaml`` said nothing about
#: ``coverage_gap`` at all. One copy false, the other silent, both green.
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
WINDOW_WITHDRAWN = (
    "for `coverage_gap` r is reset_on",
    "for coverage_gap r is reset_on",
)


def test_the_two_copies_of_the_window_contract_publish_the_same_claims() -> None:
    """T107 (review cycle 6), the ``reset_reason`` pin above applied to
    ``baseline.window``, whose two copies nothing compared.

    The gap it closes is the one that let T107's sixth site survive a
    claim-sweep: ``schemas.Baseline.window`` published "For `coverage_gap`
    R is reset_on" -- the same claim as "a `coverage_gap`'s ``reset_on``
    never precedes ``window[0]``", stated as an identity on ``window[0]``
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
