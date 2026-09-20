"""T160 -- the three-valued pin for each genuinely retired qualifier (F006 AC18).

``.claude/rules/learnings/retiring-a-ratified-behaviour-needs-a-three-valued-pin.md``
allows a ratified behaviour to be retired only when a pin exists that is

===  ==================================================  =======
1    shipped F005, the qualifier **present**              green
2    shipped F005, the qualifier **deleted**, nothing     **red**
     else changed
3    the replacement (F006, today's tree)                 green
===  ==================================================  =======

and **state 2 is the whole rule**. States 1 and 3 are what any feature-addition
pin already gives you: a test over the population a qualifier was added to
close is green on shipped F005 *by construction*, because shipped F005
contains the qualifier. This module is the only place in the tree where state
2 is observed, so it is the deliverable, and each state-2 test below captures
the failing assertion's own text rather than merely asserting that something
failed.

**Two qualifiers are genuinely retired**, and the honest list is half of what
this task delivers (task Technical Notes, corroborated across waves 2-6):

1. ``resolve_baseline_tier``'s **role** as "one tier owns the only band" --
   the cross-tier arbitration. The function itself survives (it is still
   pinned directly in ``test_hrv_trend_series.py``, and its rule-3 tie order
   is redeployed in ``_presentation_fallback``); what retires is its ownership
   of the band, which ``build_series`` no longer asks it for at all
   (``test_hrv_tier_change_per_dataset.py`` pins the absent call).
2. The ``off_baseline_tier`` exclusion (T152).

Everything else on IDEA-071's list is **retained or re-derived**, so nothing
below pins it as retired: T094/T095/T129 (the reported reset, T154), T106
(``_era_boundary``'s ordering key -- a sub-mechanism of the era clip, whose
role ``research/00``:219 states normatively and whose real cover is
``test_hrv_trend_reset.py::test_the_era_boundary_prefers_the_one_the_judged_week_is_clear_of``
plus ``test_hrv_tier_change_per_dataset.py::test_the_era_boundary_ordering_key_keeps_its_three_terms``;
neither carries the literal token ``T106``, so a grep-based retirement audit
reports it unpinned and is **wrong**), T107/T116, T093's rule-3 fallback
(T156), T117 (redeployed as AC6's gate) and T125/T132 (T158, AC24).

How state 2 is reached without touching the worktree's source
-------------------------------------------------------------
The shipped module is fetched with::

    git show 42f7705:runcoach-api/src/runcoach_api/metrics/hrv_trend.py

into pytest's own ``tmp_path_factory`` directory, the qualifier is deleted
**there** by an exact-text replacement whose anchor must occur exactly once,
and each copy is imported under a distinct module name through
``importlib.util.spec_from_file_location`` (the workspace runs pytest with
``--import-mode=importlib``; ``test_hrv_dataset_populations.py`` and
``spec/references/T130-overlap-sweep-harness.py`` load by path the same way).
``runcoach-api/src/`` is never written to. Every test prints the module name
and the file path it loaded and the slice it compared
(``a-witness-must-print-the-slice-it-compared``), and
``test_each_copy_differs_from_the_shipped_module_in_its_qualifier_alone``
prints the whole diff, so all three states are reproducible from the output.

The corpus
----------
67 local days ``[D-66, D]``, ``D`` = 2026-09-08, in ``Pacific/Auckland``. Every
day carries **both** a ``chest_strap_raw`` capture near 58-62 ms and a
``health_snapshot`` capture near 28-32 ms, and the two alternate which is the
earlier of the morning -- 06:00 and 07:00, swapping each day. The alternation
is what makes qualifier 1's state 2 legible: with the arbitration deleted the
per-day collapse keeps the **earliest** capture of the day whichever tier it
is on (the shipped direction; F006 AC4's "later" is the stale text, amendment
pending as IDEA-079), so the band lands on a mixture of the two tiers rather
than on either one. Shipped F005 resolves the strap (daily, >= 14 baseline
days, >= 3 judged-week days, higher fidelity); F006 selects the strap dataset
(judgeable, highest fidelity, nothing skipped).

What each pin asserts, and why it is that qualifier's pin
---------------------------------------------------------
``_pin_the_band_belongs_to_one_tier`` -- **no reading of one tier contributes
to another tier's band** (F006 AC1; ``research/00`` 5.4 (i) "the anti-mixing
constraint of 3.3 is honoured by construction"). Shipped F005 honours it
*through* the arbitration: one tier owns the band and every other tier's
readings are filtered out. F006 honours it *by construction*: each dataset is
built from its own tier's readings alone. That is exactly a retirement -- the
property survives, the mechanism does not -- and deleting the arbitration from
shipped F005 breaks the property.

``_pin_every_stored_row_is_accounted_for_exactly_once`` -- ``research/00`` 1.6,
restated as F006 AC15. Shipped F005 accounts for the non-baseline tier's rows
by listing them ``off_baseline_tier: <tier>``; F006 accounts for them in their
own dataset's series, excluded nowhere. Deleting the exclusion leaves them in
neither list.

**Deletion 1 necessarily removes the exclusion's call site**, because the
``off_baseline_tier`` exclusion lives inside the arbitration branch it is the
bookkeeping for -- there is no smaller edit. That does not blur the two pins,
and ``test_each_pin_is_red_only_on_its_own_qualifiers_deletion`` measures it:
the partition pin is **green** on the arbitration-deleted copy (a pooled
series still lists every row, as ``same_day_later_capture``), and the
one-tier pin is **green** on the exclusion-deleted copy (the filter is still
there, so the band is still the strap's). Each red is attributable to its own
qualifier.
"""

from __future__ import annotations

import difflib
import importlib.util
import math
import statistics
import subprocess
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo

import pytest
from runcoach_api.metrics import hrv_trend as f006

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The commit shipped F005 was released at -- the same reference
#: ``test_hrv_dataset_populations.py`` pins its "shipped F005 says" verdicts
#: against.
SHIPPED_REF = "42f7705"
SHIPPED_PATH = "runcoach-api/src/runcoach_api/metrics/hrv_trend.py"
SHOW_COMMAND = f"git show {SHIPPED_REF}:{SHIPPED_PATH}"

AUCKLAND = ZoneInfo("Pacific/Auckland")
D = date(2026, 9, 8)
STRAP = "chest_strap_raw"
SNAPSHOT = "health_snapshot"

STATE_F005 = "f005_shipped"
STATE_MINUS_ARBITRATION = "f005_minus_tier_arbitration"
STATE_MINUS_OFF_BASELINE_TIER = "f005_minus_off_baseline_tier_exclusion"


# ---------------------------------------------------------------------------
# the deletions: exact text, taken out of the shipped module's ``build_series``
# ---------------------------------------------------------------------------

#: The ``off_baseline_tier`` exclusion, verbatim from ``42f7705`` (lines
#: 972-975). It is the record that a reading of another tier contributed to
#: nothing and why -- the qualifier T152 retired.
OFF_BASELINE_TIER_EXCLUSION = """\
        if reading.tier != tier:
            excluded.append(
                Exclusion(reading.date, reading.session_id, f"{REASON_OFF_BASELINE_TIER}: {reading.tier}")
            )
"""

#: The same four lines plus the ``elif`` they guard (line 976): together they
#: are ``resolve_baseline_tier``'s **role** -- the resolved tier deciding which
#: readings may reach the band at all.
TIER_ARBITRATION = OFF_BASELINE_TIER_EXCLUSION + "        elif reading.date in series_by_day:\n"

DELETIONS = {
    STATE_MINUS_OFF_BASELINE_TIER: (
        OFF_BASELINE_TIER_EXCLUSION,
        (
            "        if reading.tier != tier:\n"
            "            pass  # T160 state 2: the off_baseline_tier exclusion, deleted\n"
        ),
    ),
    STATE_MINUS_ARBITRATION: (
        TIER_ARBITRATION,
        (
            "        if reading.date in series_by_day:"
            "  # T160 state 2: the resolved tier no longer owns the band\n"
        ),
    ),
}


# ---------------------------------------------------------------------------
# loading the copies
# ---------------------------------------------------------------------------


def _shipped_source() -> str:
    """``SHOW_COMMAND``'s output. The shipped module imports nothing outside
    the standard library, so a path load of it needs no package context."""
    proc = subprocess.run(
        ["git", "show", f"{SHIPPED_REF}:{SHIPPED_PATH}"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"`{SHOW_COMMAND}` failed: {proc.stderr.strip()}"
    assert "def resolve_baseline_tier(" in proc.stdout, f"`{SHOW_COMMAND}` returned something else"
    return proc.stdout


def _delete(source: str, anchor: str, replacement: str, label: str) -> str:
    """The one qualifier, deleted -- and nothing else. The anchor must occur
    **exactly once**, so a drift in the shipped text fails loudly here rather
    than silently patching the wrong place or nothing at all."""
    found = source.count(anchor)
    assert found == 1, f"{label}: the anchor occurs {found} times in {SHIPPED_REF}, expected exactly 1"
    return source.replace(anchor, replacement)


def _load(name: str, source: str, directory: Path) -> tuple[ModuleType, Path]:
    """One copy, under its own module name. The name is registered in
    ``sys.modules`` **before** the module body runs, and left there: under
    ``from __future__ import annotations`` every field annotation is a string,
    and ``dataclasses`` resolves them by looking the defining module up in
    ``sys.modules`` -- an unregistered copy raises ``AttributeError:
    'NoneType' object has no attribute '__dict__'`` at the first
    ``@dataclass`` (measured, 2026-09-19). The three names are distinct from
    ``runcoach_api.metrics.hrv_trend``, so nothing else in the session can
    reach a copy by import."""
    path = directory / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, path


@pytest.fixture(scope="module")
def states(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[ModuleType, Path]]:
    """The three F005 states: shipped, and one copy per deleted qualifier.
    F006 is the installed module and is not copied."""
    directory = tmp_path_factory.mktemp("t160_f005_states")
    source = _shipped_source()
    loaded = {STATE_F005: _load(STATE_F005, source, directory)}
    for name, (anchor, replacement) in DELETIONS.items():
        loaded[name] = _load(name, _delete(source, anchor, replacement, name), directory)
    return loaded


def _origin(name: str, states: dict[str, tuple[ModuleType, Path]]) -> str:
    module, path = states[name]
    return f"module={module.__name__} loaded from {path} (`{SHOW_COMMAND}`)"


# ---------------------------------------------------------------------------
# the corpus
# ---------------------------------------------------------------------------


def _local(day: date, hh: int) -> str:
    return datetime(day.year, day.month, day.day, hh, tzinfo=AUCKLAND).astimezone(UTC).isoformat()


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


STRAP_VALUES = (58.0, 60.0, 62.0)
SNAPSHOT_VALUES = (28.0, 30.0, 32.0)


def corpus(target: date = D) -> list[dict]:
    """Both tiers, every day of ``[D-66, D]``, swapping which is the earlier
    capture of the morning. Axes held constant
    (``a-sweep-must-name-the-axes-it-holds-constant``): capture density is
    daily on **both** datasets, the target is the single day ``D``, the zone
    is ``Pacific/Auckland``, no day is silent (so neither the global coverage
    gap nor F006's internal-hole clip can fire), no tier era ends (so no
    ``tier_change`` boundary exists), and the two tiers' values never overlap
    -- 58-62 ms against 28-32 ms -- so a band built from a mixture is
    separated from either tier's by far more than any tolerance."""
    rows: list[dict] = []
    for index, day in enumerate(_days(target - timedelta(days=66), target)):
        strap_hh, snapshot_hh = (6, 7) if index % 2 == 0 else (7, 6)
        rows.append(
            {
                "session_id": f"strap-{day.isoformat()}",
                "start_time": _local(day, strap_hh),
                "resting_rmssd_ms": STRAP_VALUES[index % 3],
                "hrv_source_tier": STRAP,
            }
        )
        rows.append(
            {
                "session_id": f"snapshot-{day.isoformat()}",
                "start_time": _local(day, snapshot_hh),
                "resting_rmssd_ms": SNAPSHOT_VALUES[index % 3],
                "hrv_source_tier": SNAPSHOT,
            }
        )
    return rows


def expected_mean(tier: str | None, target: date = D) -> float:
    """The independent oracle: the mean ``ln rMSSD`` over the baseline window
    ``[D-66, D-7]``, computed from the corpus rows themselves rather than from
    any module. ``tier`` ``None`` means the pooled series a deleted
    arbitration produces -- the **earliest** capture of each day whichever
    tier it is on."""
    first, last = target - timedelta(days=66), target - timedelta(days=7)
    per_day: dict[date, tuple[str, float, int]] = {}
    for index, day in enumerate(_days(first, last)):
        strap_hh, snapshot_hh = (6, 7) if index % 2 == 0 else (7, 6)
        candidates = [
            (STRAP, STRAP_VALUES[index % 3], strap_hh),
            (SNAPSHOT, SNAPSHOT_VALUES[index % 3], snapshot_hh),
        ]
        if tier is not None:
            candidates = [c for c in candidates if c[0] == tier]
        per_day[day] = min(candidates, key=lambda c: c[2])
    return statistics.fmean(math.log(value) for _tier, value, _hh in per_day.values())


# ---------------------------------------------------------------------------
# the two pins, one per retired qualifier
# ---------------------------------------------------------------------------


def _partition(module: ModuleType, rows: list[dict], target: date):
    """``(raw series, F005-shaped view, every reading the module kept)``, for
    a module of either generation. F005 returns the flat series; F006 returns
    N datasets and ``selected_view`` flattens the selected one onto the same
    shape, which is what the athlete is judged on in both."""
    raw = module.build_series(rows, AUCKLAND, target)
    if hasattr(raw, "datasets"):
        return raw, module.selected_view(raw), [r for d in raw.datasets for r in d.series]
    return raw, raw, list(raw.series)


def _pin_the_band_belongs_to_one_tier(module: ModuleType, label: str, target: date = D) -> str:
    """**No reading of one tier contributes to another tier's band** (AC1).

    Green on shipped F005 (the arbitration filters the series to one tier),
    red on shipped F005 with the arbitration deleted (the per-day collapse
    pools both tiers into one band), green on F006 (each dataset is built
    from its own tier's readings alone).
    """
    rows = corpus(target)
    _raw, view, _kept = _partition(module, rows, target)
    tiers = sorted({reading.tier for reading in view.baseline})
    observed = statistics.fmean(module.ln_rmssd(reading) for reading in view.baseline)
    band = module.build_band(module.ln_rmssd(reading) for reading in view.baseline)
    witness = (
        f"[{label}] presented tier={view.tier} baseline_n={len(view.baseline)} "
        f"tiers_in_the_band={tiers} mean_ln_rmssd={observed:.6f} "
        f"one_tier_means={{strap {expected_mean(STRAP):.6f}, snapshot {expected_mean(SNAPSHOT):.6f}}} "
        f"pooled_mean={expected_mean(None):.6f} band={band} "
        f"window={view.baseline_window[0]}..{view.baseline_window[1]} "
        f"reset={view.reset_reason}@{view.reset_on}"
    )
    print(witness)

    assert len(tiers) == 1, (
        f"the band mixes tiers: readings of {tiers} are all in one baseline of "
        f"{len(view.baseline)} days -- the anti-mixing rule (AC1, research/00 5.4 (i)) is broken. {witness}"
    )
    assert tiers[0] == view.tier, f"the band is not the presented tier's. {witness}"
    assert math.isclose(observed, expected_mean(tiers[0]), rel_tol=0, abs_tol=1e-12), (
        f"the band's mean is not the mean of {tiers[0]}'s own baseline readings. {witness}"
    )
    assert abs(observed - expected_mean(None)) > 0.3, (
        f"the band's mean is the pooled mean of both tiers, not one tier's. {witness}"
    )
    return witness


def _pin_every_stored_row_is_accounted_for_exactly_once(
    module: ModuleType, label: str, target: date = D
) -> str:
    """**Every stored row inside ``[D-66, D]`` is accounted for exactly once**
    -- in the series (F005) or some dataset's series (F006), or in
    ``excluded`` (``research/00`` 1.6, restated as F006 AC15).

    Green on shipped F005 (the other tier's rows are listed
    ``off_baseline_tier: <tier>``), red on shipped F005 with that exclusion
    deleted (they are in neither list), green on F006 (they are in their own
    dataset's series and excluded nowhere).
    """
    rows = corpus(target)
    raw, _view, kept = _partition(module, rows, target)
    stored = [row["session_id"] for row in rows]
    counted = Counter([reading.session_id for reading in kept] + [e.session_id for e in raw.excluded])
    missing = sorted(session_id for session_id in stored if counted[session_id] == 0)
    doubled = sorted(session_id for session_id, seen in counted.items() if seen > 1)
    reasons = Counter(e.reason.split(":")[0] for e in raw.excluded)
    witness = (
        f"[{label}] stored={len(stored)} kept={len(kept)} excluded={len(raw.excluded)} "
        f"accounted={len([s for s in stored if counted[s]])} missing={len(missing)} "
        f"doubled={len(doubled)} exclusion_reasons={dict(reasons)} "
        f"first_missing={missing[:3]}"
    )
    print(witness)

    assert not missing, (
        f"{len(missing)} stored rows are in neither the series nor excluded -- "
        f"research/00 1.6's partition is broken; first three: {missing[:3]}. {witness}"
    )
    assert not doubled, f"{len(doubled)} stored rows are listed twice; first three: {doubled[:3]}. {witness}"
    assert set(counted) == set(stored), f"a row nobody stored was accounted for. {witness}"
    return witness


PINS = {
    "the_band_belongs_to_one_tier": _pin_the_band_belongs_to_one_tier,
    "every_stored_row_is_accounted_for_exactly_once": _pin_every_stored_row_is_accounted_for_exactly_once,
}


# ---------------------------------------------------------------------------
# state 0: the copies are the shipped module minus one qualifier, and nothing else
# ---------------------------------------------------------------------------


def test_each_copy_differs_from_the_shipped_module_in_its_qualifier_alone(states) -> None:
    """The precondition every state-2 red rests on: "**and nothing else
    changed**". Each copy's diff against the shipped source is printed in
    full, and the lines it removes must be exactly the qualifier's -- so a red
    below cannot come from a patch that hit something wider."""
    shipped = _shipped_source()
    removed_by_state: dict[str, list[str]] = {}
    for name, (anchor, _replacement) in DELETIONS.items():
        patched = states[name][1].read_text(encoding="utf-8")
        diff = list(
            difflib.unified_diff(
                shipped.splitlines(), patched.splitlines(), SHIPPED_REF, name, lineterm="", n=2
            )
        )
        print(f"--- diff {SHIPPED_REF} -> {name} ({states[name][1]})")
        for line in diff:
            print(line)
        removed = [line[1:] for line in diff if line.startswith("-") and not line.startswith("---")]
        added = [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]
        removed_by_state[name] = removed
        # Every removed line is one of the anchor's, in the anchor's order,
        # and the anchor lines the replacement keeps verbatim are not removed
        # at all -- so each patch is the qualifier and strictly less.
        assert removed == [line for line in anchor.splitlines() if line not in _replacement.splitlines()], (
            f"{name} removed something outside its qualifier: {removed}"
        )
        assert len(added) == 1, f"{name} added more than the one line that keeps the file parsable: {added}"
        assert len([line for line in diff if line.startswith("@@")]) == 1, f"{name} is not one hunk"

    assert "REASON_OFF_BASELINE_TIER" in shipped, "shipped F005 must contain the qualifier being retired"
    assert set(removed_by_state[STATE_MINUS_OFF_BASELINE_TIER]) < set(
        removed_by_state[STATE_MINUS_ARBITRATION]
    ), (
        "deletion 1 is deletion 2 plus the branch it guards -- the exclusion is the arbitration's "
        "own bookkeeping and there is no smaller edit; the cross-check below is what separates them"
    )


# ---------------------------------------------------------------------------
# qualifier 1 -- ``resolve_baseline_tier``'s role as "one tier owns the only band"
# ---------------------------------------------------------------------------


def test_the_one_tier_band_pin_is_green_on_shipped_f005(states) -> None:
    """State 1. Shipped F005 resolves the strap and filters every snapshot
    reading out of the series, so the band is the strap's alone -- green, and
    green *by construction*, which is why states 2 and 3 exist."""
    module, _path = states[STATE_F005]
    print(_origin(STATE_F005, states))
    witness = _pin_the_band_belongs_to_one_tier(module, "state 1: shipped F005")

    assert f"presented tier={STRAP}" in witness
    raw, _view, _kept = _partition(module, corpus(), D)
    off_tier = [e for e in raw.excluded if e.reason.startswith("off_baseline_tier")]
    print(f"[state 1] off_baseline_tier exclusions={len(off_tier)} e.g. {off_tier[0].reason}")
    assert len(off_tier) == 67, "the shipped arbitration lists every snapshot row off_baseline_tier"


def test_the_one_tier_band_pin_is_red_on_f005_with_the_tier_arbitration_deleted(states) -> None:
    """**State 2 -- the deliverable.** Shipped F005 with
    ``resolve_baseline_tier``'s role as "one tier owns the only band" deleted
    and nothing else changed: the per-day collapse keeps the earliest capture
    of each morning whichever tier it is on, so the band is built from a
    mixture of the strap's 58-62 ms and the snapshot's 28-32 ms.

    The red is asserted by its own text, not merely by failing: a state-2 test
    that accepted any ``AssertionError`` would be green on a copy that failed
    to import, or on a pin that broke for an unrelated reason.
    """
    module, path = states[STATE_MINUS_ARBITRATION]
    print(_origin(STATE_MINUS_ARBITRATION, states))

    with pytest.raises(AssertionError) as excinfo:
        _pin_the_band_belongs_to_one_tier(module, "state 2: F005 minus the tier arbitration")

    text = str(excinfo.value)
    print(f"[state 2] {path.name} -- the failing assertion:\n{text}")
    assert text.startswith("the band mixes tiers: "), text
    assert f"readings of ['{STRAP}', '{SNAPSHOT}']" in text, text
    assert "the anti-mixing rule (AC1, research/00 5.4 (i)) is broken" in text, text
    # ... and the mixture is the pooled series, which is what the arbitration
    # existed to prevent: 30 strap mornings and 30 snapshot ones.
    _raw, view, _kept = _partition(module, corpus(), D)
    tiers = Counter(reading.tier for reading in view.baseline)
    print(f"[state 2] the band's own readings, by tier: {dict(tiers)}")
    assert tiers == Counter({STRAP: 30, SNAPSHOT: 30}), dict(tiers)


def test_the_one_tier_band_pin_is_green_on_f006(states) -> None:
    """State 3. F006 honours the same property with the arbitration gone: the
    selected dataset's band is the strap's, and the snapshot's readings are
    not missing but in **their own** dataset, carrying their own band -- which
    is the thing shipped F005 could not express and the reason the qualifier
    could be retired at all (AC1, AC2)."""
    print(f"module={f006.__name__} loaded from {f006.__file__} (F006, today's tree)")
    witness = _pin_the_band_belongs_to_one_tier(f006, "state 3: F006")

    assert f"presented tier={STRAP}" in witness
    series = f006.build_series(corpus(), AUCKLAND, D)
    by_tier = {dataset.tier: dataset for dataset in series.datasets}
    for tier, dataset in by_tier.items():
        print(
            f"[state 3] dataset {tier}: n={dataset.n} established={dataset.established} "
            f"band={dataset.band} week_days={len({r.date for r in dataset.window})} "
            f"tiers_in_series={sorted({r.tier for r in dataset.series})}"
        )
    assert set(by_tier) == {STRAP, SNAPSHOT}
    assert f006.select_dataset(series).selected.tier == STRAP
    for tier, dataset in by_tier.items():
        assert {r.tier for r in dataset.series} == {tier}
        assert dataset.n == 60 and dataset.established
        assert math.isclose(
            statistics.fmean(f006.ln_rmssd(r) for r in dataset.baseline),
            expected_mean(tier),
            rel_tol=0,
            abs_tol=1e-12,
        )


# ---------------------------------------------------------------------------
# qualifier 2 -- the ``off_baseline_tier`` exclusion (T152)
# ---------------------------------------------------------------------------


def test_the_exhaustive_partition_pin_is_green_on_shipped_f005(states) -> None:
    """State 1. Every snapshot row is accounted for as ``off_baseline_tier:
    health_snapshot`` -- green by construction, because shipped F005 contains
    the qualifier."""
    module, _path = states[STATE_F005]
    print(_origin(STATE_F005, states))
    witness = _pin_every_stored_row_is_accounted_for_exactly_once(module, "state 1: shipped F005")

    assert "missing=0" in witness and "doubled=0" in witness
    assert "'off_baseline_tier': 67" in witness


def test_the_exhaustive_partition_pin_is_red_on_f005_with_the_off_baseline_tier_exclusion_deleted(
    states,
) -> None:
    """**State 2 -- the deliverable.** Shipped F005 with the
    ``off_baseline_tier`` exclusion deleted and nothing else changed: the
    filter still removes every snapshot reading from the series, but nothing
    records that it did, so 67 stored rows are in neither list and
    ``research/00`` 1.6's partition is broken."""
    module, path = states[STATE_MINUS_OFF_BASELINE_TIER]
    print(_origin(STATE_MINUS_OFF_BASELINE_TIER, states))

    with pytest.raises(AssertionError) as excinfo:
        _pin_every_stored_row_is_accounted_for_exactly_once(
            module, "state 2: F005 minus the off_baseline_tier exclusion"
        )

    text = str(excinfo.value)
    print(f"[state 2] {path.name} -- the failing assertion:\n{text}")
    assert text.startswith("67 stored rows are in neither the series nor excluded"), text
    assert "research/00 1.6's partition is broken" in text, text
    assert "first three: ['snapshot-2026-07-04', 'snapshot-2026-07-05', 'snapshot-2026-07-06']" in text, text


def test_the_exhaustive_partition_pin_is_green_on_f006(states) -> None:
    """State 3. The snapshot rows are accounted for in their own dataset's
    series, and no exclusion names ``off_baseline_tier`` anywhere -- the
    reason is retired, the population is not (AC15, T152)."""
    print(f"module={f006.__name__} loaded from {f006.__file__} (F006, today's tree)")
    witness = _pin_every_stored_row_is_accounted_for_exactly_once(f006, "state 3: F006")

    assert "missing=0" in witness and "doubled=0" in witness
    assert "off_baseline_tier" not in witness
    series = f006.build_series(corpus(), AUCKLAND, D)
    assert not [e for e in series.excluded if "off_baseline_tier" in e.reason]
    assert not hasattr(f006, "REASON_OFF_BASELINE_TIER")
    kept = Counter(r.tier for d in series.datasets for r in d.series)
    print(f"[state 3] readings kept per dataset: {dict(kept)}; excluded={len(series.excluded)}")
    assert kept == Counter({STRAP: 67, SNAPSHOT: 67}) and not series.excluded


# ---------------------------------------------------------------------------
# each red is its own qualifier's
# ---------------------------------------------------------------------------


def test_each_pin_is_red_only_on_its_own_qualifiers_deletion(states) -> None:
    """The 2x2 that makes each state-2 red attributable. Deletion 1 removes
    the exclusion's call site as well (it is the arbitration's bookkeeping and
    there is no smaller edit), so without this the two reds could be one red
    counted twice. They are not: the off-diagonal is green in both
    directions."""
    observed: dict[tuple[str, str], str] = {}
    for state in (STATE_MINUS_ARBITRATION, STATE_MINUS_OFF_BASELINE_TIER):
        module, _path = states[state]
        for pin_name, pin in PINS.items():
            try:
                pin(module, f"{state} / {pin_name}")
            except AssertionError as failure:
                observed[state, pin_name] = f"RED: {str(failure).splitlines()[0][:90]}"
            else:
                observed[state, pin_name] = "green"
    for (state, pin_name), result in sorted(observed.items()):
        print(f"[2x2] {state:44} {pin_name:48} {result}")

    assert observed[STATE_MINUS_ARBITRATION, "the_band_belongs_to_one_tier"].startswith("RED")
    assert observed[STATE_MINUS_ARBITRATION, "every_stored_row_is_accounted_for_exactly_once"] == "green"
    assert observed[STATE_MINUS_OFF_BASELINE_TIER, "every_stored_row_is_accounted_for_exactly_once"].startswith(
        "RED"
    )
    assert observed[STATE_MINUS_OFF_BASELINE_TIER, "the_band_belongs_to_one_tier"] == "green"
