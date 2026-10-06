"""T049: property-based tests for the recording-interval classifier
(spec §2.4.1 step 1, descriptor per §2.2.3).

The second target IDEA-005 (sprint-002 retro) names by name:
``quality_gates._classify_recording_interval`` is a threshold classifier
over a parameterised structural space, and until now it was proven only
by hand-picked example lists.

**Why hand-picked lists are not enough here.** Measured against the real
corpus on 2026-09-05, every fixture is overwhelmingly 1s-spaced --
``dev_fields_run`` 1.00000, ``sample_health_snapshot`` 1.00000,
``sample_run`` 0.99929, ``strap_health_snapshot`` 1.00000,
``strap_hrv_sample_run`` 0.98675, ``wrist_ppg_run`` 0.99898 (these
reproduce the values recorded in T049 exactly). All six sit far above
the 0.95 cut-off and classify ``1hz``. The real corpus therefore
exercises exactly *one* of the classifier's three branches, and no
fixture-driven test can ever catch a mistake in the other two or in the
position of either threshold. Generated input is the only thing that
reaches them.

**What is pinned as a literal, and why.** Two design constants are
restated here rather than imported from the module under test:
``_UNIFORM_1HZ_MIN_FRACTION`` (0.95, written below as the exact rational
19/20 so no example ever turns on float rounding at the boundary) and
``_MAX_INTERPOLATION_GAP_S`` (5s). Importing them would make these
tests agree with whatever the module currently says -- a threshold
edited to 0.5 would still pass. They are spec-recorded implementation
defaults (F003 Decision Log), so the tests pin them the same way
``test_rr_reconstruction_properties.py`` pins §2.4.3's 300-2000ms band.

``derandomize=True`` throughout, matching T048: all gating in this repo
is a manual ``uv run pytest``, and a property test that fails only under
some seeds on some machines is worse than no test at all. Every
strategy is constructed so that no example is ever rejected -- there is
no ``assume()`` anywhere below, and no bound exists to make a property
easier to satisfy.

These tests call ``_classify_recording_interval`` directly, never
through ``apply()``/the pipeline/``TestClient``: the ``isolated_data_dir``
autouse fixture builds one database per *pytest test*, but hypothesis
runs a test body many times inside that one test, so a DB-touching
property would have every example share one database.
"""

from __future__ import annotations

from typing import NamedTuple

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from runcoach_api.ingestion.quality_gates import _classify_recording_interval

# §2.2.3's three recording-interval descriptors. Totality means the
# classifier returns one of exactly these, always.
_ONE_HZ = "1hz"
_SMART = "smart"
_IRREGULAR = "irregular"
_CLASSES = frozenset({_ONE_HZ, _SMART, _IRREGULAR})

# _UNIFORM_1HZ_MIN_FRACTION = 0.95, as the exact rational 19/20.
# "uniform >= 0.95 * total" is equivalently "20*uniform >= 19*total",
# and the two agree for every (uniform, total) pair up to total=20000 --
# verified before these bounds were chosen -- so the integer form can be
# used to place examples exactly on the boundary without any example's
# outcome depending on binary rounding of 0.95.
_UNIFORM_NUM = 19
_UNIFORM_DEN = 20

# _MAX_INTERPOLATION_GAP_S: gaps at or under this are linearly
# interpolable, gaps strictly over it are not.
_INTERPOLATION_CEILING_S = 5

# Gaps that are not ~1s and are still inside the interpolation ceiling:
# what a smart-recording device produces when it skips unchanged
# samples. 0.0 is a duplicate timestamp; 0.999/1.001 sit just outside
# the module's 1e-6 tolerance; 5.0 is the ceiling itself, which is
# fillable (the rule is strictly-greater-than).
_FILLABLE_GAPS = (0.0, 0.25, 0.999, 1.001, 2.0, 3.5, 5.0)

# Gaps past the ceiling: a real recording outage that resampling cannot
# honestly fill. 5.000001 is the first value over it; 11.0 and 81.0 are
# the two real non-1s deltas in wrist_ppg_run.fit (two pauses, each
# between a timer stop and a timer start).
_UNFILLABLE_GAPS = (5.000001, 6.0, 11.0, 81.0, 3600.0, 1.0e6)

# Deltas spanning the whole plausible domain for the totality property:
# durations are non-negative (the caller sorts records by ``t`` before
# differencing), but nothing else about them is guaranteed. The sampled
# values sit on every boundary the classifier has -- both sides of the
# 1e-6 ~1s tolerance and both sides of the 5s ceiling.
_ARBITRARY_DELTA = st.one_of(
    st.sampled_from(
        (
            0.0,
            1.0 - 1.0e-7,
            1.0,
            1.0 + 1.0e-7,
            1.0 - 1.0e-5,
            1.0 + 1.0e-5,
            5.0,
            5.000001,
            86400.0,
        )
    ),
    st.floats(min_value=0.0, max_value=1.0e6, allow_nan=False, allow_infinity=False),
)

_SETTINGS = settings(
    derandomize=True,
    deadline=None,
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_SWEEP_SETTINGS = settings(
    derandomize=True,
    deadline=None,
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Property 1 -- totality
# ---------------------------------------------------------------------------


@_SETTINGS
@given(deltas=st.lists(_ARBITRARY_DELTA, min_size=0, max_size=200))
# The degenerate shapes the acceptance criteria call out by name, pinned
# so they run on every invocation rather than only when the random
# search happens to shrink to them (there is no CI in this repo).
@example(deltas=[])  # a single-record stream: pairwise() yields nothing
@example(deltas=[1.0])  # single element, uniform
@example(deltas=[0.0])  # single element, zero-length
@example(deltas=[1.0e6])  # single element, enormous
@example(deltas=[0.0] * 50)  # all zeros: every sample duplicated
@example(deltas=[86400.0] * 50)  # all very large: nothing interpolable
@example(deltas=[5.0] * 50)  # all exactly on the interpolation ceiling
@example(deltas=[1.0] * 50)  # the real-corpus shape
def test_classification_is_total_over_any_delta_list(deltas: list[float]) -> None:
    """For *any* list of consecutive-timestamp deltas the classifier
    returns exactly one of §2.2.3's three descriptors and raises
    nothing.

    Degenerate inputs are the point. The existing example suites only
    ever feed it realistic-looking lists, so an all-zero stream (a file
    whose every timestamp is duplicated), an all-huge stream (a stream
    of pure outages) or a single-element stream have never been through
    this function. Note the empty list is genuinely reachable -- a
    one-record session produces no deltas at all -- and is included
    here rather than excluded as out of domain.
    """
    result = _classify_recording_interval(deltas)

    assert result in _CLASSES
    # Belt and braces against a future refactor returning a truthy
    # non-string (an enum member, say) that happens to compare equal.
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Property 2 -- planted structure classifies as designed
# (this is also what makes all three branches reachable)
# ---------------------------------------------------------------------------


class DeltaPlan(NamedTuple):
    """A delta list described by the truth that determines its class,
    *before* the list is built: how many ~1s deltas it carries, and how
    its non-1s gaps split across the interpolation ceiling.
    """

    expected: str
    uniform: int
    fillable: tuple[float, ...]
    unfillable: tuple[float, ...]

    @property
    def gap_count(self) -> int:
        return len(self.fillable) + len(self.unfillable)


@st.composite
def _planted_plan(draw) -> DeltaPlan:
    """Draw the target class *first*, then counts that realise it.

    Drawing the target first is what guarantees all three branches are
    reached under the strategy: ``sampled_from`` covers each of the
    three, and every subsequent bound is computed from that choice so
    no example is ever discarded. The alternative -- draw a shape and
    hope it lands in the branch of interest -- would leave coverage to
    chance, which is exactly the weakness of the existing example
    suites.

    Bounds, in terms of the two pinned constants:

    - ``1hz`` needs ``20*uniform >= 19*(uniform + gaps)``, i.e.
      ``uniform >= 19*gaps``; the gap split is then irrelevant, so it
      is drawn freely.
    - the non-``1hz`` branches need ``uniform < 19*gaps`` with at least
      one gap; ``irregular`` needs a *strict* majority of gaps over the
      ceiling (``unfillable > fillable``), ``smart`` needs no strict
      majority (``unfillable <= fillable``).
    """
    expected = draw(st.sampled_from((_ONE_HZ, _SMART, _IRREGULAR)))
    fill_pool = st.sampled_from(_FILLABLE_GAPS)
    unfill_pool = st.sampled_from(_UNFILLABLE_GAPS)

    if expected == _ONE_HZ:
        fillable = draw(st.lists(fill_pool, min_size=0, max_size=3))
        unfillable = draw(st.lists(unfill_pool, min_size=0, max_size=3))
        gaps = len(fillable) + len(unfillable)
        floor = max(1, _UNIFORM_NUM * gaps)
        uniform = draw(st.integers(min_value=floor, max_value=floor + 30))
    else:
        if expected == _IRREGULAR:
            unfillable = draw(st.lists(unfill_pool, min_size=1, max_size=6))
            fillable = draw(
                st.lists(fill_pool, min_size=0, max_size=len(unfillable) - 1)
            )
        else:
            fillable = draw(st.lists(fill_pool, min_size=1, max_size=6))
            unfillable = draw(
                st.lists(unfill_pool, min_size=0, max_size=len(fillable))
            )
        gaps = len(fillable) + len(unfillable)
        uniform = draw(
            st.integers(min_value=0, max_value=_UNIFORM_NUM * gaps - 1)
        )

    return DeltaPlan(
        expected=expected,
        uniform=uniform,
        fillable=tuple(fillable),
        unfillable=tuple(unfillable),
    )


@st.composite
def _planted_deltas(draw) -> tuple[DeltaPlan, list[float]]:
    """Materialise a plan, interleaving its gaps among the ~1s deltas in
    a drawn order -- a real smart-recorded stream does not put all its
    gaps at the end.
    """
    plan = draw(_planted_plan())
    deltas = [1.0] * plan.uniform + list(plan.fillable) + list(plan.unfillable)
    return plan, draw(st.permutations(deltas))


@_SETTINGS
@given(planted=_planted_deltas())
def test_planted_delta_structure_classifies_as_designed(
    planted: tuple[DeltaPlan, list[float]],
) -> None:
    """The classification is fixed by three counts alone: how many
    deltas are ~1s, how many gaps sit inside the 5s interpolation
    ceiling, and how many sit past it.

    Asserting against the *plan* rather than against a recomputation of
    the list is what keeps this from being the implementation written
    twice. Each of the three descriptors is planted in turn, so all
    three branches -- not just the ``1hz`` branch the whole real corpus
    lands in -- are exercised on every run.
    """
    plan, deltas = planted

    assert len(deltas) == plan.uniform + plan.gap_count
    assert _classify_recording_interval(list(deltas)) == plan.expected


def test_all_three_descriptors_are_reachable() -> None:
    """A deterministic floor under the generated coverage above: if the
    classifier ever collapsed to fewer than three outcomes -- a
    threshold moved to 0.0 or 1.0, say -- the property test could still
    pass on a shrunken search, but this cannot.
    """
    outcomes = {
        _classify_recording_interval([1.0] * 20),
        _classify_recording_interval([2.0, 3.0, 1.0]),
        _classify_recording_interval([81.0, 11.0, 1.0]),
    }

    assert outcomes == _CLASSES


# ---------------------------------------------------------------------------
# Property 3 -- monotonicity in the uniform fraction
# ---------------------------------------------------------------------------


@_SWEEP_SETTINGS
@given(
    gaps=st.lists(
        st.one_of(st.sampled_from(_FILLABLE_GAPS), st.sampled_from(_UNFILLABLE_GAPS)),
        min_size=1,
        max_size=5,
    )
)
@example(gaps=[81.0])  # wrist_ppg_run's 81 s pause, in isolation
@example(gaps=[11.0, 81.0])  # wrist_ppg_run's two real non-1s deltas
@example(gaps=[2.0, 3.0, 4.0, 5.0])  # a purely smart-recorded stream
def test_adding_uniform_deltas_only_ever_moves_toward_1hz(
    gaps: list[float],
) -> None:
    """Holding the non-1s gap distribution fixed, adding ~1s deltas can
    only move the verdict *toward* ``1hz``, never away from it.

    This is the invariant the ``_UNIFORM_1HZ_MIN_FRACTION`` design
    implies and that nothing currently checks. Three things are
    asserted over the whole sweep k = 0, 1, 2, ... :

    1. Once the verdict is ``1hz`` it stays ``1hz`` for every larger k.
       A comparison inverted to ``<=`` fails here immediately.
    2. Every non-``1hz`` verdict in the sweep is the *same* verdict --
       padding a stream with 1s deltas must not flip ``smart`` to
       ``irregular`` or back, because it changes no gap.
    3. The transition sits exactly where 19/20 puts it: ``1hz`` at
       k = 19*len(gaps) and not at k = 19*len(gaps) - 1. A threshold
       moved in either direction fails this line.
    """
    boundary = _UNIFORM_NUM * len(gaps)
    verdicts = [
        _classify_recording_interval([1.0] * k + list(gaps))
        for k in range(boundary + 2)
    ]

    first_1hz = next(
        (i for i, verdict in enumerate(verdicts) if verdict == _ONE_HZ), None
    )
    assert first_1hz is not None
    assert all(verdict == _ONE_HZ for verdict in verdicts[first_1hz:])

    below = set(verdicts[:first_1hz])
    assert len(below) <= 1
    assert _ONE_HZ not in below

    assert verdicts[boundary] == _ONE_HZ
    assert verdicts[boundary - 1] != _ONE_HZ


# ---------------------------------------------------------------------------
# Property 4 -- monotonicity in the unfillable-gap majority
# ---------------------------------------------------------------------------


@_SWEEP_SETTINGS
@given(
    fillable=st.lists(st.sampled_from(_FILLABLE_GAPS), min_size=1, max_size=8),
    unfillable=st.lists(st.sampled_from(_UNFILLABLE_GAPS), min_size=1, max_size=8),
)
def test_converting_gaps_past_the_ceiling_only_moves_toward_irregular(
    fillable: list[float], unfillable: list[float]
) -> None:
    """The second threshold's monotonicity, and the mirror of Property
    3: replacing an interpolable gap with one past the 5s ceiling can
    only move the verdict *toward* ``irregular``.

    The sweep holds the gap count constant and walks the split from
    all-fillable to all-unfillable, so nothing but the majority test can
    change the answer. No ~1s deltas are present, so the ``1hz`` branch
    is out of reach throughout -- which is itself asserted, since a
    stream of nothing but gaps must never be called uniform.
    """
    count = min(len(fillable), len(unfillable))
    verdicts = [
        _classify_recording_interval(fillable[:count - j] + unfillable[:j])
        for j in range(count + 1)
    ]

    assert _ONE_HZ not in verdicts
    assert verdicts[0] == _SMART
    assert verdicts[count] == _IRREGULAR

    first_irregular = verdicts.index(_IRREGULAR)
    assert all(verdict == _IRREGULAR for verdict in verdicts[first_irregular:])
    assert all(verdict == _SMART for verdict in verdicts[:first_irregular])
    # A strict majority is required, so the flip happens strictly past
    # the halfway point: an even split is still ``smart``.
    assert first_irregular == count // 2 + 1


# ---------------------------------------------------------------------------
# Property 5 -- the verdict is a function of the multiset, not the order
# ---------------------------------------------------------------------------


@_SETTINGS
@given(deltas=st.lists(_ARBITRARY_DELTA, min_size=1, max_size=60), data=st.data())
def test_classification_does_not_depend_on_delta_order(
    deltas: list[float], data: st.DataObject
) -> None:
    """``recording_interval`` is a whole-session descriptor: it counts
    deltas, it does not look at where in the session they fall. Any
    future rule that started reading runs or positions (a "gaps
    clustered at the end" heuristic, say) would have to be a deliberate
    change to §2.4.1 step 1, not something that slips in.
    """
    shuffled = data.draw(st.permutations(deltas))

    assert _classify_recording_interval(list(shuffled)) == _classify_recording_interval(
        deltas
    )
