"""The withdrawn ``reset_reason`` phrasings, and the files scanned for them."""

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

_REPO_ROOT = Path(__file__).resolve().parents[3]
WITHDRAWN_SCAN_FILES = (
    (
        _REPO_ROOT / "runcoach-api" / "src" / "runcoach_api" / "metrics" / "hrv_trend.py",
        "the gap's clip is the later one and this one removes nothing",
    ),
    (
        _REPO_ROOT / "runcoach-api" / "src" / "runcoach_api" / "schemas.py",
        "that fed neither the baseline nor the window",
    ),
    (
        _REPO_ROOT / "contracts" / "openapi.yaml",
        "decision-record ids this reply is grounded in",
    ),
    (
        _REPO_ROOT / "runcoach-api" / "tests" / "test_hrv_trend_endpoint.py",
        "which is the parameter's problem and is named as such",
    ),
    (
        _REPO_ROOT / "runcoach-api" / "tests" / "test_hrv_trend_reset.py",
        "nothing else separates the two runs",
    ),
    (
        _REPO_ROOT / ".shipyard" / "spec" / "features" / "F005-resting-hrv-trend.md",
        "before a worktree-isolated builder can run the drift check",
    ),
    (
        _REPO_ROOT / ".shipyard" / "spec" / "references" / "F005-trend-construction.md",
        "not the absence of one string",
    ),
)
