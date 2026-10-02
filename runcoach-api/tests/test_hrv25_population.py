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

**Tied to the count research/00 publishes.** PRIN-26 states the count this test pins
(``00-design-decisions.md``: "24,099 of 307,500 healthy-overlap rectangle rows, 9,923 suppressed, 3,315
inter, 126 switch and 1,494 and 494 walk rows"). ``test_prin_26_states_the_pinned_population`` parses
those figures and requires them to equal ``PINNED`` and ``ROWS``, so the pin and the published count
cannot drift apart: re-pinning one without the other reds.

**What the pin ratchets on, and what it does not see.** The population is counted from the frozen
rowdir evidence (T239's per-row CSVs), not from the shipped module at test time, so a code change to
``metrics/hrv_trend.py`` does not move this count by itself. It reaches this pin through two links. The
T162 provenance pin (``tests/data/T162-no-regression-rows.provenance.json``), asserted by
``test_hrv_no_regression_gate.py``, reds on a changed module blob until the rows are re-measured. And
the rowdir carries a sidecar, ``measured-module.json``, naming the module blob it was measured against,
which ``test_the_rowdir_was_measured_against_the_module_the_provenance_pins`` requires to equal the
provenance's ``measured_module.blob_sha`` (S7). Nothing hands a re-measured rowdir to this test by
itself: whoever re-measures writes the rowdir in place at ``ROWDIR`` and rewrites the sidecar, and a
rowdir left behind by a re-measurement reds here instead of being counted as if it were current.

**Fails loud, never skips.** The rowdir lives in the Shipyard data dir, which a worktree or a
runner reaches only through ``SHIPYARD_DATA_DIR``. Where it is unreachable this test fails: a
pin that cannot read its population has not pinned it.

The counter is ``tests/support/hrv25_population.py``, loaded by file path (the workspace runs
``--import-mode=importlib``, under which nothing in ``tests/`` is importable by name).
"""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SUPPORT = Path(__file__).resolve().parent / "support" / "hrv25_population.py"
TREE_ROWS = _REPO_ROOT / "runcoach-api" / "tests" / "data" / "T162-no-regression-rows.csv"
ROWS_PROVENANCE = _REPO_ROOT / "runcoach-api" / "tests" / "data" / "T162-no-regression-rows.provenance.json"
#: The rowdir's own record of the module it was measured against (S7), written beside the per-row CSVs by
#: whoever re-measures: ``{"blob_sha": <git blob sha1 of metrics/hrv_trend.py>, ...}``.
SIDECAR = "measured-module.json"

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

#: research/00, whose PRIN-26 publishes the pinned count (S4).
RESEARCH_00 = _REPO_ROOT / "specification" / "research" / "00-design-decisions.md"
_PRIN_26_COUNT = re.compile(
    r"^\*\*PRIN-26\.\*\*.*? pins at (?P<rect>[\d,]+) of (?P<rect_rows>[\d,]+) healthy-overlap rectangle rows, "
    r"(?P<rect_suppressed>[\d,]+) suppressed, (?P<inter>[\d,]+) inter, (?P<switch>[\d,]+) switch and "
    r"(?P<walk_healthy>[\d,]+) and (?P<walk_suppressed>[\d,]+) walk rows\.", re.MULTILINE)


def prin26_population(research_00: str) -> dict[tuple[str, str], int]:
    """The population PRIN-26 states, keyed like ``PINNED`` (inter and switch are one figure each, the same
    for both overlaps, as the pin holds them), plus ``("rect", "rows")`` for the rectangle's size. A PRIN-26
    line whose sentence does not parse is an assertion error: a pin tied to text it cannot read is untied."""
    match = _PRIN_26_COUNT.search(research_00)
    assert match, "research/00 PRIN-26 no longer states the count in the form this pin parses"
    n = {name: int(value.replace(",", "")) for name, value in match.groupdict().items()}
    return {
        ("rect", "healthy"): n["rect"], ("rect", "suppressed"): n["rect_suppressed"],
        ("inter", "healthy"): n["inter"], ("inter", "suppressed"): n["inter"],
        ("switch", "healthy"): n["switch"], ("switch", "suppressed"): n["switch"],
        ("walk", "healthy"): n["walk_healthy"], ("walk", "suppressed"): n["walk_suppressed"],
        ("rect", "rows"): n["rect_rows"],
    }


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


def test_prin_26_states_the_pinned_population() -> None:
    """S4: the figures PRIN-26 publishes are ``PINNED``'s populations and ``ROWS``' rectangle size."""
    stated = prin26_population(RESEARCH_00.read_text(encoding="utf-8"))
    pinned = {key: population for key, (population, _overlap) in PINNED.items()}
    print(f"[slice compared] PRIN-26 states {sorted(stated.items())}; PINNED {sorted(pinned.items())}; "
          f"rect rows {ROWS['rect']}")
    assert stated == {**pinned, ("rect", "rows"): ROWS["rect"]}


def test_a_prin_26_count_that_differs_from_the_pin_is_seen() -> None:
    """S4's perturbation: one figure changed in PRIN-26's text (24,099 to 24,100) no longer equals the
    pin, and a PRIN-26 line whose sentence no longer parses is a failure, not an empty match."""
    text = RESEARCH_00.read_text(encoding="utf-8")
    moved = prin26_population(text.replace("pins at 24,099 of", "pins at 24,100 of"))
    print(f"[slice compared] perturbed rect/healthy {moved[('rect', 'healthy')]} against {PINNED[('rect', 'healthy')][0]}")
    assert moved[("rect", "healthy")] == 24100 != PINNED[("rect", "healthy")][0]
    with pytest.raises(AssertionError):
        prin26_population(text.replace("healthy-overlap rectangle rows", "rectangle rows"))


def rowdir_blob_errors(rowdir: Path, provenance_blob: str) -> list[str]:
    """S7: the rowdir's sidecar must exist and name the module blob the T162 provenance pins. The provenance
    blob is the checkout's module (``test_hrv_no_regression_gate.py`` asserts it), so equality here is what
    ties the counted rows to the code that ships; a missing or other blob is a finding, never a skip."""
    sidecar = Path(rowdir) / SIDECAR
    if not sidecar.is_file():
        return [(f"{sidecar} is missing: the rowdir does not record the module it was measured against; "
                 f"write it at every T162 re-measurement (blob_sha of metrics/hrv_trend.py)")]
    recorded = json.loads(sidecar.read_text(encoding="utf-8")).get("blob_sha")
    if recorded != provenance_blob:
        return [(f"{sidecar} records module blob {recorded!r} and the T162 provenance pins {provenance_blob!r}: "
                 f"the rowdir this test counts is not the measurement the no-regression gate holds; re-run "
                 f"t162-gate with --rowdir into it and rewrite the sidecar")]
    return []


def test_the_rowdir_was_measured_against_the_module_the_provenance_pins() -> None:
    """S7: the counted rowdir's sidecar names the module blob ``measured_module.blob_sha`` records."""
    data_dir = _data_dir()
    assert data_dir is not None, f"the Shipyard data dir is unreachable (${DATA_DIR_ENV}): a failure, not a skip"
    rowdir = data_dir.joinpath(*_load_counter().ROWDIR)
    provenance_blob = json.loads(ROWS_PROVENANCE.read_text(encoding="utf-8"))["measured_module"]["blob_sha"]
    sidecar = rowdir / SIDECAR
    recorded = json.loads(sidecar.read_text(encoding="utf-8")).get("blob_sha") if sidecar.is_file() else None
    errors = rowdir_blob_errors(rowdir, provenance_blob)
    print(f"[slice compared] {sidecar} blob {recorded!r} against provenance measured_module.blob_sha "
          f"{provenance_blob!r}; errors {errors}")
    assert errors == []


def test_a_rowdir_with_no_sidecar_or_another_modules_blob_is_seen(tmp_path) -> None:
    """S7's perturbation: no sidecar, and a sidecar naming another blob, are each one error; the pinned blob
    is none."""
    pinned, other = "4" * 40, "d" * 40
    missing = rowdir_blob_errors(tmp_path, pinned)
    (tmp_path / SIDECAR).write_text(json.dumps({"blob_sha": other}), encoding="utf-8")
    moved = rowdir_blob_errors(tmp_path, pinned)
    (tmp_path / SIDECAR).write_text(json.dumps({"blob_sha": pinned}), encoding="utf-8")
    same = rowdir_blob_errors(tmp_path, pinned)
    print(f"[slice compared] missing {missing}; other blob {moved}; pinned blob {same}")
    assert len(missing) == 1 and "is missing" in missing[0]
    assert len(moved) == 1 and other in moved[0] and pinned in moved[0]
    assert same == []
