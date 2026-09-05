"""T048: property-based tests for RR burst detection (spec §2.4.3).

Sprint-002's retro action item (IDEA-005), aimed at the function that
earned it. ``rr_reconstruction._burst_run_indices`` needed **four**
separate rounds of fixes -- T024 -> T030 -> T037 -> ``12bb6ba`` ->
``090af1b`` -- because every round's example-based test locked in one
hand-picked reproduction case, and the next round found one more
adjacent-burst configuration that case didn't cover. Each fix read as
"provably general" until the next critic pass planted one more burst.

The counter to that is a test that plants the truth instead of
asserting a remembered answer: generate a series whose artefact
indices are known *before* it is built, then assert the flagged set
equals that set exactly -- for any burst count, any per-burst length,
any per-burst character. The historical regressions are pinned as
``@example`` cases alongside the generated strategy so they run on
every invocation even when the random search doesn't happen to
reproduce them (there is no CI in this repo -- a regression that only
reappeared under a lucky seed would go unnoticed).

``derandomize=True`` throughout for the same reason: all gating here is
a manual ``uv run pytest``, and a property test that fails only on some
seeds on some machines is worse than no test at all.

**Why these generator bounds.** The strategies are deliberately
constructed so the *only* rule that can flag a planted beat is the
burst-run analysis:

- Every generated RR value stays inside spec §2.4.3's 300-2000ms
  absolute band (asserted in each test body), so ``_out_of_band``
  contributes nothing and cannot mask a broken run analysis.
- Burst ratios deviate well past the §2.4.3 20% relative criterion, so
  a planted burst is genuinely detectable; and each baseline run is
  longer than the whole planted burst block, so ``_find_baseline_index``
  has a real established baseline to resolve in each direction.
- Series length and run count are capped: ``_burst_run_indices`` is
  O(n^2) (``_find_baseline_index`` scans the remainder of the series
  per run, per direction), so an unbounded strategy could let a single
  example dominate suite runtime.

These tests drive the pure functions directly, never ``TestClient`` or
the DB: the ``isolated_data_dir`` autouse fixture builds one database
per *pytest test*, but hypothesis calls a test body many times inside
that one test, so every example would share one database and start
raising duplicate-session errors unrelated to the property.
"""

from __future__ import annotations

from typing import NamedTuple

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from runcoach_api.ingestion import rr_reconstruction

# Spec §2.4.3's absolute plausible band. Asserted on every generated
# series so the band check never contributes a flag.
_MIN_BAND_MS = 300
_MAX_BAND_MS = 2000

# Resting-to-running human baseline, bounded so every ratio below keeps
# the whole series inside the band above (650 * 0.5 = 325ms;
# 950 * 2.0 = 1900ms).
_BASELINE_MS = st.integers(min_value=650, max_value=950)

# Burst characters. 2.0 is a missed beat, 0.5 an extra beat -- the two
# real chest-strap failure modes. 1.6 and 0.62 are non-integer-ratio
# bursts: they deviate well past the 20% criterion but do not look like
# a clean beat multiple, so the interior branch cannot lean on
# ``_is_near_integer_ratio`` to reach the right answer for them.
_BURST_RATIOS = (2.0, 1.6, 0.62, 0.5)

# Only 2.0/0.5 for the boundary property: the boundary branch requires
# a near-integer beat multiple by design (with one neighbour there is
# no "does the level return" question to ask), and 3x / one-third
# bursts would leave the 300-2000ms band at these baselines.
_BOUNDARY_RATIOS = (2.0, 0.5)

# Sustained physiological steps: past the 20% criterion (so a
# transition really is detected and the series really is two runs), but
# nowhere near 2x/3x/4x or their reciprocals, so no beat multiple can
# be read into them. An interval start or a recovery step-down -- never
# an artefact.
_SUSTAINED_RATIOS = (0.70, 0.75, 1.35, 1.40)

_MAX_BURSTS = 5
_MAX_BURST_LEN = 8

_SETTINGS = settings(
    derandomize=True,
    deadline=None,
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


class SeriesSpec(NamedTuple):
    """A ``baseline + bursts + baseline`` series, described by its truth.

    ``bursts`` is ``((length, ratio_to_baseline), ...)`` in series
    order -- so a spec names exactly which beats are artefacts before
    the series is ever built.
    """

    baseline_ms: int
    baseline_len: int
    bursts: tuple[tuple[int, float], ...]


class BoundarySpec(NamedTuple):
    """A burst hard against one end of the series, with no
    return-to-baseline available on that side.
    """

    baseline_ms: int
    burst_len: int
    burst_ratio: float
    baseline_len: int


@st.composite
def _planted_series(draw) -> SeriesSpec:
    """Bursts of independently drawn length and character, planted
    back-to-back between two genuine-baseline runs.

    ``baseline_len`` is drawn *after* the bursts, from a range starting
    above their combined length, rather than filtered down to it with
    ``assume`` -- so no example is ever rejected and the strategy stays
    dense. That relation is what makes each baseline run the longest
    run in its direction, and therefore what ``_find_baseline_index``
    must resolve to from either side; the ``max(8, ...)`` floor also
    keeps the leading/trailing runs long enough that the centred
    11-beat median *scale* at a baseline/burst boundary is taken from
    baseline rather than from the burst.
    """
    bursts = draw(
        st.lists(
            st.tuples(
                st.integers(min_value=1, max_value=_MAX_BURST_LEN),
                st.sampled_from(_BURST_RATIOS),
            ),
            min_size=1,
            max_size=_MAX_BURSTS,
        )
    )
    floor = max(8, sum(length for length, _ratio in bursts) + 1)
    return SeriesSpec(
        baseline_ms=draw(_BASELINE_MS),
        baseline_len=draw(st.integers(min_value=floor, max_value=floor + 16)),
        bursts=tuple(bursts),
    )


def _build_planted(spec: SeriesSpec) -> tuple[list[float], set[int]]:
    """Materialise a ``SeriesSpec`` into ``(values_ms, planted_indices)``."""
    values: list[float] = [float(spec.baseline_ms)] * spec.baseline_len
    planted: set[int] = set()
    for length, ratio in spec.bursts:
        level = float(round(spec.baseline_ms * ratio))
        start = len(values)
        values.extend([level] * length)
        planted.update(range(start, len(values)))
    values.extend([float(spec.baseline_ms)] * spec.baseline_len)
    return values, planted


class _HrvMsg:
    """Minimal stand-in for a ``fitdecode.FitDataMessage`` carrying one
    ``hrv`` (#78) message -- the same shape ``test_rr_reconstruction.py``
    uses, so these properties exercise ``reconstruct`` end to end and
    not only the private classifier.
    """

    name = "hrv"

    def __init__(self, values_ms: list[float]) -> None:
        self._seconds = tuple(value / 1000.0 for value in values_ms)
        self.fields: list = []

    def get_value(self, name, fallback=None):
        return self._seconds if name == "time" else fallback


def _reconstruct(values_ms: list[float]):
    return rr_reconstruction.reconstruct([_HrvMsg(values_ms)])


def _flagged_via_reconstruct(values_ms: list[float]) -> set[int]:
    intervals = _reconstruct(values_ms)
    assert [iv.seq for iv in intervals] == list(range(len(values_ms)))
    return {iv.seq for iv in intervals if iv.is_artefact}


def _assert_inside_absolute_band(values: list[float]) -> None:
    """Generator self-check: were this ever to trip, the properties
    below would be passing partly on ``_out_of_band`` rather than on
    burst detection, and would no longer test what they claim.
    """
    assert all(_MIN_BAND_MS <= value <= _MAX_BAND_MS for value in values)


# ---------------------------------------------------------------------------
# Property 1 -- exact flagging of interior bursts
# (the T030 -> T037 -> 12bb6ba -> 090af1b chain)
# ---------------------------------------------------------------------------


@_SETTINGS
@given(spec=_planted_series())
# T030: one doubled-beat burst mid-series. The centred-local-median
# rule let the burst be its own reference, marking 5 of 6 artefacts
# valid and destroying a healthy beat instead (reported valid_fraction
# 0.923 against a true 0.769 -- optimistic, so §3's <80% reject gate
# would have passed a capture it should drop).
@example(spec=SeriesSpec(800, 10, ((6, 2.0),)))
# T037: a doubled-beat burst immediately followed by a halved-beat
# burst. Each burst's immediate neighbour is the *other* burst, so the
# pairwise "does the level return" check read both transitions as a
# sustained change and flagged neither (reported 1.0 vs a true ~0.714).
@example(spec=SeriesSpec(800, 10, ((4, 2.0), (4, 0.5))))
# T037 mirror: halved-then-doubled.
@example(spec=SeriesSpec(800, 10, ((4, 0.5), (4, 2.0))))
# 12bb6ba (sprint-002 re-review, Stage 0 code review): adjacent bursts
# of *unequal* length. Stopping at the first run merely longer than the
# run being classified stops at the 6-beat burst when classifying the
# 4-beat one, so the shorter burst never resolved a real baseline.
@example(spec=SeriesSpec(800, 10, ((6, 2.0), (4, 0.5))))
# 090af1b (sprint-002 re-review, second adversarial-critic pass): three
# back-to-back bursts of varying length. A middle burst that out-sizes
# both flanking bursts is a local length maximum without being anywhere
# near baseline, so the local-dominance walk stopped there and left the
# two outer bursts unflagged (only the middle 5-beat burst was flagged;
# 6 of the 11 artefact beats were missed).
@example(spec=SeriesSpec(800, 20, ((3, 2.0), (5, 0.5), (3, 2.0))))
def test_planted_interior_bursts_are_flagged_exactly(spec: SeriesSpec) -> None:
    """Set equality, both directions at once: every planted burst beat
    is flagged (the under-flagging bug of T037/12bb6ba/090af1b) and no
    baseline beat is (the over-flagging bug T030's first cut had, which
    destroyed the healthy beats on a burst's edges).
    """
    values, planted = _build_planted(spec)
    _assert_inside_absolute_band(values)

    assert rr_reconstruction._burst_run_indices(values) == planted
    assert _flagged_via_reconstruct(values) == planted

    survivors = len(values) - len(planted)
    assert rr_reconstruction.valid_fraction(_reconstruct(values)) == survivors / len(
        values
    )


# ---------------------------------------------------------------------------
# Property 2 -- boundary-adjacent bursts (no return-to-baseline side)
# ---------------------------------------------------------------------------


@st.composite
def _boundary_series(draw) -> BoundarySpec:
    burst_len = draw(st.integers(min_value=1, max_value=_MAX_BURST_LEN))
    return BoundarySpec(
        baseline_ms=draw(_BASELINE_MS),
        burst_len=burst_len,
        burst_ratio=draw(st.sampled_from(_BOUNDARY_RATIOS)),
        baseline_len=draw(st.integers(min_value=burst_len + 1, max_value=30)),
    )


@_SETTINGS
@given(spec=_boundary_series(), trailing=st.booleans())
# T030's original reproduction: 6 doubled beats at the very START of
# the series, where the centred median window clamps and the burst
# became its own reference.
@example(spec=BoundarySpec(800, 6, 2.0, 20), trailing=False)
@example(spec=BoundarySpec(800, 6, 2.0, 20), trailing=True)
def test_burst_hard_against_a_series_end_is_flagged_exactly(
    spec: BoundarySpec, trailing: bool
) -> None:
    """A burst at either end has only one neighbour, so "does the level
    return" cannot be asked and the near-integer beat-multiple test
    stands in for it. The property is the same at either end: exactly
    the burst, and none of the baseline.
    """
    burst = [float(round(spec.baseline_ms * spec.burst_ratio))] * spec.burst_len
    baseline = [float(spec.baseline_ms)] * spec.baseline_len
    if trailing:
        values = baseline + burst
        planted = set(range(spec.baseline_len, len(values)))
    else:
        values = burst + baseline
        planted = set(range(spec.burst_len))
    _assert_inside_absolute_band(values)

    assert rr_reconstruction._burst_run_indices(values) == planted
    assert _flagged_via_reconstruct(values) == planted


# ---------------------------------------------------------------------------
# Property 3 -- sustained physiological change is never an artefact
# ---------------------------------------------------------------------------


@_SETTINGS
@given(
    baseline_ms=_BASELINE_MS,
    first_len=st.integers(min_value=8, max_value=30),
    second_len=st.integers(min_value=8, max_value=30),
    ratio=st.sampled_from(_SUSTAINED_RATIOS),
)
def test_sustained_level_change_is_never_flagged_whichever_side_is_longer(
    baseline_ms: int, first_len: int, second_len: int, ratio: float
) -> None:
    """A step that never comes back is an interval start or a recovery
    step-down, not a strap fault -- regardless of which of the two
    levels occupies more of the series. This is the over-flagging guard
    on the whole chain of fixes: every round widened what counts as
    "baseline", and a rule that widened too far would start calling
    real physiology an artefact.
    """
    values = [float(baseline_ms)] * first_len + [
        float(round(baseline_ms * ratio))
    ] * second_len
    _assert_inside_absolute_band(values)

    assert rr_reconstruction._burst_run_indices(values) == set()
    assert _flagged_via_reconstruct(values) == set()


@st.composite
def _gradual_ramp(draw) -> tuple[int, int, int]:
    """``(start_ms, step_ms, length)`` for a linear drift that stays
    inside the 300-2000ms band end to end.

    The per-beat step is drawn *after* the start and length, bounded by
    how far the ramp can travel before it would leave the band, rather
    than drawn independently and filtered -- a fixed +/-12ms step and a
    60-beat length can walk 700ms down to 292ms, which would have the
    absolute-band rule doing the property's work for it.
    """
    start_ms = draw(st.integers(min_value=700, max_value=900))
    length = draw(st.integers(min_value=20, max_value=60))
    span = length - 1
    # 12ms/beat is ~1.5% of these baselines -- an order of magnitude
    # under the 20% relative criterion, so no step is ever a transition
    # however many beats the ramp runs for.
    down = min(12, (start_ms - _MIN_BAND_MS) // span)
    up = min(12, (_MAX_BAND_MS - start_ms) // span)
    step_ms = draw(st.integers(min_value=-down, max_value=up))
    return start_ms, step_ms, length


@_SETTINGS
@given(ramp=_gradual_ramp())
def test_gradual_ramp_is_never_flagged_at_any_drift_rate(
    ramp: tuple[int, int, int],
) -> None:
    """The real ``dev_fields_run.fit`` fixture ramps 374-897ms across an
    activity. No single step here approaches the 20% relative
    criterion, so nothing in the series may be flagged -- the
    false-positive failure mode of the level-vs-local-median rule T030
    replaced.
    """
    start_ms, step_ms, length = ramp
    values = [float(start_ms + step_ms * i) for i in range(length)]
    _assert_inside_absolute_band(values)

    assert rr_reconstruction._burst_run_indices(values) == set()
    assert _flagged_via_reconstruct(values) == set()


# ---------------------------------------------------------------------------
# Property 4 -- conservation: reconstruction flags, it never fabricates
# or drops beats
# ---------------------------------------------------------------------------


@_SETTINGS
@given(
    values=st.lists(
        st.integers(min_value=1, max_value=6000).map(float),
        min_size=1,
        max_size=120,
    )
)
def test_reconstruction_conserves_every_candidate_beat(values: list[float]) -> None:
    """For *any* candidate series -- artefact-ridden, out of band,
    physiologically absurd -- ``reconstruct`` returns one row per
    candidate, in order, with the value carried through. Artefact
    status is a flag, never a filter (§2.4.3 down-weights, it does not
    delete), and ``rr_valid_fraction`` only means anything if its
    denominator is every beat the file actually carried.
    """
    intervals = _reconstruct(values)

    assert len(intervals) == len(values)
    assert [iv.seq for iv in intervals] == list(range(len(values)))
    # Value survives the hrv carrier's ms -> s -> ms round trip.
    for interval, expected in zip(intervals, values):
        assert interval.rr_ms == pytest.approx(expected)
    assert all(
        iv.rr_source == rr_reconstruction.RR_SOURCE_CHEST_STRAP for iv in intervals
    )
    assert all(iv.rr_carrier == "hrv" for iv in intervals)

    fraction = rr_reconstruction.valid_fraction(intervals)
    assert 0.0 <= fraction <= 1.0
    assert fraction == sum(1 for iv in intervals if not iv.is_artefact) / len(intervals)


@_SETTINGS
@given(
    values=st.lists(
        st.integers(min_value=1, max_value=6000).map(float),
        min_size=1,
        max_size=120,
    )
)
def test_every_out_of_band_beat_is_always_flagged(values: list[float]) -> None:
    """§2.4.3's absolute 300-2000ms band runs independently of the run
    analysis, so it holds unconditionally: no run-attribution outcome
    can rescue a beat outside the band.
    """
    intervals = _reconstruct(values)

    for interval, raw in zip(intervals, values):
        if raw < _MIN_BAND_MS or raw > _MAX_BAND_MS:
            assert interval.is_artefact is True
