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
    lo: float = Field(
        description=(
            "mean - half_width. A mean strictly below it is hrv_suppressed on an established "
            "baseline; on an unestablished one it is hrv_unavailable like every other position "
            "(see verdict)."
        )
    )
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
            "after reset_on. A third clip is per dataset and unreported: an internal capture hole of more "
            "than gap_reset_days (21) silent local days inside the window the first two clips left -- one "
            "tier silent while another bridged it, which the global gap cannot see -- clips this dataset at "
            "its last resumption, and the readings clipped away are listed `before_reset: coverage_gap` "
            "beside a `reset_reason` that stays null or the era rule's. The three clips compose as the "
            "latest first day. When a reset lands after date-7 (a coverage gap ending inside the judged "
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
        description=(
            "n >= min_baseline_readings. Below it both verdicts are withheld -- hrv_suppressed "
            "and hrv_normal alike -- and hrv_unavailable is the only verdict emitted (see verdict)."
        )
    )
    reset_on: datetime.date | None = Field(
        description=(
            "Local day the current baseline era began, **when the reset that began it is reported** "
            "(see `reset_reason`); else "
            "null. It says what the athlete is told, not how `window` was built: a tier-change era "
            "boundary clips `window` whenever it exists, and is reported here only when its stray "
            "readings fall on fewer than min_window_readings (3) days of the judged week [date-6, date] "
            "(research/00 HRV-78, HRV-80). So a null reset_on does not mean the "
            "baseline spans the full 60 days -- read `window`. For `tier_change` it is the era's true "
            "first day and may precede window[0] (the window is clipped at date-66; the era is not, and "
            "reset_on does not slide as it ages). For `coverage_gap` it is "
            "the resumption day: the gap is reported only while the resumption lies inside "
            "[date-66, date], so it never precedes window[0] because the report outlived its clip -- but it "
            "does when a tier-change era boundary clipped the window later than the resumption, since the "
            "two clips compose as the later of their first days and only the *report* is the gap's."
        )
    )
    reset_reason: Literal[hrv_trend.REASON_COVERAGE_GAP, hrv_trend.REASON_TIER_CHANGE] | None = Field(
        description=(
            "Why the current baseline era's reset is reported: more than gap_reset_days consecutive local "
            "days with no entry in the post-exclusion series (`coverage_gap`, the only reset that "
            "re-establishes a baseline), or a sustained source-tier change (`tier_change`, an era "
            "boundary that re-establishes none; research/00 HRV-34). Null when nothing reset. A timezone change is never a reset. Each report has "
            "a lifetime: `coverage_gap` is reported from the resumption until the resumption leaves "
            "[date-66, date] -- 67 days; `tier_change` for at most as long as the previous window "
            "[date-126, date-67] stays sustained by a tier other than the reported dataset's own tier, and often for "
            "less -- see below. A resumption that was also a device switch is therefore "
            "reported as `coverage_gap` first, then as `tier_change` on the same reset_on, then as null -- "
            "one era, three reports, no event between them (F005 Negative Class). The reason is the "
            "**report** only: the baseline clip is decided by the era boundary alone, never by its report, "
            "so a null here can sit beside a clipped `window`; and a third clip, per dataset and unreported, "
            "widens that state: each dataset is also clipped at its last internal capture hole of more than "
            "gap_reset_days (21) silent local days, counted exactly as coverage_gap_reset counts a gap, with "
            "the pre-hole readings listed `before_reset: coverage_gap` while this stays null or the era "
            "rule's. The three clips -- era boundary, coverage gap, internal hole -- compose as the latest "
            "first day, and only the first two are ever reported here. A reported `tier_change` is always "
            "clipped, though once the era's first day is date-66 or older the clip `max(date-66, R)` is a "
            "no-op and `window` is indistinguishable from the un-clipped [date-66, date-7]. "
            "That no-op stretch is conditional, not promised: a reported `tier_change` sits beside an "
            "un-clipped `window` only once date-66 has reached R, and then only on the days the report is "
            "still live. Liveness is rule 4's three conditions together, not any one of them alone: the "
            "reported dataset's tier (selected, or presented when none is) still holds "
            "min_baseline_readings distinct days in the baseline window, "
            "the previous window [date-126, date-67] is still sustained by a tier other than that one, "
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
    """The band, verdict and dataset-selection constants (spec 03 §3.7; construction reference).

    They are not all of dataset selection: the recency gate also applies recency_tolerance_days, which this
    response does not echo, so a client applying these six can derive a baseline.tier that
    disagrees with the reported one (IDEA-070, 2026-09-15).
    """

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
    that day is withheld: the chart may draw it, and the day's verdict is
    withheld -- no verdict of any kind is asserted on it, not merely no
    suppression (T116). ``ln_rmssd`` is independent of the band: null whenever the
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
    dataset: str | None = Field(
        description=(
            "The source tier of the dataset this day's band came from -- the dataset selected for "
            "**this** day, or the one the presentation fallback presented when none was selected "
            "(F006 AC14, T159). Selection runs per local day, so two adjacent points can be drawn "
            "against two different instruments, whose bands differ by the systematic bias between "
            "the tiers; without this field a chart switches instrument between adjacent days and "
            "says nothing about it. Null **only** when no reading of any tier exists in that day's "
            "[date-66, date] -- the structural no_tier_sustains_a_trend case -- which is narrower "
            "than `baseline` being null: a dataset holding one baseline reading has a tier and no "
            "band, so this is non-null while the other three are null. On the response's own day "
            "this is `selected_dataset` when one was selected, and `datasets[]` describes it in "
            "full."
        )
    )


class DatasetSummary(BaseModel):
    """One source tier's dataset for `date`: its own baseline, its own band and
    its own reading of the judged week against that band (F006 AC1/AC2/AC10,
    T159).

    Each tier keeps its own dataset -- no reading of one contributes to
    another's baseline (spec 03 3.7.3's anti-mixing rule, honoured by
    construction) -- and every dataset is reported here whether or not it is
    the one the verdict was taken from. Under F005 one tier owned the only band
    and every other tier's readings were listed as discarded rows; the losers
    are retained now, and this is where they are legible.

    The block is **additive**: `baseline`, `band` and `verdict` still describe
    the selected dataset, and no field that was non-nullable became nullable
    (AC12).
    """

    tier: str = Field(
        description=(
            "The source tier, which **is** the dataset's key (F006 reference 1: the stored "
            "source_device is the watch, so per-unit identity is a later feature). One entry per "
            "tier present in the **gap-clipped** span; the list is in fidelity order, highest "
            "first."
        )
    )
    n: int = Field(
        description=(
            "Baseline readings of this tier, one per local day, over **this dataset's own** "
            "post-clip baseline window. It is `baseline.n` only for the selected dataset, and each "
            "dataset's window can be clipped at a different day: the coverage gap is global, the "
            "era boundary and the internal capture hole are this dataset's own."
        )
    )
    established: bool = Field(
        description=(
            "n >= min_baseline_readings, for this dataset alone. A dataset must be established "
            "**and** hold at least min_window_readings judged-week days to be a candidate for "
            "selection at all (F006 AC8); `week_days` is the other half of that test, so a reader "
            "can tell a dataset that could not have been selected from one that could."
        )
    )
    band: Band | None = Field(
        description=(
            "This dataset's **own** SWC band over its own baseline -- the yardstick its own week "
            "is read against here. Null only when this dataset holds fewer than two baseline "
            "readings, in which case it cannot disagree with anything and is visible carrying `n` "
            "and `week_days` instead (AC10). The selected dataset's band is the response's `band`."
        )
    )
    fidelity_rank: int = Field(
        description=(
            "This tier's ordinal in the source hierarchy spec 03 3.7.1 ratifies, 0 being the "
            "highest: a chest-strap RR capture this system reduces to rMSSD itself, degrading to a "
            "device-computed numeric resting rMSSD, which research/00 HRV-04 admits at reduced "
            "fidelity: this ordinal rank. **This is the "
            "sense of quality that arbitrates**: selection promotes the lowest rank among the "
            "judgeable datasets, so `selected_reason` is recomputable by hand from this field "
            "beside `established`, `week_days` and `last_read` (research/00 1.6). The *other* "
            "sense -- a numeric per-tier **confidence weight**, at which HRV-04 never admits the "
            "numeric tiers -- is deliberately **not** here: 3.7.4 computes no confidence weight in this "
            "section and defers the weighting to the readiness fusion of Section 6, so emitting "
            "one would mint a constant Section 3 does not own. Keeping the two apart is what "
            "prevents a recency-against-quality exchange rate from existing."
        )
    )
    last_read: datetime.date | None = Field(
        description=(
            "The latest local day this tier was read on **inside the baseline window** "
            "[date-66, date-7] -- the slice the recency gate is normative over (F006 AC6), not the "
            "latest reading overall. Null when this tier has no reading in that window at all, "
            "which a dataset whose readings all sit in the judged week has. The gate is "
            "reproducible from this field: a **judgeable** dataset more than recency_tolerance_days "
            "behind the greatest `last_read` among the **established** datasets is skipped, "
            "strictly greater than, so a dataset exactly at the tolerance is kept. The response "
            "does not echo recency_tolerance_days (IDEA-070)."
        )
    )
    week_days: int = Field(
        description=(
            "Distinct local days of the judged week [date-6, date] this tier was read on -- this "
            "dataset's share of the week, which is `readings_in_window` only for the selected "
            "dataset. It is the count half of judgeability (at least min_window_readings, 3) and "
            "it is what weighs a name in `disagreed_with`: a dataset whose judged week is a single "
            "morning can disagree, and a consumer needs to know that before acting on it."
        )
    )
    week_mean: float | None = Field(
        description=(
            "Mean of ln rMSSD over this dataset's own judged-week readings; null when it has none. "
            "It is `ln_rmssd_7d_mean` for the selected dataset and is computed the same way for "
            "every other, so `below` can be recomputed by hand from this and `band` (research/00 "
            "1.6)."
        )
    )
    below: bool | None = Field(
        description=(
            "week_mean < band.lo, **strictly less** -- the same comparison the verdict makes for "
            "hrv_suppressed, made here against this dataset's own band whether or not it is "
            "judgeable and whether or not its own verdict would be withheld (AC10, taken "
            "literally). Null when either side is missing, and a dataset whose `below` is null "
            "cannot appear in `disagreed_with`. Which side each dataset falls on is the statistic "
            "the disagreement rules rest on, and it is reported for every dataset -- including "
            "when nothing is selected, where `disagreed_with` is empty by construction."
        )
    )
    reset_on: datetime.date | None = Field(
        description=(
            "This dataset's **own** reported reset day, decided per dataset (F006 AC17, "
            "T154): the global coverage gap's resumption when one fired -- the same on every "
            "dataset, since a gap measures the silence of the series as a whole -- else this "
            "dataset's own era boundary when that is reported, else null. `baseline.reset_on` is "
            "the **selected** dataset's, so a dataset that is not selected can carry a report the "
            "rest of the response never shows."
        )
    )
    reset_reason: Literal[hrv_trend.REASON_COVERAGE_GAP, hrv_trend.REASON_TIER_CHANGE] | None = Field(
        description=(
            "Why this dataset's reset is reported, with the same meanings as "
            "`baseline.reset_reason` and the same caveat: the report is not the clip. A dataset's "
            "baseline window is clipped by its era boundary whether or not the change is reported, "
            "and by an internal capture hole of more than gap_reset_days silent local days, which "
            "is never reported at all -- so a null here can sit beside an `n` far below what the "
            "60 days of [date-66, date-7] would hold."
        )
    )


class Disagreement(BaseModel):
    """One dataset reading the other side of its own band from the selected
    dataset, with the judged-week count that weighs it (F006 AC10/AC11, T159).
    """

    dataset: str = Field(description="The disagreeing dataset's source tier.")
    week_days: int = Field(
        description=(
            "That dataset's `week_days`, repeated here so the name can be weighed where it is "
            "read: a judged week of one morning can disagree, and the count is the difference "
            "between a second instrument contradicting the verdict all week and a single stray "
            "capture."
        )
    )


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
            "hrv_normal and hrv_suppressed both assert an established baseline -- baseline.n at or above "
            "the min_baseline_readings threshold the response echoes -- and differ only in where the "
            "7-day mean sits: strictly below band.lo is hrv_suppressed, inside or above the band is "
            "hrv_normal. hrv_unavailable has six causes, and the response names which of them fired; "
            "judge evaluates them in this order and reports the first: when no resting-HRV reading of any "
            "tier can sustain a trend -- the structural case, the series holding no per-tier dataset: no "
            "reading of any tier in [date-66, date] after the coverage-gap clip; when there are fewer "
            "than two baseline readings, so no band exists; when there are "
            "fewer than min_window_readings (3) readings of baseline.tier in the judged week; when the "
            "judged week is not a fair sample of the dataset being judged, its readings all predating the "
            "athlete's return to, or first adoption of, another device (T125/T132); when there is a "
            "baseline below min_baseline_readings (14), reported as established: false -- any week judged "
            "against an unestablished baseline (below, inside or above the band alike: 3.7.3 withholds "
            "the suppression there, and hrv_normal there would tell a consumer readiness is intact on "
            "evidence this same response reports unestablished, the up-regulating direction research/00 "
            "1.7 forbids); and when the judged day is after the athlete's local today in timezone, which "
            "is decided at the route and overrides whichever of the other five would otherwise have "
            "applied -- whatever the window holds, no verdict is asserted about a day that has not "
            "happened, and the other fields are still reported as computed. The band is still reported "
            "whenever the baseline can build one, established or not, so an unavailable verdict remains "
            "checkable by hand."
        )
    )
    unavailable_reason: (
        Literal[
            hrv_trend.REASON_NO_TIER,
            hrv_trend.REASON_NO_BAND,
            hrv_trend.REASON_WEEK_TOO_THIN,
            hrv_trend.REASON_WEEK_NOT_REPRESENTATIVE,
            hrv_trend.REASON_BASELINE_UNESTABLISHED,
            hrv_trend.REASON_DAY_NOT_HAPPENED,
        ]
        | None
    ) = Field(
        description=(
            "Why `verdict` is hrv_unavailable; null whenever it is not (research/00 1.6: the response "
            "must be reproducible by hand, and this was the one place that invariant failed -- F005's "
            "Negative Class row 'the verdict still cannot say why it is unavailable', closed by "
            "T137). `judge` evaluates four causes in a fixed order and reports the first that fires: "
            f"`{hrv_trend.REASON_NO_TIER}` (no resting-HRV reading of any tier can sustain a trend -- "
            "the series holds no per-tier dataset: no reading of any tier in [date-66, date] after the "
            f"coverage-gap clip) or `{hrv_trend.REASON_NO_BAND}` (a dataset was "
            "selected or presented, but its baseline holds fewer than two readings, so no band exists -- "
            "build_band answers null on fewer than two baseline readings, so no band exists to judge "
            f"the week against) whenever `band` is null; then `{hrv_trend.REASON_WEEK_TOO_THIN}` "
            "(fewer than min_window_readings (3) readings of `baseline.tier` in the judged week; "
            "readings of other tiers feed their own datasets and do not count toward "
            f"`readings_in_window`); then `{hrv_trend.REASON_WEEK_NOT_REPRESENTATIVE}` (the "
            "judged week is not a fair sample of the dataset being judged, `baseline.tier` -- T125/T132: "
            "its judged-week readings all predate a returning or brand-new device's, another dataset "
            "that could not be selected holding at least min_window_readings (3) judged-week days, "
            "every one later than every judged-week day of the dataset being judged); then "
            f"`{hrv_trend.REASON_BASELINE_UNESTABLISHED}` (a baseline below min_baseline_readings "
            "(14), reported as `established: false` -- `baseline.n` below the threshold the response "
            f"echoes, T116). `{hrv_trend.REASON_DAY_NOT_HAPPENED}` (the judged day is after the "
            "athlete's local today, `date` beyond today in `timezone`) is decided at the route, after "
            "judge, and overrides whichever of the other five would otherwise have applied -- the "
            "fields that would explain them are still computed and returned as usual, but a day that "
            "has not happened is the one true reason no verdict is asserted about it."
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
    datasets: list[DatasetSummary] = Field(
        description=(
            "One entry per source tier present in the **gap-clipped** span -- [date-66, date], "
            "or [baseline.reset_on, date] when a global coverage gap clipped the series -- in "
            "fidelity order, each with its own baseline, its own band and its own reading of "
            "the judged week against that band (F006 AC1/AC2/AC10). A tier read only before "
            "such a resumption therefore has no entry here at all: its readings are listed in "
            "`excluded` as `before_reset: coverage_gap`, so a consumer counting tiers reads "
            "them there rather than finding them nowhere (the span corrected from [date-66, "
            "date] by sprint-006 spec review, 2026-09-21). Every reading inside the span is in "
            "exactly one dataset "
            "here or in `excluded` with a reason -- never in neither, never in both (AC15, "
            "research/00 1.6): a reading of a tier the verdict was not taken from is corroboration "
            "in its own dataset, not a discarded row. Empty only when no reading of any tier "
            "exists in the span."
        )
    )
    selected_dataset: str | None = Field(
        description=(
            "The source tier of the dataset `baseline` and `band` describe, and of the one "
            "`verdict` and `below_by` describe **wherever a verdict was asserted** -- the "
            "highest-fidelity **judgeable** dataset the recency gate did not skip (F006 "
            "AC5-AC8). The scope on that second half is every verdict-free state on a selected "
            "dataset: its judged week withheld as unrepresentative (`week_not_representative`, "
            "research/00 5.4 (v)), and the withheld future day, where for a `to` after the "
            "athlete's local today the verdict is replaced with hrv_unavailable / "
            "`day_not_happened`. In both the verdict is hrv_unavailable and `below_by` is null, so "
            "those two describe no dataset, while this field still names the one the retained "
            "`baseline`/`band` were computed from -- a producer of the response, not a claim about "
            "it. `disagreed_with` is empty there for the converse reason. Null when no dataset was "
            "selected, which is *either* that no dataset is "
            "judgeable *or* that every judgeable one was skipped by the recency gate. In that case the "
            "verdict is hrv_unavailable and `baseline`/`band` are still populated, for presentation "
            "only, from the dataset the presentation fallback names (AC9; "
            "research/00 HRV-24, HRV-59): the established dataset read last in the baseline window, "
            "ties by `n` then fidelity; with none established, the densest by `n`; with no baseline "
            "reading of any tier, the densest in the judged week, ties to fidelity throughout. So "
            "every field non-nullable before F006 still carries a value and this "
            "addition stays additive (AC12). **The fallback presents; it never judges** -- no "
            "verdict is conferred by it, and `disagreed_with` is empty there even when the "
            "presented dataset's own week reads below its band, because a disagreement is with a "
            "verdict and there is none to disagree with. `datasets[]` still shows every dataset's "
            "side, so that state stays legible."
        )
    )
    selected_reason: Literal[hrv_trend.SELECTED_REASONS] | None = Field(
        description=(
            "Why `selected_dataset` is the one, drawn from a closed enum: "
            f"`{hrv_trend.SELECTED_HIGHEST_FIDELITY}` -- no judgeable dataset outranks it; "
            f"`{hrv_trend.SELECTED_HIGHER_FIDELITY_STALE}` -- one did, and the recency gate "
            "skipped it, so `baseline`/`band` come from a lower-fidelity instrument -- and, "
            "wherever a verdict is conferred, so does the verdict -- and the response says so rather "
            "than leaving that fall invisible. Where `verdict` is "
            f"`{hrv_trend.VERDICT_UNAVAILABLE}` (T125's returning athlete is served this value beside "
            f"`{hrv_trend.REASON_WEEK_NOT_REPRESENTATIVE}`) no verdict is taken from any dataset: the "
            "value still says which dataset was presented and why, kept as computed (research/00 5.4 "
            "(iii)), and makes no claim about a verdict. Non-null **exactly "
            "when** `selected_dataset` is non-null, null with null: it is derived from the "
            "selection rather than stored beside it, so it cannot disagree with which datasets "
            "were skipped. The enum is the module's own tuple, rendered rather than transcribed, "
            "so a member cannot come to exist in one copy alone."
        )
    )
    disagreed_with: list[Disagreement] = Field(
        description=(
            "The datasets whose judged week reads the **other side of their own band** from the "
            "selected dataset, in fidelity order, each with the judged-week count that weighs it "
            "(F006 AC10/AC11). Both directions are reported: a dataset below its band while the "
            "selected one reads within, and one within while the selected one reads below. A "
            "dataset below its band beside a selected dataset also below its own **agrees** with "
            "the verdict and is not named. Judgeability is never consulted -- a band from two "
            "baseline readings and a week of one can name a dataset here -- so `week_days` is what "
            "a consumer weighs the name by. **Disagreement never overrides**: `verdict` is the "
            "selected dataset's, unchanged, whatever is listed here. **Empty whenever `verdict` is "
            "`hrv_unavailable`, for any cause** (research/00 5.4 (iii), amended 2026-09-21): a "
            "disagreement is a claim *about* a verdict and none was conferred, so naming a "
            "dissenter would report a contradiction of a claim never made. That is one condition "
            "over all three verdict-free states -- `selected_dataset` null, where the presentation "
            "fallback speaks; a dataset that **is** selected whose verdict is withheld because a "
            "returning dataset's judged week is entirely later than its own "
            "(`week_not_representative`); and a day that has not happened (`day_not_happened`). "
            "`unavailable_reason` does not imply a selection: `week_not_representative` is also "
            "served with `selected_dataset` null, when the dataset the fallback presents is itself "
            "withheld, and `day_not_happened` is served on a future day with `selected_dataset` null "
            "or not. `selected_dataset` separates the second state from the first; the third is "
            "identified by `unavailable_reason` alone and can coincide with either (T168, corrected "
            "by F006 review cycle 3). "
            "`datasets[]` still carries every dataset's own `below` on those days, so the state "
            "stays legible without being called a disagreement, and `selected_dataset`, "
            "`selected_reason` and the retained `baseline`/`band` are kept as computed. "
            "This is the response's one report of HRV-25's population: hrv_normal "
            "can be promoted from the best available instrument while another dataset reads below "
            "its own band, and a consumer reading `verdict` alone is not told. That is research/00 "
            "1.7's forbidden direction, which ships only as the named exception PRIN-15 lists, "
            "owned by IDEA-099, and that population may not grow (research/00 HRV-25, PRIN-15)."
        )
    )
