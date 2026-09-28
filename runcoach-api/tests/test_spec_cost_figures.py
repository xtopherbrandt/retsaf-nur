"""The establishment-delay duration, derived from the code and held against
the two corpus sites [[T126]] corrected everywhere except (T133, review cycle
9, gap G-C9-2).

[[T126]] (`3f1430c`) corrected six sites from "roughly twelve days" to the
true duration of the establishment gate's quiet after a reset -- twenty days,
not twelve; twelve is only the subset whose verdict *changed direction* at
T116. It missed two sites, and one of them -- `research/00:220` -- is the
document this project's own precedence order names as the authority
(`project-domain-and-spec-fidelity`): `spec/06` said the corrected number,
`research/00` still said the withdrawn one, and a reader resolving the
conflict by precedence got the wrong answer.

Following T128's oracle pattern (`test_hrv_unavailable_causes.py`): the
figures are **derived from the code**, not retyped from the docstring that
already states them, so a constant moving carries this pin with it instead of
leaving it to go stale a third time. Two numbers come out of `judge`'s own
thresholds:

* the **duration** -- how many days after a reset `established` stays
  `False` -- is `MIN_BASELINE_READINGS` distinct post-reset days away, and a
  post-reset day only starts counting once it clears the gap between the
  baseline window's end and the reset day (`baseline_window`'s own
  arithmetic, read here rather than restated as the literal `6`);
* the **changed-verdict subset** -- the days that had a band before T116
  ever ran (and so could read `hrv_normal`) but were not yet established --
  is the same gap applied to `build_band`'s own minimum readings instead of
  `MIN_BASELINE_READINGS`, and the difference of the two.

Both are checked end-to-end against `judge` itself
(``test_the_cost_figure_constants_derive_from_judges_own_thresholds``)
before either corpus assertion runs, so a change to either threshold turns
this module red before a spec block is ever opened.

**What this cannot see.** It is silent on every other claim these two
paragraphs make (the establishment gate's symmetry, the up-regulation
argument, `research/00` §1.7); those are review's, not this oracle's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from runcoach_api.metrics import hrv_trend

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Removed, not spaced: markdown emphasis and code spans sit inside and
#: between words, and the two sites mark the same phrase up differently.
#: Restated from ``test_hrv_unavailable_causes.py`` rather than imported --
#: importing a test module to reach one helper is worse than the four lines
#: it costs to restate it.
_DROPPED = str.maketrans("", "", "\"'`*_")
_TYPOGRAPHY = {"≥": ">=", "≤": "<=", "—": "--", "–": "--", "−": "-"}


def _flat(text: str) -> str:
    folded = text.translate(_DROPPED)
    for symbol, ascii_form in _TYPOGRAPHY.items():
        folded = folded.replace(symbol, ascii_form)
    return " ".join(folded.split()).lower()


# ---------------------------------------------------------------------------
# The derivation: read out of the code, not retyped from its docstring
# ---------------------------------------------------------------------------


def _band_min_readings() -> int:
    """The fewest ln-values ``build_band`` needs before it stops returning
    ``None``, found by asking the function rather than repeating the
    docstring's "fewer than two". ``statistics.stdev`` (which ``build_band``
    calls) raises below two data points; this walks 0, 1, 2, ... until the
    exception stops firing, so a guard moved to a different minimum carries
    this number with it."""
    for n in range(0, 6):
        if hrv_trend.build_band([0.5] * n) is not None:
            return n
    raise AssertionError(
        "build_band returned None for every count from 0 to 5 readings: this oracle is reading nothing"
    )


def _post_reset_day_offset() -> int:
    """How many days after a post-reset reading is captured before it enters
    the baseline window, read out of ``baseline_window`` rather than typed
    as a literal.

    ``baseline_window(D) == (D-66, D-7)``: a reading dated ``X`` first lies
    inside the window on the smallest ``D`` with ``D - WINDOW_DAYS == X``,
    i.e. ``D == X + WINDOW_DAYS``. A reading on the reset day ``R`` therefore
    first counts on ``R + WINDOW_DAYS``, and the ``n``-th distinct post-reset
    day first counts on ``R + WINDOW_DAYS + (n - 1)`` -- so the additive
    offset used below is ``WINDOW_DAYS - 1``, computed here from the
    function's own arithmetic rather than typed as ``6``.
    """
    probe_date = date(2026, 1, 1)
    _, end = hrv_trend.baseline_window(probe_date)
    window_days = (probe_date - end).days
    return window_days - 1


#: The published constant this whole figure hangs off, read from the module
#: the corpus is describing rather than retyped as a literal.
MIN_BASELINE_READINGS = hrv_trend.MIN_BASELINE_READINGS

#: How many days after a reset the baseline stays unestablished --
#: ``judge`` reports ``established=False`` for every one of them, whatever
#: the band says. [[T126]]'s "20"; the withdrawn documents said "roughly
#: twelve".
ESTABLISHMENT_DELAY_DAYS = MIN_BASELINE_READINGS + _post_reset_day_offset()

#: The first post-reset day with a band at all -- the day a pre-T116 `judge`
#: could first read `hrv_normal` from, because before it there is no band to
#: read one from.
FIRST_BANDED_DAY = _band_min_readings() + _post_reset_day_offset()

#: The subset of the quiet whose verdict T116 actually changed: banded but
#: not yet established. This is the number the withdrawn phrasing names
#: correctly as a *count of changed verdicts* and incorrectly as the
#: *duration of the quiet*.
CHANGED_VERDICT_SUBSET = ESTABLISHMENT_DELAY_DAYS - FIRST_BANDED_DAY

#: The withdrawn duration claim, quoted once so both the presence and
#: absence checks below read the same literal.
WITHDRAWN_DURATION_PHRASE = "traverses roughly twelve days beneath `min_baseline_readings`"


def test_the_cost_figure_constants_derive_from_judges_own_thresholds() -> None:
    """The two numbers above, checked end-to-end against ``judge`` itself on
    synthetic series that hold nothing but a baseline of ``n`` readings and a
    normal three-reading week -- not against the docstring that already
    states them. If ``judge``'s established gate or ``build_band``'s minimum
    ever move, this goes red before either corpus assertion runs."""
    band_min = _band_min_readings()

    def series(baseline_n: int) -> hrv_trend.HrvDataset:
        day = date(2026, 3, 1)

        def reading(i: int, value: float) -> hrv_trend.Reading:
            return hrv_trend.Reading(
                date=day,
                session_id=f"r{i}",
                tier="chest_strap_raw",
                rmssd_ms=value,
                start_time=datetime(2026, 3, 1, 6, tzinfo=timezone.utc),
            )

        baseline = tuple(reading(i, 40.0 + (i % 3)) for i in range(baseline_n))
        window = tuple(reading(1000 + i, 40.0 + i) for i in range(hrv_trend.MIN_WINDOW_READINGS))
        # One dataset, hand-built (T151): ``judge`` reads a dataset's
        # ``tier``, ``baseline``, ``window`` and ``withheld``.
        return hrv_trend.HrvDataset(
            tier="chest_strap_raw",
            baseline_window=(day, day),
            series=baseline + window,
            baseline=baseline,
            window=window,
            band=None,
            n=len(baseline),
            established=len(baseline) >= MIN_BASELINE_READINGS,
            withheld=False,
        )

    probed_ns = sorted(
        {
            0,
            max(band_min - 1, 0),
            band_min,
            MIN_BASELINE_READINGS - 1,
            MIN_BASELINE_READINGS,
            MIN_BASELINE_READINGS + 3,
        }
    )
    for n in probed_ns:
        verdict = hrv_trend.judge(series(n))
        assert (verdict.band is not None) == (n >= band_min), (
            f"n={n}: judge's band presence disagrees with the derived band minimum {band_min}"
        )
        assert verdict.established == (n >= MIN_BASELINE_READINGS), (
            f"n={n}: judge's established flag disagrees with MIN_BASELINE_READINGS={MIN_BASELINE_READINGS}"
        )


# ---------------------------------------------------------------------------
# The two sites [[T126]] missed
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    """One block naming the establishment-delay cost. ``lead`` identifies
    the paragraph by text that survives the correction, rather than by a
    line number that moves."""

    label: str
    rel: str
    lead: str


SITES = (
    Site(
        label="research/00 5.4 FIG-01 the authority",
        rel="specification/research/00-design-decisions.md",
        lead="**FIG-01.** After a coverage-gap re-establishment",
    ),
    Site(
        label="spec/03 3.7.3",
        rel="specification/spec/03-derived-metric-formulas.md",
        lead="it was reachable after every coverage-gap reset this section performs",
    ),
)


def _block(site: Site) -> str:
    """The site's paragraph, flattened, located by its lead and required to
    be unique -- two lines carrying the lead means the block has moved or
    been duplicated and this would pin whichever came first."""
    path = _REPO_ROOT / site.rel
    lead = _flat(site.lead)
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if lead in _flat(line)]
    assert len(lines) == 1, (
        f"{site.label}: {len(lines)} lines carry the lead {site.lead!r}, not 1 -- the block has "
        f"moved, been reworded or been duplicated, and this oracle is reading the wrong text"
    )
    return _flat(lines[0])


@pytest.mark.parametrize("site", SITES, ids=[site.label for site in SITES])
def test_the_establishment_delay_is_stated_at_each_site(site: Site) -> None:
    """The derived duration -- not the literal ``20`` -- must appear in the
    block, phrased as ``traverses <N> days ... min_baseline_readings``. A
    constant change moves ``ESTABLISHMENT_DELAY_DAYS`` and this assertion
    with it, so the corpus is held to the code's own number rather than to
    today's measurement of it."""
    block = _block(site)
    phrase = _flat(f"traverses {ESTABLISHMENT_DELAY_DAYS} days beneath `min_baseline_readings`")
    assert phrase in block, (
        f"{site.label} ({site.rel}) does not state the establishment-delay duration as "
        f"{ESTABLISHMENT_DELAY_DAYS} days (derived from MIN_BASELINE_READINGS="
        f"{MIN_BASELINE_READINGS} and the baseline-window offset): {block!r}"
    )


@pytest.mark.parametrize("site", SITES, ids=[site.label for site in SITES])
def test_the_withdrawn_twelve_day_phrasing_is_gone_from_each_site(site: Site) -> None:
    """The stronger half, checked over each site's whole file rather than
    its block, so the withdrawn sentence cannot survive by moving paragraph
    within the same document."""
    path = _REPO_ROOT / site.rel
    text = _flat(path.read_text(encoding="utf-8"))
    assert _flat(WITHDRAWN_DURATION_PHRASE) not in text, (
        f"withdrawn by T126 as the duration (12 is the changed-verdict subset, not the "
        f"duration), back in {site.rel}: {WITHDRAWN_DURATION_PHRASE}"
    )


def test_the_withdrawn_twelve_day_phrasing_is_gone_from_every_specification_document() -> None:
    """The site list above is an allowlist, and an allowlist passes as soon
    as the sentence moves file ([[T119]]'s lesson). The committed
    ``specification/`` tree is swept whole, not just the two known sites."""
    specification = _REPO_ROOT / "specification"
    documents = sorted(specification.rglob("*.md"))
    assert len(documents) >= 6, (
        f"{len(documents)} markdown documents under {specification}: the sweep has stopped "
        f"descending and its all-clear is a report over nothing"
    )
    offenders = [
        str(path.relative_to(_REPO_ROOT))
        for path in documents
        if _flat(WITHDRAWN_DURATION_PHRASE) in _flat(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, "withdrawn as the duration by T126, back in: " + ", ".join(offenders)
