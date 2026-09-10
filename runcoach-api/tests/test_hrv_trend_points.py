"""T091: the contract's per-day ``points[]`` series on ``GET /metrics/hrv``, and the
``x-readiness: implemented`` flip that ships in the same change.

``contracts/openapi.yaml`` defines ``HrvTrend`` as a per-day series for the UI's
chart -- ``date, ln_rmssd, baseline, swc_low, swc_high`` -- and T085 served the
verdict blocks for ``to`` beside a deliberately absent ``points``. Here every
local day in ``[from, to]`` is judged against **its own** baseline
``[d-66, d-7]`` (the band is a property of the baseline, IDEA-044: a day with
no reading still carries it, and an unestablished-but-computable baseline
still asserts one), the rows are read once over the padded range, and a range
longer than 366 days is a 422.

The contract half is pinned in-suite rather than left to ``check_drift.py``:
that script's query rule only checks that a contract-*required* parameter
name exists as-built and never reads as-built required-ness, so it passes a
contract that promises ``from``/``to`` as required while the route accepts
neither. ``test_the_contract_promises_nothing_stricter_than_the_route_enforces``
is stricter, and it was observed red with the flip made and the parameters
still required (see the commit body).

Readings are seeded through the real ``mapping -> classify -> db.persist``
chain (``synthetic`` / ``classified`` from ``conftest.py``); the expected
bands are computed here from the seeded values with the pure ``build_band``,
never read back from the endpoint.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.config import AppConfig
from runcoach_api.main import app
from runcoach_api.metrics import hrv_trend
from runcoach_api.models import Session

SNAPSHOT = "health_snapshot"
AUCKLAND = "Pacific/Auckland"
CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"

# The target date F005's canonical example uses; the judged week is [D-6, D].
D = date(2026, 9, 8)
WEEK = [D - timedelta(days=6 - i) for i in range(7)]


# ---------------------------------------------------------------------------
# configuration and seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def configure(isolated_data_dir, monkeypatch):
    """``configure(zone)`` -- the ``declared_config`` pattern with a chosen
    ``athlete_timezone``; both the re-monkeypatch and the cache clear are
    required or the route keeps reading the autouse UTC config."""

    def _configure(zone: str) -> AppConfig:
        config = AppConfig(
            host="127.0.0.1",
            port=8000,
            data_dir=isolated_data_dir,
            resting_hrv_profile_names=[],
            athlete_timezone=zone,
        )
        monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: config)
        db_module._load_config_cached.cache_clear()
        return config

    return _configure


@pytest.fixture
def seed(synthetic, classified, persist_sessions):
    """``seed({day: rmssd_ms, ...})`` -- one Health Snapshot capture per local
    day at 06:00 UTC carrying exactly that rMSSD, through the real classifier
    and ``db.persist``."""

    def _seed(readings: dict[date, float]) -> list[Session]:
        sessions: list[Session] = []
        for day, value in sorted(readings.items()):
            when = datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)
            session = classified(synthetic(60, rmssd_hrv=value, start_time=when))
            assert session.hrv_source_tier == SNAPSHOT and session.resting_rmssd_ms == value
            sessions.append(session)
        persist_sessions(sessions)
        return sessions

    return _seed


def drifting_series(first: date, last: date) -> dict[date, float]:
    """An ordinary athlete's dispersion (alternating +/- 2 ms) on a slow
    upward drift, so every local day's baseline -- and therefore its band --
    is different from its neighbours'. A flat series would let a route that
    reuses ``to``'s band for every point pass."""
    n = (last - first).days + 1
    return {first + timedelta(days=i): 40.0 + 2.0 * (i % 2) + 0.05 * i for i in range(n)}


def band_for(day: date, readings: dict[date, float]) -> hrv_trend.Band | None:
    """The band the pure computation asserts for ``day``: ``build_band`` over
    the ln of the seeded values inside ``[day-66, day-7]``."""
    first, last = hrv_trend.baseline_window(day)
    return hrv_trend.build_band(math.log(v) for d, v in sorted(readings.items()) if first <= d <= last)


def get(client: TestClient, **params):
    return client.get("/metrics/hrv", params=params)


def get_points(configure, seed, readings: dict[date, float], from_: date, to: date) -> dict:
    configure("UTC")
    seed(readings)
    with TestClient(app) as client:
        response = get(client, **{"from": from_.isoformat(), "to": to.isoformat()})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# the first failing test: one point per local day, each on its own baseline
# ---------------------------------------------------------------------------


def test_one_point_per_local_day_each_judged_against_its_own_baseline(configure, seed) -> None:
    """A 75-day series ending on D; ``from=D-6&to=D`` yields exactly seven
    points in date order whose band for day ``d`` is what a direct call to the
    pure computation returns for ``target_date=d``. Red: no ``points`` field."""
    readings = drifting_series(D - timedelta(days=74), D)
    body = get_points(configure, seed, readings, WEEK[0], D)

    assert "points" in body, "the contract's series is missing"
    points = body["points"]
    assert [p["date"] for p in points] == [d.isoformat() for d in WEEK]
    for point, day in zip(points, WEEK, strict=True):
        band = band_for(day, readings)
        assert band is not None
        assert point["ln_rmssd"] == pytest.approx(math.log(readings[day]))
        assert point["baseline"] == pytest.approx(band.mean)
        assert point["swc_low"] == pytest.approx(band.lo)
        assert point["swc_high"] == pytest.approx(band.hi)
    bands = {(p["swc_low"], p["swc_high"]) for p in points}
    assert len(bands) == 7, "every day was judged against to's band, not its own"


def test_the_last_points_band_equals_the_verdicts_band(configure, seed) -> None:
    """The demo probe's assertion 4 compares them with ``==``: the last point
    and the verdict describe the same day and must carry the same floats."""
    readings = drifting_series(D - timedelta(days=74), D)
    body = get_points(configure, seed, readings, WEEK[0], D)

    last = body["points"][-1]
    assert last["date"] == body["date"] == D.isoformat()
    assert last["swc_low"] == body["band"]["lo"]
    assert last["swc_high"] == body["band"]["hi"]
    assert last["baseline"] == body["band"]["mean"]


def test_every_point_carries_the_band_its_own_judge_call_asserted(configure, seed, monkeypatch) -> None:
    """The equality above must hold because a point is rendered from the
    ``judge`` result for its day -- the same object ``band`` is rendered from
    on ``to`` -- and not because a second computation over the baseline
    happens to agree with it today (wave-5 mutation: a ``_point`` that called
    ``build_band`` itself passed every value test in this file). ``judge`` is
    wrapped to assert a band no recomputation would produce; every point,
    and ``band``, must carry that one."""
    readings = drifting_series(D - timedelta(days=74), D)
    real_judge = hrv_trend.judge

    def shifted_judge(series: hrv_trend.HrvSeries) -> hrv_trend.HrvVerdict:
        verdict = real_judge(series)
        band = verdict.band
        assert band is not None
        return replace(verdict, band=replace(band, mean=band.mean + 1.0, lo=band.lo + 1.0, hi=band.hi + 1.0))

    monkeypatch.setattr(main_module.hrv_trend, "judge", shifted_judge)
    body = get_points(configure, seed, readings, WEEK[0], D)

    for point, day in zip(body["points"], WEEK, strict=True):
        computed = band_for(day, readings)
        assert point["baseline"] == pytest.approx(computed.mean + 1.0)
        assert point["swc_low"] == pytest.approx(computed.lo + 1.0)
        assert point["swc_high"] == pytest.approx(computed.hi + 1.0)
    last = body["points"][-1]
    assert (last["baseline"], last["swc_low"], last["swc_high"]) == (
        body["band"]["mean"],
        body["band"]["lo"],
        body["band"]["hi"],
    )


def test_a_day_with_no_reading_carries_a_null_ln_rmssd_but_still_its_band(configure, seed) -> None:
    """IDEA-044: the band is a property of the baseline ``[d-66, d-7]``, which
    a missing capture on ``d`` does not touch."""
    readings = drifting_series(D - timedelta(days=74), D)
    missing = D - timedelta(days=3)
    del readings[missing]
    body = get_points(configure, seed, readings, WEEK[0], D)

    point = body["points"][3]
    assert point["date"] == missing.isoformat()
    assert point["ln_rmssd"] is None
    band = band_for(missing, readings)
    assert point["swc_low"] == pytest.approx(band.lo)
    assert point["swc_high"] == pytest.approx(band.hi)
    assert point["baseline"] == pytest.approx(band.mean)
    assert [p["ln_rmssd"] is None for p in body["points"]] == [False, False, False, True, False, False, False]


def test_a_day_with_no_asserted_band_carries_three_nulls(configure, seed) -> None:
    """A new athlete's first week plus one earlier capture: ``D``'s baseline
    holds one reading and the earlier days' hold none, so no day can build a
    band. ``baseline``, ``swc_low`` and ``swc_high`` are null together while
    ``ln_rmssd`` still shows the day's reading."""
    readings = {day: 41.0 for day in WEEK}
    readings[D - timedelta(days=8)] = 39.0
    body = get_points(configure, seed, readings, WEEK[0], D)

    assert body["band"] is None
    assert body["verdict"] == "hrv_unavailable"
    for point in body["points"]:
        assert point["ln_rmssd"] == pytest.approx(math.log(41.0))
        assert (point["baseline"], point["swc_low"], point["swc_high"]) == (None, None, None)


def test_an_unestablished_but_computable_baseline_still_carries_its_band(configure, seed) -> None:
    """2 <= n < 14: the verdict is withheld (``hrv_unavailable``, not
    established) but the chart may draw the band. The point carries it."""
    thin = {D - timedelta(days=k): v for k, v in zip((20, 18, 16, 14, 12), (38.0, 44.0, 39.0, 43.0, 40.0))}
    readings = {**thin, **{day: 30.0 for day in WEEK}}
    body = get_points(configure, seed, readings, D, D)

    assert body["verdict"] == "hrv_unavailable"
    assert body["baseline"]["established"] is False and body["baseline"]["n"] == 5
    (point,) = body["points"]
    band = band_for(D, readings)
    assert point["swc_low"] == pytest.approx(band.lo)
    assert point["swc_high"] == pytest.approx(band.hi)
    assert point["ln_rmssd"] == pytest.approx(math.log(30.0))


def test_no_parameters_yields_one_point(configure, monkeypatch) -> None:
    """Clock frozen at 2026-09-08T13:00Z, zone Auckland: ``to`` is the local
    9th, ``from`` equals it, and the series is that one day."""
    configure(AUCKLAND)
    monkeypatch.setattr(main_module, "_utcnow", lambda: datetime(2026, 9, 8, 13, 0, tzinfo=UTC))
    with TestClient(app) as client:
        response = client.get("/metrics/hrv")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["date"] == body["from"] == "2026-09-09"
    assert [p["date"] for p in body["points"]] == ["2026-09-09"]
    assert body["points"][0] == {
        "date": "2026-09-09",
        "ln_rmssd": None,
        "baseline": None,
        "swc_low": None,
        "swc_high": None,
    }


def test_the_rows_are_read_once_for_the_whole_range(configure, seed, monkeypatch) -> None:
    """The per-day loop reads the padded range ``[from-126d-26h, (to+1d)+26h]``
    once and hands the same rows to every day's ``build_series``."""
    configure("UTC")
    seed(drifting_series(D - timedelta(days=74), D))
    seen: list[tuple[str, str]] = []
    real = db_module.read_hrv_rows

    def spy(conn, start_iso, end_iso):
        seen.append((start_iso, end_iso))
        return real(conn, start_iso, end_iso)

    monkeypatch.setattr(main_module.db, "read_hrv_rows", spy)
    with TestClient(app) as client:
        response = get(client, **{"from": WEEK[0].isoformat(), "to": D.isoformat()})

    assert response.status_code == 200 and len(response.json()["points"]) == 7
    assert len(seen) == 1
    (start_iso, end_iso) = seen[0]
    first = datetime(WEEK[0].year, WEEK[0].month, WEEK[0].day, tzinfo=UTC)
    last = datetime(D.year, D.month, D.day, tzinfo=UTC)
    assert datetime.fromisoformat(start_iso) <= first - timedelta(days=126, hours=26)
    assert datetime.fromisoformat(end_iso) >= last + timedelta(days=1, hours=26)


def test_a_range_ending_after_today_carries_no_verdict_past_today(configure, seed, monkeypatch) -> None:
    """The clock is frozen so today is ``D``. A 75-day series through ``D``
    whose last week is suppressed, read over ``[D-2, D+2]``: the response's
    ``date`` is ``to``, and its verdict is ``hrv_unavailable`` with
    ``below_by`` null even though ``to``'s window holds five readings (F005:
    no suppression is asserted about a day that has not happened). The two
    future points carry no reading; they may still carry a band, which is a
    property of the baseline ``[d-66, d-7]`` and lies entirely in the past.
    Red: ``hrv_suppressed``."""
    configure("UTC")
    monkeypatch.setattr(main_module, "_utcnow", lambda: datetime(D.year, D.month, D.day, 12, 0, tzinfo=UTC))
    readings = drifting_series(D - timedelta(days=74), D)
    for day in WEEK:
        readings[day] = 25.0
    seed(readings)
    span = [D + timedelta(days=k) for k in range(-2, 3)]

    with TestClient(app) as client:
        response = get(client, **{"from": span[0].isoformat(), "to": span[-1].isoformat()})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["date"] == span[-1].isoformat()
    assert [p["date"] for p in body["points"]] == [d.isoformat() for d in span]
    assert body["readings_in_window"] == 5
    assert body["verdict"] == "hrv_unavailable"
    assert body["below_by"] is None
    assert [p["ln_rmssd"] is None for p in body["points"]] == [False, False, False, True, True]
    for point, day in zip(body["points"][3:], span[3:], strict=True):
        band = band_for(day, readings)
        assert point["swc_low"] == pytest.approx(band.lo) and point["swc_high"] == pytest.approx(band.hi)


# ---------------------------------------------------------------------------
# the range cap
# ---------------------------------------------------------------------------


def test_a_range_longer_than_366_days_is_a_422(configure) -> None:
    """366 days parses and answers; 367 is a 422 naming both parameters and
    their values. Nothing else bounds the per-day loop."""
    configure("UTC")
    with TestClient(app) as client:
        ok = get(client, **{"from": "2025-09-07", "to": "2026-09-08"})
        too_long = get(client, **{"from": "2025-09-06", "to": "2026-09-08"})

    assert ok.status_code == 200, ok.text
    assert len(ok.json()["points"]) == 367
    assert too_long.status_code == 422, too_long.text
    detail = str(too_long.json()["detail"])
    for needle in ("from", "to", "2025-09-06", "2026-09-08", "366"):
        assert needle in detail, needle


# ---------------------------------------------------------------------------
# the contract pin: stricter than check_drift.py, and observed red first
# ---------------------------------------------------------------------------


def test_the_contract_promises_nothing_stricter_than_the_route_enforces() -> None:
    """For ``GET /metrics/hrv``: (a) every parameter the contract declares
    exists as-built, (b) nothing is required in the contract while optional
    as-built, (c) the operation is marked ``implemented``, and (d) the
    contract's ``HrvTrend`` carries the verdict blocks additively -- every
    property it names is one the as-built response model serialises, and the
    UI's ``points[]`` shape is untouched."""
    with CONTRACT.open(encoding="utf-8") as fh:
        contract = yaml.safe_load(fh)
    built = app.openapi()
    contract_op = contract["paths"]["/metrics/hrv"]["get"]
    built_op = built["paths"]["/metrics/hrv"]["get"]
    built_params = {p["name"]: p for p in built_op["parameters"] if p["in"] == "query"}

    missing = [p["name"] for p in contract_op["parameters"] if p["name"] not in built_params]
    assert not missing, f"the contract declares query parameters the route does not accept: {missing}"
    stricter = [
        p["name"]
        for p in contract_op["parameters"]
        if p.get("required") and not built_params[p["name"]].get("required", False)
    ]
    assert not stricter, f"required in the contract but optional as-built: {stricter}"
    assert contract_op["x-readiness"] == "implemented"
    assert contract_op["operationId"] == built_op["operationId"] == "getHrvTrend"

    trend = contract["components"]["schemas"]["HrvTrend"]["properties"]
    built_trend = built["components"]["schemas"]["HrvTrendResponse"]["properties"]
    point_fields = {"date", "ln_rmssd", "baseline", "swc_low", "swc_high"}
    assert set(trend["points"]["items"]["properties"]) == point_fields
    assert set(built["components"]["schemas"]["HrvPoint"]["properties"]) == point_fields
    for block in ("date", "from", "verdict", "ln_rmssd_7d_mean", "band", "baseline", "window",
                  "readings_in_window", "included", "excluded", "thresholds"):
        assert block in trend, f"the contract's HrvTrend lacks the verdict block `{block}`"
    undeclared = sorted(set(trend) - set(built_trend))
    assert not undeclared, f"the contract names response fields the route does not serve: {undeclared}"
    assert set(trend["verdict"]["enum"]) == set(built_trend["verdict"]["enum"])
