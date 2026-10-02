"""T240 -- HRV-25's population, counted from T239's per-row CSVs and pinned so it cannot grow.

**What the population is.** research/00 HRV-25: the selected per-tier dataset serves ``hrv_normal``
while another reported dataset, judgeable or not, reads below its own SWC band. research/00 PRIN-15
names it as the third exception to PRIN-14's forbidden direction (owned by IDEA-099) and research/00 PRIN-26
states its count. On T162's per-row records that is the ``dissent_below`` column non-empty on a
row whose ``verdict`` is ``hrv_normal`` (the harness's ``ac22_below`` predicate, which the
committed rate rows total per scope). It is counted here from the per-row CSVs the T239
re-measurement wrote with ``--rowdir`` (``<data>/research/T239-rowdir/``, 16 files, one per
``(sweep, module, overlap)``), never by adding a metric to the harness: the rate rows stay
byte-identical (sprint-009 D15).

**What is pinned.** For the shipped module (``f006``), per scope ``(sweep, overlap)``: the
population and how many of its rows are also ``forbidden`` (T162's family, the IDEA-087
exceptions), so that no row is counted twice between the two IDEAs. The pin is exact: a count that
grows reds, and so does one that shrinks, because a shrink is a change in what ships and is
re-pinned by whoever moved it.

**Cross-check.** Each population count must equal the committed ``ac22_below`` total (the
``f006`` column) of the same scope in ``tests/data/T162-no-regression-rows.csv``, the rows the
no-regression gate reads; the walk's metric is ``walk_ac22_below``. A per-row count that does not
reproduce the rate row is a counting bug, not a finding.

**Fails loud, never skips.** The rowdir lives in the Shipyard data dir, which a worktree or a
runner reaches only through ``SHIPYARD_DATA_DIR``. Where it is unreachable this test fails: a
pin that cannot read its population has not pinned it.

The counter is ``tests/support/hrv25_population.py``, loaded by file path (the workspace runs
``--import-mode=importlib``, under which nothing in ``tests/`` is importable by name).
"""

from __future__ import annotations

import csv
import importlib.util
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SUPPORT = Path(__file__).resolve().parent / "support" / "hrv25_population.py"
TREE_ROWS = _REPO_ROOT / "runcoach-api" / "tests" / "data" / "T162-no-regression-rows.csv"

#: Named where the ``.shipyard`` breadcrumb is absent -- a worktree, a runner.
DATA_DIR_ENV = "SHIPYARD_DATA_DIR"

#: The shipped module's column value in the rowdir.
MODULE = "f006"

#: ``(sweep, overlap) -> (population, forbidden overlap)`` on the ``f006`` rowdir, pasted from the
#: ``PINNED literals`` block ``support/hrv25_population.py`` printed on 2026-10-01 over T239's
#: rowdir (written 2026-10-02 UTC). ``None`` in either cell is the red: no count has been produced.
#: The overlap is large and is the finding IDEA-099 records: 17,077 of the 24,099 healthy-overlap
#: rectangle rows are also ``forbidden`` (the IDEA-087 family), 2,901 of 9,923 suppressed; the
#: switch population is wholly inside it; the two IDEAs' counts are not additive.
PINNED = {
    ("rect", "healthy"): (24099, 17077),
    ("rect", "suppressed"): (9923, 2901),
    ("inter", "healthy"): (3315, 1275),
    ("inter", "suppressed"): (3315, 1275),
    ("switch", "healthy"): (126, 126),
    ("switch", "suppressed"): (126, 126),
    ("walk", "healthy"): (1494, 1078),
    ("walk", "suppressed"): (494, 78),
}

#: Rows per scope file, so a truncated or re-run rowdir reds before any count is compared.
ROWS = {"rect": 307500, "inter": 5100, "switch": 1400, "walk": 24000}


def _load_counter():
    spec = importlib.util.spec_from_file_location("hrv25_population", _SUPPORT)
    assert spec is not None and spec.loader is not None, f"cannot load {_SUPPORT}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve the module's annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def _data_dir() -> Path | None:
    """``$SHIPYARD_DATA_DIR`` when set, else the ``.shipyard`` breadcrumb. Unlike the no-regression
    gate's helper, a set variable is authoritative: a named dir that is not a data dir fails this
    test rather than falling back to the checkout's junction, so a runner's misnamed dir is seen."""
    named = os.environ.get(DATA_DIR_ENV)
    candidate = Path(named) if named else _REPO_ROOT / ".shipyard"
    return candidate if (candidate / "spec" / "features").is_dir() else None


def _committed_ac22_below_totals() -> dict[tuple[str, str], int]:
    """The ``f006`` column of each scope's ``total`` row for ``ac22_below`` (``walk_ac22_below`` on
    the walk) in the committed rate rows."""
    totals: dict[tuple[str, str], int] = {}
    with TREE_ROWS.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["scope"] != "total" or row["metric"] not in ("ac22_below", "walk_ac22_below"):
                continue
            totals[(row["sweep"], row["overlap"])] = int(row["f006"])
    return totals


def test_hrv_25_population_is_counted_and_does_not_grow() -> None:
    counter = _load_counter()
    data_dir = _data_dir()
    if data_dir is None:
        named = os.environ.get(DATA_DIR_ENV)
        where = f"${DATA_DIR_ENV}={named}" if named else f"{_REPO_ROOT / '.shipyard'} (${DATA_DIR_ENV} unset)"
        pytest.fail(
            f"the Shipyard data dir is unreachable: {where} names no directory with spec/features "
            f"under it, so HRV-25's population cannot be counted. This is a failure, not a skip: a "
            f"pin that cannot read its population has not pinned it."
        )
    rowdir = data_dir.joinpath(*counter.ROWDIR)
    if not rowdir.is_dir():
        pytest.fail(f"{rowdir} does not exist: re-run t162-gate with --rowdir {rowdir} (T239)")

    counts = counter.count_rowdir(rowdir, module=MODULE)
    print(f"[slice compared] rowdir={rowdir} module={MODULE} files={len(counts)} "
          + " ".join(f"{s}/{o}={c.rows}" for (s, o), c in sorted(counts.items())))
    assert set(counts) == set(PINNED), sorted(counts)
    for (sweep, overlap), c in sorted(counts.items()):
        print(f"[hrv-25 {sweep}/{overlap}] population {c.population} of {c.rows} rows "
              f"({c.normal} hrv_normal), forbidden overlap {c.forbidden_overlap}, "
              f"judgeable dissenter {c.population_judgeable}")
        assert c.rows == ROWS[sweep], (sweep, overlap, c.rows)

    committed = _committed_ac22_below_totals()
    for key, c in sorted(counts.items()):
        assert c.population == committed[key], (
            f"{key}: the per-row count {c.population} does not reproduce the committed ac22_below "
            f"total {committed[key]}; the count is wrong, not the rows"
        )

    for key, c in sorted(counts.items()):
        pinned_population, pinned_overlap = PINNED[key]
        assert pinned_population is not None and pinned_overlap is not None, (
            f"{key}: no count is pinned yet; run support/hrv25_population.py and record its output"
        )
        assert c.population == pinned_population, (
            f"{key}: HRV-25's population is {c.population}, pinned at {pinned_population}: it "
            f"{'grew' if c.population > pinned_population else 'shrank'} (research/00 HRV-25, "
            f"PRIN-15: the exception may not grow; IDEA-099 owns it)"
        )
        assert c.forbidden_overlap == pinned_overlap, (
            f"{key}: {c.forbidden_overlap} rows of HRV-25's population are also forbidden (PRIN-14), pinned "
            f"at {pinned_overlap}: the two IDEA-087 exceptions and IDEA-099's now overlap "
            f"differently"
        )
