"""``riegel_b.fit`` is the one source of the Riegel-b fit (F032 Technical Notes): a pure function
over ``(d_eq_m, duration_s, weight)`` triples that returns the line, its error and the sums it was
built from, and ``estimate`` calls it once.

The expected figures come from ``support/riegel_oracle.py`` (the F029 reference's fit restated in
raw sums, importing nothing from the estimator) and from the F032 reference section 8's printed
values, typed in here. ``test_riegel_b_byte_identity.py`` separately pins that the refactor moved
no served float.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from runcoach_api.metrics import riegel_b
from runcoach_api.metrics.riegel_b import Fit, estimate, fit

ORACLE_PATH = Path(__file__).parent / "support" / "riegel_oracle.py"
if "riegel_oracle" in sys.modules:
    riegel_oracle = sys.modules["riegel_oracle"]
else:
    _SPEC = importlib.util.spec_from_file_location("riegel_oracle", ORACLE_PATH)
    riegel_oracle = importlib.util.module_from_spec(_SPEC)  # type: ignore[arg-type]
    sys.modules["riegel_oracle"] = riegel_oracle
    _SPEC.loader.exec_module(riegel_oracle)  # type: ignore[union-attr]

ORACLE_TOLERANCE = 1e-9
PRINTED_TOLERANCE = 5e-7 + 1e-12  # half a unit in the 6th decimal

ZONE = ZoneInfo("Europe/Berlin")
INSTANT = datetime(2026, 10, 24, 12, 0, tzinfo=ZONE)

# F032 reference section 8, as (d_eq_m, duration_s, weight) with the printed b and se.
E1 = [(5000.0, 1200.0, 1.0), (10000.0, 2526.937004, 1.0), (21097.5, 5465.036808, 1.0)]
E2 = [(5000.0, 1200.0, 1.0), (10000.0, 2502.0, 1.0), (21097.5, 5530.0, 1.0), (42195.0, 11610.0, 1.0)]
E3 = [(5000.0, 1200.0, 1.0), (10000.0, 2502.0, 1.0), (21097.5, 5530.0, 1.0), (42195.0, 11610.0, 0.5)]
E4 = [
    (5000.0, 1200.0, 1.0),
    (5000.0, 1203.0, 1.0),
    (10000.0, 2540.0, 1.0),
    (10000.0, 2545.0, 1.0),
    (42195.0, 11300.0, 1.0),
]
A = [(5000.0, 1200.0, 1.0), (10000.0, 2574.828566, 1.0), (21097.5, 5841.627023, 1.0)]
WEIGHTED = [(5000.0, 1200.0, 0.6), (10000.0, 2520.0, 1.0), (21100.0, 5600.0, 0.9), (42200.0, 12000.0, 0.7)]

SECTION_8 = {
    "E1": (E1, 1.052765, 0.011856, 3.077684),
    "E2": (E2, 1.063891, 0.001578, 1.885618),
    "E3": (E3, 1.063273, 0.001710, 3.077684),
    "E4": (E4, 1.050660, 0.007739, 1.637744),
    "A": (A, 1.099280, 0.001187, 3.077684),
}


def _oracle_sums(races: list[tuple[float, float, float]]) -> tuple[float, float, float, float, list[float]]:
    """``sum_w``, ``x_w``, ``y_w``, ``sum_w_r2`` and the log residuals from the oracle's fit."""
    oracle = riegel_oracle.fit(races)
    sum_w = sum(w for _d, _t, w in races)
    x_w = sum(w * math.log(d) for d, _t, w in races) / sum_w
    y_w = sum(w * math.log(t) for _d, t, w in races) / sum_w
    log_residuals = [math.log1p(residual) for residual in oracle.residuals]
    sum_w_r2 = sum(w * r * r for (_d, _t, w), r in zip(races, log_residuals, strict=True))
    return sum_w, x_w, y_w, sum_w_r2, log_residuals


def test_fit_returns_the_line_and_its_sums() -> None:
    """E2's four races: b and se as the reference prints them, and the weighted means, the weight
    sum and the weighted squared-residual sum as the oracle computes them."""
    result = fit(E2)
    assert isinstance(result, Fit)
    assert result.clears_bar is True
    assert abs(result.b - 1.063891) <= PRINTED_TOLERANCE, result.b
    assert abs(result.se - 0.001578) <= PRINTED_TOLERANCE, result.se
    assert abs(result.t - 1.885618) <= PRINTED_TOLERANCE, result.t
    assert result.n_eff == 4.0
    sum_w, x_w, y_w, sum_w_r2, log_residuals = _oracle_sums(E2)
    assert result.sum_w == sum_w == 4.0
    assert abs(result.x_w - x_w) <= ORACLE_TOLERANCE, (result.x_w, x_w)
    assert abs(result.y_w - y_w) <= ORACLE_TOLERANCE, (result.y_w, y_w)
    assert abs(result.sum_w_r2 - sum_w_r2) <= ORACLE_TOLERANCE, (result.sum_w_r2, sum_w_r2)
    assert len(result.r) == 4
    for got, want in zip(result.r, log_residuals, strict=True):
        assert abs(got - want) <= ORACLE_TOLERANCE, (got, want)
    # The error is the documented formula over the sums the fit returns.
    sxx = sum(w * (math.log(d) - result.x_w) ** 2 for d, _t, w in E2)
    assert abs(result.se - math.sqrt(result.sum_w_r2 / (result.n_eff - 2) / sxx)) <= ORACLE_TOLERANCE


@pytest.mark.parametrize("name", [*SECTION_8, "weighted"])
def test_fit_matches_the_oracle(name: str) -> None:
    """E1 to E4, A and a set with four different weights: b, se, t, the interval ends and the log
    residuals agree with the oracle to 1e-9, and with the reference's printed figures where it
    prints them."""
    if name == "weighted":
        races, printed = WEIGHTED, None
    else:
        races, *printed = SECTION_8[name]
    oracle = riegel_oracle.fit(races)
    result = fit(races)
    assert result.clears_bar is True
    got = (result.b, result.se, result.t, result.b - result.t * result.se, result.b + result.t * result.se)
    want = (oracle.b, oracle.error, oracle.t, oracle.low, oracle.high)
    for g, o in zip(got, want, strict=True):
        assert abs(g - o) <= ORACLE_TOLERANCE, (name, g, o)
    if printed is not None:
        for g, p in zip(got[:3], printed, strict=True):
            assert abs(g - p) <= PRINTED_TOLERANCE, (name, g, p)
    sum_w, x_w, y_w, sum_w_r2, log_residuals = _oracle_sums(races)
    assert abs(result.sum_w - sum_w) <= ORACLE_TOLERANCE
    assert abs(result.x_w - x_w) <= ORACLE_TOLERANCE
    assert abs(result.y_w - y_w) <= ORACLE_TOLERANCE
    assert abs(result.sum_w_r2 - sum_w_r2) <= ORACLE_TOLERANCE
    assert [round(g, 9) for g in result.r] == [round(o, 9) for o in log_residuals]
    n_eff = sum_w * sum_w / sum(w * w for _d, _t, w in races)
    assert abs(result.n_eff - n_eff) <= ORACLE_TOLERANCE


@pytest.mark.parametrize(
    ("name", "races"),
    [
        ("two races", [(5000.0, 1200.0, 1.0), (10000.0, 2520.0, 1.0)]),
        ("three 5Ks", [(5000.0, 1200.0, 1.0), (5000.0, 1210.0, 1.0), (5000.0, 1190.0, 1.0)]),
        ("none", []),
    ],
)
def test_fit_below_the_bar_reports_it(name: str, races: list[tuple[float, float, float]]) -> None:
    """Two races, or three at one distance, do not clear the bar: ``clears_bar`` is false, the
    effective count is still reported and every figure is null, with no division attempted."""
    result = fit(races)
    assert result.clears_bar is False, name
    assert result.n_eff == float(len(races)), name
    assert (result.b, result.se, result.t, result.x_w, result.y_w, result.sum_w, result.sum_w_r2, result.r) == (
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    ), name


def _race(session_id: str, d_eq: float, time_s: float, age_days: float = 100.0, **over: object) -> dict:
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
            "gap_avg_pace_s_per_km": 1000 * time_s / d_eq,
            "gap_coverage": 1.0,
            "gps_degraded_fraction": 0.0,
            "flags": [],
        },
    }
    row.update(over)
    return row


def test_estimate_calls_fit_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """``estimate`` takes its b, error, t and residuals from one call of ``fit`` on the efforts it
    screened in, in start order."""
    calls: list[list[tuple[float, float, float]]] = []
    real = riegel_b.fit

    def counting(efforts: object) -> Fit:
        calls.append(list(efforts))  # type: ignore[call-overload]
        return real(calls[-1])

    monkeypatch.setattr(riegel_b, "fit", counting)
    rows = [
        _race("c", 21097.5, 5530.0, 200),
        _race("a", 5000.0, 1200.0, 400),
        _race("d", 42195.0, 11610.0, 100),
        _race("b", 10000.0, 2502.0, 300),
    ]
    result = estimate(rows, INSTANT, None)
    assert len(calls) == 1, calls
    assert [session.session_id for session in result.efforts] == ["a", "b", "c", "d"]
    assert [(d, t, w) for d, t, w in calls[0]] == [(e.gap_equivalent_m, e.duration_s, e.weight) for e in result.efforts]
    direct = real(calls[0])
    assert (result.value, result.error, result.t_multiplier) == (direct.b, direct.se, direct.t)
    assert [e.residual for e in result.efforts] == [math.expm1(r) for r in direct.r]
    assert abs(result.value - 1.063891) <= PRINTED_TOLERANCE


def test_screen_reads_the_readers_unreadable() -> None:
    """A row carrying the reader's ``unreadable`` key is excluded for the key's reason even when
    the stored values would read: the reader owns the unreadable-row logic. A row without the key
    (one built by hand) is still screened from its stored values."""
    flagged = _race("k", 5000.0, 1200.0, unreadable="quality_flags_unreadable")  # flags "[]" decode fine
    summary = _race("s", 10000.0, 2502.0, timer_time_s=None, unreadable="summary_unreadable")
    cleared = _race("c", 21097.5, 5530.0, quality_flags="not json", unreadable=None)
    keyless = _race("n", 42195.0, 11610.0, quality_flags="not json")
    fine = _race("f", 5000.0, 1203.0, unreadable=None)
    result = estimate([flagged, summary, cleared, keyless, fine], INSTANT, None)
    assert {e.session_id: e.reason for e in result.excluded} == {
        "k": "quality_flags_unreadable",
        "s": "summary_unreadable",
        "n": "quality_flags_unreadable",
    }
    assert [e.session_id for e in result.efforts] == ["c", "f"]
