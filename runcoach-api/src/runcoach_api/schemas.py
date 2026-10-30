import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from runcoach_api.metrics import hrv_trend


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class IngestResponse(BaseModel):
    session_id: str
    quality_flags: list[str]
    # The two optional keys are absent, never null: the route serves this model with
    # ``response_model_exclude_none``, and ``SkipJsonSchema[None]`` keeps ``null`` out of the
    # served schema. ``filter_applied`` is true only on an upload sent with
    # ``runs_and_captures_only``; ``replaced_session_id`` is set only when an overnight night
    # replaced a stored re-summary of the same night.
    filter_applied: bool | SkipJsonSchema[None] = None
    replaced_session_id: str | SkipJsonSchema[None] = None


class IngestSkipped(BaseModel):
    """``POST /sessions``'s 200: a file stored nothing.

    ``non_activity`` and ``skipped_sport`` come only with ``runs_and_captures_only`` (a file the
    rule does not keep); ``no_night`` is an HRV_STATUS file that holds no night, with or without
    it. ``filter_applied`` says whether the switch was sent.
    """

    skipped: Literal[True]
    reason: Literal["non_activity", "skipped_sport", "no_night"]
    file_type: str | None = Field(
        default=None,
        description="non_activity: the file_id type name, or its number as a string; no_night: hrv_status.",
    )
    sport: str | None = Field(default=None, description="skipped_sport: the sport as the FIT file names it.")
    filter_applied: bool


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
            "with 0 < dt <= 5 s and no moving gap exists), `no_distance` (distance_m, avg_pace_s_per_km and "
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
            "Recorded time T: the sum of dt over counted segments: consecutive records 0 < dt <= 5 s apart, or a "
            "moving gap (dt > 5 s crossed at 0.5 m/s or more of device distance, counted in full; the session "
            "carries the `moving_gap` flag). A standstill over 5 s and a dt = 0 duplicate contribute nothing."
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
    the same quantities SessionSummary names: recorded time, not elapsed
    (sampled strides and moving gaps count; a standstill does not), and
    listSessions computes them by the same rule.
    """

    session_id: str
    sport: str
    flags: list[str] = Field(
        description=(
            "The session's stored `quality_flags` first, in stored order with `smart_recording` moved to the "
            "front when present; then `distance_regressed` (a counted segment whose distance decreased), "
            "`gap_unavailable` (D > 0 and gap_coverage < 1: spec/03 section 3.3.4's raw-pace fallback applied "
            "to part of the run), `grade_clamped` (a segment's grade was clamped to +/-0.45) and `moving_gap` (a "
            "gap over 5 s crossed at 0.5 m/s or more was counted for time and distance). Each at most once."
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
    JSON ``true`` is not taken as 1. A ``display_name`` holding a lone surrogate (a ``\\ud800`` escape) is
    refused: it is not text. ``birth_date`` is a ``YYYY-MM-DD`` date; the route also refuses one
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

    @field_validator("display_name")
    @classmethod
    def _encodable(cls, value: str | None) -> str | None:
        """A ``\\ud800`` escape parses to a lone surrogate, which the store cannot write as UTF-8."""
        if value is not None:
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("the name holds a lone surrogate, which is not text") from exc
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
# The body ``metrics.session_load.compute_session_load`` builds and ``db``
# saves at upload or replaces at a recompute (F021), typed. The
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
    wins), then the reference step's no_threshold_hr, threshold_order_conflict and not_representable, which leave
    `hr_trimp.value` served while `session_load` is unavailable (not_representable is also gate 9's reason)."""

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
    "`session_file` when the session's own FIT file carried the value. Otherwise, for an HR value, the setting in "
    "effect on the run's date: `session_file_dated` (an earlier stored file, named by `session_id`), `history` (a "
    "dated entry, named by `entry_id` and `effective_from`) or `anchor` (the HR anchor in effect when the load was saved, the "
    "run having no file at or before its start, nor entry on or before its date, or that file being the anchor's own); for sex, `anchor`. Null when "
    "nothing supplied it. A body saved before dated entries existed has only `session_file` and `anchor`."
)
_LOAD_ANCHOR_UNAVAILABLE_DESCRIPTION = (
    "When the file carried no value and the anchor when the load was saved had none either, the anchor's own reason "
    "(`missing` or `order_conflict`); null otherwise."
)
_LOAD_OUT_OF_PERIOD_DESCRIPTION = (
    "True when `source` is `anchor` and that anchor came from a file dated after the run: no stored file starting at "
    "or before the run's start (a running file, for max and threshold HR) and no dated entry effective on or before "
    "the run's local date carried the value. False otherwise, and false on a body saved before dated entries existed."
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
    source: Literal["session_file", "anchor", "session_file_dated", "history"] | None = Field(
        description=_LOAD_SOURCE_DESCRIPTION
    )
    session_id: str | None = Field(
        description="The session whose file carried the value; null unless `source` is `session_file` or `session_file_dated`. With `session_file_dated` the session may since have been deleted; the value stays until recomputed."
    )
    anchor_version: int | None = Field(description="The anchor's version when the load was saved; null unless `source` is `anchor`.")
    anchor_unavailable: Literal["missing", "order_conflict"] | None = Field(
        description=_LOAD_ANCHOR_UNAVAILABLE_DESCRIPTION
    )
    entry_id: int | None = Field(
        default=None,
        description="The dated entry that supplied the value; null unless `source` is `history`.",
    )
    effective_from: str | None = Field(
        default=None,
        description="That entry's effective date (YYYY-MM-DD); null unless `source` is `history`.",
    )
    out_of_period: bool = Field(default=False, description=_LOAD_OUT_OF_PERIOD_DESCRIPTION)


class SessionLoadSexInput(BaseModel):
    """The sex value used for the coefficients, and where it came from."""

    value: Literal["male", "female"] | None = Field(
        description="The value used; null when neither the file nor the anchor had one (the men's pair is used and `sex_defaulted` is flagged)."
    )
    source: Literal["session_file", "anchor"] | None = Field(description=_LOAD_SOURCE_DESCRIPTION)
    session_id: str | None = Field(description="The session whose file carried the value; null unless `source` is `session_file`.")
    anchor_version: int | None = Field(description="The anchor's version when the load was saved; null unless `source` is `anchor`.")
    anchor_unavailable: Literal["missing", "order_conflict"] | None = Field(
        description=_LOAD_ANCHOR_UNAVAILABLE_DESCRIPTION
    )
    entry_id: int | None = Field(default=None, description="Always null: sex has no dated entries.")
    effective_from: str | None = Field(default=None, description="Always null: sex has no dated entries.")
    out_of_period: bool = Field(default=False, description="Always false: sex is the file's or the anchor's.")


class SessionLoadInputs(BaseModel):
    """Every value the load was computed from, saved with it (F017 reference, "Time basis" and "Which values")."""

    hr_time_s: float = Field(
        description="Running time with usable HR: the sum of dt over counted segments (a stride of 0 < dt <= 5 s, or a moving gap over 5 s crossed at 0.5 m/s or more, which carries the HR at its start) whose start HR is present, above 0 and not `cadence_lock`. A stationary pause adds nothing; nothing is interpolated across a gap."
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
    """The training load of one session, HR-TRIMP first (F017), as saved at its upload or its last recompute.

    Computed in the upload's own transaction, from the session's own file
    values (else the setting in effect on the run's date) and saved with
    every value used; a later anchor change moves nothing until
    POST /sessions/{id}/load/recompute replaces it. The route serves the saved
    body and reads no anchor. A deleted session has no load (404, the envelope
    of GET /sessions/{id}).
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
    after_gap: bool = Field(
        description="True on the first running day after a gap in history and the 41 days after it: the recursion carried on through the break, so these values are still mostly the break (IDEA-143, user ruling 2026-10-10). Not a re-seed, and not `provisional`."
    )


class LoadChartGap(BaseModel):
    """A gap in history (IDEA-143, user ruling 2026-10-10): a stretch of more than 21 consecutive empty
    local days (no counted and no uncounted run; an excluded ride or capture does not end it) that ended
    in a running day on or before `today`. The HRV trend's coverage-gap silence, also strict. A stretch
    still open today is not listed and marks nothing."""

    first_empty_day: datetime.date = Field(description="The first local date of the stretch: the day after the last run before the gap.")
    last_empty_day: datetime.date = Field(description="The last local date of the stretch: the day before the first run back.")
    empty_days: int = Field(description="`last_empty_day` - `first_empty_day` + 1; always more than 21.")


class LoadChart(BaseModel):
    """GET /metrics/load: the fitness, fatigue and form series (F018, spec/03 section 3.5).

    Computed on read from the saved loads (F017; a recompute replaces one), the sessions'
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
    gaps: list[LoadChartGap] = Field(
        description="Every gap in the whole history up to `today`, oldest first, whatever `from` and `to` are; empty when there is none."
    )
    days: list[LoadChartDay] = Field(
        description="Every local date from the later of `from` and `first_day` to `to`, in order, rest days included; empty when history has no day in the range."
    )


# ---------------------------------------------------------------------------
# GET /sessions (F019)
#
# Appended below the earlier models, as the load chart's are, so the lines
# cited by number stay in place.
# ---------------------------------------------------------------------------


class SessionSummary(BaseModel):
    """One row of GET /sessions (F019): the list-view projection of a stored session.

    duration_s, distance_m and avg_pace_s_per_km are the quantities
    SessionFeatures serves under the same names (recorded time, not elapsed),
    computed on read by the same segment rule over the session's records.
    They are null for a session not stored as running, for a run whose
    records count no segment, and for a row whose records could not be read
    (user ruling 2026-10-09: such a session lists with blanks, and the page
    is still served).
    """

    session_id: str
    start_time: str = Field(description="As stored: ISO 8601 in UTC with a +00:00 offset, never re-spelt.")
    sport: str = Field(description="The canonical sport as stored: `running` or `other`.")
    activity_tag: str | None = Field(description="As stored.")
    name: str | None = Field(
        description="The name saved at upload (the FIT workout name, else the activity profile name); for a session stored before the name column existed, the profile name saved in its context, else null."
    )
    distance_m: float | None = Field(description="D: the sum of max(dd, 0) over counted segments; null when D is 0.")
    duration_s: float | None = Field(description="Recorded time T: the sum of dt over counted segments; null when T is 0.")
    avg_pace_s_per_km: float | None = Field(description="1000 * T / D; null when D is 0.")
    quality_flags: list[str] = Field(description="As stored.")
    workout_id: str | None = Field(description="Always null: no planned workout is linked.")


class SessionPage(BaseModel):
    """GET /sessions: the cursor-paged envelope (the contract's Page), newest first."""

    items: list[SessionSummary] = Field(
        description="Ordered by start_time descending, ties by session_id descending."
    )
    next_cursor: str | None = Field(
        None,
        description="Pass as `since` for the next page: URL-safe base64 of the last row's start_time and session_id. Null on the last page, so no page is ever empty but the first page of an empty store.",
    )


# ---------------------------------------------------------------------------
# /me/hr-history (F021)
#
# Appended below the earlier models, as the list's are, so the lines cited by
# number stay in place.
# ---------------------------------------------------------------------------

_HrField = Literal["resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm"]


class HrHistoryEntryCreate(BaseModel):
    """The body of ``POST /me/hr-history``: one HR setting and the date it took effect.

    ``value`` takes ``PATCH /me``'s HR screen: a strict whole number of bpm above 0 (``true``,
    ``"188"`` and ``188.5`` are refused). ``effective_from`` is a ``YYYY-MM-DD`` date; the route also
    refuses one after today in ``athlete_timezone``. Sex and the body fields have no dated entries."""

    model_config = ConfigDict(extra="forbid")

    field: _HrField = Field(description="`resting_hr_bpm`, `max_hr_bpm` or `threshold_hr_bpm`.")
    value: int = Field(gt=0, strict=True, description="A whole number of bpm above 0.")
    effective_from: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        json_schema_extra={"format": "date"},
        description="The `YYYY-MM-DD` date the value took effect, not after today in the athlete's timezone.",
    )

    @field_validator("effective_from")
    @classmethod
    def _a_calendar_date(cls, value: str) -> str:
        """The pattern admits ``2016-13-01``; ``date.fromisoformat`` refuses it."""
        datetime.date.fromisoformat(value)
        return value


class HrHistoryEntry(BaseModel):
    """One stored dated HR entry (F021). The store holds at most one per field and effective date:
    a second entry for the pair replaced the value and kept the entry_id."""

    entry_id: int
    field: _HrField
    value: int
    effective_from: str = Field(json_schema_extra={"format": "date"})
    set_at: str = Field(
        description="When the value was last written, ISO 8601 in UTC; a replacement moves it, and the entry keeps its entry_id."
    )


# ---------------------------------------------------------------------------
# /imports (F020)
#
# Appended below the earlier models, as the list's are, so the lines cited by
# number stay in place. The shapes are the F020 reference's section 6; the
# rows they serve are read by ``imports.job.read_import`` and ``list_imports``.
# ---------------------------------------------------------------------------

ImportStatus = Literal["queued", "scanning", "running", "completed", "failed", "interrupted"]
ImportOutcome = Literal["stored", "replaced", "already_stored", "no_night", "skipped_sport", "failed"]


class ImportProgress(BaseModel):
    """How far the job has got: candidates screened while ``scanning``, activities done while ``running``."""

    fit_files_scanned: int = Field(description="FIT candidates screened so far (written every 100 while scanning).")
    activities_total: int = Field(
        description="Rows the job will process: activities and HRV_STATUS files, plus unreadable FIT candidates."
    )
    activities_processed: int = Field(description="Rows written so far; equal to activities_total when the job completes.")


class ImportUnreadableArchive(BaseModel):
    """An inner zip that is encrypted, could not be read, did not open, or passed its 1 GiB cap; its entries were not read."""

    label: str = Field(description="The inner zip's path in the archive.")
    reason: str = Field(description="Why it was not read.")


class ImportCounts(BaseModel):
    """The job's tallies (reference section 4). The three maps are keyed by name and count files."""

    stored: int
    already_stored: int
    failed: int
    no_night: int = Field(default=0, description="HRV_STATUS files holding no night; 0 for a job stored before the key existed.")
    replaced: int = Field(default=0, description="Nights that replaced a stored re-summary of themselves; 0 for a job stored before the key existed.")
    skipped_sport: dict[str, int] = Field(description="Skipped activities by the sport the FIT file names (a number as its string).")
    non_activity: dict[str, int] = Field(description="FIT files that are not activities, by file type name (a number for a Garmin-private type).")
    not_fit: dict[str, int] = Field(description="Entries that are not FIT files, by lower-cased extension without the dot; `(none)` without one.")
    unreadable_archives: list[ImportUnreadableArchive]


class ImportFile(BaseModel):
    """One processed activity or HRV_STATUS file (or unreadable FIT candidate), in processing order."""

    seq: int = Field(description="Processing order, from 1: oldest time_created first.")
    entry: str = Field(description="The entry's label: its path, or `<outer path>!<inner path>` inside an inner zip.")
    time_created: str | None = Field(description="file_id.time_created, ISO 8601 in UTC; null when absent or unreadable.")
    outcome: ImportOutcome
    session_id: str | None = Field(description="The stored or already stored session; kept after that session is deleted.")
    sport: str | None = Field(description="Canonical sport when stored; `other` on a night's row (stored, replaced or no_night); the FIT file's sport when skipped_sport.")
    activity_tag: str | None = Field(description="The stored session's activity_tag (resting_hrv_check and health_snapshot mark resting captures; overnight_hrv a night's stored, replaced or no_night row).")
    quality_flags: list[str] | None = Field(description="The stored or replacing session's quality flags ([] for a night); null for other outcomes.")
    status: int | None = Field(description="On failed: for a file the pipeline refused, the status POST /sessions answers for it (413 for a FIT entry over its 50 MB cap); null for a candidate that could not be screened, and otherwise.")
    detail: str | None = Field(description="On failed: for a file the pipeline refused, the message POST /sessions answers; for a candidate that could not be screened, the scan's reason (the entry is encrypted or could not be read, file_id carries no type, file_id could not be read); otherwise \"database is locked\", or the exception's type and message.")


class Import(BaseModel):
    """One archive import job (reference section 6). ``files`` is served by getImport and startImport only."""

    import_id: str
    archive_name: str = Field(description="The uploaded file's name, as sent.")
    archive_bytes: int | None = Field(description="The archive's size in bytes, as copied.")
    status: ImportStatus
    error: str | None = Field(description="On failed: the cause; otherwise null.")
    created_at: str = Field(description="ISO 8601 in UTC.")
    started_at: str | None = Field(description="When scanning began, ISO 8601 in UTC.")
    finished_at: str | None = Field(description="When the job ended (or was found interrupted at startup), ISO 8601 in UTC.")
    progress: ImportProgress
    counts: ImportCounts
    files: list[ImportFile] | None = Field(
        None, description="Every processed row in seq order; absent from listImports."
    )


class ImportPage(BaseModel):
    """GET /imports: every import job, newest first, without files."""

    items: list[Import] = Field(description="Newest first by created_at; no item carries files.")


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/load/recompute (F021 AC5)
#
# Appended below the import models. Named exactly as the contract's
# component, so the route test's walker compares like with like.
# ---------------------------------------------------------------------------


class SessionLoadRecompute(SessionLoad):
    """recomputeSessionLoad: the recomputed body, as now saved, with the SessionLoad fields at the top
    level, plus changed."""

    changed: bool = Field(
        description="Whether any field of the new body differs from the body saved before, compared after "
        "filling the dated-entry input keys' defaults on the old body; true when the session had no saved load."
    )


# ---------------------------------------------------------------------------
# PUT /race-tags (F023)
#
# Appended below the earlier models, as the list's are, so the lines cited by
# number stay in place. Named exactly as the contract's components, so the
# route test's walker compares like with like.
# ---------------------------------------------------------------------------
from collections import Counter

from pydantic import model_validator

from runcoach_api import race_tags as _race_tags

#: ``YYYY-MM-DDTHH:MM:SS`` in UTC, whole seconds, ``Z`` or ``+00:00``, ASCII digits only (``\d``
#: would admit other scripts' digits, which ``fromisoformat`` reads).
RACE_TAG_START_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(Z|\+00:00)$"
RACE_TAG_MAX_ITEMS = 500

_RaceTagOutcome = Literal[
    "tagged", "cleared", "retagged", "unchanged", "not_stored", "mismatch", "ambiguous", "other_tag"
]
_ItemTag = Literal["race", "threshold_workout"]

_ZERO_NOTE = (
    "Zero is accepted, not refused: a treadmill run without a foot pod reads 0 m in Connect and on the watch, "
    "and is still matched by its start second; under the 1 % rule 0 agrees with a stored 0 and with nothing else. "
    "Null never agrees."
)
_ZERO_DURATION_NOTE = (
    "Zero is accepted, not refused; under the 1 % rule 0 agrees with a stored 0 and with nothing else. "
    "Null never agrees."
)


class RaceTagItem(BaseModel):
    """One Connect activity's tag (F023 AC1, F026 reference section 2). Strict: any other key, a
    non-integer id, a non-boolean ``race``, a ``tag`` other than the two or null, both or neither
    of ``race`` and ``tag``, a negative or non-finite number, or a start time in any other form is
    a 422. ``start_time`` is a string, never pydantic's ``datetime``, which accepts other zones,
    fractions of a second and epoch numbers; the pattern fixes the form and
    ``datetime.fromisoformat`` refuses a date or time that does not exist."""

    model_config = ConfigDict(extra="forbid")

    connect_activity_id: int = Field(strict=True, description="The Connect activityId.")
    start_time: str = Field(
        strict=True,
        pattern=RACE_TAG_START_PATTERN,
        description="Connect's startTimeGMT as YYYY-MM-DDTHH:MM:SS in UTC, whole seconds, ending Z or +00:00; "
        "any other zone, a fraction of a second or another form is a 422.",
    )
    distance_m: float | None = Field(
        ge=0, strict=True, allow_inf_nan=False, description=f"Connect's distance in metres, or null. {_ZERO_NOTE}"
    )
    duration_s: float | None = Field(
        ge=0, strict=True, allow_inf_nan=False, description=f"Connect's duration in seconds, or null. {_ZERO_DURATION_NOTE}"
    )
    race: bool = Field(
        default=None,
        strict=True,
        description="Whether Connect's event type is race: true means the tag race, false means no tag. "
        "Exactly one of race and tag; an explicit null is a 422.",
    )
    tag: _ItemTag | None = Field(
        default=None,
        description="The tag to store: race, threshold_workout, or null to clear (F026). Exactly one of race "
        "and tag.",
    )

    @field_validator("start_time")
    @classmethod
    def _an_existing_instant(cls, value: str) -> str:
        """The pattern admits ``2024-02-30T25:00:00Z``; ``fromisoformat`` refuses it."""
        datetime.datetime.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def _exactly_one_of_race_and_tag(self) -> "RaceTagItem":
        """Judged by the keys the item carries, never by ``is None``: ``"tag": null`` is a clear,
        and ``"race": null`` never reaches here (a strict boolean)."""
        present = {"race", "tag"} & self.model_fields_set
        if len(present) != 1:
            raise ValueError(f"exactly one of race and tag is required; the item has {len(present)}")
        return self

    def wanted(self) -> str | None:
        """The tag this item asks for (``race_tags.wanted_tag`` over the key it carries)."""
        key = "tag" if "tag" in self.model_fields_set else "race"
        return _race_tags.wanted_tag({key: getattr(self, key)})


class RaceTagsUpdate(BaseModel):
    """The body of ``PUT /race-tags``: 1 to 500 items with distinct ``connect_activity_id``."""

    model_config = ConfigDict(extra="forbid")

    items: list[RaceTagItem] = Field(
        min_length=1,
        max_length=RACE_TAG_MAX_ITEMS,
        description="1 to 500 items, each connect_activity_id once; a repeated id is a 422.",
    )

    @model_validator(mode="after")
    def _distinct_ids(self) -> "RaceTagsUpdate":
        uses = Counter(item.connect_activity_id for item in self.items)
        repeated = sorted(activity_id for activity_id, n in uses.items() if n > 1)
        if repeated:
            raise ValueError(f"connect_activity_id repeated: {', '.join(str(r) for r in repeated)}")
        return self


class RaceTagItemResult(BaseModel):
    """One item's outcome, in request order."""

    connect_activity_id: int
    outcome: _RaceTagOutcome
    session_id: str | None = Field(
        description="The session tagged, cleared, retagged, left unchanged or left with another tag, or the one mismatch "
        "candidate; null for not_stored and ambiguous."
    )
    candidates: list[str] = Field(
        description="Every stored running session at the item's start second, resting captures excluded; "
        "empty for not_stored."
    )


class RaceTagWrites(BaseModel):
    """One tag's written outcomes: ``tagged`` and ``retagged`` count items that wanted the tag,
    ``cleared`` items whose stored tag it was."""

    tagged: int
    cleared: int
    retagged: int


class RaceTagCountsByTag(BaseModel):
    """``RaceTagCounts.by_tag``: the written outcomes per tag."""

    race: RaceTagWrites
    threshold_workout: RaceTagWrites


class RaceTagCounts(BaseModel):
    """How many items had each outcome; every key is present, zero included. ``by_tag`` breaks the
    written outcomes down per tag."""

    tagged: int
    cleared: int
    retagged: int
    unchanged: int
    not_stored: int
    mismatch: int
    ambiguous: int
    other_tag: int
    by_tag: RaceTagCountsByTag


assert tuple(f for f in RaceTagCounts.model_fields if f != "by_tag") == _race_tags.OUTCOMES
assert tuple(RaceTagCountsByTag.model_fields) == _race_tags.ITEM_TAGS
assert tuple(RaceTagWrites.model_fields) == _race_tags.WRITTEN_OUTCOMES


class RaceTagsResult(BaseModel):
    """``PUT /race-tags``'s 200 body."""

    items: list[RaceTagItemResult] = Field(description="One per request item, in request order.")
    counts: RaceTagCounts


# ---------------------------------------------------------------------------
# PUT /sessions/{session_id}/laps (F025)
#
# Inserted before the F024 block so the anchors at the file's tail stay in
# place. Named exactly as the contract's component. The detail's lap and
# workout step entries have no model here: ``GET /sessions/{id}`` serves a
# dict, validated against the contract's ``SessionLap`` and
# ``SessionWorkoutStep`` components by the tests.
# ---------------------------------------------------------------------------


class SessionLapsAttached(BaseModel):
    """``PUT /sessions/{session_id}/laps``'s 200 body: the rows the file replaced the session's with."""

    session_id: str
    laps: int = Field(description="How many lap rows the session now has.")
    workout_steps: int = Field(description="How many workout step rows the session now has.")


# ---------------------------------------------------------------------------
# POST /laps/candidates (F025)
#
# Inserted before the F024 block so the anchors at the file's tail stay in
# place. The item's start time and the 1 to 500 items with distinct ids are
# ``setRaceTags``' rules, read from the same constants.
# ---------------------------------------------------------------------------
from runcoach_api import lap_candidates as _lap_candidates

_LapCandidateOutcome = Literal["matched", "not_stored", "ambiguous"]


class LapCandidateItem(BaseModel):
    """One listed Connect run to match by its start second. Strict: any other key, a non-integer
    id or a start time in any other form is a 422; ``start_time`` is checked as ``RaceTagItem``'s
    is (the pattern, then ``datetime.fromisoformat``)."""

    model_config = ConfigDict(extra="forbid")

    connect_activity_id: int = Field(strict=True, description="The Connect activityId.")
    start_time: str = Field(
        strict=True,
        pattern=RACE_TAG_START_PATTERN,
        description="Connect's startTimeGMT as YYYY-MM-DDTHH:MM:SS in UTC, whole seconds, ending Z or +00:00; "
        "any other zone, a fraction of a second or another form is a 422.",
    )

    @field_validator("start_time")
    @classmethod
    def _an_existing_instant(cls, value: str) -> str:
        """The pattern admits ``2024-02-30T25:00:00Z``; ``fromisoformat`` refuses it."""
        datetime.datetime.fromisoformat(value)
        return value


class LapCandidatesRequest(BaseModel):
    """The body of ``POST /laps/candidates``: 1 to 500 items with distinct ``connect_activity_id``."""

    model_config = ConfigDict(extra="forbid")

    items: list[LapCandidateItem] = Field(
        min_length=1,
        max_length=RACE_TAG_MAX_ITEMS,
        description="1 to 500 items, each connect_activity_id once; a repeated id is a 422.",
    )

    @model_validator(mode="after")
    def _distinct_ids(self) -> "LapCandidatesRequest":
        uses = Counter(item.connect_activity_id for item in self.items)
        repeated = sorted(activity_id for activity_id, n in uses.items() if n > 1)
        if repeated:
            raise ValueError(f"connect_activity_id repeated: {', '.join(str(r) for r in repeated)}")
        return self


class LapCandidateItemResult(BaseModel):
    """One item's answer, in request order."""

    connect_activity_id: int
    outcome: _LapCandidateOutcome = Field(
        description="matched: exactly one stored running session at the item's start second (resting captures "
        "excluded); not_stored: none; ambiguous: two or more. Distance and duration are never compared."
    )
    session_id: str | None = Field(description="The matched session; null for not_stored and ambiguous.")
    laps_read_at: str | None = Field(
        description="When the matched session's laps were last read from its file; null when never read, and for "
        "not_stored and ambiguous."
    )


assert tuple(_LapCandidateOutcome.__args__) == _lap_candidates.OUTCOMES


class LapCandidatesResult(BaseModel):
    """``POST /laps/candidates``' 200 body."""

    items: list[LapCandidateItemResult] = Field(description="One per request item, in request order.")


# ---------------------------------------------------------------------------
# GET /state (F024, F026): threshold pace from tagged races and threshold workouts
#
# Appended at the end so the lines cited above stay in place.
# ---------------------------------------------------------------------------


class ThresholdPaceInterval(BaseModel):
    """The 80 % interval in s/km, fast end first (symmetric in log speed, so not in pace)."""

    low: float
    high: float


class ThresholdPaceValue(BaseModel):
    """The contract's ``ConfidenceValue`` for threshold pace."""

    value: float = Field(description="Threshold pace in s/km: 1000 / exp(x).")
    unit: Literal["s/km"]
    interval: ThresholdPaceInterval
    confidence: Literal["low", "medium", "high"] = Field(
        description="`high` while P <= R, `medium` while P <= 4 R, `low` above (F024 reference section 1)."
    )
    provisional: bool = Field(description="Always false: no seed exists, so an estimate is never provisional.")


class DeterminantTrend(BaseModel):
    """Not computed yet: always `unknown` with a null rate."""

    direction: Literal["unknown"]
    rate: None


_ThresholdKind = Literal["race", "threshold_workout"]
_KIND_DESCRIPTION = (
    "`race`, or `threshold_workout` (tagged Training in Connect, or a step's workout name listed in "
    "`threshold_workout_names`)."
)
_ADMITTED_BY_DESCRIPTION = (
    "What admitted a workout, in this order: `tag` (tagged Training in Connect) and `name` (a step row's "
    "workout name is listed in `threshold_workout_names`); a tagged workout whose laps are unread reads "
    "`[\"tag\"]`, its name unseen. Null for a race."
)
_WORKOUT_NAME_DESCRIPTION = (
    "A workout's stored workout name as written on its first step row that has one; null when none has, "
    "and for a race."
)


class ThresholdEffort(BaseModel):
    """A race or threshold workout (tagged Training or named in `threshold_workout_names`) the filter used,
    with the gain it entered with (1 for the first)."""

    session_id: str
    start_time: datetime.datetime
    kind: _ThresholdKind = Field(description=_KIND_DESCRIPTION)
    duration_s: float = Field(
        description="A race's `timer_time_s`, else the summary's `duration_s`; a workout's counted time `A`, the "
        "counted reps' summed `timer_s`."
    )
    gap_pace_s_per_km: float = Field(
        description="F013's grade-adjusted pace of the race, or of the workout's counted reps' windows."
    )
    gap_coverage: float | None = Field(
        description="F013's graded share of the distance (a workout's: over its counted reps' windows); below 1 counts "
        "at flat pace."
    )
    gps_degraded_fraction: float | None = Field(
        description="F013's degraded-GPS share of the distance (a workout's: over its counted reps' windows); shown, "
        "never judged."
    )
    gain: float
    active_blocks: int | None = Field(
        description="A workout's runs of consecutive counted laps, remainder laps included (3x10 min with jogs reads 3 when the jogs are recovery steps; Goal Pace Run steps 1 then 2 read 1); null for a race."
    )
    admitted_by: list[Literal["tag", "name"]] | None = Field(description=_ADMITTED_BY_DESCRIPTION)
    workout_name: str | None = Field(description=_WORKOUT_NAME_DESCRIPTION)


class ThresholdExclusion(BaseModel):
    """A running race or threshold workout (tagged Training or named in `threshold_workout_names`) at or
    before `as_of` that was not used, and why."""

    session_id: str
    start_time: datetime.datetime
    kind: _ThresholdKind = Field(description=_KIND_DESCRIPTION)
    admitted_by: list[Literal["tag", "name"]] | None = Field(description=_ADMITTED_BY_DESCRIPTION)
    workout_name: str | None = Field(description=_WORKOUT_NAME_DESCRIPTION)
    reason: Literal[
        "outside_threshold_band",
        "distance_regressed",
        "smart_recording",
        "gap_pace_unavailable",
        "summary_unreadable",
        "quality_flags_unreadable",
        "features_unavailable",
        "laps_not_read",
        "lap_times_unreadable",
        "no_workout_steps",
        "no_sustained_work",
        "active_time_below_minimum",
        "laps_unreadable",
    ] = Field(
        description="The first of the F024 reference section 2 rows a race fails, or of the F027 reference "
        "section 4 orders a workout fails (`laps_not_read`, `laps_unreadable`, `lap_times_unreadable`, "
        "`no_workout_steps`, `no_sustained_work`: no work rep of 290 s; the record reasons; the flags; "
        "`gap_pace_unavailable`: no sustained rep with a GAP pace; `active_time_below_minimum`: the counted "
        "reps sum to under 590 s); or, when a stored value it needs does not read, `summary_unreadable` "
        "(races only), `quality_flags_unreadable`, `features_unavailable` or `laps_unreadable` (workouts only)."
    )


class ThresholdPaceParameters(BaseModel):
    """The constants the estimate was computed with (F024 reference section 1, F027 reference section 4)."""

    typical_error: float
    doubling_days: int
    coverage: float
    high_max_error: float
    medium_max_error: float
    short_race_s: int
    workout_min_active_s: int = Field(
        description="A threshold workout's least counted time in seconds, summed over its counted work reps; no maximum."
    )
    workout_min_rep_s: int = Field(description="A work rep's least length in seconds to count as sustained work.")
    workout_rep_band: float = Field(
        description="A sustained rep counts when its GAP pace is at most this times the workout's fastest sustained rep's."
    )


class ThresholdDeterminant(BaseModel):
    """The `functional_threshold_pace` entry of the state model, from tagged races and the counted work
    reps of threshold workouts (tagged Training or named in `threshold_workout_names`).
    Its 80 % interval covers effort-to-effort noise,
    not bias: an effort read wrongly (a workout tagged Race, read whole) moves the estimate and the
    interval with it, so the interval need not contain the true pace."""

    name: Literal["functional_threshold_pace"]
    estimate: ThresholdPaceValue | None
    unavailable: Literal["no_qualifying_effort", "estimate_not_finite"] | None = Field(
        description="`no_qualifying_effort` before the first qualifying race or workout; `estimate_not_finite` "
        "when an effort's GAP pace is so far out of range that the estimate overflows. Null with an estimate."
    )
    trend: DeterminantTrend
    error: float | None = Field(description="sqrt(P) as a fraction of speed (0.025 is 2.5 %); null when unavailable.")
    short_races_only: bool = Field(
        description="At least one race used and every race used lasted under 1800 s: the pace likely reads fast. "
        "Workouts never set it; a history of workouts only reads false."
    )
    efforts: list[ThresholdEffort] = Field(description="The races and workouts used, in start-time order.")
    excluded: list[ThresholdExclusion] = Field(
        description="Every tagged running race and every threshold workout (tagged or named) not used, in "
        "start-time order."
    )
    parameters: ThresholdPaceParameters


# GET /state (F029): the endurance exponent (Riegel b) from tagged races, beside threshold pace.


class RiegelExponentInterval(BaseModel):
    """The 80 % interval of b, `b - t error` to `b + t error`."""

    low: float
    high: float


class RiegelExponentValue(BaseModel):
    """The contract's ``ConfidenceValue`` for the endurance exponent."""

    value: float = Field(description="b, the slope of ln(time) on ln(GAP-equivalent distance); the population 1.06 below the bar.")
    unit: Literal["1"]
    interval: RiegelExponentInterval
    confidence: Literal["low", "medium", "high"] = Field(
        description="`high` while the error (a fading race counted by its weight) is at most 0.02, `medium` while at "
        "most 0.04, `low` above, and for the population value (F029 reference section 1). The label reads the error alone; the interval carries the precision, t included, so a consumer comparing fits reads the interval."
    )
    provisional: bool = Field(description="True for the population value, false for a fitted b.")


class RiegelEffort(BaseModel):
    """A tagged race the fit of b used, with its weight, its fitness figures and its residual."""

    session_id: str
    start_time: datetime.datetime
    duration_s: float = Field(description="`timer_time_s`, else the summary's `duration_s`.")
    distance_m: float | None = Field(description="The summary's `distance_m`, shown beside the GAP-equivalent distance.")
    gap_pace_s_per_km: float = Field(description="F013's grade-adjusted pace of the race.")
    gap_equivalent_m: float | None = Field(
        description="`1000 * duration_s / gap_pace_s_per_km`, the distance the fit reads; null when not finite, and "
        "the estimate is then unavailable."
    )
    gap_coverage: float | None = Field(description="F013's graded share of the distance; below 1 counts at flat pace.")
    gps_degraded_fraction: float | None = Field(description="F013's degraded-GPS share of the distance; shown, never judged.")
    weight: float = Field(
        description="`min(1, (1095 - age in days) / 182)`: 1 until 913 days old, falling to 0 at 1095. The race counts "
        "by it in the effective number of races, (sum w)^2 / sum(w^2), that sets the bar, the error and t."
    )
    fitness_before: float | None = Field(
        description="The load chart's CTL on the local day before the race; null when that day is before the chart's "
        "first day."
    )
    fitness_earlier: float | None = Field(
        description="The load chart's CTL 29 days before the race's local day; null when that day is before the "
        "chart's first day."
    )
    fitness_drop: float | None = Field(
        description="`1 - fitness_before / fitness_earlier`, the fall over the four weeks; null without both figures."
    )
    fitness_provisional: bool | None = Field(
        description="The day-before chart day's `provisional`; null when that day is before the chart's first day."
    )
    fitness_after_gap: bool | None = Field(
        description="The day-before chart day's `after_gap`; null when that day is before the chart's first day."
    )
    residual: float | None = Field(
        description="`exp(r) - 1` against the fitted line, positive slower; null below the bar and when unavailable."
    )


class RiegelExclusion(BaseModel):
    """A tagged running race at or before `as_of` that the fit of b did not use, and why."""

    session_id: str
    start_time: datetime.datetime
    reason: Literal[
        "admitted_as_workout",
        "outside_window",
        "summary_unreadable",
        "duration_unavailable",
        "quality_flags_unreadable",
        "distance_regressed",
        "smart_recording",
        "fitness_dropped",
        "features_unavailable",
        "grade_clamped",
        "gap_pace_unavailable",
    ] = Field(
        description="The first of the F029 reference section 2 rows the race fails: `admitted_as_workout` (a listed "
        "workout name), `outside_window` (1095 days or more before `as_of`), `summary_unreadable` or "
        "`duration_unavailable`, `quality_flags_unreadable` or a stored flag (`distance_regressed`, "
        "`smart_recording`), `fitness_dropped` (fitness fell more than 30 % in the four weeks before the day before "
        "the race), `features_unavailable` or an F013 flag (`distance_regressed`, `smart_recording`, "
        "`grade_clamped`), `gap_pace_unavailable`."
    )
    fitness_before: float | None = Field(description="For `fitness_dropped`, the CTL on the local day before the race; else null.")
    fitness_earlier: float | None = Field(description="For `fitness_dropped`, the CTL 29 days before the race's local day; else null.")
    fitness_drop: float | None = Field(description="For `fitness_dropped`, `1 - fitness_before / fitness_earlier`; else null.")


class RiegelExponentParameters(BaseModel):
    """The constants b was computed with (F029 reference section 1), so it can be redone by hand."""

    window_days: int
    fade_days: int
    min_races: int
    min_spread: float
    fitness_drop: float
    fitness_lookback_days: int
    population_b: float
    population_error: float
    coverage: float
    high_max_error: float
    medium_max_error: float


class RiegelDeterminant(BaseModel):
    """The `riegel_exponent` entry: the individual endurance exponent b from the tagged races of the last
    1095 days that pass the section 2 screens, or the population 1.06 until three races, a fading race
    counted by its weight, span a twofold distance ratio. Its interval covers race-to-race scatter, not bias: a race not run all-out moves b and
    the interval together."""

    name: Literal["riegel_exponent"]
    estimate: RiegelExponentValue | None
    unavailable: Literal["estimate_not_finite"] | None = Field(
        description="`estimate_not_finite` when a race's GAP-equivalent distance, b, the error, the interval or a "
        "residual is not finite: `estimate`, `error` and `t_multiplier` are null, `efforts` and `parameters` "
        "still served. Null with an estimate."
    )
    trend: DeterminantTrend
    error: float | None = Field(
        description="The weighted standard error of b over the effective number of races less 2, n_eff = (sum w)^2 / "
        "sum(w^2) (0.05 for the population value); null when unavailable."
    )
    t_multiplier: float | None = Field(
        description="Student's t at 0.90 for floor(n_eff + 1e-9) - 2 degrees of freedom, n_eff the effective number of races "
        "(z above 30 and for the population value); null when unavailable."
    )
    population_default: bool = Field(
        description="True when the races used count fewer than three by their weights (the effective number of "
        "races, so a fading race counts as less than one) or the longest is under twice the shortest: the "
        "population 1.06 is served."
    )
    below_one: bool = Field(
        description="A fitted b under 1: a race used was probably not all-out, or a trail race. Outranks the label."
    )
    efforts: list[RiegelEffort] = Field(description="The races used, in start-time order.")
    excluded: list[RiegelExclusion] = Field(
        description="Every tagged running race at or before `as_of` not used, in start-time order."
    )
    parameters: RiegelExponentParameters


Determinant = Annotated[ThresholdDeterminant | RiegelDeterminant, Field(discriminator="name")]
"""One determinant of the state model, told apart by ``name``."""


# ---------------------------------------------------------------------------
# The goal race and its projected finish (F032)
# ---------------------------------------------------------------------------

GOAL_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
GOAL_NOT_MODELLED = "course profile and conditions are not modelled yet"
"""The 422 message for a `course_elevation` or `environment` that is not null (F032 reference section 2)."""


def _goal_not_modelled(value: object) -> None:
    """``course_elevation`` and ``environment`` are accepted only as null, so a client that sends a
    course profile or conditions learns they are not read rather than having them silently dropped."""
    if value is not None:
        raise ValueError(GOAL_NOT_MODELLED)


class GoalRaceInput(BaseModel):
    """The goal race as `PUT /goal` takes it (F032 reference section 2). Strict: `date` is a
    `YYYY-MM-DD` string that names a real day (a timestamp or a datetime string is a 422);
    `distance_m` a finite JSON number above 0 (a bool, a string or an integer too large for a float
    is a 422); `name` a string of at most 200 characters or null; `course_elevation` and
    `environment` only null; any other key a 422. The route refuses a date before its reference
    date."""

    model_config = ConfigDict(extra="forbid")

    date: str = Field(
        strict=True,
        pattern=GOAL_DATE_PATTERN,
        json_schema_extra={"format": "date"},
        description="The race day as `YYYY-MM-DD`, not before the reference date (the athlete's local today for "
        "`PUT /goal`; the local date of the instant used for a preview).",
    )
    distance_m: float = Field(
        strict=True, allow_inf_nan=False, gt=0, description="The race distance in metres, a finite number above 0."
    )
    name: str | None = Field(None, strict=True, max_length=200, description="The race's name, at most 200 characters.")
    course_elevation: None = Field(None, description=f"Accepted only as null: {GOAL_NOT_MODELLED}.")
    environment: None = Field(None, description=f"Accepted only as null: {GOAL_NOT_MODELLED}.")

    @field_validator("date")
    @classmethod
    def _a_calendar_date(cls, value: str | None) -> str | None:
        """The pattern admits ``2026-13-01``; ``date.fromisoformat`` refuses it."""
        if value is not None:
            datetime.date.fromisoformat(value)
        return value

    @field_validator("course_elevation", "environment", mode="before")
    @classmethod
    def _only_null(cls, value: object) -> None:
        return _goal_not_modelled(value)


class GoalPreviewRaceInput(GoalRaceInput):
    """`POST /goal/preview`'s race: as `GoalRaceInput`, with `date` optional. Absent, the route
    dates the race on the local date of the instant used, so a client's own clock never matters."""

    date: str | None = Field(  # type: ignore[assignment]
        None,
        strict=True,
        pattern=GOAL_DATE_PATTERN,
        json_schema_extra={"format": "date"},
        description="The race day as `YYYY-MM-DD`, not before the local date of the instant used; null or absent "
        "means that date.",
    )


class GoalContractInput(BaseModel):
    """The body of `PUT /goal`: the race and an optional goal time, a finite number of seconds above 0."""

    model_config = ConfigDict(extra="forbid")

    race: GoalRaceInput
    target_time_s: float | None = Field(
        None, strict=True, allow_inf_nan=False, gt=0, description="The athlete's goal finish time in seconds, or null."
    )


class GoalPreviewInput(BaseModel):
    """The body of `POST /goal/preview`: `GoalContractInput` with the race's `date` optional."""

    model_config = ConfigDict(extra="forbid")

    race: GoalPreviewRaceInput
    target_time_s: float | None = Field(
        None, strict=True, allow_inf_nan=False, gt=0, description="The athlete's goal finish time in seconds, or null."
    )


class GoalRace(BaseModel):
    """The goal race as served: the race date (the request's, else the reference date), the distance and the name."""

    date: str = Field(description="The race day, `YYYY-MM-DD`.")
    distance_m: float
    name: str | None


class PaceTargetInterval(BaseModel):
    low: float
    high: float


class PaceTargetValue(BaseModel):
    """The contract's `ConfidenceValue` for the projected time (unit `s`) and the pace (unit `s/km`):
    the value, its 80 % prediction interval (null on route `closest_race`), the label
    (`confidence.label`, the same on both) and `provisional` (true on route `closest_race`)."""

    value: float
    unit: Literal["s", "s/km"]
    interval: PaceTargetInterval | None
    confidence: Literal["low", "medium", "high"]
    provisional: bool


class GoalConfidence(BaseModel):
    """F032 reference section 4.2: the label; `basis` (`range` when the halfwidth is the uncertainty,
    `one_race` when a leave-one-out shift is; null on route `closest_race`); `uncertainty` (`u`, null
    there); `reasons`, the codes of the rules that held, in the reference's order."""

    label: Literal["low", "medium", "high"]
    basis: Literal["range", "one_race"] | None
    uncertainty: float | None
    reasons: list[
        Literal[
            "fitness_below_races",
            "population_default",
            "one_race_sets_the_line",
            "below_one",
            "outside_calibrated_range",
            "beyond_longest_race",
        ]
    ] = Field(
        description="`one_race_sets_the_line`: on the fitted line, a race without which the rest still count three "
        "effective races but span less than twice the shortest distance, so it alone sets the line; the "
        "leave-one-out row marked `sets_the_line` names it."
    )


class GoalTimeGap(BaseModel):
    """The goal time against the projection: `gap_s = target_time_s - projected_time_s` (negative when
    the goal is faster), `gap_fraction = gap_s / projected_time_s`, and whether the goal sits inside
    the interval (null on route `closest_race`, which has none)."""

    gap_s: float
    gap_fraction: float
    within_range: bool | None


class GoalRaceUsed(BaseModel):
    """A race the projection was built from, as F029 served it, with the session's stored name."""

    session_id: str
    name: str | None = Field(description="The session row's name; null when the file carried none or the session is unknown.")
    start_time: datetime.datetime
    gap_equivalent_m: float = Field(description="`D_eq`, the distance the fit reads, in metres.")
    duration_s: float = Field(description="`T`, the timer time in seconds.")
    weight: float
    residual: float | None = Field(description="F029's `exp(r) - 1`, positive slower than the line.")
    fitness_before: float | None = Field(description="The load chart's CTL on the local day before the race.")


class GoalNewestRace(BaseModel):
    """The latest race used, with its session name."""

    session_id: str
    name: str | None
    start_time: datetime.datetime


class GoalProvenance(BaseModel):
    """What the projection was built from: F029's b with its error, label and `population_default`,
    the effective race count, the races used in F029's order, the newest race, and F032 reference
    section 5's fitness figures, served beside the time and never applied to it."""

    b: float | None = Field(
        description="F029's b at the instant; the population 1.06 below the bar; null when the exponent's estimate is "
        "not finite (`riegel_unavailable`), and served on `no_races` and `projection_not_finite`."
    )
    error: float | None
    label: Literal["low", "medium", "high"] | None = Field(description="F029's own label for b.")
    population_default: bool
    n_eff: float | None = Field(description="`(sum w)^2 / sum(w^2)` over the races used; null with no race.")
    races: list[GoalRaceUsed]
    newest_race: GoalNewestRace | None
    fitness_at_races: float | None = Field(
        description="The F029-weighted mean of the races' `fitness_before`, nulls skipped; null when none carries one."
    )
    fitness_now: float | None = Field(
        description="The load chart's CTL on the local day before the local date of `as_of`, the instant the projection is "
        "for (now when no `as_of` is given; never the race date); null when that day is not in the chart."
    )


class GoalLeaveOneOut(BaseModel):
    """The projection from every race but this one (F032 reference section 4.1): its route, time and
    `shift = T_without / T - 1`, and whether this race alone sets the line."""

    session_id: str
    route: Literal["fitted_line", "closest_race"]
    projected_time_s: float
    shift: float
    sets_the_line: bool = Field(
        description="True when the other races still count three effective races but span less than twice the "
        "shortest distance without this one, so it alone sets the line (reason `one_race_sets_the_line`)."
    )


class GoalLeaveOneOutSet(BaseModel):
    """Every race's leave-one-out, in F029's race order, and `largest`, the race with the largest
    countable shift (one whose route is `fitted_line`); null when none is countable."""

    races: list[GoalLeaveOneOut]
    largest: GoalLeaveOneOut | None


class PaceTargetParameters(BaseModel):
    """The constants the projection and its label were computed with (F032 reference section 1)."""

    high_max_uncertainty: float
    medium_max_uncertainty: float
    fitness_gap: float
    max_extrapolation: float
    calibrated_min_m: float
    calibrated_max_m: float
    population_b: float
    tie_slack: float


class PaceTarget(BaseModel):
    """`POST /goal/preview` 200 and `GoalContract.target`: the projected finish over the goal
    distance from the races F029 used at the instant, by the fitted line when they clear F029's bar
    (route `fitted_line`) and by the closest race below it (route `closest_race`, no interval,
    `provisional`), with the label, the goal-time gap, the provenance and the assumptions (F032
    reference section 6). When `unavailable` is set every figure is null and the assumptions are
    still served. Nothing is stored."""

    as_of: datetime.datetime = Field(description="The instant the projection is for.")
    timezone: str = Field(description="The configured `athlete_timezone`, which the reference date was read in.")
    race: GoalRace
    target_time_s: float | None
    unavailable: Literal["no_races", "riegel_unavailable", "projection_not_finite"] | None = Field(
        description="`no_races` with no race used; `riegel_unavailable` when F029's estimate is `estimate_not_finite`; "
        "`projection_not_finite` when a served figure is not finite or overflows, or a served time or pace "
        "underflows to 0 (it must be a finite positive number)."
    )
    route: Literal["fitted_line", "closest_race"] | None
    projected_time_s: PaceTargetValue | None
    avg_pace_s_per_km: PaceTargetValue | None
    halfwidth: float | None = Field(description="`exp(t sp) - 1`, the interval's upper side as a fraction of the time.")
    confidence: GoalConfidence | None
    goal_time_gap: GoalTimeGap | None = Field(description="Null without a goal time or when unavailable.")
    provenance: GoalProvenance
    leave_one_out: GoalLeaveOneOutSet | None = Field(description="Null on route `closest_race` and when unavailable.")
    effort_note: None = Field(description="Always null: no effort note is composed.")
    assumptions: list[str] = Field(description="The defaults the projection assumes, in the reference's order.")
    parameters: PaceTargetParameters
    gap_to_goal: None = Field(
        description="Always null: the determinant breakdown is not modelled; the goal time's gap is `goal_time_gap`."
    )


class GoalContract(BaseModel):
    """`PUT /goal` 200 and `GET /goal` 200: the stored goal race and its projection, recomputed on
    every read (F032 reference sections 2 and 6). `status` is derived on read and never stored:
    `completed` when the race date is before the local date of the instant the projection is for,
    else `active`. A superseded row is never served."""

    id: str = Field(description="The stored row's id, as a string.")
    race: GoalRace
    target_time_s: float | None = Field(description="The athlete's goal finish time in seconds, or null.")
    status: Literal["active", "completed"]
    created_at: datetime.datetime = Field(description="The UTC instant the goal was set.")
    target: PaceTarget = Field(description="The projection at the instant used (`as_of`, else now); not stored.")


class StateModel(BaseModel):
    """``GET /state``: the determinant profile, computed on read from the tagged races and threshold
    workouts; nothing is stored."""

    as_of: datetime.datetime = Field(description="The instant the estimate is for.")
    timezone: str = Field(description="The configured `athlete_timezone`, which an `as_of` date was read in.")
    determinants: list[Determinant]
    readiness: None = Field(description="Not computed yet: always null.")
    gap_to_goal: None = Field(
        description="Always null here: the goal race and its projected finish are served by `GET /goal` and "
        "`POST /goal/preview`, whose `goal_time_gap` holds the gap to the goal time."
    )
