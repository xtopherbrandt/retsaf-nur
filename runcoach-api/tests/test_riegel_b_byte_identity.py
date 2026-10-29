"""``riegel_b.estimate`` and the ``riegel_exponent`` entry of ``GET /state`` are bit-exact against a
frozen copy of the fit (F032 Technical Notes: what ``GET /state`` serves stays byte-identical).

The pin is **differential and in process**: every figure the estimator serves (b, the error, the
interval ends, t, the residuals) is compared by ``float.hex()`` with ``support/riegel_b_base.py``,
a verbatim copy of the fit's arithmetic frozen at the commit before the fit was refactored, run on
the same efforts in the same process. No float literal is stored, because ``math.expm1`` differs
between the Windows venv and Linux on about a third of random inputs and the release gate runs this
file on Linux; a same-platform comparison is exact on both.

The worked sets are typed in here from the F029 reference section 4 and the F032 reference section
8 (nothing is imported from ``test_riegel_b_estimator.py``); the route test seeds the isolated
store as ``test_riegel_b_route.py`` does (``db.persist``, ``PUT /race-tags``, ``support/ctl_path.py``
for the load chart, ``main._utcnow`` frozen). A tolerance anywhere here would defeat the pin.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sqlite3
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.metrics import riegel_b
from runcoach_api.models import Context, Record, Session

SUPPORT = Path(__file__).parent / "support"


def _load_support(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


riegel_b_base = _load_support("riegel_b_base")
session_loads = _load_support("session_loads")
ctl_path = _load_support("ctl_path")

ZONE = ZoneInfo("Europe/Berlin")
INSTANT = datetime(2026, 10, 24, 12, 0, tzinfo=ZONE)
FIGURES = ("value", "error", "interval_low", "interval_high", "t_multiplier")
FLAGS = ("unavailable", "population_default", "provisional", "confidence", "below_one")


# ---------------------------------------------------------------------------
# Comparing by bits
# ---------------------------------------------------------------------------


def _hex(value: float | None) -> str | None:
    return None if value is None else float.hex(value)


def _served(result: riegel_b.RiegelEstimate) -> dict:
    """The estimate's figures as hex strings, its flags as given, and the efforts the fit read."""
    figures = {name: _hex(getattr(result, name)) for name in FIGURES}
    figures["residuals"] = [_hex(effort.residual) for effort in result.efforts]
    flags = {name: getattr(result, name) for name in FLAGS}
    efforts = [(effort.gap_equivalent_m, effort.duration_s, effort.weight) for effort in result.efforts]
    return {"figures": figures, "flags": flags, "efforts": efforts}


def _frozen(efforts: list[tuple[float, float, float]]) -> dict:
    base = riegel_b_base.base_fit(efforts)
    figures = {name: _hex(base[name]) for name in FIGURES}
    figures["residuals"] = [_hex(residual) for residual in base["residuals"]]
    return {"figures": figures, "flags": {name: base[name] for name in FLAGS}}


def _assert_bit_identical(result: riegel_b.RiegelEstimate, label: str) -> dict:
    served = _served(result)
    frozen = _frozen(served["efforts"])
    slice_compared = (
        f"{label}: efforts {served['efforts']!r}\n  served {served['figures']}\n  frozen {frozen['figures']}"
    )
    assert served["figures"] == frozen["figures"], slice_compared
    assert served["flags"] == frozen["flags"], f"{label}: served {served['flags']} frozen {frozen['flags']}"
    return served


# ---------------------------------------------------------------------------
# Rows for the estimator
# ---------------------------------------------------------------------------


def _race(
    session_id: str,
    d_eq: float,
    time_s: float,
    age_days: float = 100.0,
    fitness: tuple | None = None,
    pace: object = None,
    **over: object,
) -> dict:
    """One race-tagged running row ``age_days`` before ``INSTANT`` whose F013 GAP pace gives
    ``d_eq``; ``fitness`` is its ``(ctl_before, ctl_earlier, provisional, after_gap)``."""
    row = {
        "session_id": session_id,
        "start_time": (INSTANT.astimezone(UTC) - timedelta(days=age_days)).isoformat(),
        "sport": "running",
        "activity_tag": "race",
        "timer_time_s": time_s,
        "duration_s": None,
        "summary": f'{{"distance_m": {d_eq!r}}}',
        "quality_flags": "[]",
        "admitted_as_workout": False,
        "features": {
            "gap_avg_pace_s_per_km": 1000 * time_s / d_eq if pace is None else pace,
            "gap_coverage": 1.0,
            "gps_degraded_fraction": 0.0,
            "flags": [],
        },
        "_fitness": fitness,
    }
    row.update(over)
    return row


def _fitness(rows: list[dict]) -> dict:
    return {
        datetime.fromisoformat(row["start_time"]).astimezone(ZONE).date(): row["_fitness"]
        for row in rows
        if row.get("_fitness") is not None
    }


def _run(rows: list[dict]) -> riegel_b.RiegelEstimate:
    return riegel_b.estimate(rows, INSTANT, _fitness(rows))


FADING_AGE = 1004.0  # weight (1095 - 1004) / 182 = 0.5


def _f029_abcd(d_age: float = 100.0, screened: bool = False) -> list[dict]:
    """The F029 reference section 4 races A to D with their fitness figures (CTL the day before /
    29 days before); ``screened`` hands the figures to the fit, which then drops D (29 < 0.7 x 50)."""
    fit = (lambda f: f) if screened else (lambda f: None)
    return [
        _race("a", 5000.0, 1200.0, 400, fit((36, 37, False, False))),
        _race("b", 10000.0, 2520.0, 300, fit((40, 41, False, False))),
        _race("c", 21100.0, 5600.0, 200, fit((42, 40, False, False))),
        _race("d", 42200.0, 12000.0, d_age, fit((29, 50, False, False))),
    ]


def _f032(*races: tuple[float, float, float]) -> list[dict]:
    """F032 reference section 8 races as ``(D_eq m, T s, weight)``; a weight of 0.5 is the fading
    age, every other weight is 1 at a distinct age inside the window."""
    rows = []
    for i, (d_eq, time_s, weight) in enumerate(races):
        age = FADING_AGE if weight == 0.5 else 100.0 + 10 * i
        rows.append(_race(f"e{i}", d_eq, time_s, age))
    return rows


def _bar_by_the_slack() -> list[dict]:
    """A 5K and a 10K at weight 1 and a half 913 days 609.010783 s old: an effective count of
    2.999999999, which the bar reads as 3 only with ``COUNT_SLACK``."""
    rows = [
        _race("a", 5000.0, 1200.0, 100),
        _race("b", 10000.0, 2520.0, 200),
        _race("c", 21100.0, 5600.0, 0),
    ]
    rows[2]["start_time"] = (
        INSTANT.astimezone(UTC) - timedelta(days=913, seconds=609, microseconds=10783)
    ).isoformat()
    return rows


WORKED_SETS: dict[str, list[dict]] = {
    "f029_abcd": _f029_abcd(),
    "f029_abc_d_screened": _f029_abcd(screened=True),
    "f029_abcd_d_fading": _f029_abcd(d_age=FADING_AGE),
    "f029_ab_population": _f029_abcd()[:2],
    "f029_ahf_below_one": [
        _race("a", 5000.0, 1200.0, 300),
        _race("h", 10000.0, 2350.0, 200),
        _race("f", 21100.0, 4700.0, 100),
    ],
    "f032_e1": _f032((5000, 1200, 1), (10000, 2526.937004, 1), (21097.5, 5465.036808, 1)),
    "f032_e2": _f032((5000, 1200, 1), (10000, 2502, 1), (21097.5, 5530, 1), (42195, 11610, 1)),
    "f032_e3": _f032((5000, 1200, 1), (10000, 2502, 1), (21097.5, 5530, 1), (42195, 11610, 0.5)),
    "f032_e4": _f032((5000, 1200, 1), (5000, 1203, 1), (10000, 2540, 1), (10000, 2545, 1), (42195, 11300, 1)),
    "f032_a_critic": _f032((5000, 1200, 1), (10000, 2574.828566, 1), (21097.5, 5841.627023, 1)),
    "bar_by_count_slack": _bar_by_the_slack(),
    "three_5ks_population": [
        _race(f"5k{i}", 5000.0 + 20 * i, 1200.0 + 5 * i, 100 + 30 * i) for i in range(3)
    ],
    "estimate_not_finite_inf": _f029_abcd()[:3] + [_race("inf", 42200.0, 12000.0, 50, pace=5e-324)],
    "estimate_not_finite_zero": _f029_abcd()[:3] + [_race("zero", 42200.0, 5e-324, 50, pace=1e10)],
}
# What each set exercises, checked so a set that drifts off its branch is seen.
EXPECTED_BRANCH: dict[str, tuple[str | None, bool, bool]] = {  # (unavailable, population_default, below_one)
    "f029_abcd": (None, False, False),
    "f029_abc_d_screened": (None, False, False),
    "f029_abcd_d_fading": (None, False, False),
    "f029_ab_population": (None, True, False),
    "f029_ahf_below_one": (None, False, True),
    "f032_e1": (None, False, False),
    "f032_e2": (None, False, False),
    "f032_e3": (None, False, False),
    "f032_e4": (None, False, False),
    "f032_a_critic": (None, False, False),
    "bar_by_count_slack": (None, False, False),
    "three_5ks_population": (None, True, False),
    "estimate_not_finite_inf": ("estimate_not_finite", False, False),
    "estimate_not_finite_zero": ("estimate_not_finite", False, False),
}


@pytest.mark.parametrize("name", sorted(WORKED_SETS))
def test_estimate_floats_are_bit_identical(name: str) -> None:
    result = _run(WORKED_SETS[name])
    served = _assert_bit_identical(result, name)
    print(f"  {name}: weights {[w for _d, _t, w in served['efforts']]} figures {served['figures']}")
    assert (result.unavailable, result.population_default, result.below_one) == EXPECTED_BRANCH[name], name
    if name == "f029_abc_d_screened":
        assert [e.session_id for e in result.efforts] == ["a", "b", "c"]
    if name in ("f029_abcd_d_fading", "f032_e3"):
        assert 0.5 in [w for _d, _t, w in served["efforts"]]
    if name == "bar_by_count_slack":
        n_eff = riegel_b.effective_count([w for _d, _t, w in served["efforts"]])
        assert n_eff < 3 and n_eff + riegel_b.COUNT_SLACK >= 3


def test_random_sets_are_bit_identical() -> None:
    """2,000 seeded sets of 3 to 9 races with distances from 3 to 45 km, times off a power law with
    an exponent between 1.0 and 1.15 and up to 3 % of noise, at ages across the whole window (so
    about a sixth of the weights are fractional): every served figure is bit-equal to the frozen
    fit's. The sweep varies count, distance, exponent, noise and age; it holds the instant, the
    zone and the fitness (none) constant."""
    rng = random.Random(20261029)
    fitted = population = fractional = 0
    for n_set in range(2000):
        count = rng.randint(3, 9)
        exponent = rng.uniform(1.0, 1.15)
        rows = []
        for i in range(count):
            d_eq = rng.uniform(3000.0, 45000.0)
            time_s = 0.24 * d_eq**exponent * (1 + rng.uniform(-0.03, 0.03))
            rows.append(_race(f"r{i}", d_eq, time_s, rng.uniform(1.0, 1094.0)))
        result = _run(rows)
        served = _assert_bit_identical(result, f"set {n_set}")
        fitted += result.population_default is False
        population += result.population_default is True
        fractional += sum(w < 1 for _d, _t, w in served["efforts"])
    print(f"  2000 sets: {fitted} fitted, {population} population, {fractional} fractional weights")
    assert fitted > 1000 and population > 0 and fractional > 1000


# ---------------------------------------------------------------------------
# GET /state on the isolated store
# ---------------------------------------------------------------------------

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
FIVE_K = (datetime(2026, 9, 6, 7, 0, tzinfo=UTC), 5000.0, 252.0)
TEN_K = (datetime(2026, 9, 13, 7, 0, tzinfo=UTC), 10000.0, 262.0)
HALF = (datetime(2026, 9, 20, 7, 0, tzinfo=UTC), 21000.0, 268.0)
FADING_MARATHON = (NOW - timedelta(days=FADING_AGE), 42000.0, 285.0)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


def _store(start: datetime, distance_m: float, pace: float) -> str:
    """An untagged running session of ``distance_m`` at ``pace`` s/km with 1 Hz flat records."""
    duration_s = round(distance_m * pace / 1000.0)
    start_time = start.isoformat()
    session = Session(
        session_id=derive_session_id("fr945", start_time),
        sport="running",
        source_vendor="garmin",
        start_time=start_time,
        source_device="fr945",
        timer_time_s=float(duration_s),
        quality_flags=[],
        summary={"duration_s": float(duration_s), "distance_m": distance_m},
        context=Context(),
    )
    speed = 1000.0 / pace
    records = [
        Record(t=float(t), distance=t * speed, speed=speed, altitude=100.0, heart_rate=150.0)
        for t in range(duration_s + 1)
    ]
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        db_module.persist(conn, session, records, [], {})
    finally:
        conn.close()
    return session.session_id


def _stored(session_id: str) -> sqlite3.Row:
    conn = db_module.get_connection()
    try:
        return conn.execute(
            "SELECT start_time, summary FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()


def _put_tags(client: TestClient, session_ids: list[str]) -> None:
    """Connect's mark for each stored run: its start second in UTC, its distance 0.5 % long and its
    duration 0.5 % short (inside F023's 1 %)."""
    items = []
    for n, session_id in enumerate(session_ids):
        row = _stored(session_id)
        summary = json.loads(row["summary"])
        start = datetime.fromisoformat(row["start_time"]).astimezone(UTC)
        items.append(
            {
                "connect_activity_id": 5000
                + int(hashlib.sha256(session_id.encode()).hexdigest()[:6], 16)
                + n,
                "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "distance_m": summary["distance_m"] * 1.005,
                "duration_s": summary["duration_s"] * 0.995,
                "race": True,
            }
        )
    response = client.put("/race-tags", json={"items": items})
    assert response.status_code == 200, response.text
    outcomes = [(i["outcome"], i["session_id"]) for i in response.json()["items"]]
    print(f"  race-tags {outcomes}")
    assert [s for _o, s in outcomes] == session_ids, outcomes


def _plant_chart() -> None:
    """A load chart whose CTL varies day by day from 2026-06-01 to the frozen now, so the races'
    fitness figures are read from it (none falls 30 %); the fading marathon predates the chart."""
    zone = ZoneInfo("UTC")
    start, end = date(2026, 6, 1), NOW.date()
    loads = {start + timedelta(days=n): 30.0 + 4.0 * (n % 9) for n in range((end - start).days + 1)}
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        ctl_path.plant(conn, loads, zone)
    finally:
        conn.close()


def test_state_riegel_entry_is_byte_identical(frozen_clock: None) -> None:
    """Three races at weight 1 and a marathon at weight 0.5 (1004 days old) on the isolated store:
    the ``riegel_exponent`` entry's numeric fields equal the frozen fit run on the served efforts
    (``gap_equivalent_m``, ``duration_s``, ``weight``) by ``float.hex()``, and its other fields
    equal the frozen fit's flags."""
    _plant_chart()
    with TestClient(app) as client:
        ids = [_store(*race) for race in (FADING_MARATHON, FIVE_K, TEN_K, HALF)]
        _put_tags(client, ids)
        conn = db_module.get_connection()
        try:
            for session_id in ids:
                session_loads.plant_load(conn, session_id, value=50.0)
        finally:
            conn.close()
        response = client.get("/state")
    assert response.status_code == 200, response.text
    body = response.json()
    riegel = body["determinants"][1]
    assert riegel["name"] == "riegel_exponent"
    efforts = [(e["gap_equivalent_m"], e["duration_s"], e["weight"]) for e in riegel["efforts"]]
    estimate = riegel["estimate"]
    served = {
        "value": _hex(estimate["value"]),
        "error": _hex(riegel["error"]),
        "interval_low": _hex(estimate["interval"]["low"]),
        "interval_high": _hex(estimate["interval"]["high"]),
        "t_multiplier": _hex(riegel["t_multiplier"]),
        "residuals": [_hex(e["residual"]) for e in riegel["efforts"]],
    }
    frozen = _frozen(efforts)
    print(f"  efforts {efforts}\n  served {served}\n  frozen {frozen['figures']}")
    assert [e["session_id"] for e in riegel["efforts"]] == ids and riegel["excluded"] == []
    assert [w for _d, _t, w in efforts] == [0.5, 1.0, 1.0, 1.0]
    assert served == frozen["figures"], f"served {served}\nfrozen {frozen['figures']}"
    flags = {
        "unavailable": riegel["unavailable"],
        "population_default": riegel["population_default"],
        "provisional": estimate["provisional"],
        "confidence": estimate["confidence"],
        "below_one": riegel["below_one"],
    }
    assert flags == frozen["flags"], f"served {flags}\nfrozen {frozen['flags']}"
    assert flags["population_default"] is False and flags["unavailable"] is None
