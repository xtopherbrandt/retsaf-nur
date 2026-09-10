import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from runcoach_api.metrics import hrv_trend


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class IngestResponse(BaseModel):
    session_id: str
    quality_flags: list[str]


# ---------------------------------------------------------------------------
# GET /metrics/hrv (F005, T085)
#
# The response is reproducibility-critical (``research/00`` §1.6): a reader
# must be able to recompute the verdict by hand from what it carries, which is
# why the thresholds, the band's own mean and half-width, and every excluded
# row with its reason are in the payload rather than only in the docs. The
# field descriptions below are the documentation the contract (T091) and the
# UI read from ``/openapi.json``; the reason strings are the module's own
# constants so the schema cannot drift from what the module emits.
# ---------------------------------------------------------------------------

_EXCLUSION_REASONS = ", ".join(
    (
        f"`{hrv_trend.REASON_PRE_AMENDMENT_WINDOW}`",
        f"`{hrv_trend.REASON_NULL_TIER}`",
        f"`{hrv_trend.REASON_UNKNOWN_TIER}: <tier>`",
        f"`{hrv_trend.REASON_UNUSABLE_VALUE}: <value>`",
        f"`{hrv_trend.REASON_OFF_BASELINE_TIER}: <tier>`",
        f"`{hrv_trend.REASON_SAME_DAY_LATER_CAPTURE}`",
        f"`{hrv_trend.REASON_BEFORE_RESET}: <{hrv_trend.REASON_COVERAGE_GAP}|{hrv_trend.REASON_TIER_CHANGE}>`",
        f"`{hrv_trend.REASON_OUTSIDE_WINDOWS}`",
    )
)


class Band(BaseModel):
    """The SWC band in log space over the baseline: ``mean +/- half_width``."""

    mean: float = Field(description="Mean of ln rMSSD over the baseline readings.")
    half_width: float = Field(
        description=(
            "max(swc_factor * sample SD of ln rMSSD over the baseline, band_floor). "
            "`floored` says which branch produced it."
        )
    )
    lo: float = Field(description="mean - half_width. The verdict is hrv_suppressed strictly below this.")
    hi: float = Field(description="mean + half_width. A mean above it is hrv_normal, not unavailable.")
    floored: bool = Field(description="True when the computed half-width fell below band_floor and the floor was used.")


class Baseline(BaseModel):
    window: tuple[datetime.date, datetime.date] = Field(
        description=(
            "Closed local-date interval the baseline readings were taken from: [max(date-66, reset_on), "
            "date-7]. When a reset lands after date-7 (a coverage gap ending inside the judged week) the "
            "interval is empty and is rendered exactly as the formula yields it -- first after last -- "
            "with `n` 0, so the clip can be verified from `reset_on` and `date`; clients must not assume "
            "window[0] <= window[1]."
        )
    )
    n: int = Field(description="Number of baseline readings (one per local day, on `tier`).")
    tier: str | None = Field(
        description=(
            "The source tier the baseline and the window are built on; null only when no reading of any "
            "tier exists in [date-66, date]."
        )
    )
    established: bool = Field(
        description="n >= min_baseline_readings. Below it, hrv_suppressed is never emitted."
    )
    reset_on: datetime.date | None = Field(
        description="Local day the current baseline era began, when a reset was detected; else null."
    )
    reset_reason: Literal["coverage_gap", "tier_change"] | None = Field(
        description=(
            "Why the baseline was re-established: more than gap_reset_days consecutive local days with no "
            "entry in the post-exclusion series, or a sustained source-tier change. Null when nothing reset. "
            "A timezone change is never a reset."
        )
    )


class IncludedReading(BaseModel):
    """One reading that contributed to the 7-day window mean."""

    date: datetime.date = Field(description="The athlete's local day, in `timezone`.")
    session_id: str
    tier: str
    rmssd_ms: float = Field(description="resting_rmssd_ms as stored; ln of this is what the mean averages.")


class ExcludedReading(BaseModel):
    """One stored row inside [date-66, date] that contributed to neither the baseline nor the window."""

    date: datetime.date = Field(description="The athlete's local day, in `timezone`.")
    session_id: str
    reason: str = Field(
        description=(
            "Why the row contributed to nothing. One of: "
            + _EXCLUSION_REASONS
            + ". `outside_windows` cannot appear in this list -- rows outside [date-66, date] are read "
            "(back to date-126, for the tier-change rule) but are not listed."
        )
    )


class Thresholds(BaseModel):
    """The heuristic constants the verdict was computed with (spec 03 §3.7; construction reference)."""

    baseline_days: int
    min_baseline_readings: int
    min_window_readings: int
    gap_reset_days: int
    band_floor: float
    swc_factor: float


class HrvPoint(BaseModel):
    """One local day of the chart's series (the UI contract's ``HrvTrend.points[]``),
    judged against **its own** baseline ``[date-66, date-7]`` -- not against `to`'s.

    The band is a property of the baseline (IDEA-044), so its three fields
    are null *together*, and only when that day's baseline holds fewer than
    two readings. A baseline that is computable but not established
    (2 <= n < 14) still carries its band here even though the verdict for
    that day is withheld: the chart may draw it; the verdict may not suppress
    on it. ``ln_rmssd`` is independent of the band: null whenever the
    post-exclusion series has no reading that day, with or without a band.
    """

    date: datetime.date = Field(description="The athlete's local day, in `timezone`.")
    ln_rmssd: float | None = Field(
        description=(
            "ln of that day's reading on the baseline tier; null when the post-exclusion series has no "
            "reading that local day (the day may still carry a band)."
        )
    )
    baseline: float | None = Field(
        description=(
            "Mean of ln rMSSD over that day's own baseline [date-66, date-7]. Null together with swc_low "
            "and swc_high only when that baseline holds fewer than two readings; an unestablished but "
            "computable baseline (2 <= n < min_baseline_readings) still carries all three."
        )
    )
    swc_low: float | None = Field(description="That day's band.lo; null iff `baseline` is null.")
    swc_high: float | None = Field(description="That day's band.hi; null iff `baseline` is null.")


class HrvTrendResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    date: datetime.date = Field(description="The local day the verdict is about (`to`).")
    from_: datetime.date = Field(
        alias="from", description="The start of the requested range; defaults to `to`."
    )
    timezone: str = Field(description="The IANA zone every row was bucketed into local days with.")
    points: list[HrvPoint] = Field(
        description=(
            "One entry per local day in [from, date], in date order, each judged against its own baseline. "
            "The last entry describes `date` and its band equals `band`. At most 367 entries: a range "
            "longer than 366 days is a 422."
        )
    )
    verdict: Literal["hrv_normal", "hrv_suppressed", "hrv_unavailable"]
    ln_rmssd_7d_mean: float | None = Field(
        description=(
            "Mean of ln rMSSD over the readings in `window`. Null only when the window is empty; it is "
            "reported even when readings_in_window is below the minimum, so an unavailable verdict is "
            "still checkable by hand."
        )
    )
    below_by: float | None = Field(
        description="band.lo - ln_rmssd_7d_mean when the verdict is hrv_suppressed; null otherwise."
    )
    band: Band | None = Field(
        description=(
            "Null only when the baseline holds fewer than two readings. Asserted whenever it can be built, "
            "established or not, and whether or not the window has readings: it is a property of the "
            "baseline."
        )
    )
    baseline: Baseline
    window: tuple[datetime.date, datetime.date] = Field(
        description="The closed local-date interval [date-6, date] the mean is taken over."
    )
    readings_in_window: int = Field(description="Number of readings in `window`; below min_window_readings is unavailable.")
    included: list[IncludedReading] = Field(
        description="The readings that fed ln_rmssd_7d_mean, only; the baseline is summarised by `baseline`."
    )
    excluded: list[ExcludedReading] = Field(
        description="Every stored row inside [date-66, date] that fed neither the baseline nor the window."
    )
    thresholds: Thresholds
