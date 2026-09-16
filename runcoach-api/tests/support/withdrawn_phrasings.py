"""The withdrawn contract phrasings. Declarations only -- see the scan."""

from __future__ import annotations

from pathlib import Path

RESET_REASON_WITHDRAWN = (
    "never sits beside an unclipped",
    "last stretch of every",
    "every report's lifetime",
    "only while the old tier still sustains",
    "is no longer sustained by the old tier",
)

RESET_REASON_WITHDRAWN_IDIOMS = (
    "so that (b) fails",
    "the report stops only when",
    "the reset stops being reported when the previous window",
)

VERDICT_WITHDRAWN = (
    "too few readings this week, a below-band week on an unestablished baseline",
    "suppression is withheld, not read as normal",
    "hrv_suppressed only when the 7-day mean is strictly below band.lo on an established baseline",
    "then a fresh baseline is begun for the new tier and a suppression verdict is withheld until it is established",
)

WINDOW_WITHDRAWN = (
    "tier_change`). for `coverage_gap` r is reset_on",
    "tier_change). for coverage_gap r is reset_on",
)

ESTABLISHED_WITHDRAWN = ("below it, hrv_suppressed is never emitted",)

THRESHOLDS_WITHDRAWN = (
    "the heuristic constants the verdict was computed with (spec 03",
    "the heuristic constants the verdict was computed with (spec/03",
)

_REPO_ROOT = Path(__file__).resolve().parents[3]

WITHDRAWN_SCAN_ANCHORS = (
    (
        _REPO_ROOT / "runcoach-api" / "src" / "runcoach_api" / "metrics" / "hrv_trend.py",
        "one history, the boundary dated 2026-07-03 on the full population and 2026-08-14 on the gap-clipped one",
        True,
    ),
    (
        _REPO_ROOT / "runcoach-api" / "src" / "runcoach_api" / "schemas.py",
        "that fed neither the baseline nor the window",
        True,
    ),
    (
        _REPO_ROOT / "contracts" / "openapi.yaml",
        "decision-record ids this reply is grounded in",
        True,
    ),
    (
        _REPO_ROOT / "runcoach-api" / "tests" / "test_hrv_trend_endpoint.py",
        "which is the parameter's problem and is named as such",
        True,
    ),
    (
        _REPO_ROOT / "runcoach-api" / "tests" / "test_hrv_trend_reset.py",
        "nothing else separates the two runs",
        True,
    ),
    (
        _REPO_ROOT / ".shipyard" / "spec" / "features" / "F005-resting-hrv-trend.md",
        "before a worktree-isolated builder can run the drift check",
        False,
    ),
    (
        _REPO_ROOT / ".shipyard" / "spec" / "references" / "F005-trend-construction.md",
        "not the absence of one string",
        False,
    ),
)
