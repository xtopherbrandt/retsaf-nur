import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
# The response is reproducibility-critical (``research/00`` PRIN-12): a reader
# must be able to recompute the verdict by hand from what it carries (the
# inputs it does not serve are PRIN-12's OPEN exceptions, PRIN-24 and PRIN-27), which is
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
            "n >= min_baseline_readings. Below it neither hrv_suppressed nor hrv_normal is asserted "
            "-- hrv_unavailable is the only verdict emitted (see verdict), with unavailable_reason "
            "baseline_unestablished unless a cause before it fired first."
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
            "no-op and `window` is indistinguishable from the nominal baseline window [date-66, date-7] "
            "before any clip (research/00 T-09). "
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
    """The band, verdict and dataset-selection constants (spec/03 §3.7; construction reference).

    The tolerance of the recency gate is served here as recency_tolerance_days, beside the six the block
    already served (PRIN-24 names window_days as the one verdict-affecting constant it does not serve),
    so selected_reason and baseline.tier are recomputable from the response alone (research/00 PRIN-12;
    C33, 2026-09-23, which reversed IDEA-070's narrowing of 2026-09-15).
    """

    baseline_days: int
    min_baseline_readings: int
    min_window_readings: int
    gap_reset_days: int
    band_floor: float
    swc_factor: float
    recency_tolerance_days: int


class HrvPoint(BaseModel):
    """One local day of the chart's series (the UI contract's ``HrvTrend.points[]``),
    judged against **its own** dataset baseline window -- the nominal ``[date-66, date-7]``
    clipped on that day (``research/00`` HRV-12, T-09) -- not against `to`'s.

    The band is a property of the baseline (IDEA-044), so its three fields
    are null *together*, and only when that day's baseline holds fewer than
    two readings. A baseline that is computable but not established
    (2 <= n < 14) still carries its band here even though that day's verdict
    is ``hrv_unavailable`` (``baseline_unestablished``, unless a cause before
    it fired first): the chart may draw it, and no verdict of any kind is
    asserted on the day, not merely no suppression (T116). ``ln_rmssd`` is
    independent of the band: null whenever the
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
            "Mean of ln rMSSD over that day's own dataset baseline window, the nominal [date-66, date-7] "
            "clipped at the latest of the coverage-gap resumption, the era boundary and the last internal "
            "hole (research/00 HRV-12, T-09). Null together with swc_low "
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
            "The source tier, which **is** the dataset's key (F006 reference 1: the stored source_device "
            "is the watch; since F007 the store also records the connected heart-rate sensor's serial, but "
            "no rule reads it, so a per-unit key is a later feature). One entry per tier present in the "
            "**gap-clipped** span; the list is in fidelity order, highest first."
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
            "beside `established`, `week_days` and `last_read` (research/00 PRIN-12). The *other* "
            "sense -- a numeric per-tier **confidence weight**, which HRV-04 rules out for the "
            "numeric tiers -- is deliberately **not** here: 3.7.4 computes no confidence weight in this "
            "section and defers the weighting to the readiness fusion of Section 6, so emitting "
            "one would mint a constant Section 3 does not own. Keeping the two apart is what "
            "prevents a recency-against-quality exchange rate from existing."
        )
    )
    last_read: datetime.date | None = Field(
        description=(
            "The latest local day this tier was read on **inside the series baseline window**, the "
            "nominal [date-66, date-7] clipped at the coverage-gap resumption (research/00 HRV-15, T-09) "
            "-- the slice the recency gate is normative over (F006 AC6), not the "
            "latest reading overall. Null when this tier has no reading in that window at all, "
            "which a dataset whose readings all sit in the judged week has. The gate is "
            "reproducible from this field: a **judgeable** dataset more than recency_tolerance_days "
            "behind the greatest `last_read` among the **established** datasets is skipped, "
            "strictly greater than, so a dataset exactly at the tolerance is kept. The tolerance "
            "is served as thresholds.recency_tolerance_days (C33, reversing IDEA-070)."
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
            "PRIN-12, less its OPEN exceptions, PRIN-24 and PRIN-27)."
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
            "nominal 60-day window [date-66, date-7] would hold, since `n` counts this dataset's clipped "
            "baseline window (research/00 HRV-12, T-09)."
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
            "against an unestablished baseline (below, inside or above the band alike: 3.7.3 serves "
            "hrv_unavailable there rather than hrv_suppressed, and hrv_normal there would tell a "
            "consumer readiness is intact on "
            "evidence this same response reports unestablished, the up-regulating direction research/00 "
            "PRIN-14 forbids); and when the judged day is after the athlete's local today in timezone, which "
            "is decided at the route and overrides whichever of the other five would otherwise have "
            "applied -- whatever the window holds, no verdict is asserted about a day that has not "
            "happened, and the other fields are still reported as computed. The band is still reported "
            "whenever the baseline can build one, established or not, so an unavailable verdict on an "
            "unestablished baseline remains checkable by hand."
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
            "Why `verdict` is hrv_unavailable; null whenever it is not (research/00 PRIN-12: the verdict "
            "must be reproducible by hand from the response, its unserved inputs being PRIN-12's OPEN "
            "exceptions (PRIN-24, PRIN-27), and this was the one place that rule failed -- F005's "
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
            "research/00 PRIN-23): a reading of a tier the verdict was not taken from is corroboration "
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
            "research/00 HRV-31), and the future day, where for a `to` after the "
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
            "value still says which dataset was presented and why, kept as computed (research/00 HRV-57"
            "), and makes no claim about a verdict. Non-null **exactly "
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
            "`hrv_unavailable`, for any cause** (research/00 HRV-22, amended 2026-09-21): a "
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
            "PRIN-14's forbidden direction, which ships only as the named exception PRIN-15 lists, "
            "owned by IDEA-099, and that population may not grow (research/00 HRV-25, PRIN-15)."
        )
    )


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/features (F013)
#
# The response ``metrics.session_features.compute_session_features`` builds,
# typed. Every feature is a value or an unavailable reason, never both and
# never neither (a value is never imputed, spec/03 section 3.2), so a consumer
# that reads ``value`` without checking ``unavailable`` reads null, not a
# number. The field descriptions are what the contract and the UI read from
# ``/openapi.json``.
# ---------------------------------------------------------------------------


class FeatureValue(BaseModel):
    """One session feature: a `value`, or the reason it is `unavailable`; exactly one of the two is non-null."""

    value: float | None = Field(description="The feature's value in the unit its name states; null when unavailable.")
    unavailable: str | None = Field(
        description=(
            "Why no value is served, null when one is. The reason codes, by feature: `sport_not_running` (every "
            "feature of a non-running session) and `no_records` (every feature of a running session with no "
            "records) take precedence over all others; then `no_counted_segments` (duration_s, when no segment "
            "with 0 < dt <= 5 s exists), `no_distance` (distance_m, avg_pace_s_per_km and "
            "gap_avg_pace_s_per_km, when the counted distance is 0: a zero is reported as unavailable, never as "
            "a value), `no_30s_block` and `no_motion` (ngp_speed_m_s and ngp_pace_s_per_km), `no_heart_rate` "
            "and `cadence_lock` (avg_hr_bpm), `no_cadence` (avg_cadence_spm), `no_power` and "
            "`mixed_power_models` (avg_power_w), `no_altitude` (total_ascent_m and total_descent_m) and "
            "`not_recorded` (the three env_* features, always the case on the FIT-upload path today)."
        )
    )


class PowerFeatureValue(FeatureValue):
    """`avg_power_w` with the model the power stream was recorded under."""

    power_model: str | None = Field(
        description=(
            "The one `power_model` the session's power samples carry (a sample with a null model still counts); "
            "null when no power was recorded or when the models are mixed (`mixed_power_models`)."
        )
    )


class SessionFeatureValues(BaseModel):
    """The 14 features of F013, in the response's order (F013 reference, section 7)."""

    duration_s: FeatureValue = Field(
        description=(
            "Recorded time T: the sum of dt over counted segments (consecutive records 0 < dt <= 5 s apart). "
            "A pause or an unfilled gap longer than 5 s contributes nothing; a dt = 0 duplicate contributes nothing."
        )
    )
    distance_m: FeatureValue = Field(
        description=(
            "Distance D: the sum of max(dd, 0) over counted segments. A regressing segment contributes 0 m and "
            "raises the `distance_regressed` flag."
        )
    )
    avg_pace_s_per_km: FeatureValue = Field(description="1000 * T / D.")
    gap_avg_pace_s_per_km: FeatureValue = Field(
        description=(
            "Grade-adjusted average pace: 1000 * T / sum(v_actual * g * dt) over counted segments, g being "
            "Minetti's cost of gradient relative to the flat, over a +/-25 m window of reconstructed distance, "
            "clamped to +/-0.45. A segment with no grade contributes at g = 1, so `avg_pace_s_per_km / "
            "gap_avg_pace_s_per_km` is the distance-weighted mean g, and flat ground leaves pace unchanged."
        )
    )
    ngp_speed_m_s: FeatureValue = Field(
        description=(
            "Normalized graded speed: fourth root of the mean fourth power of the 30 s trailing mean of device speed "
            "* g over each full window of each block (blocks split at a gap over 5 s and at a speedless record; a "
            "record reached by a dt = 0 segment is dropped and splits nothing; a block under 30 records has none)."
        )
    )
    ngp_pace_s_per_km: FeatureValue = Field(description="1000 / ngp_speed_m_s.")
    avg_hr_bpm: FeatureValue = Field(
        description=(
            "Time-weighted mean of the heart rate at each counted segment's start, over records with a heart "
            "rate above 0 and without the `cadence_lock` sample flag."
        )
    )
    avg_cadence_spm: FeatureValue = Field(
        description="Time-weighted mean of cadence at each counted segment's start, over records with cadence > 0."
    )
    avg_power_w: PowerFeatureValue = Field(
        description="Time-weighted mean of power at each counted segment's start, with its `power_model`."
    )
    total_ascent_m: FeatureValue = Field(
        description=(
            "Ascent by a 1 m hysteresis over the present altitude samples in t order; no vendor total is read."
        )
    )
    total_descent_m: FeatureValue = Field(description="Descent by the same 1 m hysteresis.")
    env_temperature_c: FeatureValue = Field(description="The session context's `env_temperature_c`, verbatim.")
    env_humidity_pct: FeatureValue = Field(description="The session context's `env_humidity_pct`, verbatim.")
    env_wind_ms: FeatureValue = Field(description="The session context's `env_wind_ms`, verbatim.")


class SessionFeaturesResponse(BaseModel):
    """The features of one session, computed on read from its stored canonical records (F013).

    Nothing is stored: every request recomputes from the records, so a deleted
    session has no features (404, the envelope of GET /sessions/{id}). A
    non-running session, or one with no records, is a 200 with every feature
    unavailable and the reason named: an all-unavailable session is an
    answer, not an error. duration_s, distance_m and avg_pace_s_per_km are
    the same quantities SessionSummary names: recorded time, not elapsed, so a
    future listSessions projects these values.
    """

    session_id: str
    sport: str
    flags: list[str] = Field(
        description=(
            "The session's stored `quality_flags` first, in stored order with `smart_recording` moved to the "
            "front when present; then `distance_regressed` (a counted segment whose distance decreased), "
            "`gap_unavailable` (D > 0 and gap_coverage < 1: spec/03 section 3.3.4's raw-pace fallback applied "
            "to part of the run) and `grade_clamped` (a segment's grade was clamped to +/-0.45). Each at most once."
        )
    )
    gap_coverage: float | None = Field(
        description=(
            "The share of D contributed by segments that had a grade (two altitude samples in their +/-25 m "
            "window). 1.0 when every metre was graded, 0.0 with no altitude at all; null when D = 0."
        )
    )
    grade_clamped_fraction: float | None = Field(
        description="The share of D contributed by segments whose grade was clamped to +/-0.45; null when D = 0."
    )
    gps_degraded_fraction: float | None = Field(
        description=(
            "The share of D contributed by segments starting at a `gps_degraded` record. Reported, not "
            "excluded: averaged pace is trusted once degraded samples are averaged. Null when D = 0."
        )
    )
    features: SessionFeatureValues


# ---------------------------------------------------------------------------
# GET /me (F016)
#
# The athlete's profile as ``profile.resolve`` computes it, one entry per field,
# and the four HR anchors as ``db.read_hr_anchors`` serves them, beside the
# contract's existing ``id``, ``display_name``, ``units`` and ``created_at``.
# ``FeatureValue`` is the precedent: a value, or the reason it is
# ``unavailable``, never both and never neither. The entry classes differ only
# in the type of ``value`` and ``entered_value``.
# ---------------------------------------------------------------------------

_SOURCE_DESCRIPTION = (
    "`fit` when a stored session's FIT file supplied the value, `entered` when the athlete's entry did, "
    "null when unavailable."
)
_Sex = Literal["female", "male", "unspecified"]
_SESSION_ID_DESCRIPTION = "The session whose file supplied the value; null unless `source` is `fit`."
_ENTERED_VALUE_DESCRIPTION = (
    "The athlete's latest entry for the field, shown even when a FIT value shadows it; null when there is "
    "none or it was cleared."
)


class ProfileEntryBase(BaseModel):
    """The fields every profile entry carries besides its value."""

    unavailable: Literal["missing"] | None = Field(
        description="`missing` when no stored file and no entry supplies a value; null when `value` is served."
    )
    source: Literal["fit", "entered"] | None = Field(description=_SOURCE_DESCRIPTION)
    session_id: str | None = Field(description=_SESSION_ID_DESCRIPTION)
    recorded_at: str | None = Field(
        description=(
            "The supplying session's stored start time when `source` is `fit`, the time the entry was set when it "
            "is `entered`; null when unavailable."
        )
    )


class ProfileSexEntry(ProfileEntryBase):
    """`sex`: `male` or `female` from a FIT file, or `male`, `female` or `unspecified` as entered."""

    value: _Sex | None = Field(description="The effective value; null when unavailable.")
    entered_value: _Sex | None = Field(description=_ENTERED_VALUE_DESCRIPTION)


class ProfileDateEntry(ProfileEntryBase):
    """`birth_date`: entered only; no FIT file carries it."""

    value: datetime.date | None = Field(description="The effective value; null when unavailable.")
    entered_value: datetime.date | None = Field(description=_ENTERED_VALUE_DESCRIPTION)


class ProfileNumberEntry(ProfileEntryBase):
    """A body field: `body_mass_kg` in kg or `height_cm` in cm."""

    value: float | None = Field(description="The effective value; null when unavailable.")
    entered_value: float | None = Field(description=_ENTERED_VALUE_DESCRIPTION)


class ProfileIntegerEntry(ProfileEntryBase):
    """A heart-rate field in bpm. For `max_hr_bpm` and `threshold_hr_bpm` only sessions stored as running count."""

    value: int | None = Field(description="The effective value; null when unavailable.")
    entered_value: int | None = Field(description=_ENTERED_VALUE_DESCRIPTION)


class AnchorBase(BaseModel):
    """The fields every HR anchor carries besides its value."""

    unavailable: Literal["missing", "order_conflict"] | None = Field(
        description=(
            "Null when the anchor is served. `missing` when the field has no effective value (for `sex`, also "
            "when it is `unspecified`); `order_conflict` when the effective values break the ordering rule: "
            "resting HR must be below max HR, or both conflict, and threshold HR must be above resting and below "
            "max, checked against whichever of the two are served, or threshold alone conflicts."
        )
    )
    source: Literal["fit", "entered"] | None = Field(description=_SOURCE_DESCRIPTION)
    session_id: str | None = Field(description=_SESSION_ID_DESCRIPTION)
    recorded_at: str | None = Field(
        description="As the field entry's `recorded_at`; null when the anchor is unavailable."
    )
    version: int | None = Field(
        description=(
            "Starts at 1 and moves when, and only when, the served value changes. The source does not move it, and "
            "neither does a period unavailable. Null when the anchor is unavailable."
        )
    )


class HrAnchor(AnchorBase):
    """A heart-rate anchor in bpm."""

    value: int | None = Field(description="The served value; null when unavailable.")
    entered_value: int | None = Field(description=_ENTERED_VALUE_DESCRIPTION)


class SexAnchor(AnchorBase):
    """The `sex` anchor: `male` or `female` when served."""

    value: Literal["female", "male"] | None = Field(description="The served value; null when unavailable.")
    entered_value: _Sex | None = Field(description=_ENTERED_VALUE_DESCRIPTION)


class HrAnchors(BaseModel):
    """The four anchors a load computation reads, each served or unavailable with its reason."""

    resting_hr_bpm: HrAnchor
    max_hr_bpm: HrAnchor
    threshold_hr_bpm: HrAnchor
    sex: SexAnchor


class UnitPrefs(BaseModel):
    """The athlete's display units. An unknown key is refused, so a misspelt unit is a 422 on
    ``PATCH /me`` rather than a change silently dropped."""

    model_config = ConfigDict(extra="forbid")

    distance: Literal["km", "mi"]
    pace: Literal["min_per_km", "min_per_mi"]
    temperature: Literal["c", "f"]


_HR_ENTRY = Field(
    None, gt=0, strict=True, description="A whole number of bpm above 0; null clears the entry."
)
_BODY_ENTRY_DESCRIPTION = "A finite number above 0; null clears the entry."


class AthleteProfileUpdate(BaseModel):
    """The body of ``PATCH /me``: any of the entered profile fields plus ``display_name`` and ``units``.

    A field left out is unchanged. For a profile field, null clears the entry, so the field falls to the
    stored FIT value or unavailable, never to an earlier entry. HR values are strict whole numbers above 0
    (``true``, ``"188"`` and ``188.5`` are refused); body values are finite numbers above 0, strict so a
    JSON ``true`` is not taken as 1. ``birth_date`` is a ``YYYY-MM-DD`` date; the route also refuses one
    after today in ``athlete_timezone``. Ordering across fields is not checked here: it is the anchor
    lookup's rule, and ``GET /me`` shows a conflicting entry as ``order_conflict``."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(None, description="The athlete's display name; null clears it.")
    units: UnitPrefs = Field(None, description="The athlete's display units, all three; not nullable.")  # type: ignore[assignment]
    sex: _Sex | None = Field(None, description="`male`, `female` or `unspecified`; null clears the entry.")
    birth_date: str | None = Field(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        json_schema_extra={"format": "date"},
        description="A `YYYY-MM-DD` date, not after today in the athlete's timezone; null clears the entry.",
    )
    body_mass_kg: float | None = Field(
        None, gt=0, allow_inf_nan=False, strict=True, description=f"In kg. {_BODY_ENTRY_DESCRIPTION}"
    )
    height_cm: float | None = Field(
        None, gt=0, allow_inf_nan=False, strict=True, description=f"In cm. {_BODY_ENTRY_DESCRIPTION}"
    )
    resting_hr_bpm: int | None = _HR_ENTRY
    max_hr_bpm: int | None = _HR_ENTRY
    threshold_hr_bpm: int | None = _HR_ENTRY

    @field_validator("birth_date")
    @classmethod
    def _a_calendar_date(cls, value: str | None) -> str | None:
        """The pattern admits ``1980-13-01``; ``date.fromisoformat`` refuses it."""
        if value is not None:
            datetime.date.fromisoformat(value)
        return value


class Athlete(BaseModel):
    """The athlete's profile (F016). Each field's effective value is the stored session with the latest start
    time whose file carries it (for max and threshold HR, sessions stored as running only), else the latest
    entered value, else unavailable. Computed on read: nothing is cached, so deleting a session recomputes it."""

    id: str
    display_name: str | None
    sex: ProfileSexEntry
    birth_date: ProfileDateEntry
    units: UnitPrefs
    created_at: str = Field(description="When the athlete's settings were first created, ISO 8601 in UTC.")
    body_mass_kg: ProfileNumberEntry
    height_cm: ProfileNumberEntry
    resting_hr_bpm: ProfileIntegerEntry
    max_hr_bpm: ProfileIntegerEntry
    threshold_hr_bpm: ProfileIntegerEntry
    anchors: HrAnchors = Field(
        description="The resting, max and threshold HR and sex anchors under the ordering rule, each with its version."
    )


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/load (F017)
#
# The body ``metrics.session_load.compute_session_load`` builds and
# ``db._save_session_load`` saves in the upload's transaction, typed. The
# route serves the saved body unchanged, so these models describe a stored
# document: every field is required and a value or a reason is null, never
# absent. Appended below the earlier models, as the profile models are, so
# the lines cited above stay in place. The enum classes are named for the
# feature (``SessionLoadReason``) because each feature's reason enum is its
# own OpenAPI component and two components cannot share a name.
# ---------------------------------------------------------------------------
from enum import Enum


class SessionLoadReason(str, Enum):
    """Why HR-TRIMP, and with it `session_load`, has no value: the F017 reference's gates in order (first match
    wins), then the two reference reasons that leave `hr_trimp.value` served while `session_load` is unavailable."""

    SPORT_NOT_RUNNING = "sport_not_running"
    DECLARED_CAPTURE = "declared_capture"
    NO_HR = "no_hr"
    MISSING_ANCHOR = "missing_anchor"
    ORDER_CONFLICT = "order_conflict"
    AVG_HR_BELOW_RESTING = "avg_hr_below_resting"
    AVG_HR_ABOVE_MAX = "avg_hr_above_max"
    WRIST_HR_THRESHOLD_UNKNOWN = "wrist_hr_threshold_unknown"
    WRIST_HR_AT_THRESHOLD = "wrist_hr_at_threshold"
    NOT_REPRESENTABLE = "not_representable"
    NO_THRESHOLD_HR = "no_threshold_hr"
    THRESHOLD_ORDER_CONFLICT = "threshold_order_conflict"


_SESSION_WIDE_REASON_DESCRIPTION = (
    "`sport_not_running` and `declared_capture` (gates 1 and 2) are carried by every metric; otherwise "
    "the metric's own reason."
)
_LOAD_SOURCE_DESCRIPTION = (
    "`session_file` when the session's own FIT file carried the value, `anchor` when the HR anchor in effect at "
    "upload supplied it; null when neither did."
)
_LOAD_ANCHOR_UNAVAILABLE_DESCRIPTION = (
    "When the file carried no value and the anchor at upload had none either, the anchor's own reason "
    "(`missing` or `order_conflict`); null otherwise."
)


class SessionLoadValue(BaseModel):
    """`session_load`: HR-TRIMP on the threshold-hour scale (one hour at threshold HR scores 100), or why not."""

    value: float | None = Field(description="TRIMP / threshold_hour_reference * 100; null when unavailable.")
    unavailable: SessionLoadReason | None = Field(
        description="HR-TRIMP's reason when it has no value, else `no_threshold_hr`, `threshold_order_conflict` or `not_representable`; null when `value` is served."
    )
    driver: Literal["hr_trimp"] | None = Field(description="The metric behind `value`; null whenever `value` is null.")


class HrTrimp(BaseModel):
    """Banister's HR-TRIMP over running time with usable HR, with the reference that scales it."""

    value: float | None = Field(description="duration_min * r * k * e^(c r), r = (HR_avg - HR_rest) / (HR_max - HR_rest); null when unavailable.")
    unavailable: SessionLoadReason | None = Field(description="The first gate that refused the session; null when `value` is served.")
    unavailable_fields: list[Literal["resting_hr_bpm", "max_hr_bpm"]] = Field(
        description="Under `missing_anchor`, the fields with no value used; empty otherwise."
    )
    threshold_hour_reference: float | None = Field(
        description="60 * r_thr * k * e^(c r_thr) with the session's own coefficients; null when unavailable or when the threshold has no usable value."
    )
    coefficients: Literal["male", "female"] | None = Field(
        description="The Banister pair used: `male` (0.64, 1.92) or `female` (0.86, 1.67); null when `value` is null."
    )


class Rtss(BaseModel):
    """rTSS: unavailable until threshold pace exists (`no_threshold_pace`)."""

    value: float | None = Field(description="Always null today: no threshold pace exists to compute it from.")
    unavailable: Literal["no_threshold_pace", "sport_not_running", "declared_capture"] = Field(
        description=f"`no_threshold_pace` on a counted running session. {_SESSION_WIDE_REASON_DESCRIPTION}"
    )


class Srpe(BaseModel):
    """sRPE-load: unavailable until an RPE input exists (`no_rpe`)."""

    value: float | None = Field(description="Always null today: no RPE input path exists.")
    unavailable: Literal["no_rpe", "sport_not_running", "declared_capture"] = Field(
        description=f"`no_rpe` on a counted running session. {_SESSION_WIDE_REASON_DESCRIPTION}"
    )


class SessionLoadMetrics(BaseModel):
    """The three load metrics of spec/03 section 3.4; only HR-TRIMP is computed."""

    hr_trimp: HrTrimp
    rtss: Rtss
    srpe: Srpe


class SessionLoadHrInput(BaseModel):
    """One HR value used (resting, max or threshold, in bpm) and where it came from. The value is an unbounded
    integer: an entered max of 2^63 is saved and served exactly."""

    value: int | None = Field(description="The value used, in bpm; null when neither the file nor the anchor had one.")
    source: Literal["session_file", "anchor"] | None = Field(description=_LOAD_SOURCE_DESCRIPTION)
    session_id: str | None = Field(description="The session whose file carried the value; null unless `source` is `session_file`.")
    anchor_version: int | None = Field(description="The anchor's version at upload; null unless `source` is `anchor`.")
    anchor_unavailable: Literal["missing", "order_conflict"] | None = Field(
        description=_LOAD_ANCHOR_UNAVAILABLE_DESCRIPTION
    )


class SessionLoadSexInput(BaseModel):
    """The sex value used for the coefficients, and where it came from."""

    value: Literal["male", "female"] | None = Field(
        description="The value used; null when neither the file nor the anchor had one (the men's pair is used and `sex_defaulted` is flagged)."
    )
    source: Literal["session_file", "anchor"] | None = Field(description=_LOAD_SOURCE_DESCRIPTION)
    session_id: str | None = Field(description="The session whose file carried the value; null unless `source` is `session_file`.")
    anchor_version: int | None = Field(description="The anchor's version at upload; null unless `source` is `anchor`.")
    anchor_unavailable: Literal["missing", "order_conflict"] | None = Field(
        description=_LOAD_ANCHOR_UNAVAILABLE_DESCRIPTION
    )


class SessionLoadInputs(BaseModel):
    """Every value the load was computed from, saved with it (F017 reference, "Time basis" and "Which values")."""

    hr_time_s: float = Field(
        description="Running time with usable HR: the sum of dt over counted segments (0 < dt <= 5 s) whose start HR is present, above 0 and not `cadence_lock`. A pause adds nothing; nothing is imputed."
    )
    recorded_time_s: float | None = Field(description="F013's `duration_s`, reported beside `hr_time_s`; null when unavailable.")
    timer_time_s: float | None = Field(description="The FIT `session.total_timer_time` stored at ingest; null when the file carried none.")
    hr_time_fraction: float | None = Field(
        description="hr_time_s / timer_time_s with no cut-off and no clamp (a full run reads within 0.01 of 1 on either side); null when `timer_time_s` is null or 0."
    )
    avg_hr_bpm: float | None = Field(description="F013's served `avg_hr_bpm`, the dt-weighted mean over the same segments; null when unavailable.")
    hr_source: str | None = Field(description="The session's stored `hr_source` (`chest_strap`, `wrist_ppg` or null); anything but `chest_strap` takes the wrist path.")
    resting_hr_bpm: SessionLoadHrInput
    max_hr_bpm: SessionLoadHrInput
    threshold_hr_bpm: SessionLoadHrInput
    sex: SessionLoadSexInput


class SessionLoad(BaseModel):
    """The training load of one session, HR-TRIMP first (F017), as saved at its upload.

    Computed once, in the upload's own transaction, from the session's own
    file values (else the HR anchors in effect at upload) and saved with
    every value used; a later anchor change moves nothing, and the load is
    recomputed only by delete and re-upload. The route serves the saved body
    and reads no anchor. A deleted session has no load (404, the envelope of
    GET /sessions/{id}).
    """

    session_id: str
    sport: str
    session_load: SessionLoadValue
    flags: list[Literal["wrist_hr", "sex_defaulted"]] = Field(
        description="`wrist_hr` on every running session whose `hr_source` is not `chest_strap`, whatever the outcome; `sex_defaulted` when TRIMP was computed with the men's pair for want of a sex value."
    )
    input_flags: list[str] = Field(
        description="F013's feature flags and the session's stored `quality_flags`, passed through, each at most once."
    )
    metrics: SessionLoadMetrics
    inputs: SessionLoadInputs = Field(description="The values used and their sources, reported under every gate, gates 1 and 2 included.")


# ---------------------------------------------------------------------------
# GET /metrics/load (F018): the fitness, fatigue and form chart
#
# Appended below the session load models. Named exactly as the contract's
# components, so the route test's walker compares like with like.
# ---------------------------------------------------------------------------


class LoadChartReason(str, Enum):
    """Why an uncounted run's saved load is unavailable: F017's `SessionLoadReason` minus the two
    exclusion reasons (`sport_not_running` and `declared_capture` exclude a session instead)."""

    NO_HR = "no_hr"
    MISSING_ANCHOR = "missing_anchor"
    ORDER_CONFLICT = "order_conflict"
    AVG_HR_BELOW_RESTING = "avg_hr_below_resting"
    AVG_HR_ABOVE_MAX = "avg_hr_above_max"
    WRIST_HR_THRESHOLD_UNKNOWN = "wrist_hr_threshold_unknown"
    WRIST_HR_AT_THRESHOLD = "wrist_hr_at_threshold"
    NOT_REPRESENTABLE = "not_representable"
    NO_THRESHOLD_HR = "no_threshold_hr"
    THRESHOLD_ORDER_CONFLICT = "threshold_order_conflict"


class LoadChartCounted(BaseModel):
    """A session the day counted: its saved load is a number and adds to the day's `load`."""

    session_id: str
    load: float = Field(description="The session's saved `session_load.value`, unrounded.")


class LoadChartUncounted(BaseModel):
    """A running session the day could not count: its saved load is unavailable for a reason other
    than the two exclusions. It adds nothing and marks the day; it is never served as a 0."""

    session_id: str
    reason: LoadChartReason = Field(description="The saved `session_load.unavailable` reason.")


class LoadChartExcluded(BaseModel):
    """A session that is not running load by design: a non-running sport or a declared resting
    capture. It adds nothing and does not mark the day."""

    session_id: str
    reason: Literal["sport_not_running", "declared_capture"]


class LoadChartSeed(BaseModel):
    """The start value both curves begin from (user ruling C5; spec/03 section 3.5.3): the mean of
    the day loads over the first 42 days of history, or over all of it when shorter."""

    value: float = Field(description="CTL_0 = ATL_0; the mean daily load over `window_days`, unrounded.")
    window_days: int = Field(description="The days averaged: 42, or the length of history when shorter.")
    provisional_until: datetime.date = Field(
        description="`first_day` + 41: the last day inside the first 42 days of history, a future date while history is shorter."
    )


class LoadChartDay(BaseModel):
    """One local date of history, with the sessions it counted, could not count and excluded."""

    date: datetime.date = Field(description="The local date in `athlete_timezone`.")
    load: float = Field(description="The sum of the counted sessions' saved loads; 0 on a rest day or a marked day.")
    ctl: float = Field(description="CTL_(d-1) + (load - CTL_(d-1)) * (1 - e^(-1/42)), unrounded.")
    atl: float = Field(description="ATL_(d-1) + (load - ATL_(d-1)) * (1 - e^(-1/7)), unrounded.")
    tsb: float = Field(description="CTL_(d-1) - ATL_(d-1): yesterday's values, so 0 on the first day.")
    provisional: bool = Field(description="True on every day inside the first 42 days of history, where the seed still moves.")
    marked: bool = Field(description="True when `uncounted` is non-empty: a run without a load moved this day like a rest day.")
    uncounted_in_window: int = Field(description="Uncounted sessions on the 42 days ending this day.")
    counted: list[LoadChartCounted] = Field(description="Ordered by `start_time` then `session_id`.")
    uncounted: list[LoadChartUncounted] = Field(description="Ordered by `start_time` then `session_id`.")
    excluded: list[LoadChartExcluded] = Field(description="Ordered by `start_time` then `session_id`.")


class LoadChart(BaseModel):
    """GET /metrics/load: the fitness, fatigue and form series (F018, spec/03 section 3.5).

    Computed on read from the loads F017 saved at upload, the sessions'
    `start_time` and the configured `athlete_timezone`; nothing is stored.
    The curves always run from the first day of history; `from` and `to`
    choose which days are returned.
    """

    timezone: str = Field(description="The configured `athlete_timezone` the days were bucketed in.")
    today: datetime.date = Field(description="The athlete's local today, resolved once per request: the default `to`.")
    first_day: datetime.date | None = Field(
        description="The first local date holding a counted or uncounted session; null when the store holds no running session."
    )
    seed: LoadChartSeed | None = Field(description="Null when `first_day` is null or after `today`.")
    days: list[LoadChartDay] = Field(
        description="Every local date from the later of `from` and `first_day` to `to`, in order, rest days included; empty when history has no day in the range."
    )
