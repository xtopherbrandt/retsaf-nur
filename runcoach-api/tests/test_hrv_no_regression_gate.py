"""T162 -- the release gate: no forbidden-direction rate (PRIN-14) of F006 is worse than shipped F005's.

F006 AC21 (GATE-04) says a forbidden-direction (PRIN-14) rate that worsens **blocks release**. A markdown
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

**What this gate proves, and what it does not.** It is a **ratchet on
measured evidence**, not a live invariant over the rule. Every assertion
below is over rows in a committed CSV, and that file changes only when
someone re-runs the ~13-minute ``t162-gate`` harness and re-commits its
output. So "no forbidden-direction rate (PRIN-14) worsens" is proved of the rule **as it stood when
the sweep last ran** -- and of nothing else. A change to
``metrics/hrv_trend.py`` that reintroduces a regression does not redden a
single row here, because no row is recomputed: the wave-7 mutation pass
reverted T164's recency-reference widening -- exactly the stale-band
regression T164 had just paid -- and this module passed 7/7.

**The provenance pin is what keeps that evidence honest.**
``tests/data/T162-no-regression-rows.provenance.json`` records the git blob
sha of the module the rows were measured against, the commit that introduced
it, the shipped-F005 ref the comparison used, the harness, the row count and
the date; ``test_the_rows_were_measured_against_this_checkouts_rule`` recomputes
that blob sha from the module in this checkout and **fails** when it differs.
It is not a skip and not a warning: a stale ratchet that still reports green
is worse than no gate, because it is read as a release decision. What the pin
converts is the failure mode -- from "the gate silently proves a claim about
a rule that no longer exists" into "the gate says the evidence is out of date
and names the command that renews it". It still cannot tell you whether the
new rule is *better*; only the re-measurement can, and that is the point.

**Axes.** This module asserts nothing about geometry; the rows carry their
own axes (``sweep``, ``overlap``, ``ret_density``, ``car_density``, ``c``,
``orientation``, ``value_level``) and the harness prints the full
axes-held-constant tables beside them. The report is
``spec/references/F006-no-regression-report.md``.

**What is gated and what is only surfaced.** ``gated`` is 1 on the forbidden-direction (PRIN-14)
rates AC21 blocks release on (``hrv_normal`` while the athlete's own return is
suppressed), on the stale-band rates AC21 also gates (the under-calling exposure),
and on AC22's promotion-with-a-dissenter exposure. It is 0 on AC23's dataset-flip rate,
which by the feature's own text triggers the deferred **hysteresis
decision** rather than blocking release; the flip rows are asserted
*present* here so the number cannot be quietly dropped, and their comparison
is the report's.

**The one exception, and why it is here rather than in a comment.** T164
paid the stale-band regression -- the recency reference is now taken over
every established dataset, and ``normal_stale_band`` is back to shipped
F005's count exactly. The **other** regression T162 measured is deliberately
**not** paid, by user decision of 2026-09-20: it exists only under the
independent-instruments fixture and reverses under the correlated one, and
T162 established that the recorded corpus cannot settle which of the two
worlds this is (IDEA-087). An unpaid regression that nobody can see is how a
gate rots, so it is carried here **by name, by row count and by condition**
(``DEFERRED_EXCEPTION*`` below), it is printed by the gate on every run, and
``test_the_deferred_forbidden_rate_exception_is_exactly_the_rows_it_names``
reds if its own row count moves **in either direction**. It is not a
"warn": every other gated rate still blocks release outright.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The committed copy of the comparison rows: what this gate reads, on every
#: machine, data dir or not.
TREE_ROWS = _REPO_ROOT / "runcoach-api" / "tests" / "data" / "T162-no-regression-rows.csv"

#: The rows' provenance: the state of the tree they were measured against.
#: Written beside them rather than inside them because it is a fact about the
#: *measurement*, not a row of it, and because the CSV is regenerated verbatim
#: by the harness and must stay byte-equal to the data-dir original.
ROWS_PROVENANCE = _REPO_ROOT / "runcoach-api" / "tests" / "data" / "T162-no-regression-rows.provenance.json"

#: The module whose behaviour the rows are a measurement of. If this file's
#: contents change, the rows describe a rule that is no longer in the tree.
MEASURED_MODULE = _REPO_ROOT / "runcoach-api" / "src" / "runcoach_api" / "metrics" / "hrv_trend.py"

#: The re-measure command, named in the failure message rather than left for
#: the reader to reconstruct -- a gate that says "this is stale" without
#: saying how to renew it is a gate people learn to edit rather than obey.
REMEASURE_COMMAND = (
    "uv run --package runcoach-api python "
    ".shipyard/spec/references/T130-overlap-sweep-harness.py t162-gate "
    "--f005 <git show 42f7705:runcoach-api/src/runcoach_api/metrics/hrv_trend.py> "
    "--rows .shipyard/spec/references/T162-no-regression-rows.csv --procs <n>   (~13 min at --procs 16)"
)

#: The same file's path inside the Shipyard data dir, where ``t162-gate``
#: writes it and where a re-run would move it.
DATA_DIR_ROWS = ("spec", "references", "T162-no-regression-rows.csv")

#: Named where the ``.shipyard`` breadcrumb is absent -- a worktree, a runner.
DATA_DIR_ENV = "SHIPYARD_DATA_DIR"

#: The count columns that must be integers on every row.
_COUNTS = ("f005", "f006", "denom")

#: -------------------------------------------------------------------------
#: The one measured regression this gate does **not** block release on, named.
#:
#: **What it is.** The forbidden-direction rate (PRIN-14) -- the athlete's own return is
#: suppressed (25 ms) and ``hrv_normal`` is promoted from the overlapping
#: carrier's week -- is worse on F006 than on shipped F005 at ``c = 4`` and
#: ``c = 5`` only: rectangle 22,217 -> 22,232 in total (+15 of 307,500,
#: +0.07% relative), on 17 of 150 cells, and **better** at ``c <= 2``
#: (1,713 -> 1,614 at ``c = 0``); the walk 1,104 -> 1,108 of 24,000 on three
#: cells. Counted three ways over the same rows (``forbidden``, its
#: ``via carrier_week`` decomposition, and the ``ret_week >= 3`` subset) plus
#: the walk's own metric, that is the 64 rows below.
#:
#: **Its condition, which is the whole reason it is deferred.** It exists
#: **only** under ``T161_OVERLAP=healthy`` -- T130's fixture, in which the
#: carrier keeps reading 38/44 on a morning the athlete's strap reads 25, i.e.
#: the two datasets are *independent instruments*. Under
#: ``T161_OVERLAP=suppressed`` -- one athlete, one physiology, two devices --
#: F006 is better at every ``c`` (3,625 -> 3,497; the T130-comparable subset
#: 128 -> 0) and the walk is equal (78 -> 78). Every excepted row is therefore
#: an ``overlap == "healthy"`` row, and that is asserted, not assumed.
#:
#: **What must be answered before it is re-priced: IDEA-087.** The
#: sign-agreement rate -- how often the two datasets fall on the same side of
#: their own bands -- is the statistic every one of these counts depends on,
#: and T162 established that the recorded corpus cannot supply it (no
#: simultaneous pair exists or can exist on one watch; n = 2). T162's two
#: overlap variants bracket the answer at 0% and 100%, and this rate's sign
#: changes across that bracket. Spending a rule change on it while its sign
#: is unknown is the parameter-before-measurement mistake the dataset model's
#: §10 names.
#:
#: **Mechanism, so the exception is not a shrug.** T153's hole clip leaves
#: the returning dataset unestablished from the eighth morning back while the
#: overlapping carrier stays judgeable, so F006 keeps promoting from the
#: carrier where shipped F005 re-admitted the strap at ``r = 8`` on a band
#: mixing era A with the return -- a verdict the dataset model's §9 calls
#: worse than its successor, which on this population happens to land on the
#: right answer.
DEFERRED_EXCEPTION = (
    "the PRIN-14 forbidden-direction rate (a suppressed return promoted hrv_normal from the carrier's "
    "week) at c = 4 and c = 5, under the independent-instruments fixture only -- deferred pending IDEA-087"
)

#: Exactly how many gated rows the exception covers. Re-measured by T164 on
#: 2026-09-20 over the regenerated rows; T162 measured the same regression at
#: the same counts before the reference-set change, which is the evidence that
#: T164 moved the stale-band rate and left this one untouched.
DEFERRED_EXCEPTION_ROWS = 64

#: The exception's shape, as a predicate rather than a list of row ids: the
#: forbidden-rate family, on the healthy-overlap side only.
DEFERRED_EXCEPTION_CRITERION = "AC21"
DEFERRED_EXCEPTION_OVERLAP = "healthy"
DEFERRED_EXCEPTION_METRICS = frozenset(
    {"forbidden", "forbidden_carrier_week", "forbidden_ret_week_ge3", "walk_forbidden"}
)

#: The marginal totals the exception is allowed to be, quoted so that a change
#: in the regression's *size* reds even if its row count happens not to move.
#: ``(sweep, overlap, metric) -> (f005, f006)``.
DEFERRED_EXCEPTION_TOTALS = {
    ("rect", "healthy", "forbidden"): (22217, 22232),
    ("rect", "healthy", "forbidden_carrier_week"): (22217, 22232),
    ("rect", "healthy", "forbidden_ret_week_ge3"): (7479, 7494),
    ("walk", "healthy", "walk_forbidden"): (1104, 1108),
}

#: The metric T164 paid, and its shipped-F005 figures, pinned here so the
#: payment cannot silently un-happen: ``hrv_normal`` on an entirely pre-layoff
#: band was 3,705 of 307,500 rectangle rows and 254 of 24,000 walk rows under
#: shipped F006 (T155's judgeable-only reference), and is back to F005's exact
#: 1,896 and 96 under T164's established reference.
PAID_BY_T164 = {
    ("rect", "healthy", "normal_stale_band"): (1896, 1896),
    ("rect", "suppressed", "normal_stale_band"): (1896, 1896),
    ("walk", "healthy", "walk_stale_normal"): (96, 96),
    ("walk", "suppressed", "walk_stale_normal"): (96, 96),
}

#: -------------------------------------------------------------------------
#: AC23's worsened cells -- decided 2026-09-21, and the decision is CONDITIONAL.
#:
#: **What this pin is.** AC23 says the dataset-flip rate "is measured across
#: the AC19 sweeps and compared against F005 per AC21; a worse rate triggers
#: the deferred hysteresis decision". "Per AC21" is load-bearing: AC21's
#: comparison is per cell as well as marginal, and
#: ``test_the_rows_are_the_population_the_gate_needs`` asserts exactly that,
#: on the stated reasoning that "any rate that worsens" cannot be checked on
#: a marginal that a worsened cell can hide inside. Until sprint-006 review
#: iteration 1 the *only* AC23 assertion pinned ``gated == "0"`` -- that the
#: rows exist and do not block release. **Nothing asserted the direction of
#: the comparison at any scope**, so a re-measurement that tripled the flip
#: rate at the cells below would have left every test in this module green.
#:
#: **What the rows hold.** On the committed evidence 80 of the 1,200
#: ``walk_flips`` cells are worse on F006 than on shipped F005, every one of
#: them 0 flips per 40-morning walk -> 2, and every one of them at
#: ``car_density = 2wk`` -- the sub-daily carrier, i.e. the whole worsened
#: population sits on one value of one axis. The marginal that was reported
#: beside them improved (18.4684 -> 9.359 flips per athlete-year), which is
#: precisely how 80 worsened cells stayed invisible:
#: ``.claude/rules/learnings/a-sweep-must-name-the-axes-it-holds-constant.md``.
#:
#: **The decision was taken on 2026-09-21, and it is CONDITIONAL.** On AC23's
#: own text these 80 cells **trigger** the deferred hysteresis decision --
#: they oblige it to be *taken*, not hysteresis to be *built*. It was taken,
#: by the user, and the answer is **no hysteresis** (**IDEA-089**, which is
#: ``status: conditional``, not ``resolved``). So this is no longer an open
#: question; neither is it a closed one.
#:
#: **What it was decided on.** Measured at ``car_density = 2wk`` -- the exact
#: axis value every worsened cell sits on -- not one PRIN-14 forbidden family
#: moved: all nine of them are equal across all 850 family×cell rows
#: (370 cells). AC22 promotion exposure *improved* there, 52 better and
#: 0 worse (``ac22_below`` 18,
#: ``ac22_literal`` 18, ``walk_ac22_below`` 16; an earlier record said 34,
#: which silently dropped ``ac22_literal`` -- 52 is the figure the stated
#: filter produces). The marginal halved, 18.4684 -> 9.359. The reasoning: a
#: flip is a **proxy**, and what PRIN-14 forbids is a **harm** -- a wrong
#: verdict in the up-regulating direction. Here the flips buy withheld days,
#: the cautious direction, while every forbidden family is byte-identical to
#: shipped F005.
#:
#: **The decision is against the set as measured on 2026-09-21, and it is not
#: a licence for that set to grow.** That is why this pin keeps its exact
#: behaviour: growth, shrinkage and a shift in *which* cells worsen all still
#: red here, and name what moved.
#:
#: **The two conditions** IDEA-089 was made conditional on (sprint-006
#: review, Stage 4.6 critic), of which the second is still not met:
#:
#: 1. **The authority now carries the decision.** ``specification/research/00-design-decisions.md``
#:    GATE-02 (decision C05): the system **MUST NOT add hysteresis** to
#:    dataset selection, and the worsened dataset-flip set must remain the 80
#:    pinned ``walk_flips`` cells at ``car_density = 2wk``. It is revisited
#:    only if IDEA-089's part (b) shows harm.
#: 2. **The corpus cannot express the harm.** ``spec/references/T130-overlap-sweep-harness.py``'s
#:    ``era()`` gives every tier the same value generator -- its own docstring:
#:    "the band's dispersion is the same at every density" -- so both tiers
#:    carry statistically identical bands. "No forbidden-direction (PRIN-14) rate moved where the flips
#:    worsened" is therefore true **by construction**: a property of the
#:    fixtures, not a finding about the rule. Under a real dispersion gap (F006:
#:    2.16% strap vs 17.49% PPG; GATE-02's IDEA-089 (b)) the snapshot band is wider, so a flip
#:    makes ``hrv_normal`` strictly more likely -- PRIN-14's forbidden direction.
#:    No row of the CSV can show that, because SD is pinned equal across tiers.
#:
#: Until both are met AC23 is **PARTIAL, not MET**, and this pin is what keeps
#: the conditional decision honest.
AC23_METRIC = "walk_flips"

#: The worsened set, as the three products the rows actually form. Each row is
#: ``(ret_density, car_density, orientations, carrier overlaps c)`` and is
#: taken over both ``overlap`` variants and both ``value_level`` values, which
#: every worsened cell spans in full. Note the asymmetry the product records:
#: at the daily retirement density **both** walk orientations worsen, at the
#: two 4wk densities only the strap-returns orientation does, and only out to
#: ``c = 3``.
AC23_WORSENED_PRODUCTS = (
    ("daily", "2wk", ("snapshot-returns", "strap-returns"), (0, 1, 2, 3, 4, 5)),
    ("4wk-clustered", "2wk", ("strap-returns",), (0, 1, 2, 3)),
    ("4wk-spread", "2wk", ("strap-returns",), (0, 1, 2, 3)),
)

#: Both overlap variants and both value levels, spanned in full by every
#: product above -- named rather than inlined, because "the worsening is
#: indifferent to the fixture's independence assumption" is itself a finding:
#: unlike ``DEFERRED_EXCEPTION``, this one does **not** reverse under the
#: correlated-instrument variant, so IDEA-087's open question does not cover it.
AC23_WORSENED_OVERLAPS = ("healthy", "suppressed")
AC23_WORSENED_VALUE_LEVELS = ("healthy", "suppressed")


def _ac23_worsened_cells() -> frozenset[tuple[str, str, str, str, str, str]]:
    """The 80 pinned cell keys, expanded from ``AC23_WORSENED_PRODUCTS``.

    ``(overlap, ret_density, car_density, c, orientation, value_level)`` --
    every axis a ``walk_flips`` cell row carries, so two different cells can
    never collapse onto one key (asserted below against the row count)."""
    return frozenset(
        (overlap, ret_density, car_density, str(c), orientation, value_level)
        for ret_density, car_density, orientations, carriers in AC23_WORSENED_PRODUCTS
        for orientation in orientations
        for c in carriers
        for overlap in AC23_WORSENED_OVERLAPS
        for value_level in AC23_WORSENED_VALUE_LEVELS
    )


#: Every pinned cell worsens by exactly this much: 0 flips of a 40-morning
#: walk on shipped F005, 2 on F006. Pinned beside the membership because a set
#: that kept its shape while each cell's worsening tripled is the
#: re-measurement this module was blind to, and membership alone would not see it.
AC23_WORSENED_CELL_VALUES = (0.0, 2.0)
AC23_WORSENED_CELL_DENOM = 40.0

#: The marginal that concealed them, quoted so the concealment is part of the
#: record rather than a sentence in an idea file: the same rows, summed, read
#: as a halving. ``(sweep, overlap) -> (f005, f006)`` per athlete-year.
AC23_MARGINAL = {
    ("walk", "healthy"): (18.4684, 9.359),
    ("walk", "suppressed"): (18.4684, 9.359),
}


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


def worse_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Every gated rate row on which F006 is worse than shipped F005 --
    **including** the deferred exception, which is partitioned out separately
    and never filtered away here.

    The comparison is **recomputed** from ``f005``/``f006`` rather than read
    off the ``worse`` column: a gate that trusts a column the same script
    wrote is a gate on the script's opinion, not on the measurement. The
    column is checked against this separately, so a disagreement is itself a
    failure.
    """
    return [
        row
        for row in rows
        if row["gated"] == "1" and float(row["f006"]) > float(row["f005"])
    ]


def name(row: dict[str, str]) -> str:
    """One worsened row, with every axis that identifies it."""
    return (
        f"{row['criterion']} {row['sweep']}/{row['overlap']} {row['scope']} "
        f"{row['metric']} [ret={row['ret_density'] or '-'} car={row['car_density'] or '-'} "
        f"c={row['c'] or '-'} {row['orientation'] or '-'} {row['value_level'] or '-'}]: "
        f"F005 {row['f005']} -> F006 {row['f006']} of {row['denom']}"
    )


def worsened(rows: list[dict[str, str]]) -> list[str]:
    """Every worsened gated row, named -- the raw PRIN-14 direction, exception
    included. The *gate* asserts on ``unexcused`` below; this is what the
    three-valued perturbation pin exercises, and what the exception is
    partitioned out of."""
    return [name(row) for row in worse_rows(rows)]


def is_deferred_exception(row: dict[str, str]) -> bool:
    """Whether a worsened row is the one regression T164 deliberately leaves
    unpaid (``DEFERRED_EXCEPTION``). Structural, so a row that drifts out of
    the exception's shape -- a different criterion, a different metric, or the
    *suppressed*-overlap side where the regression does not exist at all --
    is not silently covered by it."""
    return (
        row["criterion"] == DEFERRED_EXCEPTION_CRITERION
        and row["overlap"] == DEFERRED_EXCEPTION_OVERLAP
        and row["metric"] in DEFERRED_EXCEPTION_METRICS
    )


def deferred(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """The worsened rows the exception covers."""
    return [row for row in worse_rows(rows) if is_deferred_exception(row)]


def unexcused(rows: list[dict[str, str]]) -> list[str]:
    """**The gate's predicate**: every worsened gated row the exception does
    not cover, named. Non-empty blocks release."""
    return [name(row) for row in worse_rows(rows) if not is_deferred_exception(row)]


def git_blob_sha(path: Path) -> str:
    """``git hash-object`` for one file, computed here rather than shelled out.

    The gate must work in a bare checkout with no git binary on PATH and with
    the file uncommitted or dirty, so the sha is taken over the **bytes on
    disk** -- which is also what makes it a statement about the rule this run
    would execute, rather than about the rule some commit holds.

    ``\\r\\n`` is folded to ``\\n`` first, which is what git's own clean filter
    does to a text file on the way into the object store (this repository is
    checked out CRLF on Windows and the committed blob is LF). Without the
    fold the pin would compare a platform against a sha and red on every
    Windows checkout -- a gate that fails for a reason that is not the rule
    changing is a gate that gets deleted.
    """
    data = path.read_bytes().replace(b"\r\n", b"\n")
    # sha1 because that is git's own object hash, not because anything here is a secret.
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def provenance() -> dict:
    assert ROWS_PROVENANCE.is_file(), (
        f"{ROWS_PROVENANCE} is missing: the rows below are a snapshot of a measurement and "
        f"nothing records what they were measured against, so every assertion over them is a "
        f"claim about an unknown rule. Restore it, or re-measure: {REMEASURE_COMMAND}"
    )
    return json.loads(ROWS_PROVENANCE.read_text(encoding="utf-8"))


def test_the_rows_were_measured_against_this_checkouts_rule() -> None:
    """**The provenance pin.** Everything else in this module asserts over a
    committed CSV that changes only when someone re-runs a ~13-minute sweep.
    That makes the gate a ratchet on evidence: it proves "no forbidden-direction (PRIN-14) rate
    worsens" of the rule **as it stood when the sweep last ran**, and of
    nothing else.

    The hole that closes here was measured, not imagined. The wave-7 mutation
    pass reverted ``select_dataset``'s recency reference from every
    ``established`` dataset back to ``is_judgeable`` only -- reinstating,
    exactly, the stale-band regression T164 had just paid -- and this module
    passed **7/7**, because no row is recomputed from the rule.

    So the rows carry a provenance and this asserts it: the git blob sha of
    ``metrics/hrv_trend.py`` **in this checkout** must equal the one the rows
    were measured against. A changed rule reds here and names the command that
    renews the evidence.

    It is deliberately neither a skip nor a warning. Both were considered and
    both are what this project has learned to distrust: a gate that degrades
    to "informational" when its premise fails still reports green, and green
    is read as a release decision. The failure is the honest state -- the
    evidence is out of date, and only a re-measurement can say whether the new
    rule is better or worse.

    The witness prints both shas before it asserts
    (``a-witness-must-print-the-slice-it-compared``): an exit code is a summary
    of evidence nobody has seen.
    """
    record = provenance()
    measured = record["measured_module"]
    recorded_sha = measured["blob_sha"]
    current_sha = git_blob_sha(MEASURED_MODULE)
    rel = measured["path"]

    print(f"provenance: {ROWS_PROVENANCE.name}")
    print(f"  rows      : {TREE_ROWS.name}, {record['rows']['row_count']} rows, "
          f"measured {record['rows']['measured_on']}, committed {record['rows']['committed_by'][:7]}")
    print(f"  vs shipped: F005 at {record['compared_against']['ref']}")
    print(f"  rule       {rel}")
    print(f"    recorded blob {recorded_sha}  (introduced by {measured['introduced_by'][:7]})")
    print(f"    current  blob {current_sha}")
    print(f"    -> {'MATCH' if current_sha == recorded_sha else 'DIFFERS -- the evidence is stale'}")

    assert current_sha == recorded_sha, (
        f"THE RULE HAS CHANGED SINCE THE EVIDENCE WAS MEASURED, so this gate's rows describe a "
        f"rule that is not in this checkout and every green below is a claim about the old one.\n"
        f"  {rel}\n"
        f"    recorded (measured against): {recorded_sha}\n"
        f"    current  (in this tree)    : {current_sha}\n"
        f"Re-measure, then regenerate and re-commit BOTH the rows "
        f"({TREE_ROWS.relative_to(_REPO_ROOT).as_posix()}, byte-equal to the data-dir original) "
        f"and the report (spec/references/F006-no-regression-report.md and its mirror), and update "
        f"{ROWS_PROVENANCE.name} with the new blob sha, its introducing commit and the date:\n"
        f"    {REMEASURE_COMMAND}\n"
        f"Do not edit the recorded sha to make this green: the sha is the claim, and moving it "
        f"without a re-run asserts a measurement nobody performed."
    )


def test_the_provenance_describes_the_rows_it_sits_beside() -> None:
    """The provenance is only worth asserting on if it is about *these* rows.

    Two ways it could quietly stop being: the row file is regenerated and the
    record keeps describing the old one, or the record is written against a
    ref this repository cannot resolve. So the recorded row count is checked
    against the CSV actually read, and the fields the failure message above
    depends on are required to be present and non-empty.
    """
    record = provenance()
    rows = _rows()
    recorded = int(record["rows"]["row_count"])
    print(f"provenance row_count {recorded} vs {TREE_ROWS.name} {len(rows)} rows")
    assert recorded == len(rows), (
        f"the provenance records {recorded} rows and {TREE_ROWS.name} holds {len(rows)}: the rows "
        f"were regenerated and the record was not, so it describes a measurement that is no longer "
        f"the one this gate reads. Re-measure and rewrite both: {REMEASURE_COMMAND}"
    )
    for section, field in (
        ("measured_module", "blob_sha"),
        ("measured_module", "path"),
        ("measured_module", "introduced_by"),
        ("compared_against", "ref"),
        ("harness", "blob_sha"),
        ("rows", "measured_on"),
    ):
        value = record.get(section, {}).get(field)
        assert isinstance(value, str) and value.strip(), (
            f"the provenance's {section}.{field} is empty: the pin above and its failure message "
            f"are built out of these fields, and a blank one is a pin that says nothing"
        )
    assert record["measured_module"]["path"] == MEASURED_MODULE.relative_to(_REPO_ROOT).as_posix(), (
        f"the provenance records a measured module at {record['measured_module']['path']} while "
        f"the pin checks {MEASURED_MODULE.relative_to(_REPO_ROOT).as_posix()}: the sha compared is "
        f"not the sha recorded"
    )


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
        "the gated set is AC21's PRIN-14 and stale-band rates and AC22's exposure, and nothing else"
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
    """**The gate** (F006 AC21). Any rate AC21 gates -- the forbidden-direction (PRIN-14) rates,
    ``hrv_normal`` promoted while the athlete's own return is suppressed, via the carrier's
    week or a band every reading of which predates the layoff; the under-calling exposure,
    ``hrv_normal`` on a stale band at all; ``hrv_normal`` promoted while another dataset
    reads the other side of its own band (AC22) -- that is higher on F006
    than on shipped F005, on any swept cell, blocks release.

    **Except** the one regression named in ``DEFERRED_EXCEPTION`` at the top
    of this module, which is partitioned out here and asserted *exactly*, row
    count and marginal totals alike, by the test below. This assertion is on
    everything else, undiluted: one unexcused worsened cell reds it.

    The witness prints the slice it compared before it asserts
    (``a-witness-must-print-the-slice-it-compared``): an exit code is a
    summary of evidence nobody has seen. The deferred exception is printed
    with it, on every run, so it can never become invisible.
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

    excepted = deferred(rows)
    print(f"  DEFERRED EXCEPTION ({len(excepted)} of {len(worse_rows(rows))} worsened rows, "
          f"NOT blocking release by user decision 2026-09-20): {DEFERRED_EXCEPTION}")
    for row in [r for r in excepted if r["scope"] == "total"]:
        print(f"    excepted TOTAL {row['sweep']}/{row['overlap']} {row['metric']}: "
              f"F005 {row['f005']} -> F006 {row['f006']} of {row['denom']}")

    regressions = unexcused(rows)
    assert not regressions, (
        f"{len(regressions)} PRIN-14 rate(s) are worse on F006 than on shipped F005 and are NOT "
        f"covered by the deferred exception, so AC21 blocks release: " + "; ".join(regressions[:40])
    )


def test_the_deferred_forbidden_rate_exception_is_exactly_the_rows_it_names() -> None:
    """The exception, pinned so it cannot grow, shrink or wander.

    An exception nobody can see is how a gate rots, and the two ways it rots
    are (a) quietly widening to cover a *new* regression and (b) quietly
    outliving the regression it was written for. Both are closed here: the
    worsened rows the exception covers must number **exactly**
    ``DEFERRED_EXCEPTION_ROWS``, their marginal totals must be exactly
    ``DEFERRED_EXCEPTION_TOTALS``, and the unexcused set must be empty (which
    together means the exception is neither too small nor too large for the
    rows as measured).

    Its **condition** is asserted too, because the condition is the whole
    justification: the regression exists only where the two datasets are
    modelled as independent instruments. So every excepted row is an
    ``overlap == "healthy"`` row, and on the ``suppressed`` side **no** row of
    the same metrics is worse at all -- F006 is better or equal there at every
    ``c``. If that ever stops being true, the deferral's premise has changed
    and this reds.

    And what T164 **did** pay is pinned beside it (``PAID_BY_T164``), because
    the argument for deferring the second regression is partly that the first
    one was paid in full: ``hrv_normal`` on an entirely pre-layoff band is
    back to shipped F005's exact count, in both overlap variants.

    **Before this exception may be re-priced, IDEA-087 must be answered**:
    the strap/snapshot sign-agreement rate, which T162 showed the recorded
    corpus cannot settle and which T159's ``datasets[]`` could estimate from
    ordinary use at no capture cost.
    """
    rows = _rows()
    excepted = deferred(rows)
    for row in excepted[:10]:
        print(f"excepted: {name(row)}")
    print(f"deferred exception: {len(excepted)} rows (pinned {DEFERRED_EXCEPTION_ROWS}) -- "
          f"{DEFERRED_EXCEPTION}")

    assert len(excepted) == DEFERRED_EXCEPTION_ROWS, (
        f"the deferred exception now covers {len(excepted)} worsened rows, not the "
        f"{DEFERRED_EXCEPTION_ROWS} it was measured and justified at. A regression that grew is a "
        f"new regression and is not covered by this deferral; one that shrank means the deferral "
        f"is out of date. Re-measure (t162-gate), re-read IDEA-087, and re-decide -- do not move "
        f"this number to make the suite green. Rows now excepted: "
        + "; ".join(name(row) for row in excepted[:20])
    )
    assert all(row["overlap"] == DEFERRED_EXCEPTION_OVERLAP for row in excepted), (
        "an excepted row is on the suppressed-overlap side, where this regression does not exist: "
        "the deferral's premise is that it is conditional on the independence assumption"
    )
    assert {row["metric"] for row in excepted} == set(DEFERRED_EXCEPTION_METRICS), (
        f"the excepted metrics are {sorted({row['metric'] for row in excepted})}, not "
        f"{sorted(DEFERRED_EXCEPTION_METRICS)}: the exception covers the forbidden-rate family and "
        f"nothing else"
    )

    wanted = set(DEFERRED_EXCEPTION_TOTALS) | set(PAID_BY_T164)
    totals = {
        (row["sweep"], row["overlap"], row["metric"]): (int(row["f005"]), int(row["f006"]))
        for row in rows
        if row["scope"] == "total" and (row["sweep"], row["overlap"], row["metric"]) in wanted
    }
    assert set(totals) == wanted, f"marginal total rows missing from the sweep: {sorted(wanted - set(totals))}"
    for key, expected in DEFERRED_EXCEPTION_TOTALS.items():
        assert totals.get(key) == expected, (
            f"the excepted regression's marginal total moved: {key} is {totals.get(key)}, pinned "
            f"at {expected} (F005 -> F006). Its size is part of what was deferred."
        )
    for key, expected in PAID_BY_T164.items():
        assert totals.get(key) == expected, (
            f"T164 paid the stale-band regression and this says so: {key} is {totals.get(key)}, "
            f"pinned at {expected}. F006 must meet shipped F005 exactly here."
        )

    # The condition, on the other side of the bracket: no forbidden-rate row
    # is worse under the suppressed-overlap fixture, at any scope.
    correlated = [
        name(row)
        for row in worse_rows(rows)
        if row["overlap"] == "suppressed" and row["metric"] in DEFERRED_EXCEPTION_METRICS
    ]
    assert not correlated, (
        "the forbidden rate is worse under T161_OVERLAP=suppressed too, so it is no longer "
        "conditional on the two datasets being independent instruments and IDEA-087 no longer "
        "gates it: " + "; ".join(correlated[:20])
    )
    assert not unexcused(rows), "the gate's own assertion; repeated here so this pin cannot be read alone"


def _ac23_cells(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Every per-cell AC23 flip-rate row. ``scope == "cell"`` excludes the two
    marginal rows, which are the thing the per-cell comparison exists to see
    past."""
    return [row for row in rows if row["metric"] == AC23_METRIC and row["scope"] == "cell"]


def _ac23_key(row: dict[str, str]) -> tuple[str, str, str, str, str, str]:
    return (
        row["overlap"],
        row["ret_density"],
        row["car_density"],
        row["c"],
        row["orientation"],
        row["value_level"],
    )


def test_the_ac23_flip_rate_comparison_is_asserted_and_its_worsened_cells_are_pinned() -> None:
    """**AC23's comparison, asserted** (sprint-006 review iteration 1, M1).

    Before this test the only AC23 assertion in the module pinned
    ``gated == "0"``: that the flip rows exist and do not block release.
    Nothing asserted the *direction* of the comparison AC23 is written around
    -- "compared against F005 per AC21; a worse rate triggers the deferred
    hysteresis decision" -- at any scope, so the 80 cells on which F006 flips
    more than shipped F005 were carried by the evidence and read by nothing.

    So the worsened set is pinned **exactly**, by the axes that identify each
    cell and by the size of the worsening, and this names what moved when it
    moves: cells that stop worsening, cells that start, and cells that worsen
    by more than the pinned 0 -> 2 all red here.

    **These 80 cells were decided on 2026-09-21, conditionally.** On AC23's
    own text a worse rate **triggers** the deferred hysteresis decision, and
    this evidence is a worse rate over an entire sub-population: every
    worsened cell sits at ``car_density = 2wk``, the sub-daily carrier. The
    decision that trigger obliges was taken -- **no hysteresis** -- and it is
    **conditional**, because the corpus pins tier dispersion equal so it cannot
    express the harm a flip would cause (the one condition still open; the
    authority now carries the decision as research/00 GATE-02). See the
    ``AC23_WORSENED_*`` block above and **IDEA-089** (``status: conditional``).
    The decision is against the set *as measured on 2026-09-21*; it is not a
    licence for that set to grow, which is what this test exists to detect.

    The marginal is printed beside the per-cell result on every run, because
    the two together are the finding: a rate can improve overall and worsen
    on one whole value of one axis, and a sweep that reports only the
    marginal cannot say so
    (``.claude/rules/learnings/a-sweep-must-name-the-axes-it-holds-constant.md``).

    The witness prints the slice it compared before it asserts
    (``a-witness-must-print-the-slice-it-compared``): an exit code is a
    summary of evidence nobody has seen.
    """
    rows = _rows()
    cells = _ac23_cells(rows)
    assert cells, (
        f"no per-cell {AC23_METRIC} rows are in {TREE_ROWS.name}: AC23's comparison has no "
        f"population, so every assertion below would pass vacuously. Re-measure: {REMEASURE_COMMAND}"
    )
    worse = [row for row in cells if float(row["f006"]) > float(row["f005"])]
    better = [row for row in cells if float(row["f006"]) < float(row["f005"])]
    found = {_ac23_key(row) for row in worse}
    pinned = _ac23_worsened_cells()

    print(f"AC23 ({AC23_METRIC}) over {TREE_ROWS.name}: {len(cells)} cells, "
          f"{len(worse)} worse on F006, {len(better)} better, "
          f"{len(cells) - len(worse) - len(better)} equal")
    for row in worse[:8]:
        print(f"  worsened cell: {name(row)}")
    if len(worse) > 8:
        print(f"  ... and {len(worse) - 8} more worsened cells, every one of them pinned below")
    by_group: dict[tuple[str, str, str], int] = {}
    for row in worse:
        key = (row["ret_density"], row["car_density"], row["orientation"])
        by_group[key] = by_group.get(key, 0) + 1
    for key, count in sorted(by_group.items()):
        print(f"  grouping ret={key[0]} car={key[1]} {key[2]}: {count} cells")
    for row in [r for r in rows if r["metric"] == "walk_flips_per_athlete_year"]:
        direction = "worse" if float(row["f006"]) > float(row["f005"]) else "improved"
        print(f"  MARGINAL {row['sweep']}/{row['overlap']}: F005 {row['f005']} -> F006 "
              f"{row['f006']} per athlete-year -- {direction}, and it is what concealed the "
              f"{len(worse)} worsened cells above (IDEA-089, CONDITIONAL)")

    assert len(found) == len(worse), (
        f"{len(worse)} worsened cell rows collapse onto {len(found)} keys, so two cells share one "
        f"identity and the pin below cannot tell them apart: the row axes have changed"
    )
    missing = sorted(pinned - found)
    appeared = sorted(found - pinned)
    assert found == pinned, (
        f"AC23's worsened cell set has moved. The hysteresis decision recorded in IDEA-089 -- no "
        f"hysteresis, 2026-09-21 -- is CONDITIONAL and was taken against this set exactly as "
        f"measured on that date, not against whatever it becomes.\n"
        f"  no longer worse ({len(missing)}): {missing[:20]}\n"
        f"  newly worse ({len(appeared)}): {appeared[:20]}\n"
        f"Do not edit the pin to make this green: re-measure (t162-gate), then re-open IDEA-089 "
        f"and re-take the decision on the new evidence -- a set that grew is a decision made on a "
        f"set that no longer exists, one that emptied means AC23's trigger no longer fires."
    )

    off_axis = [name(row) for row in worse if row["car_density"] != "2wk"]
    assert not off_axis, (
        "a worsened AC23 cell sits off the 2wk carrier density, so the worsening is no longer "
        "confined to the sub-daily carrier and IDEA-089's account of the axis is out of date: "
        + "; ".join(off_axis[:20])
    )

    mis_sized = [
        f"{name(row)} (pinned {AC23_WORSENED_CELL_VALUES[0]} -> {AC23_WORSENED_CELL_VALUES[1]} "
        f"of {AC23_WORSENED_CELL_DENOM})"
        for row in worse
        if (float(row["f005"]), float(row["f006"])) != AC23_WORSENED_CELL_VALUES
        or float(row["denom"]) != AC23_WORSENED_CELL_DENOM
    ]
    assert not mis_sized, (
        "the size of AC23's worsening moved while its cell set did not -- exactly the "
        "re-measurement this module was blind to before the set was pinned: "
        + "; ".join(mis_sized[:20])
    )

    marginal = {
        (row["sweep"], row["overlap"]): (float(row["f005"]), float(row["f006"]))
        for row in rows
        if row["metric"] == "walk_flips_per_athlete_year"
    }
    assert marginal == AC23_MARGINAL, (
        f"AC23's marginal flip rate moved: {marginal}, pinned at {AC23_MARGINAL}. The marginal is "
        f"quoted here because it is the half of the evidence that was read, and the per-cell set "
        f"above is the half that was not (IDEA-089)."
    )


def test_the_worse_column_agrees_with_the_recomputed_comparison() -> None:
    """The rows carry a ``worse`` column; the gate above does not read it.
    They must nevertheless agree, or the emitted evidence and the assertion
    over it are two different claims.

    **Widened past ``gated == "1"``** (sprint-006 review iteration 1, M1).
    The filter used to be ``gated == "1"``, which left the column unchecked on
    every ungated row -- and the column is not written as ``f006 > f005`` at
    all: the harness writes it as ``gated AND worse``, so all 1,644 ungated
    rows on which F006 is worse carry ``worse = 0``, AC23's 80 flip cells
    among them. A reader taking the column at its name reads those rows as
    "not worse", which is one of the three reasons the AC23 comparison stayed
    invisible (IDEA-089).

    Of the review's two options -- widen this, or rename the column to
    ``gated_and_worse`` -- widening is taken, because the rename cannot be
    made honestly here: the name lives in a **measured** CSV whose provenance
    is pinned to a git blob sha of ``metrics/hrv_trend.py``
    (``test_the_rows_were_measured_against_this_checkouts_rule``), and the
    only legitimate way to rewrite that file is a ~13-minute re-run of
    ``t162-gate``. Editing a header in place would assert a measurement
    nobody performed. So the column keeps the name the sweep gave it, and this
    test pins **what the name actually means**, over every row rather than
    over the gated quarter of them, with the trap stated in the failure
    message instead of left in the header.
    """
    rows = _rows()
    gated_worse = [r for r in rows if r["gated"] == "1" and float(r["f006"]) > float(r["f005"])]
    ungated_worse = [r for r in rows if r["gated"] == "0" and float(r["f006"]) > float(r["f005"])]
    flagged = [row for row in rows if row["worse"] == "1"]
    print(f"worse column over {TREE_ROWS.name}: {len(rows)} rows, {len(flagged)} carry worse=1")
    print(f"  gated and worse   : {len(gated_worse)} rows")
    print(f"  UNGATED and worse : {len(ungated_worse)} rows, of which "
          f"{len([r for r in ungated_worse if r['worse'] == '1'])} carry worse=1 -- the column is "
          f"'gated AND worse', not 'worse'")
    print(f"  of those, AC23 {AC23_METRIC} cells: "
          f"{len([r for r in ungated_worse if r['metric'] == AC23_METRIC])} (IDEA-089, CONDITIONAL)")

    assert ungated_worse, (
        "no ungated row is worse on F006, so the clause below distinguishing 'gated AND worse' "
        "from 'worse' compares nothing and this test would pass whatever the column meant"
    )
    disagreed = [
        f"{row['sweep']}/{row['overlap']}/{row['metric']}/{row['scope']} "
        f"[ret={row['ret_density'] or '-'} car={row['car_density'] or '-'} c={row['c'] or '-'}]: "
        f"column worse={row['worse']} but gated={row['gated']} and F005 {row['f005']} -> "
        f"F006 {row['f006']}"
        for row in rows
        if (row["worse"] == "1")
        != (row["gated"] == "1" and float(row["f006"]) > float(row["f005"]))
    ]
    assert not disagreed, (
        "the worse column is not 'gated AND f006 > f005' on every row, so neither reading of it is "
        "safe and the rows and the assertions over them are two different claims: "
        + "; ".join(disagreed[:20])
    )


def test_the_gate_predicate_is_three_valued_over_a_perturbation() -> None:
    """The predicate the gate asserts on, pinned in five states, so that
    neither its green nor its red can be an accident of the rows it happened
    to be handed -- and so that the deferred exception cannot swallow a
    regression it was not written for.

    On the rows as measured the **raw** PRIN-14 comparison is still red: T164's
    re-measurement leaves 64 worsened gated rows, every one of them the
    deferred exception (T162 found 548 before the reference-set change). The
    *gate* is green on them, because ``unexcused`` partitions those 64 out.
    A perturbation test that only added a 65th would prove nothing about the
    predicate, so states 1 to 3 are taken over a *clamped* copy:

    1. **green** -- every gated row clamped to ``f006 = f005`` (a build that
       is exactly as good as shipped F005 on every swept cell) names no
       regression;
    2. **red** -- that same copy with **one** gated row raised by one, chosen
       **outside** the exception's shape (``rect``/``suppressed``, where the
       forbidden rate is better on F006 and the deferral does not reach),
       names **exactly** that row, on the gate's own predicate;
    3. **green again** -- that same copy with that row *lowered* by one (an
       improvement, the tolerated direction) names none, so the predicate is
       on the PRIN-14 direction and not on any difference.

    Then the two states the exception itself needs:

    4. **the exception does not leak** -- raising a row that *is* inside the
       exception's shape leaves the gate green (that is what the deferral
       means) while the raw comparison sees it, so the two predicates are
       genuinely different and the gate is not simply ignoring everything;
    5. **and it cannot absorb a new regression silently** -- that same
       raised row moves the exception's **row count** off its pin, which is
       what ``test_the_deferred_forbidden_rate_exception_is_exactly_the_rows_it_names``
       reds on. State 4 without state 5 would be a hole.

    And, because the clamp is where the first green comes from, the real rows
    are asserted to still contain the deferred 64 -- if a future run makes the
    raw comparison empty, the clamp step would silently become the only thing
    under test, and the exception should be retired rather than left standing.
    """
    rows = _rows()
    real = worsened(rows)
    assert real, (
        "the measured rows name no regression at all, so the clamped copy below is no longer a "
        "distinct state from them AND the deferred exception no longer has anything to except: "
        "re-point state 1 of this pin at the real rows, delete the clamp, and delete the exception"
    )
    assert not unexcused(rows), "the gate itself is red; see its own assertion"
    print(f"state 0 (rows as measured): {len(real)} worsened rows, {len(deferred(rows))} of them "
          f"the deferred exception, {len(unexcused(rows))} unexcused -- the gate is GREEN. "
          f"First three worsened: {real[:3]}")

    clamped = []
    for row in rows:
        copy = dict(row)
        if copy["gated"] == "1":
            copy["f006"] = copy["f005"]
            copy["worse"] = "0"
        clamped.append(copy)
    assert not worsened(clamped), "state 1: a build equal to F005 on every gated cell must be green"
    assert not deferred(clamped), "state 1: nothing is worse, so nothing is excepted either"
    print("state 1 (every gated row clamped to f006 = f005): 0 regressions, 0 excepted")

    victim_index = next(
        i for i, row in enumerate(clamped)
        if row["gated"] == "1" and row["scope"] == "total" and row["metric"] == "forbidden"
        and row["sweep"] == "rect" and row["overlap"] == "suppressed"
    )
    victim = clamped[victim_index]
    assert not is_deferred_exception(victim), (
        "state 2's victim is inside the deferred exception, so the perturbation would be excused "
        "and this pin would prove nothing about the gate"
    )

    worse_copy = [dict(row) for row in clamped]
    worse_copy[victim_index]["f006"] = str(float(victim["f005"]) + 1)
    found = unexcused(worse_copy)
    print(f"state 2 (that copy, {victim['sweep']}/{victim['overlap']}/{victim['metric']} raised "
          f"{victim['f005']} -> {worse_copy[victim_index]['f006']}): {len(found)} regressions: {found}")
    assert len(found) == 1, f"state 2 named {len(found)} regressions, expected exactly 1: {found[:10]}"
    assert victim["metric"] in found[0] and victim["overlap"] in found[0], found

    better_copy = [dict(row) for row in clamped]
    better_copy[victim_index]["f006"] = str(max(0.0, float(victim["f005"]) - 1))
    assert not unexcused(better_copy), (
        "state 3: a rate that IMPROVES was reported as a regression -- the gate is on the PRIN-14 "
        "direction, not on any difference"
    )
    print("state 3 (that copy, the same rate LOWERED by one): 0 regressions")

    excepted_index = next(
        i for i, row in enumerate(clamped)
        if row["gated"] == "1" and row["scope"] == "total" and row["metric"] == "forbidden"
        and row["sweep"] == "rect" and row["overlap"] == "healthy"
    )
    excepted_victim = clamped[excepted_index]
    assert is_deferred_exception(excepted_victim), excepted_victim

    inside_copy = [dict(row) for row in clamped]
    inside_copy[excepted_index]["f006"] = str(float(excepted_victim["f005"]) + 1)
    assert worsened(inside_copy), "state 4: the raw PRIN-14 comparison must still see the excepted row"
    assert not unexcused(inside_copy), (
        "state 4: a worsened row inside the exception's shape reached the gate, so the deferral is "
        "not actually partitioned out"
    )
    print(f"state 4 (that copy, {excepted_victim['sweep']}/{excepted_victim['overlap']}/"
          f"{excepted_victim['metric']} raised by one): {len(worsened(inside_copy))} worsened, "
          f"0 unexcused -- the exception holds")

    assert len(deferred(inside_copy)) == 1 != DEFERRED_EXCEPTION_ROWS, (
        "state 5: raising a row inside the exception did not move the exception's own row count, "
        "so the exception could absorb a new regression without any pin noticing"
    )
    print(f"state 5 (the same copy): the exception's row count is {len(deferred(inside_copy))}, "
          f"not its pinned {DEFERRED_EXCEPTION_ROWS} -- the exception pin would red")


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
