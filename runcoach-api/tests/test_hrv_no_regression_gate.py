"""T162 -- the release gate: no §1.7 rate of F006 is worse than shipped F005's.

F006 AC21 says "any §1.7 rate that worsens **blocks release**". A markdown
report cannot say that: a document asserting an invariant is a restatement of
it, and this project has already paid for that once
(``.claude/rules/learnings/a-published-invariant-needs-a-test-that-can-break-it.md``,
``spec/ideas/IDEA-034``). So the comparison T162 measured is emitted as rows,
and this module is the assertion whose **failure mode is the invariant being
violated**: it reads those rows and reds the moment any gated rate is higher
on F006 than on shipped F005.

**What the rows are.** ``spec/references/T162-no-regression-rows.csv`` in the
Shipyard data dir, written by ``t162-gate`` in
``spec/references/T130-overlap-sweep-harness.py`` -- every T161 sweep (T125's
2050-row return rectangle over 25 capture-density pairs x carrier overlap
``c`` = 0..5, the 40-morning device-return walk in both orientations, T150's
matched pairs and switch-away rows) run twice over, once on the installed
F006 module and once on shipped F005 (``git show 42f7705:``), under both
overlap variants (the carrier's overlap mornings healthy, T130's fixture, and
suppressed, the correlated-instrument variant). Each row is one metric over
one cell, with the **same fixture rows judged by both modules**, so ``f005``
and ``f006`` share a denominator and the comparison is paired: "worse" is
simply ``f006 > f005``.

**Why the tree carries a copy.** The data dir is a machine-local breadcrumb
(``.shipyard``), gitignored, exactly as ``test_normative_mirror`` describes.
A gate that only runs where the data dir is, is a gate that does not run in a
worktree or on a runner -- and this one is the release gate. So the rows are
**committed** at ``tests/data/T162-no-regression-rows.csv``, the gate reads
the committed copy on every machine, and where the data dir *is* reachable a
second test compares the two byte for byte, so a re-run of the sweep that
moved a number cannot leave a stale copy behind it.

**Axes.** This module asserts nothing about geometry; the rows carry their
own axes (``sweep``, ``overlap``, ``ret_density``, ``car_density``, ``c``,
``orientation``, ``value_level``) and the harness prints the full
axes-held-constant tables beside them. The report is
``spec/references/F006-no-regression-report.md``.

**What is gated and what is only surfaced.** ``gated`` is 1 on the §1.7
rates AC21 blocks release on -- every one counts an ``hrv_normal`` promoted
on evidence that is not the athlete's own current reading -- and on AC22's
promotion-with-a-dissenter exposure. It is 0 on AC23's dataset-flip rate,
which by the feature's own text triggers the deferred **hysteresis
decision** rather than blocking release; the flip rows are asserted
*present* here so the number cannot be quietly dropped, and their comparison
is the report's.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The committed copy of the comparison rows: what this gate reads, on every
#: machine, data dir or not.
TREE_ROWS = _REPO_ROOT / "runcoach-api" / "tests" / "data" / "T162-no-regression-rows.csv"

#: The same file's path inside the Shipyard data dir, where ``t162-gate``
#: writes it and where a re-run would move it.
DATA_DIR_ROWS = ("spec", "references", "T162-no-regression-rows.csv")

#: Named where the ``.shipyard`` breadcrumb is absent -- a worktree, a runner.
DATA_DIR_ENV = "SHIPYARD_DATA_DIR"

#: The count columns that must be integers on every row.
_COUNTS = ("f005", "f006", "denom")


def _data_dir() -> Path | None:
    named = os.environ.get(DATA_DIR_ENV)
    candidates = [Path(named)] if named else []
    candidates.append(_REPO_ROOT / ".shipyard")
    for candidate in candidates:
        if (candidate / "spec" / "features").is_dir():
            return candidate
    return None


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _rows() -> list[dict[str, str]]:
    assert TREE_ROWS.is_file(), (
        f"{TREE_ROWS} is not in this checkout: the AC21 gate has no rows to read, so it "
        f"would pass vacuously. Re-run t162-gate (see the module docstring) and commit its output."
    )
    return _read(TREE_ROWS)


def worsened(rows: list[dict[str, str]]) -> list[str]:
    """Every gated rate row on which F006 is worse than shipped F005, named.

    The comparison is **recomputed** from ``f005``/``f006`` rather than read
    off the ``worse`` column: a gate that trusts a column the same script
    wrote is a gate on the script's opinion, not on the measurement. The
    column is checked against this separately, so a disagreement is itself a
    failure.
    """
    out: list[str] = []
    for row in rows:
        if row["gated"] != "1":
            continue
        f005, f006 = float(row["f005"]), float(row["f006"])
        if f006 > f005:
            out.append(
                f"{row['criterion']} {row['sweep']}/{row['overlap']} {row['scope']} "
                f"{row['metric']} [ret={row['ret_density'] or '-'} car={row['car_density'] or '-'} "
                f"c={row['c'] or '-'} {row['orientation'] or '-'} {row['value_level'] or '-'}]: "
                f"F005 {row['f005']} -> F006 {row['f006']} of {row['denom']}"
            )
    return out


def test_the_rows_are_the_population_the_gate_needs() -> None:
    """The positive control, before the gate itself: a green gate over an
    empty, one-sided or all-zero row set would prove nothing.

    So: the rows exist; both modules and both overlap variants are present;
    every sweep T161 ran is present; the gated metrics are present at every
    scope; and -- the clause that matters -- **shipped F005 is itself
    non-zero on the gated metrics**, so "F006 <= F005 everywhere" is a
    comparison that had something to compare and not an artefact of a
    fixture in which nothing is ever promoted.
    """
    rows = _rows()
    assert len(rows) > 1000, f"only {len(rows)} comparison rows: the sweep did not complete"
    assert {row["overlap"] for row in rows} == {"healthy", "suppressed"}, (
        "both overlap variants are required: reference §10's correlated-pair caveat is the axis "
        "the whole forbidden-flip pricing turns on"
    )
    assert {row["sweep"] for row in rows} == {"rect", "walk", "inter", "switch"}, (
        "every T161 sweep runs on both modules (AC21), not only the rectangle"
    )
    assert {row["scope"] for row in rows} >= {"cell", "by_c", "total"}, (
        "the gate is per cell as well as marginal: 'any rate that worsens' cannot be checked on "
        "a marginal that a worsened cell can hide inside"
    )
    gated = [row for row in rows if row["gated"] == "1"]
    assert len(gated) > 500, f"only {len(gated)} gated rows: AC21 has almost nothing to gate on"
    assert {row["criterion"] for row in gated} == {"AC21", "AC22"}, (
        "the gated set is AC21's §1.7 rates and AC22's promotion exposure, and nothing else"
    )
    for metric in ("forbidden", "forbidden_ret_week_ge3", "normal_stale_band",
                   "ac22_below", "ac22_below_min_window", "walk_forbidden"):
        present = [row for row in gated if row["metric"] == metric]
        assert present, f"the gated metric {metric} is missing from the rows"
        nonzero = [row for row in present if float(row["f005"]) > 0]
        assert nonzero, (
            f"shipped F005 scores 0 on {metric} everywhere, so 'F006 is no worse' is vacuous "
            f"on it -- the fixture population does not reach this rate at all"
        )
    # AC23 is surfaced, never gated: its rows must be here, and must not be gated.
    flips = [row for row in rows if row["metric"] == "walk_flips_per_athlete_year"]
    assert flips, "the AC23 dataset-flip rate per athlete-year is not in the rows"
    assert all(row["gated"] == "0" and row["criterion"] == "AC23" for row in flips), (
        "AC23's flip rate triggers the deferred hysteresis decision; it does not block release, "
        "and gating it here would make the gate say something the feature file does not"
    )
    for row in rows:
        for column in _COUNTS:
            float(row[column])  # every count parses, or this raises


def test_the_paired_comparison_is_actually_paired() -> None:
    """Both modules judged the **same** fixture rows, so the denominators
    are shared and a count difference is a rule difference and not a
    population difference. The ``rows`` metric is that denominator, and on
    every cell it must be identical on the two modules."""
    mismatched = [
        f"{row['sweep']}/{row['overlap']}/{row['scope']} {row['ret_density']}/{row['car_density']} "
        f"c={row['c']}: F005 judged {row['f005']} rows, F006 judged {row['f006']}"
        for row in _rows()
        if row["metric"] == "rows" and row["f005"] != row["f006"]
    ]
    assert not mismatched, (
        "the two modules did not judge the same population, so no rate below is comparable: "
        + "; ".join(mismatched[:10])
    )


def test_no_1_7_rate_worsens_against_shipped_f005() -> None:
    """**The gate** (F006 AC21). Any §1.7 rate -- ``hrv_normal`` promoted
    while the athlete's own return is suppressed, whether via the carrier's
    week or a band every reading of which predates the layoff; ``hrv_normal``
    on a stale band at all; ``hrv_normal`` promoted while another dataset
    reads the other side of its own band (AC22) -- that is higher on F006
    than on shipped F005, on any swept cell, blocks release.

    The witness prints the slice it compared before it asserts
    (``a-witness-must-print-the-slice-it-compared``): an exit code is a
    summary of evidence nobody has seen.
    """
    rows = _rows()
    gated = [row for row in rows if row["gated"] == "1"]
    by_criterion: dict[str, list[dict[str, str]]] = {}
    for row in gated:
        by_criterion.setdefault(row["criterion"], []).append(row)
    print(f"AC21 gate over {TREE_ROWS.name}: {len(rows)} comparison rows, {len(gated)} gated")
    for criterion, group in sorted(by_criterion.items()):
        print(f"  {criterion}: {len(group)} rows, metrics "
              f"{sorted({row['metric'] for row in group})}")
    for row in [r for r in gated if r["scope"] == "total"]:
        print(f"  TOTAL {row['sweep']}/{row['overlap']} {row['metric']}: "
              f"F005 {row['f005']} -> F006 {row['f006']} of {row['denom']} "
              f"({'worse' if float(row['f006']) > float(row['f005']) else 'no worse'})")
    regressions = worsened(rows)
    assert not regressions, (
        f"{len(regressions)} §1.7 rate(s) are worse on F006 than on shipped F005, so AC21 blocks "
        f"release: " + "; ".join(regressions[:40])
    )


def test_the_worse_column_agrees_with_the_recomputed_comparison() -> None:
    """The rows carry a ``worse`` column; the gate above does not read it.
    They must nevertheless agree, or the emitted evidence and the assertion
    over it are two different claims."""
    disagreed = [
        f"{row['sweep']}/{row['metric']}/{row['scope']}: column worse={row['worse']} but "
        f"F005 {row['f005']} -> F006 {row['f006']}"
        for row in _rows()
        if row["gated"] == "1"
        and (row["worse"] == "1") != (float(row["f006"]) > float(row["f005"]))
    ]
    assert not disagreed, "; ".join(disagreed[:20])


def test_the_gate_predicate_is_three_valued_over_a_perturbation() -> None:
    """The predicate the gate asserts on, pinned in three states, so that
    neither its green nor its red can be an accident of the rows it happened
    to be handed.

    On the rows as measured the gate above is **red**: T162 found 548 gated
    rate rows worse on F006 than on shipped F005, which is the release block
    itself, not a defect of this module. A perturbation test that only added
    a 549th would prove nothing about the predicate, so the three values are
    taken over a *clamped* copy instead:

    1. **green** -- every gated row clamped to ``f006 = f005`` (a build that
       is exactly as good as shipped F005 on every swept cell) names no
       regression;
    2. **red** -- that same copy with **one** gated row raised by one names
       **exactly** that row;
    3. **green again** -- that same copy with one gated row *lowered* by one
       (an improvement, the tolerated direction) names none, so the predicate
       is on the §1.7 direction and not on any difference.

    And, because the clamp is where the first green comes from, the real rows
    are asserted to be red -- if a future run makes them green the clamp step
    would silently become the only thing under test, and this says so.
    """
    rows = _rows()
    real = worsened(rows)
    assert real, (
        "the measured rows name no regression, so the clamped copy below is no longer a distinct "
        "state from them: re-point state 1 of this pin at the real rows and delete the clamp"
    )
    print(f"state 0 (rows as measured): {len(real)} regressions -- the gate is red, which is the "
          f"release block T162 reports. First three: {real[:3]}")

    clamped = []
    for row in rows:
        copy = dict(row)
        if copy["gated"] == "1":
            copy["f006"] = copy["f005"]
            copy["worse"] = "0"
        clamped.append(copy)
    assert not worsened(clamped), "state 1: a build equal to F005 on every gated cell must be green"
    print(f"state 1 (every gated row clamped to f006 = f005): 0 regressions")

    victim_index = next(
        i for i, row in enumerate(clamped)
        if row["gated"] == "1" and row["scope"] == "total" and row["metric"] == "forbidden"
        and row["sweep"] == "rect" and row["overlap"] == "healthy"
    )
    victim = clamped[victim_index]

    worse_copy = [dict(row) for row in clamped]
    worse_copy[victim_index]["f006"] = str(float(victim["f005"]) + 1)
    found = worsened(worse_copy)
    print(f"state 2 (that copy, {victim['sweep']}/{victim['overlap']}/{victim['metric']} raised "
          f"{victim['f005']} -> {worse_copy[victim_index]['f006']}): {len(found)} regressions: {found}")
    assert len(found) == 1, f"state 2 named {len(found)} regressions, expected exactly 1: {found[:10]}"
    assert victim["metric"] in found[0] and victim["overlap"] in found[0], found

    better_copy = [dict(row) for row in clamped]
    better_copy[victim_index]["f006"] = str(max(0.0, float(victim["f005"]) - 1))
    assert not worsened(better_copy), (
        "state 3: a rate that IMPROVES was reported as a regression -- the gate is on the §1.7 "
        "direction, not on any difference"
    )
    print(f"state 3 (that copy, the same rate LOWERED by one): 0 regressions")


def test_the_committed_rows_are_the_rows_the_sweep_wrote() -> None:
    """The drift gate. Where the Shipyard data dir is reachable, the
    committed copy must be byte-equal to the file ``t162-gate`` wrote there,
    so a re-run that moved a number cannot leave the gate reading a stale
    copy. Skipped -- loudly -- where the data dir is unreachable, for
    exactly ``test_normative_mirror``'s reason: a comparison against a
    corpus that is not there is not a comparison, and the copy can only be
    desynchronised where the original is.
    """
    data_dir = _data_dir()
    if data_dir is None:
        pytest.skip(
            f"the Shipyard data dir is unreachable: neither ${DATA_DIR_ENV} nor "
            f"{_REPO_ROOT / '.shipyard'} names a directory with spec/features under it, so there "
            f"is no original to compare {TREE_ROWS.name} against. The gate itself still ran: it "
            f"reads the committed copy."
        )
    original = data_dir.joinpath(*DATA_DIR_ROWS)
    assert original.is_file(), (
        f"{original} does not exist, but the committed copy does: the rows the gate reads have no "
        f"source. Re-run t162-gate with --rows {original}."
    )
    same = original.read_bytes() == TREE_ROWS.read_bytes()
    print(f"compared {TREE_ROWS} <-> {original}: {'equal' if same else 'DRIFTED'} "
          f"({TREE_ROWS.stat().st_size} vs {original.stat().st_size} bytes)")
    assert same, (
        f"{TREE_ROWS} has drifted from {original}: the gate is reading rows the sweep did not "
        f"write. Recopy (the data dir is where t162-gate writes; the tree copy is what the gate "
        f"reads on a machine without it)."
    )
