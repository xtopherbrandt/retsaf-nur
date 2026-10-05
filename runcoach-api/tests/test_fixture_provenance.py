"""F014 AC1: the fixture provenance table, checked against the decoded files.

``tests/fixtures/README.md`` holds one row per ``*.fit`` file in the fixture
directory: what the file really is (``kind``), its qualifiers (``notes``), its
recording ``mode``, the ``devices`` that wrote it, whether it carries
``positions``, and whether it may be cited as ``run proof``. The rows were
confirmed by the user on 2026-10-04 and live in the F014 reference
(``spec/references/F014-fixture-provenance.md`` section 1, Shipyard data dir).

This module is the guard that keeps the table honest:

- the directory and the table name the same files (a file without a row, or
  a row naming a missing file, fails and names it);
- ``kind`` and ``run proof`` stay inside their closed sets;
- ``positions`` and ``devices`` are re-derived from the decoded file with
  fitdecode, using the reference's definitions, and a row that disagrees
  fails and names the file.

Definitions, from the reference:

- a position value is any field named ``*_lat``/``*_long`` in any message, or
  lap fields 27-30 (the lap bounding box, which fitdecode leaves unnamed);
  the invalid sentinel decodes to ``None`` and is not a value;
- ``devices`` is the creator (``file_id`` manufacturer and product) plus each
  ANT+ ``device_info`` entry whose manufacturer is not ``None``. The watch's
  internal entries (products 3709, 3799, gnss, the sensor hub) are local, so
  they fall outside it.

The display names come from ``DEVICE_NAMES`` below; a decoded device the map
does not know fails the test and names the key, so a new sensor is a finding,
never a silent pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path

import fitdecode
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
README = FIXTURES / "README.md"

COLUMNS = ("file", "kind", "notes", "mode", "devices", "positions", "run proof")
KINDS = frozenset({"real run", "real walk", "HRV capture", "health snapshot"})
RUN_PROOF = frozenset({"yes", "walk only", "no"})
POSITIONS = frozenset({"yes", "no"})
LAP_BOUNDING_BOX_FIELDS = frozenset({27, 28, 29, 30})
RUN_PROOF_RULE = "cite-only-real-activity-fixtures-as-proof.md"

#: (manufacturer, product) as fitdecode decodes them -> the table's display name.
DEVICE_NAMES: dict[tuple[str, object], str] = {
    ("garmin", "fr945_lte"): "FR945 LTE",
    ("garmin", "fr955"): "FR955",
    ("garmin", "hrm_pro_plus"): "HRM-Pro Plus",
    ("garmin", 21): "Garmin product 21",
    ("polar_electro", 2): "Polar HR strap",
    ("stryd", 4660): "Stryd",
    ("dynastream_oem", "axh01"): "Dynastream OEM axh01 HR",
}


@dataclass(frozen=True)
class Row:
    file: str
    kind: str
    notes: str
    mode: str
    devices: str
    positions: str
    run_proof: str


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_table(text: str) -> list[Row]:
    """The rows of the one table whose header is ``COLUMNS``, in file order."""
    lines = text.splitlines()
    for at, line in enumerate(lines):
        if line.lstrip().startswith("|") and tuple(_cells(line)) == COLUMNS:
            break
    else:
        raise AssertionError(f"{README.name} has no table with the header {COLUMNS}")
    rows = []
    for line in lines[at + 2:]:
        if not line.lstrip().startswith("|"):
            break
        cells = _cells(line)
        assert len(cells) == len(COLUMNS), f"{README.name}: row has {len(cells)} cells: {line!r}"
        rows.append(Row(*cells))
    return rows


def _readme_text() -> str:
    assert README.is_file(), f"{README} does not exist: every fixture lacks a provenance row"
    return README.read_text(encoding="utf-8")


def _rows_by_file() -> dict[str, Row]:
    rows = parse_table(_readme_text())
    names = [row.file for row in rows]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert not duplicates, f"provenance rows named more than once: {duplicates}"
    return {row.file: row for row in rows}


def _on_disk() -> list[str]:
    return sorted(p.stem for p in FIXTURES.glob("*.fit"))


@dataclass(frozen=True)
class Decoded:
    position_fields: tuple[str, ...]
    creator: tuple[str, object]
    sensors: frozenset[tuple[str, object]]


@cache
def decode(stem: str) -> Decoded:
    position_fields: set[str] = set()
    creator = None
    sensors: set[tuple[str, object]] = set()
    with fitdecode.FitReader(str(FIXTURES / f"{stem}.fit")) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            for field in frame.fields:
                if field.value is None:
                    continue
                name = field.name or ""
                if name.endswith(("_lat", "_long")):
                    position_fields.add(f"{frame.name}.{name}")
                elif frame.name == "lap" and field.def_num in LAP_BOUNDING_BOX_FIELDS:
                    position_fields.add(f"lap.{field.def_num}")
            if frame.name == "file_id" and creator is None:
                creator = (frame.get_value("manufacturer", fallback=None),
                           frame.get_value("product", fallback=None))
            elif frame.name == "device_info":
                manufacturer = frame.get_value("manufacturer", fallback=None)
                source = frame.get_value("source_type", fallback=None)
                if source == "antplus" and manufacturer is not None:
                    sensors.add((manufacturer, frame.get_value("product", fallback=None)))
    assert creator is not None, f"{stem}.fit: no file_id message"
    return Decoded(tuple(sorted(position_fields)), creator, frozenset(sensors))


def _display(stem: str, key: tuple[str, object]) -> str:
    assert key in DEVICE_NAMES, (
        f"{stem}.fit: decoded device {key!r} has no display name in DEVICE_NAMES")
    return DEVICE_NAMES[key]


def derived_devices(stem: str) -> tuple[str, frozenset[str]]:
    decoded = decode(stem)
    return (_display(stem, decoded.creator),
            frozenset(_display(stem, key) for key in decoded.sensors))


def parse_devices(stem: str, cell: str) -> tuple[str, frozenset[str]]:
    creator, sep, rest = cell.partition("; ")
    assert sep, f"{stem}: devices cell {cell!r} is not 'creator; sensors'"
    if rest == "none":
        return creator, frozenset()
    sensors = [s.strip() for s in rest.split(",")]
    assert len(sensors) == len(set(sensors)), f"{stem}: devices cell {cell!r} repeats a sensor"
    return creator, frozenset(sensors)




def directory_mismatch(on_disk: list[str], rows: dict[str, Row]) -> str | None:
    """The guard's finding, naming each file, or ``None`` when they agree."""
    without_row = sorted(set(on_disk) - set(rows))
    missing_file = sorted(set(rows) - set(on_disk))
    if not without_row and not missing_file:
        return None
    return ("fixture directory and provenance table disagree: "
            f"files without a row {without_row}, rows whose file is missing {missing_file}")


def value_problems(row: Row) -> list[str]:
    """Closed-set violations in one row, each naming the file."""
    problems = []
    if row.kind not in KINDS:
        problems.append(f"{row.file}: kind {row.kind!r} is not one of {sorted(KINDS)}")
    if row.run_proof not in RUN_PROOF:
        problems.append(f"{row.file}: run proof {row.run_proof!r} is not one of {sorted(RUN_PROOF)}")
    if row.positions not in POSITIONS:
        problems.append(f"{row.file}: positions {row.positions!r} is not one of {sorted(POSITIONS)}")
    if not row.mode:
        problems.append(f"{row.file}: mode is empty")
    return problems


def decoded_problems(row: Row) -> list[str]:
    """Disagreements between the row and its decoded file, each naming the file."""
    stem = row.file
    problems = []
    decoded = decode(stem)
    has_positions = "yes" if decoded.position_fields else "no"
    print(f"[slice compared] {stem}: positions row {row.positions!r} decoded {has_positions!r} "
          f"from {list(decoded.position_fields)[:4]}")
    if row.positions != has_positions:
        problems.append(
            f"{stem}: positions says {row.positions!r} but the decoded file has "
            f"{len(decoded.position_fields)} position field(s) {list(decoded.position_fields)[:4]}")
    expected = derived_devices(stem)
    stated = parse_devices(stem, row.devices)
    print(f"[slice compared] {stem}: devices row {stated[0]}; {sorted(stated[1])} "
          f"decoded {expected[0]}; {sorted(expected[1])}")
    if stated != expected:
        problems.append(
            f"{stem}: devices says {row.devices!r} but the decoded creator and ANT+ sensors are "
            f"{expected[0]!r}; {sorted(expected[1]) or 'none'}")
    return problems


def test_every_fixture_has_a_provenance_row() -> None:
    """The directory-equals-table guard: a fixture added without a row would
    otherwise fall outside the table; a row whose file was removed would
    describe nothing. A collected test, so its failure is reported as one."""
    rows = _rows_by_file()
    on_disk = _on_disk()
    print(f"[slice compared] on disk {on_disk}; rows {sorted(rows)}")
    finding = directory_mismatch(on_disk, rows)
    assert finding is None, finding


@pytest.mark.parametrize("stem", _on_disk())
def test_provenance_row_matches_the_decoded_file(stem: str) -> None:
    rows = _rows_by_file()
    assert stem in rows, f"{stem}.fit has no provenance row in {README.name}"
    problems = value_problems(rows[stem])
    assert not problems, problems
    problems = decoded_problems(rows[stem])
    assert not problems, problems


def test_the_historical_wrist_ppg_name_carries_the_users_ruling() -> None:
    row = _rows_by_file()["wrist_ppg_run"]
    assert row.notes == "HR from the chest strap (user, 2026-10-04); the name is historical"


def test_the_readme_explains_run_proof_and_points_to_its_rule() -> None:
    text = _readme_text()
    prose = re.sub(r"\s+", " ", "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("|")))
    assert "run proof" in prose, "the README prose never explains run proof"
    assert RUN_PROOF_RULE in prose, f"the README does not point to the rule {RUN_PROOF_RULE}"


# The guard's failure branches, driven through the same check functions on a
# row set edited in memory, so a green suite shows each branch can fire.

def _real_rows() -> dict[str, Row]:
    return dict(_rows_by_file())


def test_a_fixture_without_a_row_is_named() -> None:
    rows = _real_rows()
    rows.pop("sample_run")
    finding = directory_mismatch(_on_disk(), rows)
    assert finding is not None and "files without a row ['sample_run']" in finding


def test_a_row_whose_file_is_missing_is_named() -> None:
    rows = _real_rows()
    rows["ghost_run"] = replace(rows["sample_run"], file="ghost_run")
    finding = directory_mismatch(_on_disk(), rows)
    assert finding is not None and "rows whose file is missing ['ghost_run']" in finding


@pytest.mark.parametrize(("column", "value"), [("kind", "run"), ("run_proof", "maybe")])
def test_an_out_of_set_value_is_named(column: str, value: str) -> None:
    row = replace(_real_rows()["sample_run"], **{column: value})
    problems = value_problems(row)
    assert len(problems) == 1 and problems[0].startswith("sample_run: ") and repr(value) in problems[0]


@pytest.mark.parametrize(
    ("stem", "column", "value"),
    [
        ("sample_run", "positions", "no"),
        ("hilly_run_8k_fr945", "positions", "yes"),
        ("strap_hrv_capture", "devices", "FR945 LTE; HRM-Pro Plus"),
        ("sample_health_snapshot", "devices", "FR945 LTE; HRM-Pro Plus"),
        ("dev_fields_run", "devices", "FR945 LTE; Polar HR strap, Stryd"),
    ],
)
def test_a_row_disagreeing_with_the_decoded_file_is_named(stem: str, column: str, value: str) -> None:
    row = replace(_real_rows()[stem], **{column: value})
    problems = decoded_problems(row)
    assert len(problems) == 1 and problems[0].startswith(f"{stem}: {column} says")
