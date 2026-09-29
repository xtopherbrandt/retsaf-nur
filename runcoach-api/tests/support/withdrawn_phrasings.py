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

SILENCE_RATE_WITHDRAWN = (
    "replaces a chest strap every four months",
    "129 of 365 days -- 35% of the calendar",
)

RESET_COMPOSITION_WITHDRAWN = (
    "a coverage gap or a tier change collapses the baseline",
    "a coverage gap **or a tier change** collapses the baseline deliberately",
    "a tier change or a coverage gap collapses the baseline",
    "gap resets and tier changes collapse the baseline",
    "coverage-gap or tier-change reset",
    "tier-change or coverage-gap reset",
    "every reset collapses the baseline",
    "either reset collapses the baseline",
    "any reset collapses the baseline",
    "both resets collapse the baseline",
    "a baseline re-establishment (a coverage gap or a source-tier change, §3.3) collapses the baseline deliberately",
)

THRESHOLDS_WITHDRAWN = (
    "the heuristic constants the verdict was computed with (spec 03",
    "the heuristic constants the verdict was computed with (spec/03",
)

_REPO_ROOT = Path(__file__).resolve().parents[3]

WITHDRAWN_SCAN_ANCHORS = (
    (
        _REPO_ROOT / "runcoach-api" / "src" / "runcoach_api" / "metrics" / "hrv_trend.py",
        "one history, the boundary dated 2026-07-03 on the full population and 2026-08-14 on the gap-clipped one",
    ),
    (
        _REPO_ROOT / "runcoach-api" / "src" / "runcoach_api" / "schemas.py",
        "accepted, measured against shipped F005 rather than denied",
    ),
    (
        _REPO_ROOT / "contracts" / "openapi.yaml",
        "decision-record ids this reply is grounded in",
    ),
    (
        _REPO_ROOT / "runcoach-api" / "tests" / "test_hrv_trend_endpoint.py",
        "took the contract to 0.2.0-draft",
    ),
    (
        _REPO_ROOT / "runcoach-api" / "tests" / "test_hrv_trend_reset.py",
        "and no reset is ever reported either",
    ),
    (
        _REPO_ROOT / "spec-mirror" / "features" / "F005-resting-hrv-trend.md",
        "before a worktree-isolated builder can run the drift check",
    ),
    (
        _REPO_ROOT / "spec-mirror" / "references" / "F005-trend-construction.md",
        "not the absence of one string",
    ),
)
