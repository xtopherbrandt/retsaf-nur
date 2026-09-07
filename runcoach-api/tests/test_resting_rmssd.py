"""T042: the resting rMSSD, pinned to the strict pairwise adjacency rule.

Own file per T042's task notes, mirroring ``rmssd.py``'s own separation
from ``hrv_classification.py``.

The whole point of this suite is the F004 ``@must`` scenario "rMSSD only
ever differences beats that were adjacent in the series". Two readings of
"exclude flagged pairs" give different numbers:

* **pairwise over the original series** -- walk adjacent pairs of the
  reconstructed list; a pair contributes only when *neither* beat is
  flagged; and
* **compact-then-difference** -- filter the flagged beats out first, then
  difference the compacted list. That forms a difference between two
  beats that were never adjacent, manufacturing a measurement that does
  not exist.

The second one interpolates nothing, so a "do not interpolate" guard does
not catch it, and it yields a plausible number -- which is what makes it
dangerous. ``_compact_then_diff_rmssd`` below implements the *wrong*
statistic deliberately, purely so the tests can assert the two disagree
on the data where they must.

The other axis under test is ``None`` vs ``0.0``. ``None`` means "no value
available"; ``0.0`` means "measured zero". They are not interchangeable --
this is the ``rr_valid_fraction`` convention already documented on
``models.Session``, and E003 takes ``ln(rMSSD)``, so handing it a
manufactured zero would be worse than handing it nothing.
"""

from __future__ import annotations

import math
import random

import pytest

from runcoach_api.ingestion.rmssd import resting_rmssd
from runcoach_api.models import RRInterval


def _beats(
    values: list[float | None],
    flagged: set[int] | None = None,
    *,
    artefact_default: bool | None = False,
) -> list[RRInterval]:
    """Build a reconstructed-shaped beat list.

    ``rr_reconstruction.reconstruct()`` flags, never excises: one
    ``RRInterval`` per beat, in original merge order, with a monotonic
    ``seq``. These stand-ins keep that shape so the tests exercise the
    same list the real pipeline hands over.
    """
    flagged = flagged or set()
    return [
        RRInterval(
            seq=i,
            rr_ms=v,
            is_artefact=True if i in flagged else artefact_default,
        )
        for i, v in enumerate(values)
    ]


def _compact_then_diff_rmssd(rr_intervals: list[RRInterval]) -> float | None:
    """The WRONG statistic, implemented on purpose as a contrast value.

    Never import this anywhere but this test module.
    """
    kept = [
        rr.rr_ms
        for rr in rr_intervals
        if rr.is_artefact is not True and rr.rr_ms is not None
    ]
    squares = [
        (curr - prev) ** 2 for prev, curr in zip(kept, kept[1:], strict=False)
    ]
    if not squares:
        return None
    return math.sqrt(sum(squares) / len(squares))


# --- The worked example from the task spec and the acceptance probe -------


PROBE_MS = [800.0, 1200.0, 400.0, 1200.0, 800.0]
PROBE_FLAGGED = {2}


def test_worked_example_is_pairwise_not_compacted() -> None:
    """Index 2 flagged: both pairs touching it drop out.

    Pairs (0,1) and (3,4) survive -> diffs [+400, -400] -> exactly 400.0.
    Compact-then-diff would keep [800, 1200, 1200, 800] -> diffs
    [+400, 0, -400] -> 326.5986..., inventing a difference between beats
    1 and 3, which were never adjacent.
    """
    got = resting_rmssd(_beats(PROBE_MS, PROBE_FLAGGED))

    assert got is not None
    assert math.isclose(got, 400.0, rel_tol=0, abs_tol=1e-9)


def test_worked_example_diverges_from_the_wrong_statistic() -> None:
    """Pin the contrast so a compact-then-diff regression can't read green."""
    beats = _beats(PROBE_MS, PROBE_FLAGGED)

    wrong = _compact_then_diff_rmssd(beats)

    assert wrong is not None
    assert math.isclose(wrong, math.sqrt(320000.0 / 3), rel_tol=0, abs_tol=1e-9)
    assert math.isclose(wrong, 326.598632, rel_tol=0, abs_tol=1e-6)
    assert not math.isclose(resting_rmssd(beats), wrong, rel_tol=0, abs_tol=1e-6)


def test_no_difference_spans_a_flagged_beat() -> None:
    """A flagged beat severs the series: nothing bridges across it.

    Both neighbours of the flagged beat are far from each other, so a
    bridging difference would be large and obvious. Only the two pairs
    entirely on one side of the flag may contribute.
    """
    ms = [1000.0, 1010.0, 1.0, 500.0, 510.0]
    got = resting_rmssd(_beats(ms, {2}))

    # Surviving pairs: (0,1) -> +10 and (3,4) -> +10. rMSSD == 10.0.
    assert math.isclose(got, 10.0, rel_tol=0, abs_tol=1e-9)


# --- Boundary positions a pairwise walk is most likely to get wrong ------


def test_flagged_beat_at_index_zero() -> None:
    ms = [50.0, 1000.0, 1020.0, 1040.0]
    got = resting_rmssd(_beats(ms, {0}))

    # Only pairs (1,2) and (2,3) survive -> diffs [+20, +20] -> 20.0.
    assert math.isclose(got, 20.0, rel_tol=0, abs_tol=1e-9)


def test_flagged_beat_at_final_index() -> None:
    ms = [1000.0, 1020.0, 1040.0, 50.0]
    got = resting_rmssd(_beats(ms, {3}))

    assert math.isclose(got, 20.0, rel_tol=0, abs_tol=1e-9)


def test_two_separated_flagged_beats_exclude_four_pairs() -> None:
    """More than one pair excluded, from two separate places in the series."""
    ms = [1000.0, 1030.0, 5.0, 900.0, 940.0, 9.0, 800.0, 850.0]
    #      0       1       2*    3      4      5*    6      7
    # Surviving pairs: (0,1)=+30, (3,4)=+40, (6,7)=+50.
    got = resting_rmssd(_beats(ms, {2, 5}))

    expected = math.sqrt((30.0**2 + 40.0**2 + 50.0**2) / 3)
    assert math.isclose(got, expected, rel_tol=0, abs_tol=1e-9)


def test_adjacent_flagged_beats_drop_three_pairs() -> None:
    ms = [1000.0, 1040.0, 3.0, 4.0, 900.0, 960.0]
    #      0       1       2*   3*   4      5
    got = resting_rmssd(_beats(ms, {2, 3}))

    expected = math.sqrt((40.0**2 + 60.0**2) / 2)
    assert math.isclose(got, expected, rel_tol=0, abs_tol=1e-9)


# --- None vs 0.0: absence is not a measurement ---------------------------


def test_clean_two_beat_series_measures_zero() -> None:
    """One contributing pair with no variation is a real measurement."""
    got = resting_rmssd(_beats([1000.0, 1000.0]))

    assert got is not None
    assert isinstance(got, float)
    assert math.isclose(got, 0.0, rel_tol=0, abs_tol=1e-9)


def test_empty_series_has_no_value() -> None:
    assert resting_rmssd([]) is None


def test_single_beat_forms_no_pair() -> None:
    assert resting_rmssd(_beats([1000.0])) is None


def test_all_flagged_series_has_no_value() -> None:
    """Zero contributing pairs is "no measurement", not a measured zero."""
    assert resting_rmssd(_beats([900.0, 950.0], {0, 1})) is None


def test_every_pair_touching_a_flag_has_no_value() -> None:
    """Alternate flagging leaves no pair with two clean beats."""
    ms = [900.0, 950.0, 910.0, 960.0, 905.0]
    assert resting_rmssd(_beats(ms, {1, 3})) is None


# --- Tolerant of the nullable model surface ------------------------------


def test_none_rr_ms_is_non_contributing_and_does_not_raise() -> None:
    """``rr_ms`` is ``float | None``; a null beat must behave like a flag."""
    ms: list[float | None] = [1000.0, 1020.0, None, 800.0, 830.0]
    got = resting_rmssd(_beats(ms))

    expected = math.sqrt((20.0**2 + 30.0**2) / 2)
    assert math.isclose(got, expected, rel_tol=0, abs_tol=1e-9)


def test_all_rr_ms_none_has_no_value() -> None:
    assert resting_rmssd(_beats([None, None, None])) is None


def test_is_artefact_none_is_not_flagged() -> None:
    """``is_artefact`` is ``bool | None``; only ``True`` excludes."""
    beats = _beats([1000.0, 1040.0, 1000.0], artefact_default=None)

    got = resting_rmssd(beats)

    assert math.isclose(got, 40.0, rel_tol=0, abs_tol=1e-9)


def test_is_artefact_false_and_none_mix_identically() -> None:
    explicit_false = _beats([1000.0, 1040.0, 1000.0])
    left_none = _beats([1000.0, 1040.0, 1000.0], artefact_default=None)

    assert resting_rmssd(explicit_false) == resting_rmssd(left_none)


# --- Purity and non-mutation --------------------------------------------


def test_does_not_mutate_or_reorder_the_input() -> None:
    beats = _beats(PROBE_MS, PROBE_FLAGGED)
    before = [(b.seq, b.rr_ms, b.is_artefact) for b in beats]

    resting_rmssd(beats)

    assert [(b.seq, b.rr_ms, b.is_artefact) for b in beats] == before


def test_series_order_is_list_order_not_seq_order() -> None:
    """List order *is* original-series adjacency; ``seq`` is not re-sorted on.

    ``_collect_candidates`` makes a single linear pass in file order, so
    the stored list is already the series. A ``None`` ``seq`` must not
    change the answer (sorting on it would raise or reorder).
    """
    beats = [
        RRInterval(seq=None, rr_ms=v, is_artefact=False)
        for v in [1000.0, 1040.0, 1000.0]
    ]

    assert math.isclose(resting_rmssd(beats), 40.0, rel_tol=0, abs_tol=1e-9)


# --- The reference document's §4 synthetic doubled-burst series ----------


def _synthetic_doubled_burst() -> tuple[list[RRInterval], list[float]]:
    """A resting series with a true rMSSD of 75.39 and one 6-beat burst.

    Built deterministically: a seeded successive-difference series is
    rescaled so the clean series' rMSSD is exactly 75.39, then beats
    40-45 are doubled -- the missed-beat signature the burst detector
    flags -- and marked ``is_artefact``.

    The reference document does not publish its generator, so this is a
    reconstruction, not the original series: the seed below was selected
    so that a series with §4's stated characteristics (79 differences,
    true rMSSD 75.39, one 6-beat doubled burst) reproduces §4's recorded
    pair of outcomes to both decimal places. What is *not* seed-dependent
    is the structural claim the next test makes -- that the pairwise
    answer is exactly the rMSSD of the real differences that survive,
    with nothing manufactured. The reconstruction exists so the recorded
    numbers have something to be checked against.

    Returns the flagged series and the underlying clean values.
    """
    rng = random.Random(40631)
    diffs = [rng.gauss(0.0, 1.0) for _ in range(79)]
    raw = math.sqrt(sum(d * d for d in diffs) / len(diffs))
    diffs = [d * (75.39 / raw) for d in diffs]

    clean = [1000.0]
    for d in diffs:
        clean.append(clean[-1] + d)

    burst = range(40, 46)
    values = [v * 2 if i in burst else v for i, v in enumerate(clean)]
    return _beats(values, set(burst)), clean


def _rmssd_of(values: list[float]) -> float:
    squares = [(b - a) ** 2 for a, b in zip(values, values[1:], strict=False)]
    return math.sqrt(sum(squares) / len(squares))


def test_synthetic_doubled_burst_reproduces_the_reference_divergence() -> None:
    """§4's second row: true 75.39, pairwise 74.98, compact-then-diff 76.43."""
    beats, clean = _synthetic_doubled_burst()

    true_rmssd = _rmssd_of(clean)
    assert math.isclose(true_rmssd, 75.39, rel_tol=0, abs_tol=1e-9)

    pairwise = resting_rmssd(beats)
    compact = _compact_then_diff_rmssd(beats)

    assert pairwise is not None and compact is not None
    # Reconstructed, so matched to within 0.01 ms of §4's recorded pair
    # rather than bit-for-bit: 74.9818 and 76.4386 here.
    assert math.isclose(pairwise, 74.98, rel_tol=0, abs_tol=0.01)
    assert math.isclose(compact, 76.43, rel_tol=0, abs_tol=0.01)

    # The correct statistic sits just below the truth, having sampled
    # 72 of its 79 real differences. The wrong one is pushed *above* the
    # truth by the manufactured difference bridging the excised burst --
    # so the error is not a rounding artefact and does not even share a
    # sign with the loss of data it purports to repair.
    assert pairwise < true_rmssd < compact


def test_pairwise_answer_is_made_only_of_differences_that_really_existed() -> None:
    """The seed-independent claim: no value in the answer is manufactured.

    The pairwise rMSSD over the corrupted series must equal, exactly, the
    rMSSD over just those clean successive differences that do not touch
    the burst. Every square it sums is a real measurement between two
    beats that really were adjacent; none is invented, and none of the
    burst's own ~1000 ms internal differences leaks in.
    """
    beats, clean = _synthetic_doubled_burst()
    burst = set(range(40, 46))

    surviving = [
        (clean[i + 1] - clean[i]) ** 2
        for i in range(len(clean) - 1)
        if i not in burst and i + 1 not in burst
    ]
    # 79 differences in all; the 6-beat burst touches 7 of them.
    assert len(surviving) == 72

    expected = math.sqrt(sum(surviving) / len(surviving))
    assert math.isclose(resting_rmssd(beats), expected, rel_tol=0, abs_tol=1e-9)


def test_compact_then_diff_invents_a_difference_that_never_existed() -> None:
    """Names the manufactured value, so the contrast isn't just a number.

    Compaction joins beat 39 to beat 46 -- beats separated by six others
    in the real series -- and counts the gap between them as though it
    were a beat-to-beat difference.
    """
    beats, clean = _synthetic_doubled_burst()

    bridged = (clean[46] - clean[39]) ** 2
    surviving = [
        (clean[i + 1] - clean[i]) ** 2
        for i in range(len(clean) - 1)
        if i not in range(40, 46) and i + 1 not in range(40, 46)
    ]

    fabricated = math.sqrt((sum(surviving) + bridged) / (len(surviving) + 1))
    assert math.isclose(
        _compact_then_diff_rmssd(beats), fabricated, rel_tol=0, abs_tol=1e-9
    )
    assert not math.isclose(
        resting_rmssd(beats), fabricated, rel_tol=0, abs_tol=1e-6
    )


def test_a_non_finite_beat_is_non_contributing_rather_than_poisoning_the_walk() -> None:
    """One unusable beat costs its two pairs, not the whole capture.

    ``inf`` and ``nan`` are ``float`` instances carrying no measurement, so they
    are treated exactly as a ``None`` beat already was: non-contributing. The
    alternative -- letting them into the sum -- makes the *entire* rMSSD ``inf``
    or ``nan`` from a single bad beat, and ``nan <= 0`` is ``False``, so the
    classifier's non-positive gate would pass it through to
    ``resting_rmssd_ms``.

    Adjacency still holds across the hole: the excluded beat drops pairs
    ``(1,2)`` and ``(2,3)``, and no synthetic ``(1,3)`` pair is manufactured --
    the same rule the flagged-beat cases above pin.
    """
    # Asymmetric on purpose. A symmetric series gives the same number under
    # both the correct rule and the compact-then-difference rule this module
    # has a dedicated test to refuse, so it would pin nothing.
    #
    #   series:   1000, 1020, <bad>, 1000, 1060
    #   correct:  (1000,1020) and (1000,1060) contribute; the two pairs
    #             touching the bad beat are lost   -> mean(400, 3600) = 2000
    #   compacted: the bad beat is excised and a (1020, 1000) pair is
    #             manufactured across the hole      -> mean(400, 400, 3600)
    correct = math.sqrt(2000.0)
    compacted = resting_rmssd(_beats([1000.0, 1020.0, 1000.0, 1060.0]))

    for bad in (float("inf"), float("-inf"), float("nan")):
        poisoned = _beats([1000.0, 1020.0, bad, 1000.0, 1060.0])
        result = resting_rmssd(poisoned)

        assert result is not None
        assert math.isfinite(result), f"{bad!r} reached the result as {result!r}"
        assert result == pytest.approx(correct)
        # Adjacency survives the hole: nothing is manufactured across it.
        assert result != pytest.approx(compacted)


def test_an_overflowing_sum_of_finite_squares_answers_none() -> None:
    """The ``float | None`` contract holds even when every input is finite.

    ``_contributes`` guarantees finite beats, so the only remaining route to a
    non-finite result is arithmetic: each squared difference here is a finite
    ``1e308``, but their **sum** overflows to ``inf`` silently -- float addition
    saturates rather than raising, unlike ``(a - b) ** 2`` itself, which raises
    ``OverflowError`` above ~1.3e154 and so never reaches the mean.

    Not reachable from a file -- ``rr_reconstruction._out_of_band`` flags
    anything outside the plausible RR band long before this -- but
    ``resting_rmssd`` is a public function whose contract says a returned float
    is a usable measurement, and E003 is told it may ``ln()`` the column this
    feeds. Pinned so the guard cannot be dropped silently by a later refactor.
    """
    assert resting_rmssd(_beats([0.0, 1e154, 0.0, 1e154, 0.0])) is None
