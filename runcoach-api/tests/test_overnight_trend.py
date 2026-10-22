"""The HRV trend reads overnight nights as its third dataset, and the others are unmoved.

``GET /metrics/hrv`` is read through ``TestClient`` against the autouse
isolated store. Nights are seeded through ``db.persist_night`` from
``tests/data/overnight-27-nights.csv``: 27 consecutive real nights ending
2026-10-10 (America/Vancouver dates), one per local day, decoded from the
athlete's export (``overnight-27-nights.provenance.json`` records the source
and the script). Each CSV row becomes one ``ingestion.overnight.Night``
whose ``sessions`` row is the one ``map_night`` builds; the 5-minute series is
not stored (the trend reads only the row's ``resting_rmssd_ms``), so
``values`` is empty and ``summary.hrv_values`` keeps the file's own count.

Health Snapshot captures are seeded through the real ``mapping -> classify ->
db.persist`` chain, as ``test_hrv_trend_endpoint.py`` seeds them: one
capture at 06:00 local time per day carrying exactly the ``rmssd_hrv`` chosen
here. A night's summary timestamp is near 05:00 local, so a day holding both
holds one reading of each tier.

The zone is America/Vancouver for this whole module (the ``vancouver``
fixture below), not the suite's UTC default: the CSV's dates are Vancouver
dates, and ``conftest.py``'s default is left as it is for every other module.

The with-and-without pins compare the ``health_snapshot`` entry of
``datasets[]`` and the top-level ``excluded`` rows whose ``session_id`` is a
snapshot's, as JSON dumps, and print the keys and a hash of each slice before
asserting, so a run shows what was compared.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api.config import AppConfig
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.ingestion.overnight import HRV_STATUS_FILE_TYPE, Night
from runcoach_api.main import app
from runcoach_api.models import Context, Session

NIGHTS_CSV = Path(__file__).parent / "data" / "overnight-27-nights.csv"
ZONE = "America/Vancouver"
OVERNIGHT = "health_api_overnight"
SNAPSHOT = "health_snapshot"
# The device the 27 real files name (``fr945_lte`` firmware 17.4), as
# ``overnight.map_night`` spells a night's: ``mapping._build_source_device``'s
# string followed by ``overnight.NIGHT_DEVICE_SUFFIX``.
NIGHT_DEVICE = "fr945_lte fw17.4 hrv_status"

# The last night's day. Baseline window [2026-08-05, 2026-10-03], judged
# week [2026-10-04, 2026-10-10], previous window [2026-06-06, 2026-08-04].
D = date(2026, 10, 10)
FIRST_NIGHT = date(2026, 9, 14)


@pytest.fixture(autouse=True)
def vancouver(isolated_data_dir, monkeypatch) -> AppConfig:
    """The isolated ``AppConfig`` with ``athlete_timezone`` America/Vancouver,
    and ``db._load_config_cached`` cleared so the route reads it."""
    config = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=isolated_data_dir,
        resting_hrv_profile_names=[],
        threshold_workout_names=[],
        athlete_timezone=ZONE,
    )
    monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: config)
    db_module._load_config_cached.cache_clear()
    return config


# ---------------------------------------------------------------------------
# seeding
# ---------------------------------------------------------------------------


def days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def at(day: date, hh: int = 6, mm: int = 0) -> datetime:
    """The aware UTC instant of Vancouver wall time ``hh:mm`` on ``day``."""
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZoneInfo(ZONE)).astimezone(UTC)


def night(start_time: str, series_start: str | None, average_ms: float, hrv_values: int) -> Night:
    """The ``Night`` ``map_night`` builds for a file with this summary, less its series."""
    session = Session(
        session_id=derive_session_id(NIGHT_DEVICE, start_time),
        sport="other",
        source_vendor="garmin",
        start_time=start_time,
        activity_tag="overnight_hrv",
        source_device=NIGHT_DEVICE,
        hr_source="wrist_ppg",
        rmssd_precomputed=average_ms,
        resting_rmssd_ms=average_ms,
        hrv_source_tier=OVERNIGHT,
        rr_source="overnight_ppg",
        name="Overnight HRV",
        quality_flags=[],
        summary={
            "duration_s": 0.0,
            "series_start": series_start,
            "last_night_5_min_high": None,
            "hrv_values": hrv_values,
            "hrv_values_valid": hrv_values,
        },
        context=Context(
            ingested_at=datetime.now(UTC).isoformat(),
            provenance={"raw_file_type": HRV_STATUS_FILE_TYPE},
        ),
    )
    return Night(session=session, values=[], sidecar={})


def real_nights() -> list[Night]:
    with NIGHTS_CSV.open(newline="", encoding="utf-8") as fh:
        return [
            night(
                row["summary_timestamp_utc"],
                row["series_start_utc"] or None,
                float(row["last_night_average_ms"]),
                int(row["hrv_values"]),
            )
            for row in csv.DictReader(fh)
        ]


def synthetic_nights(first: date, last: date, average_ms: float = 40.0) -> list[Night]:
    """One night per Vancouver day in ``[first, last]``, summarised at 05:05 local."""
    nights = []
    for day in days(first, last):
        start = at(day, 5, 5)
        nights.append(night(start.isoformat(), (start - timedelta(hours=7)).isoformat(), average_ms, 84))
    return nights


def persist_nights(nights: list[Night]) -> None:
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        for item in nights:
            db_module.persist_night(conn, item)
    finally:
        conn.close()


class Snapshots:
    """Health Snapshot captures through the real classifier, kept for persisting."""

    def __init__(self, synthetic, classified, persist_sessions) -> None:
        self._synthetic = synthetic
        self._classified = classified
        self._persist_sessions = persist_sessions
        self.sessions: list[Session] = []

    def add(self, when: datetime, rmssd: float) -> Session:
        session = self._classified(self._synthetic(60, rmssd_hrv=rmssd, start_time=when))
        assert session.hrv_source_tier == SNAPSHOT and session.resting_rmssd_ms == rmssd
        self.sessions.append(session)
        return session

    def daily(self, first: date, last: date, values) -> None:
        for day, value in zip(days(first, last), values):
            self.add(at(day), value)

    def persist(self) -> None:
        self._persist_sessions(self.sessions)

    @property
    def ids(self) -> set[str]:
        return {s.session_id for s in self.sessions}


@pytest.fixture
def snapshots(synthetic, classified, persist_sessions) -> Snapshots:
    return Snapshots(synthetic, classified, persist_sessions)


def alternating(n: int) -> list[float]:
    """38/44 ms alternating, an ordinary dispersion the band floor does not touch."""
    return [38.0 + 6.0 * (i % 2) for i in range(n)]


# The lapsed snapshot set: daily from D-120 (2026-06-12) to 2026-09-10, then
# silent for 20 days, then daily from 2026-10-01 to D -- the shape of the real
# 2026-09-10 to 2026-10-01 lapse. Two days carry a second, later capture
# (listed ``same_day_later_capture``): one in the baseline window and one
# inside the judged week, on a day a night also holds.
SNAPSHOT_FIRST = D - timedelta(days=120)
LAPSE_LAST_READ = date(2026, 9, 10)
LAPSE_RESUME = date(2026, 10, 1)
LAPSE_DATE = date(2026, 9, 20)


def seed_lapsed_snapshot_set(snapshots: Snapshots) -> None:
    before = days(SNAPSHOT_FIRST, LAPSE_LAST_READ)
    snapshots.daily(SNAPSHOT_FIRST, LAPSE_LAST_READ, alternating(len(before)))
    snapshots.daily(LAPSE_RESUME, D, [40.0, 41.0, 42.0, 39.0, 43.0, 40.0, 41.0, 42.0, 40.0, 41.0])
    snapshots.add(at(date(2026, 8, 20), 9), 30.0)
    snapshots.add(at(date(2026, 10, 5), 9), 31.0)
    snapshots.persist()


# ---------------------------------------------------------------------------
# reading and comparing
# ---------------------------------------------------------------------------


def read(client: TestClient, to: date) -> dict:
    response = client.get("/metrics/hrv", params={"to": to.isoformat()})
    assert response.status_code == 200, response.text
    return response.json()


def by_tier(body: dict) -> dict[str, dict]:
    return {entry["tier"]: entry for entry in body["datasets"]}


def snapshot_slice(body: dict, snapshot_ids: set[str]) -> dict:
    """The served snapshot dataset: its ``datasets[]`` entry and its ``excluded`` rows."""
    return {
        "dataset": by_tier(body).get(SNAPSHOT),
        "excluded": [row for row in body["excluded"] if row["session_id"] in snapshot_ids],
    }


def dump(value) -> str:
    return json.dumps(value, sort_keys=True)


def show(label: str, value: dict) -> str:
    """Print the slice's keys and the sha256 of its dump, and return the dump."""
    text = dump(value)
    dataset = value.get("dataset") or {}
    print(
        f"{label}: dataset keys={sorted(dataset)} excluded={len(value.get('excluded', []))} "
        f"sha256={hashlib.sha256(text.encode()).hexdigest()}"
    )
    return text


def bodies_with_and_without(snapshots: Snapshots, nights: list[Night], targets: list[date]):
    """``{target: (without, with)}``: the response before the nights are stored, then after."""
    seed_lapsed_snapshot_set(snapshots)
    with TestClient(app) as client:
        without = {t: read(client, t) for t in targets}
    persist_nights(nights)
    with TestClient(app) as client:
        with_nights = {t: read(client, t) for t in targets}
    return {t: (without[t], with_nights[t]) for t in targets}


# ---------------------------------------------------------------------------
# the nights alone
# ---------------------------------------------------------------------------


def test_27_nights_make_an_established_overnight_dataset() -> None:
    """27 consecutive real nights, one per Vancouver day: 20 in the baseline
    window [2026-08-05, 2026-10-03] (from 2026-09-14), 7 in the judged week."""
    nights = real_nights()
    assert len(nights) == 27
    persist_nights(nights)

    with TestClient(app) as client:
        body = read(client, D)

    datasets = by_tier(body)
    assert OVERNIGHT in datasets, body["datasets"]
    entry = datasets[OVERNIGHT]
    assert entry["established"] is True, entry
    assert entry["n"] == 20, entry
    assert entry["week_days"] == 7, entry
    assert entry["band"] is not None, entry
    assert entry["band"]["lo"] < entry["band"]["hi"], entry


def test_nights_alone_give_the_verdict() -> None:
    persist_nights(real_nights())

    with TestClient(app) as client:
        body = read(client, D)

    assert body["selected_dataset"] == OVERNIGHT, body
    assert body["baseline"]["tier"] == OVERNIGHT
    assert body["verdict"] != "hrv_unavailable", body["verdict"]


def test_a_judgeable_snapshot_dataset_beside_the_nights_is_selected(snapshots) -> None:
    seed_lapsed_snapshot_set(snapshots)
    persist_nights(real_nights())

    with TestClient(app) as client:
        body = read(client, D)

    datasets = by_tier(body)
    assert set(datasets) == {SNAPSHOT, OVERNIGHT}, body["datasets"]
    assert datasets[SNAPSHOT]["established"] is True and datasets[SNAPSHOT]["week_days"] == 7
    assert datasets[OVERNIGHT]["established"] is True
    assert body["selected_dataset"] == SNAPSHOT
    assert body["baseline"]["tier"] == SNAPSHOT


# ---------------------------------------------------------------------------
# the snapshot dataset with and without the nights
# ---------------------------------------------------------------------------


def test_the_snapshot_dataset_is_identical_with_and_without_nights(snapshots) -> None:
    """The snapshot set: daily 2026-06-12 to 2026-09-10, 20 silent days, daily
    2026-10-01 to 2026-10-10, plus a later capture on 2026-08-20 and on
    2026-10-05. Axis held constant: the lapse is 21 or fewer silent days, so
    neither the global coverage-gap reset nor the per-dataset hole clip fires
    with or without the nights (the over-21 lapse is the next test's). Read on
    2026-10-10, on 2026-09-20 (inside the lapse, while the nights run) and on
    2026-06-12 (the first snapshot)."""
    targets = [D, LAPSE_DATE, SNAPSHOT_FIRST]
    pairs = bodies_with_and_without(snapshots, real_nights(), targets)

    for target, (without, with_nights) in pairs.items():
        before = snapshot_slice(without, snapshots.ids)
        after = snapshot_slice(with_nights, snapshots.ids)
        a = show(f"{target} without nights", before)
        b = show(f"{target} with nights   ", after)
        assert before["dataset"] is not None, target
        assert a == b, (target, a, b)

    # The nights were read: the comparison is not between two stores without them.
    assert OVERNIGHT in by_tier(pairs[D][1]) and OVERNIGHT not in by_tier(pairs[D][0])
    assert OVERNIGHT in by_tier(pairs[LAPSE_DATE][1])
    # And the slice is not empty: the 2026-08-20 later capture is listed on 2026-10-10.
    assert any(row["reason"] == "same_day_later_capture" for row in snapshot_slice(pairs[D][0], snapshots.ids)["excluded"])


def test_a_lapse_over_21_days_bridged_by_nights_withdraws_only_the_gap_report(snapshots) -> None:
    """The snapshot set: daily 2026-06-12 to 2026-08-15, 23 silent days, daily
    2026-09-08 to 2026-10-10. Nights: synthetic ones 2026-08-10 to 2026-09-13,
    then the 27 real ones, so no day of the snapshot lapse is silent for the
    series as a whole. Without the nights the global coverage-gap reset fires
    on 2026-09-08; with them it cannot, and the per-dataset hole clip at the
    same day keeps the snapshot band and ``n``. Only the report goes: the
    accepted cost in F028's Technical Notes, made visible here."""
    resume = date(2026, 9, 8)
    last_read = date(2026, 8, 15)
    assert (resume - last_read).days - 1 > 21
    before_lapse = days(SNAPSHOT_FIRST, last_read)
    snapshots.daily(SNAPSHOT_FIRST, last_read, alternating(len(before_lapse)))
    snapshots.daily(resume, D, alternating(len(days(resume, D))))
    snapshots.persist()

    with TestClient(app) as client:
        without = read(client, D)
    persist_nights(synthetic_nights(date(2026, 8, 10), FIRST_NIGHT - timedelta(days=1)) + real_nights())
    with TestClient(app) as client:
        with_nights = read(client, D)

    before = snapshot_slice(without, snapshots.ids)
    after = snapshot_slice(with_nights, snapshots.ids)
    show("over-21 lapse without nights", before)
    show("over-21 lapse with nights   ", after)

    gap_report = {"reset_on": resume.isoformat(), "reset_reason": "coverage_gap"}
    no_report = {"reset_on": None, "reset_reason": None}
    report_keys = set(gap_report)
    assert {k: before["dataset"][k] for k in report_keys} == gap_report, before["dataset"]
    assert {k: after["dataset"][k] for k in report_keys} == no_report, after["dataset"]
    assert {k: without["baseline"][k] for k in report_keys} == gap_report, without["baseline"]
    assert {k: with_nights["baseline"][k] for k in report_keys} == no_report, with_nights["baseline"]

    rest_before = {k: v for k, v in before["dataset"].items() if k not in report_keys}
    rest_after = {k: v for k, v in after["dataset"].items() if k not in report_keys}
    show("over-21 lapse without nights, less the report", {"dataset": rest_before, "excluded": before["excluded"]})
    show("over-21 lapse with nights,    less the report", {"dataset": rest_after, "excluded": after["excluded"]})
    assert rest_before["n"] == len(days(resume, D - timedelta(days=7)))
    assert rest_before["band"] is not None and rest_before["established"] is True
    assert dump(rest_before) == dump(rest_after)
    assert dump(before["excluded"]) == dump(after["excluded"])


def test_a_nights_era_in_the_previous_window_leaves_the_snapshot_dataset_unchanged(snapshots) -> None:
    """The lapsed snapshot set of the identical-with-and-without test, plus
    synthetic nights through the previous window [2026-06-06, 2026-08-04]
    (``tier_change_reset`` reads it; the backfill reaches it and the 27 real
    nights do not) and the 27 real nights. Axis held constant: both tiers are
    daily across the previous window, from 2026-06-12 for the snapshots."""
    previous_first = D - timedelta(days=126)
    previous_last = D - timedelta(days=67)
    nights = synthetic_nights(previous_first, previous_last) + real_nights()
    pairs = bodies_with_and_without(snapshots, nights, [D, LAPSE_DATE])

    for target, (without, with_nights) in pairs.items():
        before = snapshot_slice(without, snapshots.ids)
        after = snapshot_slice(with_nights, snapshots.ids)
        a = show(f"{target} previous-window nights: without", before)
        b = show(f"{target} previous-window nights: with   ", after)
        assert before["dataset"] is not None, target
        assert a == b, (target, a, b)
    assert OVERNIGHT in by_tier(pairs[D][1])


def test_the_csv_holds_27_consecutive_vancouver_days() -> None:
    zone = ZoneInfo(ZONE)
    local_days = [datetime.fromisoformat(n.session.start_time).astimezone(zone).date() for n in real_nights()]
    assert local_days == days(FIRST_NIGHT, D)
