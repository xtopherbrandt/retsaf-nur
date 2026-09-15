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
    hi: float = Field(
        description=(
            "mean + half_width. A mean above it is hrv_normal on an established baseline, not "
            "unavailable; on an unestablished one it is hrv_unavailable like every other position "
            "(see verdict)."
        )
    )
    floored: bool = Field(description="True when the computed half-width fell below band_floor and the floor was used.")


class Baseline(BaseModel):
    window: tuple[datetime.date, datetime.date] = Field(
        description=(
            "Closed local-date interval the baseline readings were taken from: [max(date-66, R), "
            "date-7], where R is the day the current baseline era began. **R is not reset_on.** A "
            "tier-change era boundary clips this window whether or not the change is reported, so a "
            "clipped window beside a null `reset_reason` is a correct state: the band is era-correct "
            "and the athlete is simply told nothing about it (the readings clipped away are listed "
            "`before_reset: tier_change`). For `coverage_gap`, R is the later of the resumption and "
            "any era boundary: the two clips compose as the later of their first days, so R is "
            "reset_on only when no era boundary falls after the resumption, and window[0] may lie "
            "after reset_on. When a reset lands after date-7 (a coverage gap ending inside the judged "
            "week) the interval is empty and is rendered exactly as the formula yields it -- first "
            "after last -- with `n` 0, so the clip can be verified from `reset_on` and `date`; "
            "clients must not assume window[0] <= window[1]."
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
        description=(
            "Local day the current baseline era began, **when the re-establishment is reported**; else "
            "null. It says what the athlete is told, not how `window` was built: a tier-change era "
            "boundary clips `window` whenever it exists, and is reported here only when the other tier "
            "was also not in use in the judged week [date-6, date]. So a null reset_on does not mean the "
            "baseline spans the full 60 days -- read `window`. For `tier_change` it is the era's true "
            "first day and may precede window[0] (the window is clipped at date-66; the era is not, and "
            "reset_on does not slide as it ages). For `coverage_gap` it is "
            "the resumption day: the gap is reported only while the resumption lies inside "
            "[date-66, date], so it never precedes window[0] because the report outlived its clip -- but it "
            "does when a tier-change era boundary clipped the window later than the resumption, since the "
            "two clips compose as the later of their first days and only the *report* is the gap's."
        )
    )
    reset_reason: Literal["coverage_gap", "tier_change"] | None = Field(
        description=(
            "Why the baseline was re-established: more than gap_reset_days consecutive local days with no "
            "entry in the post-exclusion series (`coverage_gap`), or a sustained source-tier change "
            "(`tier_change`). Null when nothing reset. A timezone change is never a reset. Each report has "
            "a lifetime: `coverage_gap` is reported from the resumption until the resumption leaves "
            "[date-66, date] -- 67 days; `tier_change` for at most as long as the previous window "
            "[date-126, date-67] stays sustained by a tier other than the resolved one, and often for "
            "less -- see below. A resumption that was also a device switch is therefore "
            "reported as `coverage_gap` first, then as `tier_change` on the same reset_on, then as null -- "
            "one era, three reports, no event between them (F005 Negative Class). The reason is the "
            "**report** only: the baseline clip is decided by the era boundary alone, so a null here can "
            "sit beside a clipped `window`; a reported `tier_change` is always clipped, though once the "
            "era's first day is date-66 or older the clip `max(date-66, R)` is a no-op and `window` is "
            "indistinguishable from the un-clipped [date-66, date-7]. "
            "That no-op stretch is conditional, not promised: a reported `tier_change` sits beside an "
            "un-clipped `window` only once date-66 has reached R, and then only on the days the report is "
            "still live. Liveness is rule 4's three conditions together, not any one of them alone: the "
            "resolved tier still holds min_baseline_readings distinct days in the baseline window, the "
            "previous window [date-126, date-67] is still sustained by a tier other than the resolved one, "
            "and the two eras still do not interleave -- the era boundary's stray readings fewer than "
            "min_baseline_readings days in all and fewer than min_window_readings days inside the judged week "
            "[date-6, date] (`tier_change_reset` clauses (a), (b) and (c)). Any one can lapse while the "
            "others hold -- a new tier that never reaches min_baseline_readings, an old tier whose density "
            "decays, a few days of the other device inside the judged week -- and this is null on such a day. "
            "So every day a `tier_change` is reported before date-66 reaches R carries a clipped `window`, "
            "and a client cannot infer from seeing a `tier_change` that an un-clipped `window` will follow "
            "(F005 decision log D4, 2026-09-13; T103, T105, T109)."
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
    verdict: Literal["hrv_normal", "hrv_suppressed", "hrv_unavailable"] = Field(
        description=(
            "hrv_normal and hrv_suppressed both assert an established baseline -- baseline.n at or "
            "above the min_baseline_readings threshold the response echoes -- and differ only in "
            "where the 7-day mean sits: strictly below band.lo is hrv_suppressed, inside or above "
            "the band is hrv_normal. hrv_unavailable when no band, too few readings this week, any "
            "week judged against an unestablished baseline (below, inside or above the band alike: "
            "3.7.3 withholds the suppression there, and hrv_normal there would tell a consumer "
            "readiness is intact on evidence this same response reports unestablished, the "
            "up-regulating direction research/00 1.7 forbids), or when `date` is after the athlete's "
            "local today in `timezone` -- whatever the window holds, no verdict is asserted about a "
            "day that has not happened, and the other fields are still reported as computed. The "
            "band is still reported whenever the baseline can build one, established or not, so an "
            "unavailable verdict remains checkable by hand."
        )
    )
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
    readings_in_window: int = Field(
        description=(
            "Number of readings in `window`; below min_window_readings is unavailable. Reported as "
            "computed even for a future `date`, whose verdict is unavailable regardless."
        )
    )
    included: list[IncludedReading] = Field(
        description="The readings that fed ln_rmssd_7d_mean, only; the baseline is summarised by `baseline`."
    )
    excluded: list[ExcludedReading] = Field(
        description="Every stored row inside [date-66, date] that fed neither the baseline nor the window."
    )
    thresholds: Thresholds
