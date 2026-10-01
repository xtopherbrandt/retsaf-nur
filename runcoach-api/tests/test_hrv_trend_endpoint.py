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


def _strap_beats_reading(rmssd_ms: float) -> list[RRInterval]:
    """An alternating beat series whose rMSSD is exactly ``rmssd_ms`` -- the
    ``_seed_beats`` construction in ``conftest.py``: every successive
    difference is ``rmssd_ms`` in magnitude."""
    values = (900.0, 900.0 + rmssd_ms)
    return [
        RRInterval(seq=i, rr_ms=values[i % 2], rr_source="chest_strap_ecg", is_artefact=False)
        for i in range(160)
    ]


class Seeder:
    """Builds sessions through the real classifier and persists them."""

    def __init__(self, synthetic, classified, persist_sessions) -> None:
        self._synthetic = synthetic
        self._classified = classified
        self._persist_sessions = persist_sessions
        self.sessions: list[Session] = []
        self._beats: dict[str, list[RRInterval]] = {}

    def snapshot(self, when: datetime, rmssd: float) -> Session:
        """A Health Snapshot capture carrying exactly ``rmssd`` (Tier 2)."""
        session = self._classified(self._synthetic(60, rmssd_hrv=rmssd, start_time=when))
        assert session.hrv_source_tier == SNAPSHOT and session.resting_rmssd_ms == rmssd
        return self._keep(session)

    def snapshots(self, dates: Iterable[date], values: Iterable[float]) -> list[Session]:
        """One snapshot per local day at 06:00 UTC, pairing ``dates`` with
        ``values`` as ``zip`` does -- ``repeat(v)`` seeds a flat series."""
        return [self.snapshot(at(day), value) for day, value in zip(dates, values)]

    def strap(self, when: datetime, rmssd: float | None = None) -> Session:
        """A declared chest-strap capture whose reading is computed from beats (Tier 1).

        ``rmssd`` engineers the beats to that exact reading; left ``None`` the
        capture carries ``_strap_beats``, as every strap here did before it."""
        beats = _strap_beats() if rmssd is None else _strap_beats_reading(rmssd)
        messages = self._synthetic(
            total_timer_time=150.0, avg_heart_rate=60, sport_profile_name=PROFILE, start_time=when
        )
        session = self._classified(messages, beats, 1.0, [PROFILE])
        assert session.hrv_source_tier == STRAP and session.resting_rmssd_ms > 0
        if rmssd is not None:
            assert session.resting_rmssd_ms == pytest.approx(rmssd), (session.resting_rmssd_ms, rmssd)
        self._beats[session.session_id] = beats
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
            s.session_id: self._beats.get(s.session_id, _strap_beats())
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
    it by hand (``research/00`` PRIN-12), and every exclusion names a reason.
    Red: the route does not exist (404)."""
    configure("UTC")
    values = baseline_values(20)
    seeder.snapshots(BASELINE_20, values)
    week = [40.0, 41.0, 42.0, 43.0]
    seeder.snapshots(days(D - timedelta(days=6), D - timedelta(days=3)), week)
    later = seeder.snapshot(at(D - timedelta(days=3), hh=9), 30.0)
    strap = seeder.strap(at(D - timedelta(days=2)))
    strap_reading_ms = strap.resting_rmssd_ms
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
        "recency_tolerance_days": hrv_trend.RECENCY_TOLERANCE_DAYS,
    }
    reasons = {entry["session_id"]: entry["reason"] for entry in body["excluded"]}
    # F006 (T152): the strap capture feeds the strap's own dataset and is in
    # no excluded list; shipped F005 listed it ``off_baseline_tier: <tier>``.
    assert reasons == {
        later.session_id: "same_day_later_capture",
        run.session_id: "null_tier",
        legacy.session_id: "pre_amendment_window",
    }
    assert strap.session_id not in reasons
    assert all(entry["reason"] for entry in body["excluded"])
    assert all(entry["date"] for entry in body["excluded"])
    # T159, re-pointing this pin off absence: "in neither list" was all T152
    # could say, and it is satisfied by a row that fell out of the response
    # entirely. The strap capture is now asserted **present**, in its own
    # dataset, with the shape AC10's second sentence requires of a dataset
    # that has no band -- its ``n`` and its judged-week count (wave-4 review;
    # AC15's "accounted for exactly once" needs the positive half to mean
    # anything).
    datasets = _by_tier(body)
    assert set(datasets) == {SNAPSHOT, STRAP}, body["datasets"]
    assert datasets[STRAP] | {"tier": STRAP} == {
        "tier": STRAP,
        "n": 0,
        "established": False,
        "band": None,
        "fidelity_rank": 0,
        "last_read": None,
        "week_days": 1,
        "week_mean": pytest.approx(math.log(strap_reading_ms)),
        "below": None,
        "reset_on": None,
        "reset_reason": None,
    }, datasets[STRAP]
    assert datasets[SNAPSHOT]["n"] == 20 and datasets[SNAPSHOT]["week_days"] == 4
    assert body["selected_dataset"] == SNAPSHOT
    assert body["selected_reason"] == hrv_trend.SELECTED_HIGHEST_FIDELITY
    assert body["disagreed_with"] == []
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
    exercised the branch beside the bug). Red: ``hrv_suppressed``.

    **The four F006 fields are asserted here too** (sprint-006 review
    iteration 1, M2): this case said nothing about them, so which of them
    ``_withhold_future`` reaches was incidental rather than decided.
    ``disagreed_with`` is withheld with the verdict it is a claim about, and
    ``datasets[]``/``selected_dataset``/``selected_reason`` are kept as
    computed because they are what produced the retained ``baseline``/``band``
    (``research/00`` PRIN-12, reproducible by hand). On this single-tier fixture
    the dissent list is empty on every day including ``D``, so the
    ``disagreed_with`` clause below is a *consistency* check only; the one
    that can tell the rule from the fixture is
    ``test_a_future_day_names_no_dissenter_because_no_verdict_was_conferred``,
    which seeds a dataset that really does dissent."""
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
    print("today:", today["selected_dataset"], today["selected_reason"],
          [d["tier"] for d in today["datasets"]], today["disagreed_with"])
    for k, body in ahead.items():
        print(f"D+{k}:", body["verdict"], body["unavailable_reason"], body["selected_dataset"],
              body["selected_reason"], [d["tier"] for d in body["datasets"]],
              body["disagreed_with"])
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
        # F006 (M2): the claim is withheld, its producers are not.
        assert body["disagreed_with"] == [], (k, body["disagreed_with"])
        assert body["selected_dataset"] == today["selected_dataset"], (k, body["selected_dataset"])
        assert body["selected_reason"] == today["selected_reason"], (k, body["selected_reason"])
        assert [d["tier"] for d in body["datasets"]] == [d["tier"] for d in today["datasets"]], k


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
    ``excluded[]`` lists every non-contributing row across ``[to-66, to]``.
    A strap capture deep inside the baseline is the strap's own dataset,
    not a non-contributing row: shipped F005 listed it
    ``off_baseline_tier: <tier>`` and F006 (T152) lists it nowhere, so the
    list is empty here."""
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
    assert body["excluded"] == []
    assert deep.session_id not in {r["session_id"] for r in body["included"]}
    # T159, re-pointing this pin off absence (wave-4 review): "in neither
    # ``included[]`` nor ``excluded[]``" is also what a dropped row looks
    # like. The deep strap capture is asserted **present** in the strap's own
    # dataset -- one baseline-window day, no band, no judged-week reading --
    # which is the claim T152 was actually making.
    datasets = _by_tier(body)
    assert set(datasets) == {SNAPSHOT, STRAP}, body["datasets"]
    assert datasets[STRAP]["n"] == 1
    assert datasets[STRAP]["band"] is None and datasets[STRAP]["below"] is None
    assert datasets[STRAP]["week_days"] == 0 and datasets[STRAP]["week_mean"] is None
    assert datasets[STRAP]["last_read"] == (D - timedelta(days=20)).isoformat()
    assert datasets[STRAP]["established"] is False
    assert datasets[SNAPSHOT]["n"] == 20 and datasets[SNAPSHOT]["band"] is not None
    assert body["selected_dataset"] == SNAPSHOT


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
    # F006 (T152): the 14 July strap mornings are the strap's own dataset
    # and nothing is excluded; shipped F005 listed all 14
    # ``off_baseline_tier: chest_strap_raw``.
    assert on_suppressed_day["excluded"] == []
    # T159, re-pointing this pin off absence (wave-4 review): an empty
    # ``excluded[]`` is equally what losing all 14 rows would produce. They
    # are asserted **present** in the strap's own dataset, all 14 of them, on
    # the very day the snapshot is judged suppressed -- the abandoned trial
    # keeps its own baseline and its own band, and is simply not the dataset
    # the verdict came from.
    trial = _by_tier(on_suppressed_day)
    assert set(trial) == {SNAPSHOT, STRAP}, on_suppressed_day["datasets"]
    assert trial[STRAP]["n"] == 14
    assert trial[STRAP]["established"] is True
    assert trial[STRAP]["band"] is not None
    assert trial[STRAP]["week_days"] == 0 and trial[STRAP]["week_mean"] is None
    assert trial[STRAP]["last_read"] == "2026-07-16"
    assert trial[STRAP]["band"]["mean"] != trial[SNAPSHOT]["band"]["mean"]
    # It is established but not judgeable (no judged-week day), so it is not a
    # candidate at all (AC8) and the snapshot is selected with nothing skipped.
    assert on_suppressed_day["selected_dataset"] == SNAPSHOT
    assert on_suppressed_day["selected_reason"] == hrv_trend.SELECTED_HIGHEST_FIDELITY
    assert on_suppressed_day["disagreed_with"] == []

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
        hrv_trend.REASON_SAME_DAY_LATER_CAPTURE,
        hrv_trend.REASON_OUTSIDE_WINDOWS,
        hrv_trend.REASON_BEFORE_RESET,
    ):
        assert constant in reason, constant
    # F006 (T152) retired F005's ``off_baseline_tier`` -- a reading of another
    # tier is in its own dataset -- and the schema, the module and the
    # checked-in contract drop it together (the three-valued pin's F006
    # leg; the removal is the sprint's one breaking contract change).
    assert "off_baseline_tier" not in reason
    assert not hasattr(hrv_trend, "REASON_OFF_BASELINE_TIER")
    assert "off_baseline_tier" not in CONTRACT.read_text(encoding="utf-8")
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
#: The entries are **constrained by ``research/00`` HRV-38 and F005** -- rule
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
    ("SILENCE_RATE_WITHDRAWN", 1, "394adbef2936", "T138's composed-silence scenario, the event the rule cannot detect"),
    ("SILENCE_RATE_WITHDRAWN", 2, "02e9956b8dc3", "T138's composed-silence headline, the rate and its share of the year"),
    ("RESET_COMPOSITION_WITHDRAWN", 1, "1a4863e860cf", "T163's false composition, the sentence removed from judge"),
    ("RESET_COMPOSITION_WITHDRAWN", 2, "0d63463e6a26", "the same claim in the documents' markup spelling"),
    ("RESET_COMPOSITION_WITHDRAWN", 3, "7432e919d018", "the same claim with the two reset kinds reordered"),
    ("RESET_COMPOSITION_WITHDRAWN", 4, "aef736c5caa9", "T142's plural spelling of the composition"),
    ("RESET_COMPOSITION_WITHDRAWN", 5, "9556095e259a", "the hyphenated adjectival idiom, spec/06's order"),
    ("RESET_COMPOSITION_WITHDRAWN", 6, "27778a76f89a", "the hyphenated adjectival idiom, reordered"),
    ("RESET_COMPOSITION_WITHDRAWN", 7, "e9406060395a", "the every-reset quantifier composition"),
    ("RESET_COMPOSITION_WITHDRAWN", 8, "3a2100b7e86f", "the either-reset quantifier composition"),
    ("RESET_COMPOSITION_WITHDRAWN", 9, "0a8bd6644939", "the any-reset quantifier composition"),
    ("RESET_COMPOSITION_WITHDRAWN", 10, "17628c8c2129", "the both-resets quantifier composition"),
    ("RESET_COMPOSITION_WITHDRAWN", 11, "3ac56c9d3a23", "research/00's C14 parenthetical, the source-tier change that collapses"),
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
SCOPED_SUITE_COLLECTED = 476  # re-measured 2026-10-01 (sprint-009 wave 3 test-fix, IDEA-072),
#                              # as the last action before the commit: +1. One pin added to this
#                              # file, the walk's nested-checkout prune
#                              # (test_the_walk_prunes_a_nested_checkout_but_not_the_directory_around_it).
#                              # No identity elsewhere changed. Nothing publishes this literal;
#                              # the previous value's own note follows.
# SCOPED_SUITE_COLLECTED = 475  # re-measured 2026-09-30 (T221, sprint-009), as the last action
#                              # before the commit: +2. One parametrized pin added to this file,
#                              # F010 AC3's recency boundary (two cases, exactly the tolerance
#                              # and one past it), the test PRIN-12 and HRV-17 now name on their
#                              # Pinned lines. Nothing publishes this literal; the previous
#                              # value's own note follows.
# SCOPED_SUITE_COLLECTED = 473  # re-measured 2026-09-22 (sprint-006 code review cycle 2,
#                              # iteration 1), as the last action before the commit: +1. One pin
#                              # added to this file, S4's conferred-suppressed dissent pin (a
#                              # ``verdict != VERDICT_NORMAL`` predicate emptied the list on a
#                              # conferred hrv_suppressed and every other pin stayed green). The
#                              # cycle's other test change (S2) is in test_hrv_unavailable_causes.py,
#                              # not one of the five suites. Nothing publishes this literal; the
#                              # previous value's own note follows.
# SCOPED_SUITE_COLLECTED = 472  # re-measured 2026-09-21 (sprint-006 code review, iteration 1),
#                              # as the last action before the commit: +2. Two pins added to this
#                              # file, both review findings: M2's future-day dissent pin (a
#                              # withheld day names no dissenter, with the day that HAS happened
#                              # as its control) and S2's reset_reason derivation pin (the fourth
#                              # hand-typed copy of a module-owned enum, now read off the
#                              # annotation's AST because a values-only check cannot tell a
#                              # derived Literal from a transcribed one). The review's other two
#                              # findings landed outside the five suites --
#                              # test_hrv_no_regression_gate.py (M1) and
#                              # test_source_change_rule_sweep.py (S1) -- so they do not reach
#                              # this number. Nothing publishes it; the previous value was the
#                              # final spec review's, whose own note follows.
# SCOPED_SUITE_COLLECTED = 470  # re-measured 2026-09-21 (sprint-006 final spec review, finding
#                              # 1), as the last action before the commit: +1. One pin added to
#                              # this file, in the T159 rendering block: a tier read only
#                              # before a global coverage gap is absent from datasets[] and
#                              # wholly in excluded as before_reset: coverage_gap -- the claim
#                              # both published copies of the datasets[] description got wrong
#                              # ("one entry per source tier present in [date-66, date]"), and
#                              # which nothing could break, the two-copy oracle comparing field
#                              # sets rather than a description against the code. No identity
#                              # elsewhere changed: the two schema copies and the contract were
#                              # reworded, not re-pinned. Nothing publishes this number; the
#                              # previous value was T159's, whose own note follows.
# SCOPED_SUITE_COLLECTED = 469  # re-measured 2026-09-21 (T159), as the last action before the
#                              # commit: +10. Two of the five suites moved.
#                              # Nine identities added to this file on F006's
#                              # response block (datasets[], selected_dataset,
#                              # selected_reason, disagreed_with and the
#                              # dataset each point's band came from): both
#                              # tiers rendered with their own band, the
#                              # no-band dataset carrying n and its week count,
#                              # the null-selection pair with the fallback
#                              # still populating baseline/band, the fall to a
#                              # lower tier with the recency gate recomputed
#                              # from the response, the dissenter named with
#                              # its week count (2 cases), the per-dataset
#                              # reported reset, the per-point identity across
#                              # a selection switch, and the two-copy schema/
#                              # contract pin. One added to
#                              # test_hrv_trend_points.py (every point names
#                              # the dataset its band came from, including the
#                              # day that has a dataset and no band). No
#                              # identity elsewhere changed: the three
#                              # endpoint pins the wave-4 review flagged as
#                              # weakened to absence-only were re-pointed in
#                              # place at datasets[] membership, not added.
#                              # Nothing publishes this number; the previous
#                              # value was T156's, whose own note follows.
# SCOPED_SUITE_COLLECTED = 459  # re-measured 2026-09-19 (T156), as the last action before the
#                              # commit: +7. Seven pins added to
#                              # test_hrv_trend_series.py on the presentation
#                              # fallback formalised (AC9; F005's rule 3 over
#                              # datasets): clause 1's three terms (read last,
#                              # then n, then fidelity) and clauses 2 and 3
#                              # (densest baseline, densest week, each with its
#                              # fidelity tie). The precedence pins themselves
#                              # are in test_hrv_unavailable_reason.py, outside
#                              # the five. No identity elsewhere changed.
#                              # Nothing publishes this number; the previous
#                              # value was T158's, whose own note follows.
# SCOPED_SUITE_COLLECTED = 452  # re-measured 2026-09-19 (T158), as the last action before the
#                              # commit: +12. Twelve pins added to
#                              # test_hrv_trend_series.py on the withhold
#                              # retained at dataset scope (AC24): the
#                              # brand-new-device series, the zero / one /
#                              # thirteen / fourteen baseline-day boundary,
#                              # the week-day count at 3 and 2, the tied and
#                              # one-later day-order boundary, the carrier
#                              # recording through the return at c = 0 and 1
#                              # (T130's disarm, pinned as current), and the
#                              # skipped-set equality with select_dataset.
#                              # No identity elsewhere changed: T125/T132's
#                              # pins in the band, reset and unavailable-
#                              # reason suites were re-pointed in place.
#                              # Nothing publishes this number; the previous
#                              # value was T152's, whose own note follows.
# SCOPED_SUITE_COLLECTED = 440  # re-measured 2026-09-19 (T152), as the last action before the
#                              # commit: +2. Two pins added to
#                              # test_hrv_trend_series.py on F006's per-dataset
#                              # partition (the dual-capture morning feeding
#                              # both datasets with neither excluded anywhere,
#                              # and every span row accounted for exactly once
#                              # across the datasets and ``excluded``). No
#                              # identity elsewhere changed: the pins that
#                              # observed ``off_baseline_tier`` were re-pointed
#                              # in place, not added or deleted. Nothing
#                              # publishes this number; the previous value was
#                              # T155's, whose own note follows.
# SCOPED_SUITE_COLLECTED = 438  # re-measured 2026-09-19 (T155), as the last action before the
#                              # commit: +15. Fifteen pins added to
#                              # test_hrv_trend_series.py on F006's selection
#                              # (highest-fidelity judgeable dataset, the
#                              # baseline-window recency gate, its boundary
#                              # at the tolerance and +1, the simultaneous
#                              # reference set, the confidence-weight pin,
#                              # and the six adversarial probes plus a
#                              # hypothesis contract). One identity renamed,
#                              # not added: the T151 bridge pin is now
#                              # test_the_selected_view_hands_judge_the_dataset_the_selection_picks.
#                              # The build helpers were re-pointed at
#                              # selected_view, not added to. Nothing
#                              # publishes this number; the previous value
#                              # was T151's, whose own note follows.
# SCOPED_SUITE_COLLECTED = 423  # re-measured 2026-09-19 (T151), as the last action before the
#                              # commit: +4. Four pins added to
#                              # test_hrv_trend_series.py on F006's N-dataset
#                              # shape (a dataset per tier with its own band
#                              # and n; per-dataset collapse; the global gap
#                              # clipping every dataset alike; the T151
#                              # bridge). No identity elsewhere changed: the
#                              # suites' build helpers were re-pointed at the
#                              # bridge, not added to. Nothing publishes this
#                              # number; the previous value was T124's second
#                              # pass, whose own note follows.
# SCOPED_SUITE_COLLECTED = 419  # re-measured 2026-09-18 (T124, iteration 2), as the last action
#                              # before the commit: +3. Three pins added to this
#                              # suite: the walk's .shipyard* prune, and the two
#                              # absent-anchor pins (reach walk, anchor case).
#                              # The third absent-path pin went to
#                              # test_hrv_unavailable_causes.py, which is not
#                              # one of the five. Nothing publishes this
#                              # number; the previous value was T124's first
#                              # pass, whose own note follows.
# SCOPED_SUITE_COLLECTED = 416  # re-measured 2026-09-18 (T124), as the last action before the
#                              # commit: +1. T124 added
#                              # test_the_walk_reaches_every_mirrored_document
#                              # to this suite and no other identity changed:
#                              # the two breadcrumb anchor cases were
#                              # re-pointed at spec-mirror/, not added. Nothing
#                              # publishes this number; the previous value was
#                              # T147's, whose own note follows.
# SCOPED_SUITE_COLLECTED = 415  # re-measured 2026-09-18 (T147), as the last action before the
#                              # commit: +1. T147 added one pin to
#                              # test_hrv_trend_band.py -- the capture-density
#                              # walk -- and strengthened an existing
#                              # test_hrv_trend_reset.py test in place, which
#                              # changes no identity. Nothing publishes this
#                              # number; the previous value was T140's, whose
#                              # own note follows.
# SCOPED_SUITE_COLLECTED = 414  # re-measured 2026-09-18 (T140), as the last action before the
#                              # commit: +1. T140 added one pin to this file, the two-copies
#                              # assertion for unavailable_reason, and touched no other test's
#                              # identity here. Its other two parametrizations grew in
#                              # test_hrv_unavailable_causes.py, which is not one of the five
#                              # suites, so they do not reach this number. Nothing publishes this
#                              # literal; T138 left it at 413.

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

    ``RECENCY_TOLERANCE_DAYS``' comment, ``research/00`` GATE-07, F005's Negative
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
    assert len(WITHDRAWN_ORDER) == 30, (
        f"WITHDRAWN_ORDER has {len(WITHDRAWN_ORDER)} rows, not 30: a row and its"
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
#: than skips when an anchored file is not reached -- every anchored file,
#: since T124. The normative F005 and F006 documents used to be under the
#: second root only, scanned wherever the breadcrumb was and nowhere else;
#: T124 put committed copies under ``spec-mirror/``, so the first root reaches
#: them on every machine, and ``test_normative_mirror.py`` is the gate that
#: keeps each copy byte-equal to its data-dir original. On a machine with the
#: breadcrumb both copies are scanned, which is harmless duplication.
SCAN_ROOTS = (_REPO_ROOT, _REPO_ROOT / ".shipyard")

#: Floors on how many files each root must yield, by root index. Measured
#: 2026-09-15 on this tree: **125** files under the committed root (127 found,
#: two held out) and **139** under the breadcrumb (265 found, 126 held out as
#: history) -- so what a checkout without the breadcrumb scans is 125 files,
#: against the five the allowlist reached there. Re-measured 2026-09-18 (T124):
#: **137** under the committed root before the mirror and **143** with it --
#: the five copied documents and the mirror's README. Those numbers are a
#: measurement of one tree on one date. The floors below are the invariant,
#: and they sit under the measurement on purpose: their job is not to pin
#: a count -- ordinary growth and ordinary deletion must not trip them -- but
#: to catch the one failure a tree walk has that an allowlist does not, a walk
#: that has stopped descending and is reporting all-clear over nothing. The
#: root-0 floor was raised with the mirror; that the mirror itself is reached
#: is ``test_the_walk_reaches_every_mirrored_document``, not the floor.
SCAN_ROOT_FLOORS = (130, 100)

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

#: Machinery again, matched on a **directory-name prefix** at any depth. Each
#: row is ``(prefix, why)``. Detached mutation-testing worktrees (``.mut-T108``
#: and friends) are a second copy of the tree at some earlier commit, so every
#: hit in one is a duplicate of a hit already counted -- or a historical one.
#: ``.shipyard*`` is the breadcrumb under any name: T124's acceptance probe
#: hides the symlink by renaming it to ``.shipyard.probe-hidden`` inside the
#: repo root, and the name row above did not know that spelling, so the walk
#: descended into the data dir's historical files through the renamed link
#: and failed one phrasing sweep on the author machine. A breadcrumb is never
#: corpus, whatever it is called.
SCAN_EXCLUDED_DIR_PREFIXES = (
    (".mut-", "a detached mutation worktree: a second copy of the tree at an earlier commit"),
    (".shipyard", "the data-dir breadcrumb under any name, renamed or not; never corpus"),
)

#: Machinery a third way, matched on **what a directory holds** rather than on
#: what it is called: a directory that carries this entry is a nested git
#: checkout -- a second copy of the tree at some commit -- and the walk does
#: not descend into it. IDEA-072 is the measurement: the ``.mut-`` row above
#: already records that a second copy of the tree is never corpus, and the
#: builder worktrees Shipyard dispatches into ``.claude/worktrees/<agent>/``
#: are the same thing under a name no row knew, so a wave whose suite ran in
#: the main checkout while one builder was still live reported every
#: retraction quote in that builder's ``CHANGELOG.md`` and every literal in
#: its ``withdrawn_phrasings.py`` as a phantom offender (23 in T128's run, 37
#: in sprint-009 wave 3's). ``.claude`` itself is not pruned, because
#: ``.claude/rules/`` holds the graduated learnings and is live prose the
#: walk must keep reaching; what is pruned is the checkout, wherever it sits
#: and whatever it is called. The root's own ``.git`` is a file in a worktree
#: and a directory in the main checkout; neither is a child directory the
#: walk would descend into, so the root is never mistaken for a nested one.
SCAN_EXCLUDED_CHECKOUT_MARKER = (
    ".git",
    "a nested git checkout (a builder worktree, a mutation worktree, a clone): a second copy of the tree",
)

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

#: One of the two exclusions that are not history. ``withdrawn_phrasings.py`` holds the
#: literals the scan searches for, so reading it would match every one of them
#: by construction. It is kept honest by
#: ``test_the_unscanned_declaration_module_stays_a_declaration_table`` rather
#: than by trust.
SCAN_EXCLUDED_DECLARATIONS = (
    0,
    "runcoach-api/tests/support/withdrawn_phrasings.py",
    "it declares the phrasings the scan searches for; every literal in it is a declaration",
)

#: The other (F008, R4). ``research00_old_meanings.py`` holds research/00's
#: superseded wordings as literals for F011's sweep to search for, on the same
#: ground as ``withdrawn_phrasings.py``: it is a file of literals, not history.
#: It cannot sit in ``SCAN_EXCLUDED_DECLARATIONS``, whose AST rule allows
#: declarations only, and it holds a dataclass and ``normalize()``. What keeps
#: it from being a hiding place is
#: ``test_the_old_meanings_module_exposes_exactly_its_four_public_names`` in
#: ``test_research00_traceability.py``, which lives there so that no test is
#: added here (``SCOPED_SUITE_COLLECTED``).
SCAN_EXCLUDED_LITERALS = (
    0,
    "runcoach-api/tests/support/research00_old_meanings.py",
    "it holds research/00's old meanings as literals the sweeps search for; nothing in it is live prose",
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
    return _walk(SCAN_ROOTS[root_index])


def _walk(root: Path) -> tuple[Path, ...]:
    """The uncached walk behind ``_candidate_files``, over any root, so the
    prune tables can be exercised against a tree built for the purpose."""
    pruned = {name for name, _reason in SCAN_EXCLUDED_DIR_NAMES}
    prefixes = tuple(prefix for prefix, _reason in SCAN_EXCLUDED_DIR_PREFIXES)
    marker, _marker_reason = SCAN_EXCLUDED_CHECKOUT_MARKER
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in pruned and not d.startswith(prefixes) and not (Path(dirpath) / d / marker).exists()
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
    literals_root, literals_rel, _literals_why = SCAN_EXCLUDED_LITERALS
    kept: list[Path] = []
    for path in _candidate_files(root_index):
        rel = path.relative_to(root).as_posix()
        if _history_exclusion(root_index, rel) is not None:
            continue
        if root_index == declaring_root and rel == declaring_rel:
            continue
        if root_index == literals_root and rel == literals_rel:
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
#: is ``(path, a live phrase that file must contain)``.
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
#: Every row is required to exist and to be reached, on every machine. The
#: last two used to carry a ``committed: False`` flag -- they lived under the
#: gitignored breadcrumb and their cases skipped where it was absent, which
#: is to say everywhere but one laptop. T124 re-pointed them at the committed
#: copies under ``spec-mirror/`` and deleted the flag with the skip: a row
#: that may be absent is a row the scan may silently not read.
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
#: Re-anchored again on 2026-09-21 (T159) for ``schemas.py`` and for **this** file,
#: both of which gained a tail: the per-dataset response block and its field
#: descriptions, and the endpoint section that pins them. Their old anchors then
#: sat at 87.3% and 87.6% -- the same failure T138 hit, in the same way, two files
#: over -- and each was moved to the last live sentence of its file's new tail.
#: The 98% floor is what noticed both times; neither was found by reading.
WITHDRAWN_SCAN_ANCHORS = _DECLARATIONS.WITHDRAWN_SCAN_ANCHORS


def test_the_anchor_table_still_speaks_for_every_file_it_was_built_for() -> None:
    """A dropped row takes its parametrised case with it, so the remaining
    cases still pass and the count in the summary is the only trace. Pinned by
    name (T114): the parametrisation cannot notice its own absence. Under the
    walk a dropped row no longer stops the file being *scanned* -- the walk
    still reaches it -- it stops the file being proved *read*, which is the
    same false all-clear in slower motion."""
    assert [path.name for path, _anchor in WITHDRAWN_SCAN_ANCHORS] == [
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

    Every row fails rather than skips when absent, which is what turns "in a
    checkout without the breadcrumb the walk is the committed tree alone"
    from a sentence into a checked claim: all seven anchored files are reached
    on every machine, and the assertion below is what says so. Until T124 the
    two breadcrumb rows were ``continue``d over when absent, and that was the
    mechanism that let the two normative documents go unscanned everywhere
    but one laptop."""
    reached = set(_all_scanned_files())
    for path, _anchor in WITHDRAWN_SCAN_ANCHORS:
        assert path.exists(), f"{path} is committed and must be in every checkout"
        assert path in reached, (
            f"{path} is not in the walk: an exclusion pattern, a pruned directory or a "
            f"suffix has taken a file with live prose out of the scan"
        )


def test_the_walk_reaches_every_mirrored_document() -> None:
    """T124. The committed copies of the normative documents are the reason
    the scan no longer skips anywhere, so the walk over the committed root
    must reach every one of them -- by walking ``spec-mirror/``, not by naming
    the four this task copied, because T161 and T162 add to it. An exclusion
    pattern widened onto the mirror would otherwise hide the documents again
    while the anchors (which open their files directly) stayed green."""
    mirror = SCAN_ROOTS[0] / "spec-mirror"
    copies = sorted(path for path in mirror.rglob("*.md") if path.name != "README.md")
    assert len(copies) >= 4, f"{len(copies)} documents under {mirror}: the mirror T124 committed is not here"
    reached = set(_scanned_files(0))
    unreached = [str(path.relative_to(SCAN_ROOTS[0])) for path in copies if path not in reached]
    assert not unreached, f"mirrored documents the committed walk does not reach: {unreached}"


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
    """The declaration module is one of the two files held out for a
    non-historical reason (the other, ``SCAN_EXCLUDED_LITERALS``, is held to
    its four public names by ``test_research00_traceability.py``), so it is a
    place prose could sit unread -- which is exactly
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
    ("path", "anchor"),
    WITHDRAWN_SCAN_ANCHORS,
    ids=[path.name for path, _anchor in WITHDRAWN_SCAN_ANCHORS],
)
def test_the_withdrawn_reset_reason_phrasings_are_gone_from_every_live_copy(path: Path, anchor: str) -> None:
    """The positive half of the scan, one case per file whose live prose this
    feature's corrections had to reach: the anchor says this file's text was
    read to its tail, so the negative sweep above is a report over something
    rather than over an empty string. The phrasing check is repeated here
    because it reports per file, and because it is what fails first if a
    correction is reverted in one of the seven files that have carried one.
    An absent file fails here rather than skips (T124): the skip was what let
    two of the seven go unread on every machine but one."""
    assert path.exists(), f"{path} is committed and must be in every checkout"
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


#: A path no checkout carries, for the two absent-anchor pins below. Asserted
#: absent before each use so a pin cannot quietly start exercising a real file.
_ABSENT_ANCHOR_PATH = _REPO_ROOT / "spec-mirror" / "features" / "F999-in-no-checkout.md"


def test_the_walk_prunes_the_breadcrumb_under_any_name(tmp_path: Path) -> None:
    """T124's probe renames ``.shipyard`` to ``.shipyard.probe-hidden`` inside
    the repo root for the duration of the run. A prune table that knows the
    breadcrumb by its exact name only lets the walk descend through the
    renamed link into the data dir's history, and that was measured: one
    phrasing sweep failed on the author machine under the renamed link. Built
    against a tree of its own so the assertion is on the prune rule and not
    on what this machine happens to have at its root."""
    (tmp_path / "live.md").write_text("corpus\n", encoding="utf-8")
    for name in (".shipyard", ".shipyard.probe-hidden", ".shipyard-anything", ".mut-T999"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "history.md").write_text("never corpus\n", encoding="utf-8")
    (tmp_path / "nested" / ".shipyard.moved").mkdir(parents=True)
    (tmp_path / "nested" / ".shipyard.moved" / "history.md").write_text("never corpus\n", encoding="utf-8")
    assert _walk(tmp_path) == (tmp_path / "live.md",)


def test_the_walk_prunes_a_nested_checkout_but_not_the_directory_around_it(tmp_path: Path) -> None:
    """IDEA-072's pin. A builder worktree under ``.claude/worktrees/<agent>/``
    is a whole second copy of the tree, ``CHANGELOG.md`` and the declaration
    module included, and the walk reading it from the main checkout reported
    every quoted retraction in it as a live offender (sprint-009 wave 3, 37
    phantom hits in one builder's copy). The rule is on what a directory
    holds, not what it is called: a child carrying ``.git`` -- the file a
    worktree has, or the directory a clone has -- is pruned wherever it sits,
    and the directory around it is not, because ``.claude/rules/`` is live
    prose the walk must keep reaching. Built against a tree of its own so the
    assertion is on the prune rule and not on which builders happen to be
    live on this machine."""
    marker, _reason = SCAN_EXCLUDED_CHECKOUT_MARKER
    (tmp_path / "live.md").write_text("corpus\n", encoding="utf-8")
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    (tmp_path / ".claude" / "rules" / "learning.md").write_text("corpus\n", encoding="utf-8")
    worktree = tmp_path / ".claude" / "worktrees" / "agent-0123456789abcdef0"
    worktree.mkdir(parents=True)
    (worktree / marker).write_text("gitdir: elsewhere\n", encoding="utf-8")
    (worktree / "CHANGELOG.md").write_text("never corpus\n", encoding="utf-8")
    (worktree / "deep" / "tests").mkdir(parents=True)
    (worktree / "deep" / "tests" / "withdrawn.py").write_text("'never corpus'\n", encoding="utf-8")
    clone = tmp_path / "elsewhere" / "clone"
    (clone / marker).mkdir(parents=True)
    (clone / "CHANGELOG.md").write_text("never corpus\n", encoding="utf-8")
    (tmp_path / "elsewhere" / "sibling.md").write_text("corpus\n", encoding="utf-8")
    assert _walk(tmp_path) == (
        tmp_path / ".claude" / "rules" / "learning.md",
        tmp_path / "elsewhere" / "sibling.md",
        tmp_path / "live.md",
    )


def test_an_anchored_file_that_is_absent_fails_the_reach_walk_rather_than_skipping(monkeypatch) -> None:
    """The pin on T124's deletion of the ``continue``-on-absent-anchor path.
    Every anchored path exists in a committed checkout, so the deletion is
    invisible to the reach walk itself: put the ``continue`` back and the
    walk is green on every machine, absent rows silently stepped over. This
    drives one absent row through it and requires the failure."""
    assert not _ABSENT_ANCHOR_PATH.exists(), f"{_ABSENT_ANCHOR_PATH} exists; this pin needs an absent path"
    monkeypatch.setattr(
        sys.modules[__name__], "WITHDRAWN_SCAN_ANCHORS", ((_ABSENT_ANCHOR_PATH, "an anchor nothing carries"),)
    )
    with pytest.raises(AssertionError, match="must be in every checkout"):
        test_the_walk_reaches_every_file_an_anchor_speaks_for()


def test_an_anchored_file_that_is_absent_fails_its_anchor_case_rather_than_skipping() -> None:
    """The pin on T124's deletion of the ``pytest.skip`` in the parametrised
    anchor case. Same shape as the walk pin above: with every anchored file
    committed, a re-introduced ``if not path.exists(): pytest.skip(...)`` can
    never fire and leaves the parametrisation green, so nothing but this
    would notice it. A skip raised there is converted to a failure rather than
    allowed to escape, because a skip *is* the outcome this pin refuses."""
    assert not _ABSENT_ANCHOR_PATH.exists(), f"{_ABSENT_ANCHOR_PATH} exists; this pin needs an absent path"
    with pytest.raises(AssertionError, match="must be in every checkout"):
        try:
            test_the_withdrawn_reset_reason_phrasings_are_gone_from_every_live_copy(
                _ABSENT_ANCHOR_PATH, "an anchor nothing carries"
            )
        except pytest.skip.Exception as skipped:
            pytest.fail(f"an absent anchored file was skipped rather than failed: {skipped}")


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
    constrained by ``research/00`` HRV-38 and F005 rather than by the sentence
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
#: user decision of 2026-09-15 and from ``research/00`` PRIN-14, and each is
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
#: files -- so a restored asymmetric sentence in ``research/00`` HRV-34,
#: spec/03 §3.7.3, the construction reference or any docstring was invisible to it *by
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

#: The same treatment for ``unavailable_reason``, added by T140. T137
#: published this field -- twenty lines below ``verdict`` in the same schema
#: object -- as a **fourth** pair of hand-synchronised description copies, and
#: pinned neither the pair nor the prose. The six enum *members* are read by
#: value out of the module, the schema ``Literal`` and the YAML ``enum`` by
#: ``test_hrv_unavailable_reason``'s
#: ``test_the_six_unavailable_reason_names_are_the_same_six_in_the_module_the_schema_and_the_contract``,
#: so a **renamed member** does red -- but the sentences saying what each
#: member means, in what order they are reported, and which of them overrides
#: the rest were held by nothing. That is the exact failure mode the
#: ``verdict`` pin's own docstring names: ``check_drift.py`` compares path,
#: method, 2xx codes and required query parameters, never prose, so a
#: correction landing in one copy only is invisible to every other gate.
#:
#: Authorship (``contract-tables-need-an-independent-oracle``): these are not a
#: transcription of the paragraph. They are the four things a client reading
#: this field can act on -- the null invariant, that what it reports is the
#: *first* cause to fire rather than all that hold, the distinction between the
#: structural no-tier case and a resolved tier whose baseline is merely too
#: thin, and that ``day_not_happened`` is decided elsewhere and beats the other
#: five -- and each is reproduced against the running app by a named
#: behavioural pin rather than by this file: entry 1 by
#: ``test_hrv_unavailable_reason.test_unavailable_reason_is_null_whenever_the_verdict_is_asserted``,
#: entry 2 by ``..._reports_the_withheld_week_before_an_unestablished_baseline``,
#: entry 3 by ``..._no_tier_when_the_store_holds_no_reading_at_all`` and
#: ``..._no_band_when_a_resolved_tier_has_fewer_than_two_baseline_readings``,
#: entry 4 by ``test_a_future_to_and_a_pre_history_to_are_both_unavailable_with_200``
#: and ``test_a_to_a_few_days_ahead_with_a_full_window_asserts_no_verdict`` in
#: this file. This test constrains the **words**; nothing here fails because
#: ``judge`` changed, which is the division T112 named.
#:
#: No withdrawn tuple: nothing about this field has been retracted. That
#: machinery exists for claims that were published and became false, and
#: inventing an entry for it would put a digest in ``WITHDRAWN_ORDER`` pinning
#: a sentence no copy ever carried.
UNAVAILABLE_REASON_CLAIMS = (
    "null whenever it is not",
    "reports the first that fires",
    "a dataset was selected or presented, but its baseline holds fewer than two readings, so no band exists",
    "is decided at the route, after judge, and overrides whichever of the other five",
)

#: The run the two copies must state identically, from this anchor to the end.
#: It starts at the ordering sentence and not at the field's first word
#: **because the two copies genuinely differ before it**: the YAML parenthesis
#: cites ``research/00`` PRIN-12 and dates the closure, the schema copy states the
#: reproduce-it-by-hand property that section is about. Both are true, neither
#: is the rule. Everything from here on is the rule, and it is identical once
#: flattened -- which it was not before T140: the two copies punctuated the
#: ``day_not_happened`` clause differently (a dash in the YAML, a colon in the
#: schema), a divergence nothing in the tree could see because no pin compared
#: them.
UNAVAILABLE_REASON_SHARED_ANCHOR = (
    "judge evaluates four causes in a fixed order and reports the first that fires"
)

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
#: ``established`` can act on -- what the flag *is*, that **neither** verdict,
#: not just the suppression, is asserted beneath it, and what is emitted
#: instead -- taken from the 2026-09-15 user decision and ``research/00`` PRIN-14,
#: the same source ``VERDICT_CLAIMS`` is constrained by. Each is reproduced
#: against ``judge`` by a named behavioural pin rather than by this file:
#: ``test_hrv_trend_band.test_the_establishment_gate_flips_normal_at_exactly_fourteen_readings``
#: for the 14-reading boundary and for ``hrv_normal`` being withheld, and
#: ``test_hrv_trend_band.test_a_thin_baseline_inside_the_band_is_unavailable_not_normal``
#: for ``hrv_unavailable`` being what is emitted in its place.
ESTABLISHED_CLAIMS = (
    "n >= min_baseline_readings",
    "below it neither hrv_suppressed nor hrv_normal is asserted",
    "hrv_unavailable is the only verdict emitted",
    "unavailable_reason baseline_unestablished",
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
ESTABLISHED_SHARED_ANCHOR = "below it neither hrv_suppressed nor hrv_normal is asserted"


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
#:
#: **Inverted 2026-09-30 (T220, F010; C33 of 2026-09-23 reversed IDEA-070).**
#: The block now carries ``recency_tolerance_days`` as its seventh key, so
#: claims 2 and 3 state the new two-copy promise -- the tolerance is served
#: here and the selection is recomputable from the response -- in place of the
#: narrowing they pinned from 2026-09-15 to 2026-09-30. Claim 1 is unchanged.
#: The oracle below now requires the tier rule's remainder to be **empty**.
THRESHOLDS_CLAIMS = (
    "the band, verdict and dataset-selection constants",
    "the tolerance of the recency gate is served here as recency_tolerance_days",
    "selected_reason and baseline.tier are recomputable from the response alone",
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
#: is the second sentence's opening. Re-anchored 2026-09-30 (T220): the old
#: anchor, "they are not all of dataset selection", was the retracted narrowing.
THRESHOLDS_SHARED_ANCHOR = "the tolerance of the recency gate is served here"


#: Withdrawn by [[T141]] (review cycle 10, G-C10-2), the fifth withdrawal this
#: feature has had to record and the first that is not a false *rule* but a
#: false *quantity*. T138 answered the cycle-9 critic's question -- at what
#: point does a rule that mostly says nothing stop being conservative -- by
#: composing this feature's four silences against a rate, and published the
#: total into ``research/00`` (since withdrawn: FIG-05) and F005's Negative Class. Entry 1 is the
#: scenario that produced the largest term of that total: a same-tier device
#: replacement, which the rule **cannot detect** -- ``tier_change_reset`` keys
#: on ``hrv_source_tier`` and there is no notion of device identity anywhere in
#: the rule's vocabulary, so the event fires no reset and costs zero silent
#: days. Entry 2 is the headline the total was published as. Both are declared
#: rather than merely deleted because the user decision was to **withdraw the
#: figure, not re-derive it** (any replacement rate would assume how often a
#: real athlete crosses between the three tiers -- a judgement about people, in
#: the document that is this system's authority on measured facts), and a
#: figure that is never going to be recomputed is exactly the kind that comes
#: back by being re-typed from a task file or a review verdict.
#:
#: Entry 1 is the load-bearing one. The two numbers are arithmetic that could
#: in principle be re-derived for some other scenario; the scenario is the part
#: that names an event outside the rule's vocabulary, and a withdrawal that
#: struck the numbers and kept it would leave the defect in place.
#:
#: Neither entry is positional (see ``POSITIONAL_WITHDRAWN``): the task file
#: and the cycle-10 verdict that quote them as the thing being withdrawn are
#: both held out by ``SCAN_EXCLUDED_HISTORY``, so a bare-fragment match here is
#: a restoration and not a record.
SILENCE_RATE_WITHDRAWN = _DECLARATIONS.SILENCE_RATE_WITHDRAWN


#: Withdrawn by [[T163]] (2026-09-19) and put under the walk 2026-09-20
#: (sprint-006 wave-7 mutation pass, finding 1). The claim is that the 20-day
#: unestablished traverse ``judge``'s establishment gate opens onto is reached
#: after **either** reset this feature performs. It is true of a coverage gap,
#: which collapses the baseline deliberately, and false of a sustained tier
#: change, which collapses **nothing**: the outgoing tier keeps its 60-day
#: baseline, ``established`` stays true, ``n`` decays 60 -> 47, and the switch
#: traverses zero unestablished days. Its own quiet is 18 days by week
#: coverage, a different mechanism with ``reset_reason`` null throughout.
#:
#: Why it is here rather than in a probe. T138 measured the composition false
#: and corrected it at its three *document* sites, but carried
#: ``behaviour_change: false``, whose scope rule forbade any edit under
#: ``runcoach-api/src/`` -- so the sentence survived in the file the correction
#: was about. T163 corrected it there. Both tasks guarded it with a one-shot
#: ``! grep`` inside their own acceptance probe, and T163's had to be
#: whitespace-flattened to fire at all, because the live sentence wrapped
#: mid-phrase across two source lines ("...or a tier" / "change collapses...")
#: and the line-oriented form passed vacuously against the unfixed file. A
#: probe that runs once at acceptance is exactly how the claim survived T138;
#: the wave-7 mutant put the sentence back into ``judge`` and 95 tests passed.
#: These entries are what runs every time, over a flattened read, so neither
#: the wrap nor the one-shot matters again.
#:
#: The family, not one literal (entries in declaration order): 1 is the
#: sentence T163 removed; 2 is the same claim in the markup spelling the
#: document sites use, scoped by ``deliberately`` because the F005 mirror
#: carries the asterisked form followed by ``until now`` as T142's dated
#: retraction; 3 is it reordered; 4 is T142's plural spelling; 5-6 are the
#: hyphenated adjectival idiom in both orders -- entry 5 is the spelling
#: ``spec/06`` 6.2.4 cause (4) carried until T163, where the two kinds are
#: joined into one composed reset that the 20 days are said to follow; 7-10
#: are the quantifier compositions the claim is restated as when the two reset
#: kinds are not named at all.
#:
#: What they must not match, checked by running the walk: the corrected
#: sentences now live in ``judge``, ``research/00`` HRV-72, HRV-73 and
#: ``spec/03`` §3.7.3; the module docstring's *true* line 7, which joins the two reset
#: rules as the two things this module implements ("a baseline after a
#: coverage gap or a sustained tier change") and is why no entry here is the
#: bare conjunction; and the records that quote the claim as the thing being
#: withdrawn, each under its own dated correction -- the F005 mirror,
#: ``F005-decision-log.md``, ``spec/06``, ``test_hrv_trend_band.py`` and
#: ``test_hrv_unavailable_causes.py`` (``CHANGELOG.md`` is held out by
#: ``SCAN_EXCLUDED_HISTORY`` and needs no scoping). Every one of those quotes
#: carries the markup asterisks or a different subject, which is what the
#: entries are shaped around.
#:
#: None of them is positional (see ``POSITIONAL_WITHDRAWN``): each is anchored
#: on the *predicate* the claim is false about -- the baseline collapsing, the
#: reset being followed by the traverse -- rather than on a clause that only
#: one sentence's position supplies, so all ten sweep the whole walk.
RESET_COMPOSITION_WITHDRAWN = _DECLARATIONS.RESET_COMPOSITION_WITHDRAWN


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
    + SILENCE_RATE_WITHDRAWN
    + RESET_COMPOSITION_WITHDRAWN
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


#: T140. ``unavailable_reason``'s two copies differ in one more way than
#: ``verdict``'s do: ``schemas.py`` marks every field and enum member it names
#: as a **code span** (16 backticks) and the YAML copy marks none, so the two
#: publish one sentence in two markups. Same problem ``_TYPOGRAPHY`` was added
#: for, same answer -- fold, rather than re-type a published description into
#: its twin's conventions.
#:
#: It is **not** folded into ``_flat`` itself, which is the tempting move. That
#: would silently change ``_flat(WINDOW_WITHDRAWN[0])`` -- the one declared
#: phrasing carrying backticks, quoted in the schema copy's own spelling --
#: moving its ``WITHDRAWN_ORDER`` digest and collapsing that tuple's two
#: deliberately distinct spellings into one string searched twice. The fold
#: belongs to the comparison that needs it, not to every sweep in this file.
#: ``test_hrv_unavailable_causes.py``'s own ``_flat`` drops code spans for the
#: same reason, over documents where nothing is digested.
def _unmarked(text: str) -> str:
    """``_flat`` with markdown code spans dropped as well."""
    return _flat(text.replace("`", ""))


def test_the_two_copies_of_the_unavailable_reason_contract_publish_the_same_claims() -> None:
    """T140, deliverable 3: the same pin for the field ``verdict`` now points
    at, which shipped in T137 with its *members* pinned and its *prose* held by
    nothing.

    ``verdict`` and ``unavailable_reason`` are two fields of one schema object
    describing one rule, and T140 exists because they contradicted each other:
    ``verdict`` enumerated four causes, ``unavailable_reason`` twenty lines
    below it published six, and every gate in the tree was green -- the pin
    above passed **because both copies of ``verdict`` were wrong identically**,
    which is what a pin comparing two copies and nothing else can always do.
    That is why T140's other deliverable points
    ``test_hrv_unavailable_causes.py``'s oracle at both copies: this test holds
    the two copies to each other, that one holds them to ``judge``, and neither
    is sufficient alone.

    The failure modes, each run to confirm this goes red on it: (1) a claim
    dropped from either copy; (2) the correction landed in one copy only, which
    the shared run catches even where both copies still carry every claim.
    """
    target = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    contract = _unmarked(
        target["components"]["schemas"]["HrvTrend"]["properties"]["unavailable_reason"]["description"]
    )
    served = _unmarked(
        app.openapi()["components"]["schemas"]["HrvTrendResponse"]["properties"]["unavailable_reason"][
            "description"
        ]
    )

    for claim in UNAVAILABLE_REASON_CLAIMS:
        flat = _unmarked(claim)
        assert flat in contract, f"contracts/openapi.yaml no longer publishes: {claim}"
        assert flat in served, f"schemas.HrvTrendResponse.unavailable_reason no longer publishes: {claim}"

    start = contract.find(_unmarked(UNAVAILABLE_REASON_SHARED_ANCHOR))
    assert start != -1, "the contract's shared run no longer starts where the anchor says"
    shared = contract[start:]
    assert shared in served, (
        "the two copies of unavailable_reason have stopped stating the cause order in the same "
        "words; T137 wrote both and pinned neither. The contract says: " + shared
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

    **Inverted 2026-09-30 (T220, F010).** C33 (2026-09-23) reversed IDEA-070:
    the constant is published as the block's seventh key, so ``THRESHOLDS_CLAIMS``
    now states that promise and ``THRESHOLDS_SHARED_ANCHOR`` starts the new
    second sentence. The three failure modes above are unchanged in shape;
    ``THRESHOLDS_WITHDRAWN`` still holds the pre-IDEA-070 "heuristic constants"
    promise, which remains retracted -- the block is not every heuristic the
    verdict uses, it is the seven the band, the verdict and the tier rule apply.
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
    remainder then empties and, before T220, this was red, so the shape was
    held from this side too (since T220 the empty remainder is the asserted
    state; see the inversion below).

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
    (``recency_tolerance_days`` published in both copies) -- red until T220,
    remainder empty (since T220 that is the asserted state); a second
    unpublished constant entering the tier rule
    (``WINDOW_DAYS`` applied in ``_recency_struck`` -- deliberately in the
    **factored helper**, where the pre-T125 oracle could not have seen it) --
    red, remainder ``['recency_tolerance_days', 'window_days']``; and the name struck
    from the served description -- red on ``schemas.Thresholds`` -- and from the
    YAML copy -- red on ``contracts/openapi.yaml``.

    **Inverted 2026-09-30 (T220, F010).** C33 (2026-09-23) reversed IDEA-070 and
    the block gained ``recency_tolerance_days`` as its seventh key, so the
    remainder the oracle computes -- the constants the tier rule applies less
    the keys the block publishes -- must now be **empty** in both copies. The
    oracle is unchanged: it still walks the tier rule's call graph, so it
    reddens on a second unpublished constant entering the rule (the
    ``WINDOW_DAYS`` mutation above still fires) and on the seventh key being
    struck from either copy, which is the state this inversion replaced. The
    description check is kept in its published form: both copies must name the
    served key, so a block that carries the key and describes it as absent is
    red too.
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
            f"{label}: the description calls these the band, verdict and dataset-selection "
            f"constants, but the tier rule applies none of the published keys {sorted(keys)}"
        )
        omitted = applied - keys
        assert omitted == set(), (
            f"{label}: the tier rule ({', '.join(sorted(reached))}) applies {sorted(applied)} "
            f"and the block publishes {sorted(keys)}, so the constants it applies and does not "
            f"echo are {sorted(omitted)} -- since T220 (C33) the block serves every one of them"
        )
        description = _flat(block["description"])
        assert "recency_tolerance_days" in description, (
            f"{label} serves recency_tolerance_days but its description does not name it: the "
            f"block's description is a false account of what it carries"
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
    # F006's selection, as the route itself applies it (T155).
    series = hrv_trend.selected_view(hrv_trend.build_series(rows, ZoneInfo("UTC"), to))
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
      chain -- six of the seven (the snapshot era's rows, which shipped F005
      listed ``off_baseline_tier`` here, are the snapshot's own dataset
      under F006 and appear in no list; T152 retired the reason and its
      published value with it);
    * a snapshot era, a 27-day silence and a resumption -- the seventh.

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


# ---------------------------------------------------------------------------
# F006 T159: datasets[], selected_dataset, selected_reason, disagreed_with,
# and the dataset each point's band came from
#
# AC10, AC12, AC13, AC14, AC15. ``baseline`` and ``band`` keep their names and
# now mean *the selected dataset's*; four fields are added beside them, and
# ``points[]`` gains the dataset its band came from because selection runs per
# local day.
#
# What these pins are for, stated once. Every row of F006's Negative Class
# that names who notices a residual answers "nobody from the response" and
# then names ``datasets[]`` as the thing that would show it: the non-selected
# dataset's reported reset (AC17/T154), the covering dataset's ``n`` and week
# count under the presentation fallback (T156), the returning dataset's latest
# baseline-window day behind a stale band (AC17's second residual), and the
# per-dataset side against its own band that IDEA-087's sign-agreement rate
# would be estimated from. This section is where those become reachable, so
# each pin asserts the *presence and value* of a field, never merely that
# something is absent.
# ---------------------------------------------------------------------------


def _by_tier(body: dict) -> dict[str, dict]:
    """``datasets[]`` keyed by tier, having first asserted the list's order.

    The key is the tier (F006 reference §1: the dataset key is the tier, not
    the device), so keying cannot collide -- ``select_dataset`` refuses a
    series carrying two datasets of one tier rather than resolving it by
    position. The order is fidelity order, highest first, which is the order
    ``selected_reason`` is decided in and the order ``disagreed_with`` is
    reported in, so it is asserted here rather than left to each caller."""
    datasets = body["datasets"]
    ranks = [d["fidelity_rank"] for d in datasets]
    assert ranks == sorted(ranks), datasets
    assert [d["tier"] for d in datasets] == [
        hrv_trend.TIER_FIDELITY[rank] for rank in ranks
    ], datasets
    return {d["tier"]: d for d in datasets}


def straps_at(seeder: Seeder, dates: Iterable[date], hh: int = 7) -> list[Session]:
    """One declared strap capture per local day at ``hh``:00 UTC.

    07:00 rather than ``Seeder.straps``'s 06:00 because every synthetic file
    shares one ``source_device``, so a strap capture at the snapshot's own
    instant derives the same session id and is the same session."""
    return [seeder.strap(at(day, hh=hh)) for day in dates]


def test_both_tiers_are_rendered_with_their_own_band_and_n_and_a_selected_reason(configure, seeder) -> None:
    """**T159's first failing test.** A response body carries ``datasets[]``
    with a band and ``n`` for *both* tiers and a non-null ``selected_reason``
    -- red before this task, where the schema has neither field.

    A daily strap and a daily snapshot over the same 67 local days: both
    datasets are established and judgeable, the strap is selected on fidelity
    (AC5), and the snapshot -- the loser -- still reports its own band, its
    own ``n`` and its own ``established`` (AC2). Under shipped F005 one tier
    owned the only band and the other tier's 61 readings were listed
    ``off_baseline_tier``; there was no field of the response they could be
    read from, which is what this block is.

    The two bands are asserted **different**: the point of per-tier baselining
    is that the loser keeps its own yardstick, and a renderer that copied the
    presented band onto every dataset would pass every other assertion here.
    """
    configure("UTC")
    span = days(D - timedelta(days=66), D)
    seeder.snapshots(span, baseline_values(len(span)))
    straps_at(seeder, span)
    seeder.persist()

    with TestClient(app) as client:
        response = get(client, to=D.isoformat())

    assert response.status_code == 200, response.text
    body = response.json()
    datasets = _by_tier(body)
    print("datasets:", json.dumps(body["datasets"], indent=1))
    print("selected:", body["selected_dataset"], body["selected_reason"], body["disagreed_with"])

    assert set(datasets) == {STRAP, SNAPSHOT}
    assert [d["tier"] for d in body["datasets"]] == [STRAP, SNAPSHOT]
    for tier, rank in ((STRAP, 0), (SNAPSHOT, 1)):
        entry = datasets[tier]
        assert entry["n"] == 60, entry
        assert entry["established"] is True, entry
        assert entry["week_days"] == 7, entry
        assert entry["last_read"] == (D - timedelta(days=7)).isoformat(), entry
        assert entry["fidelity_rank"] == rank, entry
        assert entry["reset_on"] is None and entry["reset_reason"] is None, entry
        assert set(entry["band"]) == {"mean", "half_width", "lo", "hi", "floored"}, entry
        assert entry["below"] is False, entry
        assert entry["week_mean"] is not None, entry

    assert body["selected_dataset"] == STRAP
    assert body["selected_reason"] == hrv_trend.SELECTED_HIGHEST_FIDELITY
    assert body["verdict"] == "hrv_normal"
    assert body["disagreed_with"] == []

    # ``baseline``/``band`` are the selected dataset's, and the loser's band is
    # its own, not a copy.
    assert body["baseline"]["tier"] == STRAP
    assert body["baseline"]["n"] == datasets[STRAP]["n"]
    assert body["baseline"]["established"] == datasets[STRAP]["established"]
    assert body["band"] == datasets[STRAP]["band"]
    assert body["ln_rmssd_7d_mean"] == pytest.approx(datasets[STRAP]["week_mean"])
    assert body["readings_in_window"] == datasets[STRAP]["week_days"]
    assert datasets[SNAPSHOT]["band"]["mean"] != datasets[STRAP]["band"]["mean"]
    assert abs(datasets[SNAPSHOT]["band"]["mean"] - datasets[STRAP]["band"]["mean"]) > 0.5


def test_a_dataset_with_no_band_is_visible_carrying_its_n_and_its_judged_week_count(
    configure, seeder
) -> None:
    """AC10's second sentence, which nothing else in the response provides: a
    dataset holding fewer than two baseline readings has no band, cannot
    disagree with anything, and is instead visible in ``datasets[]`` carrying
    its ``n`` **and its judged-week count**.

    The judged-week count is the field T157 exposes as ``BandReading.week_days``
    and the only place it reaches a consumer. A strap read once inside the
    baseline window and on the last three mornings: ``n`` 1, ``band`` null,
    ``below`` null (it cannot be on either side of a band it does not have),
    ``week_days`` 3 -- and it is *not* named in ``disagreed_with``, which is
    the half of AC10's sentence a renderer could get wrong by treating a null
    ``below`` as "not below"."""
    configure("UTC")
    seeder.snapshots(days(D - timedelta(days=66), D), baseline_values(67))
    straps_at(seeder, [D - timedelta(days=20)])
    straps_at(seeder, days(D - timedelta(days=2), D))
    seeder.persist()

    with TestClient(app) as client:
        body = get(client, to=D.isoformat()).json()

    datasets = _by_tier(body)
    print("datasets:", json.dumps(body["datasets"], indent=1))
    assert datasets[STRAP]["n"] == 1
    assert datasets[STRAP]["band"] is None
    assert datasets[STRAP]["below"] is None
    assert datasets[STRAP]["week_days"] == 3
    assert datasets[STRAP]["week_mean"] is not None
    assert datasets[STRAP]["established"] is False
    assert datasets[STRAP]["last_read"] == (D - timedelta(days=20)).isoformat()
    assert body["disagreed_with"] == []

    assert datasets[SNAPSHOT]["n"] == 60
    assert datasets[SNAPSHOT]["band"] is not None
    assert datasets[SNAPSHOT]["week_days"] == 7
    assert body["selected_dataset"] == SNAPSHOT
    assert body["selected_reason"] == hrv_trend.SELECTED_HIGHEST_FIDELITY


# ---------------------------------------------------------------------------
# F010 AC3 (T221): the recency skip is recomputable from the response alone,
# at the exact boundary, pinned by PRIN-12 and HRV-17
# ---------------------------------------------------------------------------


#: The two boundary geometries, as ``behind`` days between the strap's latest
#: baseline-window read and the snapshot's: exactly the tolerance is kept, one
#: day past it is skipped. They are the only two points where ``>`` and ``>=``
#: disagree, so a served tolerance of any other value fails one of them.
RECENCY_BOUNDARY_CASES = (
    (hrv_trend.RECENCY_TOLERANCE_DAYS, False),
    (hrv_trend.RECENCY_TOLERANCE_DAYS + 1, True),
)


def _recompute_selection(body: dict, tolerance: int) -> tuple[str | None, str | None, dict[str, int]]:
    """``(selected_dataset, selected_reason, gap per tier)`` recomputed from
    ``datasets[]`` and ``tolerance`` alone, as HRV-15 and HRV-19 state the
    rule: candidates are the judgeable datasets (established, and at least
    ``min_window_readings`` judged-week days); the reference is the latest
    ``last_read`` over every **established** dataset; a candidate whose gap
    is strictly more than ``tolerance`` is skipped; the highest-fidelity
    survivor is selected, ``higher_fidelity_skipped_stale`` when a skipped
    candidate outranks it and ``highest_fidelity_judgeable`` otherwise."""
    datasets = body["datasets"]
    established = [d for d in datasets if d["established"]]
    reference = max(date.fromisoformat(d["last_read"]) for d in established)
    gaps = {d["tier"]: (reference - date.fromisoformat(d["last_read"])).days for d in established}
    judgeable = [
        d for d in sorted(datasets, key=lambda d: d["fidelity_rank"])
        if d["established"] and d["week_days"] >= body["thresholds"]["min_window_readings"]
    ]
    skipped_first = False
    for d in judgeable:
        if gaps[d["tier"]] > tolerance:
            skipped_first = True
            continue
        reason = hrv_trend.SELECTED_HIGHER_FIDELITY_STALE if skipped_first else hrv_trend.SELECTED_HIGHEST_FIDELITY
        return d["tier"], reason, gaps
    return None, None, gaps


@pytest.mark.parametrize(("behind", "expect_skipped"), RECENCY_BOUNDARY_CASES, ids=["exactly_the_tolerance", "one_past_it"])
def test_the_recency_skip_is_recomputable_from_the_response_at_the_exact_boundary(
    configure, seeder, behind: int, expect_skipped: bool
) -> None:
    """F010 AC3, the Pinned test of PRIN-12 and HRV-17: with the tolerance
    served as ``thresholds.recency_tolerance_days``, a reader holding only
    ``datasets[].last_read`` and that one number reproduces ``selected_dataset``
    and ``selected_reason`` at the exact boundary in both directions.

    The geometry is ``test_the_gate_boundary_is_strictly_greater_than_the_tolerance``
    (the series suite) through ``db.persist`` and ``GET /metrics/hrv``: a
    daily snapshot ``D-66..D``, the strap on ``D-66..strap_last`` plus the
    three week days ``D-4, D-2, D`` (judgeable), with ``strap_last`` exactly
    ``behind`` days before the snapshot's ``D-7``. The gap is measured on the
    **baseline window's** last reads, never the judged week, which is what
    ``last_read`` serves. Exactly the tolerance behind is kept (the strap,
    ``highest_fidelity_judgeable``); one day further is skipped (the snapshot,
    ``higher_fidelity_skipped_stale``), so the two cases differ.

    **Perturbation clause** (AC3's last line): the same recomputation with
    ``tolerance - 1`` or ``tolerance + 1`` no longer matches what was served
    in at least one of the two cases, so a served tolerance of any value but
    the module constant fails this test rather than passing it vacuously."""
    configure("UTC")
    strap_last = D - timedelta(days=7) - timedelta(days=behind)
    seeder.snapshots(days(D - timedelta(days=66), D), baseline_values(67))
    straps_at(seeder, days(D - timedelta(days=66), strap_last))
    straps_at(seeder, [D - timedelta(days=4), D - timedelta(days=2), D])
    seeder.persist()

    with TestClient(app) as client:
        response = get(client, to=D.isoformat())
    assert response.status_code == 200, response.text
    body = response.json()
    datasets = _by_tier(body)

    tolerance = body["thresholds"]["recency_tolerance_days"]
    assert isinstance(tolerance, int) and not isinstance(tolerance, bool), body["thresholds"]
    # Both tiers judgeable by construction; the strap's baseline-window last
    # read is exactly ``behind`` days before the snapshot's.
    assert datasets[STRAP]["established"] is True and datasets[STRAP]["week_days"] == 3, datasets[STRAP]
    assert datasets[SNAPSHOT]["established"] is True and datasets[SNAPSHOT]["week_days"] == 7, datasets[SNAPSHOT]
    assert datasets[STRAP]["last_read"] == strap_last.isoformat(), datasets[STRAP]
    assert datasets[SNAPSHOT]["last_read"] == (D - timedelta(days=7)).isoformat(), datasets[SNAPSHOT]

    selected, reason, gaps = _recompute_selection(body, tolerance)
    served = (body["selected_dataset"], body["selected_reason"])
    outcome = "skipped" if selected == SNAPSHOT else "kept"
    print(f"[slice compared] tolerance={tolerance} gap={gaps[STRAP]} -> {outcome}; served={served}")

    assert gaps[STRAP] == behind, gaps
    assert (selected, reason) == served, (selected, reason, served, gaps)
    expected = (SNAPSHOT, hrv_trend.SELECTED_HIGHER_FIDELITY_STALE) if expect_skipped else (STRAP, hrv_trend.SELECTED_HIGHEST_FIDELITY)
    assert served == expected, (served, expected, gaps)
    # The two parametrized cases are opposite outcomes: the boundary is
    # strict on the served number, not somewhere near it.
    assert {skipped for _, skipped in RECENCY_BOUNDARY_CASES} == {False, True}

    # Perturbation: one of tolerance +- 1 flips this case's recomputation away
    # from what was served (tolerance - 1 flips the kept case, tolerance + 1
    # the skipped one), so the served value is the only one this test accepts.
    flipped = {
        t: _recompute_selection(body, t)[:2] != served for t in (tolerance - 1, tolerance + 1)
    }
    print(f"[perturbation] {flipped}")
    assert flipped[tolerance - 1 if not expect_skipped else tolerance + 1] is True, flipped
    assert any(flipped.values()), flipped


def test_selected_reason_is_null_exactly_when_selected_dataset_is_and_the_fallback_still_populates(
    configure, seeder
) -> None:
    """AC13's null half and AC12's whole claim, on one seeding read on two
    days. A daily snapshot ending at ``D-7``: judged from ``D-7`` the week is
    full and the dataset is selected; judged from ``D`` the judged week
    ``[D-6, D]`` is empty, nothing is judgeable, and **no dataset is
    selected**.

    On that day the pair is null together -- which is T144's shape, kept
    closed: ``selected_reason`` is derived from the selection rather than
    stored beside it, so it cannot be non-null beside a null
    ``selected_dataset``. And every field that was non-nullable in the
    contract before F006 still carries a value, because AC9's presentation
    fallback populates ``baseline`` and ``band`` from the dataset the athlete
    was read on last: ``baseline.n`` 60, ``established`` true, a band, and
    ``week_too_thin`` computed on that dataset's own ``n``. That is what makes
    this addition additive rather than a breaking change.

    ``disagreed_with`` is empty here **by rule, not by accident**: the
    fallback presents and never judges, so there is no verdict to disagree
    with (IDEA-082, settled by T156). ``datasets[]`` still shows the dataset's
    own side, which is the only reason that state is legible at all."""
    configure("UTC")
    seeder.snapshots(days(D - timedelta(days=66), D - timedelta(days=7)), baseline_values(60))
    seeder.persist()

    with TestClient(app) as client:
        unselected = get(client, to=D.isoformat()).json()
        selected = get(client, to=(D - timedelta(days=7)).isoformat()).json()

    for body in (unselected, selected):
        assert (body["selected_dataset"] is None) == (body["selected_reason"] is None), body

    print("no selection:", unselected["selected_dataset"], unselected["selected_reason"])
    print("datasets:", json.dumps(unselected["datasets"], indent=1))
    assert unselected["selected_dataset"] is None
    assert unselected["selected_reason"] is None
    assert unselected["disagreed_with"] == []
    assert unselected["verdict"] == "hrv_unavailable"
    assert unselected["unavailable_reason"] == "week_too_thin"
    # AC12: nothing non-nullable before F006 became nullable.
    assert unselected["baseline"]["tier"] == SNAPSHOT
    assert unselected["baseline"]["n"] == 60
    assert unselected["baseline"]["established"] is True
    assert unselected["baseline"]["window"] == [
        (D - timedelta(days=66)).isoformat(),
        (D - timedelta(days=7)).isoformat(),
    ]
    assert unselected["band"] is not None
    datasets = _by_tier(unselected)
    assert set(datasets) == {SNAPSHOT}
    assert datasets[SNAPSHOT]["n"] == 60
    assert datasets[SNAPSHOT]["week_days"] == 0
    assert datasets[SNAPSHOT]["week_mean"] is None
    assert datasets[SNAPSHOT]["below"] is None
    assert datasets[SNAPSHOT]["band"] == unselected["band"]

    assert selected["selected_dataset"] == SNAPSHOT
    assert selected["selected_reason"] == hrv_trend.SELECTED_HIGHEST_FIDELITY
    assert _by_tier(selected)[SNAPSHOT]["week_days"] == 7


def test_the_response_says_the_verdict_fell_to_a_lower_tier_and_the_gate_is_reproducible(
    configure, seeder
) -> None:
    """AC13's second member, and the only field in the response that says a
    fall happened. A strap established over ``[D-66, D-40]`` and read again on
    the last three mornings, beside a daily snapshot: the strap is judgeable
    and the highest fidelity, and the recency gate skips it because its latest
    reading **inside the baseline window** is ``D-40``, 33 days behind the
    snapshot's ``D-7`` and more than ``recency_tolerance_days`` (28).

    ``selected_reason`` is therefore ``higher_fidelity_skipped_stale`` rather
    than ``highest_fidelity_judgeable``: the verdict is being taken from the
    lower-fidelity instrument and the response says so. Without this member
    the two cases are indistinguishable from outside, and the athlete judged
    against the watch while wearing the strap has no way to see it.

    The gate itself is recomputed here from the rendered fields alone --
    ``last_read`` per dataset, ``established``, ``week_days`` -- which is the
    ``research/00`` PRIN-12 obligation the response carries for every other rule
    it applies. The tolerance is read here from the module constant; since T220
    (C33, 2026-09-23, reversing IDEA-070) the response serves the same value as
    ``thresholds.recency_tolerance_days``, pinned equal to the module constant by
    ``test_the_endpoint_reports_every_input_that_produced_the_verdict``."""
    configure("UTC")
    straps_at(seeder, days(D - timedelta(days=66), D - timedelta(days=40)))
    straps_at(seeder, days(D - timedelta(days=2), D))
    seeder.snapshots(days(D - timedelta(days=66), D), baseline_values(67))
    seeder.persist()

    with TestClient(app) as client:
        body = get(client, to=D.isoformat()).json()

    datasets = _by_tier(body)
    print("datasets:", json.dumps(body["datasets"], indent=1))
    print("selected:", body["selected_dataset"], body["selected_reason"])

    assert body["selected_dataset"] == SNAPSHOT
    assert body["selected_reason"] == hrv_trend.SELECTED_HIGHER_FIDELITY_STALE
    assert body["baseline"]["tier"] == SNAPSHOT

    strap, snapshot = datasets[STRAP], datasets[SNAPSHOT]
    assert strap["established"] is True and strap["week_days"] >= hrv_trend.MIN_WINDOW_READINGS
    assert strap["last_read"] == (D - timedelta(days=40)).isoformat()
    assert snapshot["last_read"] == (D - timedelta(days=7)).isoformat()
    # The gate, recomputed from the response: the reference is the greatest
    # ``last_read`` over the ESTABLISHED datasets (T164), strictly greater than
    # the tolerance is skipped.
    reference = max(date.fromisoformat(d["last_read"]) for d in body["datasets"] if d["established"])
    behind = (reference - date.fromisoformat(strap["last_read"])).days
    assert behind == 33
    assert behind > hrv_trend.RECENCY_TOLERANCE_DAYS
    assert (reference - date.fromisoformat(snapshot["last_read"])).days == 0


@pytest.mark.parametrize(
    ("week", "expected_week_days"),
    [(7, 7), (1, 1)],
    ids=["a_full_dissenting_week", "a_one_morning_dissenting_week"],
)
def test_disagreed_with_names_the_dissenter_and_the_judged_week_count_that_weighs_it(
    configure, seeder, week: int, expected_week_days: int
) -> None:
    """AC10 and AC11 through the response, with T157's own finding rendered:
    a judged week of **one** morning can name a dissenter, so the count is
    reported beside the name and the consumer can weigh it.

    A daily strap is selected and reads within its own band; a snapshot with a
    20-day baseline reads 25 ms across its judged week, well below its own
    band's ``lo``. It is named in ``disagreed_with`` in both parametrised
    cases -- with a full week, where it is judgeable, and with a single
    morning, where it is not -- because judgeability is never consulted for
    the naming (AC10, taken literally).

    ``verdict`` is ``hrv_normal`` in both: **disagreement never overrides**
    (AC11). This is HRV-25's population -- the named exception PRIN-15
    lists, owned by IDEA-099 -- rendered rather than denied: up-regulation
    while contrary evidence exists, and ``disagreed_with``
    is the whole of what the response says about it, which is why the count
    matters: one 25 ms morning and seven of them are very different evidence
    behind the same name.

    The dissent is recomputable by hand from the block (``research/00`` PRIN-12):
    the snapshot's ``week_mean`` is strictly below its own ``band.lo`` while
    the strap's is not below its own."""
    configure("UTC")
    straps_at(seeder, days(D - timedelta(days=66), D))
    seeder.snapshots(days(D - timedelta(days=26), D - timedelta(days=7)), baseline_values(20))
    seeder.snapshots(days(D - timedelta(days=week - 1), D), repeat(25.0))
    seeder.persist()

    with TestClient(app) as client:
        body = get(client, to=D.isoformat()).json()

    datasets = _by_tier(body)
    print("datasets:", json.dumps(body["datasets"], indent=1))
    print("disagreed_with:", body["disagreed_with"], "verdict:", body["verdict"])

    assert body["selected_dataset"] == STRAP
    assert body["verdict"] == "hrv_normal"
    assert body["disagreed_with"] == [{"dataset": SNAPSHOT, "week_days": expected_week_days}]

    assert datasets[SNAPSHOT]["week_days"] == expected_week_days
    assert datasets[SNAPSHOT]["below"] is True
    assert datasets[SNAPSHOT]["week_mean"] < datasets[SNAPSHOT]["band"]["lo"]
    assert datasets[STRAP]["below"] is False
    assert datasets[STRAP]["week_mean"] >= datasets[STRAP]["band"]["lo"]
    # The named dataset's count is the one the block reports for it, not a
    # second derivation: a renderer pairing the name with the wrong dataset's
    # week would pass the membership assertion alone.
    assert body["disagreed_with"][0]["week_days"] == datasets[SNAPSHOT]["week_days"]
    assert body["disagreed_with"][0]["week_days"] != datasets[STRAP]["week_days"] or expected_week_days == 7


def test_a_conferred_suppressed_verdict_names_a_dissenter_too(configure, seeder) -> None:
    """Cycle-2 review, S4: the served dissent rule is keyed on **no verdict
    conferred** (``research/00`` HRV-22, amended 2026-09-21, T167), not
    on the verdict being ``hrv_normal``. Every other served non-empty
    ``disagreed_with`` pin sits on an ``hrv_normal`` day, so a predicate
    over-broadened to ``verdict != VERDICT_NORMAL`` -- which also empties the
    list on a conferred ``hrv_suppressed`` -- passed all of them.

    The dissent fixture mirrored: the daily strap is selected and this time
    reads **below** its own band (a 38/44 ms baseline over ``[D-66, D-7]``,
    25 ms across the judged week), so the verdict is conferred and is
    ``hrv_suppressed``; the snapshot's 20-day 38/44 baseline is read against a
    41 ms week, **within** its own band -- the other side from the selected
    dataset -- so it is named, with its seven-morning count (AC10, AC11)."""
    configure("UTC")
    strap_baseline = days(D - timedelta(days=66), D - timedelta(days=7))
    for day, value in zip(strap_baseline, baseline_values(len(strap_baseline))):
        seeder.strap(at(day, hh=7), rmssd=value)
    for day in days(D - timedelta(days=6), D):
        seeder.strap(at(day, hh=7), rmssd=25.0)
    seeder.snapshots(BASELINE_20, baseline_values(20))
    seeder.snapshots(days(D - timedelta(days=6), D), repeat(41.0))
    seeder.persist()

    with TestClient(app) as client:
        body = get(client, to=D.isoformat()).json()

    datasets = _by_tier(body)
    print("datasets:", json.dumps(body["datasets"], indent=1))
    print("disagreed_with:", body["disagreed_with"], "verdict:", body["verdict"])

    assert body["selected_dataset"] == STRAP
    assert body["verdict"] == "hrv_suppressed"
    assert body["unavailable_reason"] is None
    assert body["disagreed_with"] == [{"dataset": SNAPSHOT, "week_days": 7}]

    assert datasets[STRAP]["below"] is True
    assert datasets[STRAP]["week_mean"] < datasets[STRAP]["band"]["lo"]
    assert datasets[SNAPSHOT]["below"] is False
    assert datasets[SNAPSHOT]["week_mean"] >= datasets[SNAPSHOT]["band"]["lo"]


def test_a_future_day_names_no_dissenter_because_no_verdict_was_conferred(
    configure, seeder, monkeypatch
) -> None:
    """F006 x ``_withhold_future`` (sprint-006 review iteration 1, M2).

    **One of three, not the rule itself (T167, ``B-CR-002``, 2026-09-21).** The
    served list is empty wherever ``verdict`` is ``hrv_unavailable``, for any
    cause -- ``research/00`` HRV-22 as amended, restated in
    ``spec/03`` §3.7.4. M2 guarded this state alone and left the neighbouring one, a
    **selected** dataset whose verdict is withheld (``week_not_representative``),
    naming a dissenter; that state is pinned by
    ``test_hrv_dataset_populations.test_a_withheld_verdict_names_no_dissenter_and_a_conferred_one_still_does``.
    What follows is this day's case and its argument, which the general rule
    subsumes rather than replaces.

    ``_withhold_future`` replaces the verdict with ``hrv_unavailable`` /
    ``day_not_happened`` for a day after the athlete's local today, but
    ``datasets[]``, ``selected_dataset``, ``selected_reason`` and
    ``disagreed_with`` are built from ``series.selection``, **which never
    sees the clock**. Left alone, a response for ``to = today + 1`` carried
    ``verdict: hrv_unavailable`` and ``below_by: null`` and, beside them,
    ``disagreed_with: [{dataset: health_snapshot, ...}]`` -- a dissenter
    named against a verdict that was withheld.

    That contradicts two published statements and F006's own reasoning in a
    third: ``schemas.disagreed_with`` says "**Disagreement never overrides**:
    ``verdict`` is the selected dataset's, unchanged" (here it was the
    clock's), and ``hrv_trend.disagreed_with`` decided the AC9 presentation
    fallback the other way -- "a disagreement is with a verdict, and the
    presentation fallback confers none; naming a dissenter against
    ``hrv_unavailable`` would report a contradiction of a claim never made".
    ``_withhold_future`` confers no verdict either, so the same argument
    applies, and the split it implies is what this pins:

    * ``disagreed_with`` is **empty** on a withheld future day -- it is a
      claim *about* a verdict and none was conferred;
    * ``selected_dataset`` and ``selected_reason`` are **kept as computed** --
      ``_withhold_future``'s own justification is that everything which
      *produced* the verdict (band, baseline, ``readings_in_window``, week
      mean) is left alone so the response stays reproducible by hand
      (``research/00`` PRIN-12), and these two identify which dataset the retained
      ``baseline``/``band`` came from. They are producers, not claims.

    The fixture is the dissent fixture, not a thin one: on ``to = D`` the
    snapshot **is** named, and the same rows one, two and four days ahead
    name nobody. Without that control the empty list would be the empty list
    of a day on which nothing disagreed anyway -- the branch-beside-the-bug
    shape the D + 400 row was already caught by.
    """
    configure("UTC")
    monkeypatch.setattr(main_module, "_utcnow", lambda: datetime(D.year, D.month, D.day, 12, 0, tzinfo=UTC))
    straps_at(seeder, days(D - timedelta(days=66), D))
    seeder.snapshots(days(D - timedelta(days=26), D - timedelta(days=7)), baseline_values(20))
    seeder.snapshots(days(D - timedelta(days=6), D), repeat(25.0))
    seeder.persist()

    with TestClient(app) as client:
        today = get(client, to=D.isoformat()).json()
        ahead = {k: get(client, to=(D + timedelta(days=k)).isoformat()).json() for k in (1, 2, 4)}

    print("today:", today["verdict"], today["selected_dataset"], today["selected_reason"],
          today["disagreed_with"])
    for k, body in ahead.items():
        print(f"D+{k}:", body["verdict"], body["unavailable_reason"], body["selected_dataset"],
              body["selected_reason"], body["disagreed_with"],
              "datasets:", [d["tier"] for d in body["datasets"]])

    # The control: on the day that has happened, the dissenter IS named, so
    # the empty lists below are the rule and not the fixture.
    assert today["verdict"] == "hrv_normal"
    assert today["selected_dataset"] == STRAP
    assert today["disagreed_with"] == [{"dataset": SNAPSHOT, "week_days": 7}]

    for k, body in ahead.items():
        assert body["verdict"] == "hrv_unavailable", (k, body["verdict"])
        assert body["unavailable_reason"] == "day_not_happened", (k, body["unavailable_reason"])
        assert body["below_by"] is None, k
        # The claim about a verdict: withheld with it.
        assert body["disagreed_with"] == [], (k, body["disagreed_with"])
        # The producers of the retained baseline/band: kept as computed.
        assert body["selected_dataset"] == STRAP, (k, body["selected_dataset"])
        assert body["selected_reason"] == today["selected_reason"], (k, body["selected_reason"])
        assert [d["tier"] for d in body["datasets"]] == [d["tier"] for d in today["datasets"]], k
        # And the state that makes the empty list a decision rather than an
        # absence: the snapshot still reads the other side of its own band on
        # this day, and the block still says so.
        datasets = _by_tier(body)
        assert datasets[SNAPSHOT]["below"] is True, (k, datasets[SNAPSHOT])
        assert datasets[STRAP]["below"] is False, (k, datasets[STRAP])
        assert body["baseline"]["established"] is True and body["band"] is not None, k


def test_each_dataset_carries_its_own_reported_reset_and_only_the_selected_ones_is_presented(
    configure, seeder
) -> None:
    """AC17's second half (T154) rendered: ``tier_change_reset`` is asked once
    **per dataset**, with that dataset's own tier, so the two datasets of one
    series can report different things -- and ``baseline.reset_on`` /
    ``baseline.reset_reason`` are the **selected** dataset's alone.

    A daily strap to ``D-61`` then a daily snapshot from ``D-60``: the
    snapshot's era cleanly follows the strap's, so the snapshot reports
    ``(D-60, tier_change)`` and its window is clipped there, while the strap
    -- which *is* the previous window's tier, so clause (b) short-circuits --
    reports nothing at all. A renderer that copied the presented reset onto
    every entry, or nulled every entry, passes neither half.

    **What this geometry cannot show, and where it is shown instead.** T154
    measured the mirror case -- a *non-selected* dataset carrying a reported
    ``tier_change`` the rest of the response never shows -- and it needs three
    tiers: clause (c) refuses a boundary whose other tier is dense after it,
    and a judgeable dataset holds at least ``min_window_readings`` judged-week
    days by definition, so at N = 2 the two cannot both hold. The classifier
    never writes ``health_api_overnight`` (reference §10: N is 3 in the enum
    and 2 in every real corpus), so no endpoint seeding can reach it. It is
    pinned at module scope instead, by
    ``test_hrv_tier_change_per_dataset.py::test_the_view_presents_the_selected_datasets_own_report_and_carries_the_others``.
    """
    configure("UTC")
    straps_at(seeder, days(D - timedelta(days=126), D - timedelta(days=61)))
    seeder.snapshots(days(D - timedelta(days=60), D), baseline_values(61))
    seeder.persist()

    with TestClient(app) as client:
        body = get(client, to=D.isoformat()).json()

    datasets = _by_tier(body)
    era = (D - timedelta(days=60)).isoformat()
    print("datasets:", json.dumps(body["datasets"], indent=1))
    print("baseline:", json.dumps(body["baseline"], indent=1))

    assert body["selected_dataset"] == SNAPSHOT
    assert body["baseline"]["reset_reason"] == "tier_change"
    assert body["baseline"]["reset_on"] == era
    assert datasets[SNAPSHOT]["reset_on"] == era
    assert datasets[SNAPSHOT]["reset_reason"] == "tier_change"
    assert datasets[STRAP]["reset_on"] is None
    assert datasets[STRAP]["reset_reason"] is None
    # AC15: the strap's six in-span mornings are in its own dataset and in no
    # excluded list -- accounted for exactly once, positively.
    assert datasets[STRAP]["n"] == 6
    assert datasets[STRAP]["established"] is False
    assert body["excluded"] == []


def test_the_points_name_the_dataset_each_days_band_came_from_across_a_switch(
    configure, seeder
) -> None:
    """AC14, and the defect it exists for (``CRITIC-F005`` priority 3).

    Selection runs per **local day** and reads nothing from yesterday, so a
    chart's adjacent points can be drawn against two different instruments. A
    daily snapshot over 200 days with a daily strap starting at ``D-30``: the
    strap reaches ``min_baseline_readings`` days inside ``[d-66, d-7]`` on
    ``d = D-10`` exactly, and from that day it is judgeable, outranks the
    snapshot and is selected. Every earlier point in the range is the
    snapshot's.

    The band **steps** at that boundary by about 0.66 in log space -- the
    systematic bias between a chest-strap RR capture and a device-computed
    numeric rMSSD, not a change in the athlete -- and before this field there
    was nothing in the response that said which instrument drew which day. The
    step is asserted here, beside the names, because it is the reason the
    names are needed."""
    configure("UTC")
    seeder.snapshots(days(D - timedelta(days=200), D), baseline_values(201))
    straps_at(seeder, days(D - timedelta(days=30), D))
    seeder.persist()

    first = D - timedelta(days=20)
    with TestClient(app) as client:
        body = get(client, **{"from": first.isoformat(), "to": D.isoformat()}).json()

    points = body["points"]
    named = {p["date"]: p["dataset"] for p in points}
    print("points:", [(p["date"], p["dataset"]) for p in points])

    assert [p["date"] for p in points] == [d.isoformat() for d in days(first, D)]
    switch = D - timedelta(days=10)
    for day in days(first, switch - timedelta(days=1)):
        assert named[day.isoformat()] == SNAPSHOT, day
    for day in days(switch, D):
        assert named[day.isoformat()] == STRAP, day

    # The last point is ``to``'s, so it names what ``selected_dataset`` names
    # and carries the response's own band.
    assert points[-1]["dataset"] == body["selected_dataset"] == STRAP
    assert points[-1]["swc_low"] == pytest.approx(body["band"]["lo"])

    # The step the name explains.
    by_date = {p["date"]: p for p in points}
    before = by_date[(switch - timedelta(days=1)).isoformat()]
    after = by_date[switch.isoformat()]
    assert abs(after["baseline"] - before["baseline"]) > 0.5, (before, after)
    others = [
        abs(b["baseline"] - a["baseline"])
        for a, b in pairwise(points)
        if b["date"] != switch.isoformat()
    ]
    assert max(others) < 0.05, max(others)


def test_a_tier_read_only_before_a_coverage_gap_is_absent_from_datasets_and_wholly_excluded(
    configure, seeder
) -> None:
    """``datasets[]`` holds one entry per tier present in the **gap-clipped**
    span, not per tier present in ``[D-66, D]`` (sprint-006 spec review,
    finding 1).

    Both published copies said "one entry per source tier present in
    [date-66, date]", and that sentence is false whenever a global coverage
    gap clips the series: ``build_series`` applies the **global** clip before
    the per-tier partition, so a tier read only on the far side of the silence
    has no reading left for a dataset to be built from. ``build_series``'s own
    docstring was already right ("every tier present in the gap-clipped
    readings"); the wire copies were not, and a consumer counting
    ``datasets[]`` against the tiers it knows it captured on was counting
    against a claim nothing could break.

    Nothing caught it because nothing could:
    ``test_the_schema_and_the_contract_both_publish_the_dataset_block`` compares
    the two copies' **field sets** and never a description against the code,
    and no mutant in T159's 7/7 table encodes dataset membership under a clip.

    The geometry is the reviewer's. 27 declared chest-strap captures on
    ``[D-66, D-40]``; the whole series silent across the 25 local days
    ``D-39..D-15``, which is more than ``GAP_RESET_DAYS``; then a daily Health
    Snapshot from ``D-14`` to ``D``. So ``gap_reset_on`` is ``D-14``,
    ``datasets[]`` renders ``health_snapshot`` alone, and all 27 strap rows
    are in ``excluded`` as ``before_reset: coverage_gap`` -- present, named,
    and in no dataset.

    **Three-valued** (``retiring-a-ratified-behaviour-needs-a-three-valued-pin``
    one altitude up: the claim being retired is the published span). Green as
    built. Against a mutant that partitions ``unclipped_readings`` instead of
    the gap-clipped ``readings`` -- which is the response the withdrawn
    sentence describes -- the first assertion below fails with
    ``assert ['chest_strap_raw', 'health_snapshot'] == ['health_snapshot']``,
    because the pre-gap tier is rendered as a dataset of its own (``n`` 0,
    ``band`` null). Green again on restore.

    The PRIN-23 / AC15 partition is asserted over the rendered body rather than
    assumed, because that is the half the correction is asking a consumer to
    rely on: the 27 excluded strap rows, plus the surviving dataset's own
    ``n`` and ``week_days``, account for all 42 stored rows inside the span,
    once each and in exactly one place.
    """
    configure("UTC")
    strap_span = days(D - timedelta(days=66), D - timedelta(days=40))
    snapshot_span = days(D - timedelta(days=14), D)
    silence = (D - timedelta(days=39), D - timedelta(days=15))
    strap_sessions = straps_at(seeder, strap_span)
    snapshot_sessions = seeder.snapshots(snapshot_span, baseline_values(len(snapshot_span)))
    seeder.persist()

    with TestClient(app) as client:
        response = get(client, to=D.isoformat())

    assert response.status_code == 200, response.text
    body = response.json()

    # The slice compared, printed: an exit code is a summary of evidence
    # nobody has seen (``a-witness-must-print-the-slice-it-compared``).
    silent_days = (silence[1] - silence[0]).days + 1
    print(
        f"strap {strap_span[0]}..{strap_span[-1]} ({len(strap_sessions)} rows) | "
        f"silence {silence[0]}..{silence[1]} ({silent_days} local days > "
        f"GAP_RESET_DAYS {hrv_trend.GAP_RESET_DAYS}) | "
        f"snapshot {snapshot_span[0]}..{snapshot_span[-1]} ({len(snapshot_sessions)} rows)"
    )
    print("datasets:", json.dumps(body["datasets"], indent=1))
    print("reset:", body["baseline"]["reset_reason"], body["baseline"]["reset_on"])
    print("excluded:", json.dumps(sorted(f"{e['date']} {e['reason']}" for e in body["excluded"]), indent=1))

    # The corrected sentence. The clip is what decides membership, so the
    # pre-gap tier is not here at all -- not here carrying an empty baseline,
    # which is the shape a tier read only in the judged week has.
    assert [d["tier"] for d in body["datasets"]] == [SNAPSHOT], body["datasets"]
    datasets = _by_tier(body)
    assert STRAP not in datasets, body["datasets"]
    assert body["baseline"]["reset_reason"] == "coverage_gap"
    assert body["baseline"]["reset_on"] == (D - timedelta(days=14)).isoformat()

    # Where the clipped-away tier's rows went -- all of them, with the reason
    # the corrected sentence now names.
    excluded = {entry["session_id"]: entry["reason"] for entry in body["excluded"]}
    strap_ids = [s.session_id for s in strap_sessions]
    snapshot_ids = [s.session_id for s in snapshot_sessions]
    assert len(strap_ids) == 27 and len(snapshot_ids) == 15
    assert sorted(excluded) == sorted(strap_ids), excluded
    assert {excluded[session_id] for session_id in strap_ids} == {"before_reset: coverage_gap"}

    # research/00 PRIN-23 / AC15 across ``datasets[]`` u ``excluded``: the
    # surviving dataset's own counts cover the whole snapshot era (8 baseline
    # days [D-14, D-7] and 7 judged-week days), none of it is excluded, and
    # the two lists together are the 42 stored rows exactly once each.
    survivor = datasets[SNAPSHOT]
    assert not set(snapshot_ids) & set(excluded), sorted(set(snapshot_ids) & set(excluded))
    assert survivor["n"] == 8 and survivor["week_days"] == 7, survivor
    assert survivor["established"] is False, survivor
    accounted = survivor["n"] + survivor["week_days"] + len(excluded)
    assert accounted == len(strap_ids) + len(snapshot_ids) == 42, (
        f"{accounted} rows accounted for against {len(strap_ids) + len(snapshot_ids)} stored: "
        f"research/00 PRIN-23's partition is broken over datasets[] u excluded"
    )
    assert len(body["excluded"]) == len({entry["session_id"] for entry in body["excluded"]})

    # "Empty only when no reading of any tier exists in the span" still holds:
    # a clip always leaves its own resumption reading behind.
    assert body["datasets"], body


#: The two schema classes that declare ``reset_reason``, and the path to the
#: same field in ``contracts/openapi.yaml``. Both copies are new as a pair in
#: sprint-006: ``Baseline.reset_reason`` is F005's, ``DatasetSummary``'s is
#: T159's, and the second is what took a hand-typed enum from one site to four.
RESET_REASON_SCHEMA_CLASSES = ("Baseline", "DatasetSummary")

#: The source of the two schema copies, read as text because only the text
#: distinguishes a derived Literal from a transcribed one.
SCHEMAS_SOURCE = Path(__file__).resolve().parents[1] / "src" / "runcoach_api" / "schemas.py"


def _reset_reason_annotation(class_name: str) -> ast.expr:
    """The ``reset_reason`` annotation of one schema class, from the source
    rather than from the resolved type: ``Literal["coverage_gap", ...]`` and
    ``Literal[hrv_trend.REASON_COVERAGE_GAP, ...]`` resolve to the *same*
    object, so only the source says which of the two was written."""
    source = SCHEMAS_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name]
    assert len(classes) == 1, f"{len(classes)} class {class_name} in {SCHEMAS_SOURCE.name}, not 1"
    fields = [
        node
        for node in classes[0].body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "reset_reason"
    ]
    assert len(fields) == 1, f"{len(fields)} reset_reason fields on {class_name}, not 1"
    return fields[0].annotation


def test_reset_reason_is_derived_from_the_module_in_both_schema_copies_and_the_contract() -> None:
    """``reset_reason`` was the **fourth** hand-typed copy of a two-member
    enum the module owns (sprint-006 review iteration 1, S2).

    ``hrv_trend`` owns ``REASON_COVERAGE_GAP`` and ``REASON_TIER_CHANGE``,
    and both sibling enums in this same response are already held to it:
    ``selected_reason`` subscripts ``Literal`` with the module's own
    ``SELECTED_REASONS`` tuple and is pinned module<->schema<->contract by the
    test above, and ``unavailable_reason`` has
    ``test_the_six_unavailable_reason_names_are_the_same_six_in_the_module_the_schema_and_the_contract``.
    ``reset_reason`` had neither, while this sprint **doubled** its
    hand-typed sites: ``schemas.Baseline``, ``schemas.DatasetSummary`` and
    two places in ``contracts/openapi.yaml``. Renaming a module constant
    would have left all four declaring the old name, silently.

    Both halves of the fix are asserted, because either alone leaves a hole:

    * **The values agree**, module to both schema copies to both contract
      copies. A structural drift check does not read enum members, so this is
      the only thing that sees a contract left behind.
    * **The schema copies are *derived*, not transcribed.** This is the half
      that needs the source: ``Literal["coverage_gap", "tier_change"]`` and
      ``Literal[hrv_trend.REASON_COVERAGE_GAP, hrv_trend.REASON_TIER_CHANGE]``
      resolve to the same annotation object, so a values-only pin stays green
      over a hand-typed copy and would simply move with a rename made in two
      places out of four. Reading the annotation's AST is what tells them
      apart, and it is the same oracle style ``test_hrv_unavailable_causes``
      uses on ``judge``.

    The contract copies stay literal by necessity -- YAML cannot import the
    module -- which is exactly why the values half is asserted against them.

    The witness prints the annotation source of both schema copies and both
    published enums before asserting
    (``a-witness-must-print-the-slice-it-compared``).
    """
    module_names = (hrv_trend.REASON_COVERAGE_GAP, hrv_trend.REASON_TIER_CHANGE)
    assert len(set(module_names)) == 2, module_names

    served = app.openapi()["components"]["schemas"]
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    published = contract["components"]["schemas"]["HrvTrend"]["properties"]
    contract_enums = {
        "Baseline": published["baseline"]["properties"]["reset_reason"]["enum"],
        "DatasetSummary": published["datasets"]["items"]["properties"]["reset_reason"]["enum"],
    }

    print(f"module: hrv_trend reset reasons {module_names}")
    for class_name in RESET_REASON_SCHEMA_CLASSES:
        annotation = _reset_reason_annotation(class_name)
        print(f"  {SCHEMAS_SOURCE.name}:{annotation.lineno} {class_name}.reset_reason: "
              f"{ast.unparse(annotation)}")
        print(f"  openapi.yaml {class_name}.reset_reason enum: {contract_enums[class_name]}")

    for class_name in RESET_REASON_SCHEMA_CLASSES:
        annotation = _reset_reason_annotation(class_name)
        literal = next(
            (
                node
                for node in ast.walk(annotation)
                if isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "Literal"
            ),
            None,
        )
        assert literal is not None, (
            f"{class_name}.reset_reason is not a Literal enum at all: {ast.unparse(annotation)}"
        )
        members = literal.slice.elts if isinstance(literal.slice, ast.Tuple) else [literal.slice]
        transcribed = [ast.unparse(member) for member in members if isinstance(member, ast.Constant)]
        assert not transcribed, (
            f"{class_name}.reset_reason hand-types its enum members {transcribed} instead of "
            f"naming the module constants that own them. Renaming REASON_COVERAGE_GAP or "
            f"REASON_TIER_CHANGE would leave this copy declaring the old name and nothing would "
            f"red -- the hole selected_reason does not have. Write "
            f"Literal[hrv_trend.REASON_COVERAGE_GAP, hrv_trend.REASON_TIER_CHANGE], as "
            f"selected_reason does with SELECTED_REASONS: {ast.unparse(annotation)}"
        )
        qualified = [
            ast.unparse(member)
            for member in members
            if isinstance(member, ast.Attribute)
            and isinstance(member.value, ast.Name)
            and member.value.id == "hrv_trend"
        ]
        assert len(qualified) == len(members) == 2, (
            f"{class_name}.reset_reason names {qualified} of {len(members)} members through "
            f"hrv_trend: every member must come from the module that owns it"
        )

        served_enum = served[class_name]["properties"]["reset_reason"]
        enum = next(part["enum"] for part in served_enum["anyOf"] if "enum" in part)
        assert tuple(enum) == module_names, (class_name, enum, module_names)
        assert {"type": "null"} in served_enum["anyOf"], class_name
        assert tuple(contract_enums[class_name]) == module_names, (
            f"contracts/openapi.yaml publishes {contract_enums[class_name]} for "
            f"{class_name}.reset_reason while the module owns {list(module_names)}: the YAML "
            f"cannot import the module, which is why its copy is pinned here"
        )


def test_the_schema_and_the_contract_both_publish_the_dataset_block() -> None:
    """The contract and the as-built schema move in one change set (the
    project's API-contract rule), so the four added fields are asserted in
    **both** copies and against each other -- the two-copy shape T140 exists
    for, one field family over.

    ``selected_reason``'s enum is the module's own ``SELECTED_REASONS`` tuple
    **in order**, in both copies. T156 pins that tuple by exact equality;
    ``schemas.py`` subscripts ``Literal`` with the tuple itself rather than
    transcribing its members, so a member added to the module appears here
    without a hand edit and a member cannot come to exist in one copy alone --
    which is the hole T144 closed on ``unavailable_reason`` and this task was
    told not to re-open."""
    served = app.openapi()["components"]["schemas"]
    trend = served["HrvTrendResponse"]["properties"]
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    published = contract["components"]["schemas"]["HrvTrend"]["properties"]

    for field in ("datasets", "selected_dataset", "selected_reason", "disagreed_with"):
        assert field in trend, field
        assert field in published, field

    dataset_fields = {
        "tier",
        "n",
        "established",
        "band",
        "fidelity_rank",
        "last_read",
        "week_days",
        "week_mean",
        "below",
        "reset_on",
        "reset_reason",
    }
    assert set(served["DatasetSummary"]["properties"]) == dataset_fields
    assert set(published["datasets"]["items"]["properties"]) == dataset_fields
    assert set(served["Disagreement"]["properties"]) == {"dataset", "week_days"}
    assert set(published["disagreed_with"]["items"]["properties"]) == {"dataset", "week_days"}

    enum = next(part["enum"] for part in trend["selected_reason"]["anyOf"] if "enum" in part)
    assert tuple(enum) == hrv_trend.SELECTED_REASONS
    assert tuple(published["selected_reason"]["enum"]) == hrv_trend.SELECTED_REASONS
    assert {"type": "null"} in trend["selected_reason"]["anyOf"]
    assert published["selected_reason"]["nullable"] is True
    assert published["selected_dataset"]["nullable"] is True

    # T152's breaking change took the contract to 0.2.0-draft; this one is
    # additive and does not move it (AC12).
    assert contract["info"]["version"] == "0.2.0-draft"
