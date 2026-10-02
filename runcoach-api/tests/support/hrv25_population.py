"""T240 -- count HRV-25's population from T162's per-row CSVs (F009 AC7, IDEA-099).

The population (research/00 HRV-25; the third exception research/00 PRIN-15 names; its count
research/00 PRIN-26 states): the selected per-tier dataset serves ``hrv_normal`` while another
reported dataset, judgeable or not, reads below its own SWC band. In the harness's per-row record
(``spec/references/T130-overlap-sweep-harness.py``, ``describe_t162``) that is ``verdict ==
"hrv_normal"`` with ``dissent_below`` non-empty: ``dissent_below`` lists the tiers whose own
``read_against_band`` is below while the selected dataset's is not, judgeable or not, and
``ac22_below`` is the same predicate totalled per cell. ``dissent_below_judgeable`` is the
judgeable subset, reported beside it.

The overlap with T162's ``forbidden`` family (the IDEA-087 exceptions): a row is ``forbidden``
when the athlete's own return is suppressed and ``hrv_normal`` is promoted from the carrier's
week or a stale band. A population row that is also ``forbidden`` would be counted by two IDEAs;
``forbidden_overlap`` is how many are.

Reads one rowdir, the directory ``t162-gate --rowdir`` wrote (T239: ``<data>/research/T239-rowdir``,
16 files named ``t162-<sweep>-<module>-<overlap>.csv``), streaming each file once; nothing is
loaded whole. The harness is not imported and not edited (sprint-009 D15).

Loaded by ``test_hrv25_population.py`` by file path. Run it to print the counts::

    uv run --package runcoach-api python runcoach-api/tests/support/hrv25_population.py [data-dir]

The data dir is the argument, else ``$SHIPYARD_DATA_DIR``, else ``<repo>/.shipyard``.
"""

import csv
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

#: Under the Shipyard data dir.
ROWDIR = ("research", "T239-rowdir")

#: ``t162-<sweep>-<module>-<overlap>.csv``.
_NAME = re.compile(r"^t162-(?P<sweep>[a-z]+)-(?P<module>f00[56])-(?P<overlap>healthy|suppressed)\.csv$")

NORMAL = "hrv_normal"


@dataclass
class Count:
    sweep: str
    overlap: str
    module: str
    path: Path
    rows: int = 0
    normal: int = 0
    population: int = 0
    population_judgeable: int = 0
    forbidden: int = 0
    forbidden_overlap: int = 0


def count_file(path: Path, sweep: str, overlap: str, module: str) -> Count:
    c = Count(sweep, overlap, module, path)
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            c.rows += 1
            if row["module"] != module:
                raise ValueError(f"{path}: row {c.rows} has module {row['module']!r}, file says {module!r}")
            forbidden = row["forbidden"] == "True"
            c.forbidden += forbidden
            if row["verdict"] != NORMAL:
                continue
            c.normal += 1
            if row["dissent_below"]:
                c.population += 1
                c.forbidden_overlap += forbidden
                c.population_judgeable += bool(row["dissent_below_judgeable"])
    return c


def count_rowdir(rowdir: Path, module: str = "f006") -> dict[tuple[str, str], Count]:
    """``(sweep, overlap) -> Count`` over the rowdir's files for ``module``."""
    counts: dict[tuple[str, str], Count] = {}
    for path in sorted(Path(rowdir).iterdir()):
        m = _NAME.match(path.name)
        if not m or m.group("module") != module:
            continue
        counts[(m.group("sweep"), m.group("overlap"))] = count_file(path, m.group("sweep"), m.group("overlap"), module)
    if not counts:
        raise FileNotFoundError(f"{rowdir}: no t162-*-{module}-*.csv file")
    return counts


def _data_dir(argv: list[str]) -> Path:
    if len(argv) > 1:
        return Path(argv[1])
    named = os.environ.get("SHIPYARD_DATA_DIR")
    if named:
        return Path(named)
    return Path(__file__).resolve().parents[3] / ".shipyard"


def main(argv: list[str]) -> int:
    rowdir = _data_dir(argv).joinpath(*ROWDIR)
    print(f"rowdir: {rowdir}")
    for module in ("f006", "f005"):
        counts = count_rowdir(rowdir, module=module)
        print(f"\n## {module}: {len(counts)} files")
        print("| sweep | overlap | rows | hrv_normal | population (dissent_below) | judgeable dissenter | forbidden rows | forbidden overlap |")
        print("|---|---|---|---|---|---|---|---|")
        for (sweep, overlap), c in sorted(counts.items()):
            print(f"| {sweep} | {overlap} | {c.rows} | {c.normal} | {c.population} | {c.population_judgeable} | {c.forbidden} | {c.forbidden_overlap} |")
        print("\nPINNED literals for test_hrv25_population.py (" + module + "):")
        for (sweep, overlap), c in sorted(counts.items()):
            print(f'    ("{sweep}", "{overlap}"): ({c.population}, {c.forbidden_overlap}),')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
