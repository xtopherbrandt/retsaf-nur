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
- ``kind``, ``mode`` and ``run proof`` equal ``REFERENCE_ROWS`` below, a
  literal copy of the reference section 1 rows kept in this file, so an edit
  to the README alone (calling the resting sample a run, say) fails;
- ``positions`` and ``devices`` are re-derived from the decoded file with
  fitdecode, using the reference's definitions, and a row that disagrees
  fails and names the file;
- a fixture whose decoded file carries positions must be on ``POSITIONS_ALLOWED``, the files
  committed with them; any other fails, pointing at the stripper, until the user rules on it;
- ``mode`` is re-derived too, as far as the decoded file can say: the
  recording interval (``1 Hz`` when more than half the record steps are 1 s,
  ``6 s steps`` when more than half are 6 s; a file with no majority step is
  ``variable steps``, and a majority step other than 1 s or 6 s is named
  ``<n> s steps``; no README cell uses either, so the row fails) and the length of each
  pause, a record gap over 5 s (over the step plus 5 s in a stepped file;
  over 5 s in a ``variable steps`` file too) that opens on a timer stop and
  closes on a timer start. Such a gap with no
  timer pair would be a moving dropout, which the cell has no word for, so
  it fails. The qualifier word ``pause`` says neither manual nor auto and is
  not derived: every timer event in the corpus decodes with
  ``timer_trigger`` ``manual``.

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
import sys
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import cache
from itertools import pairwise
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

#: file -> (kind, mode, run proof), copied from the F014 reference section 1 rows (Shipyard data
#: dir), never from the README. The README must agree with it; a change to either is a change to
#: both, made on purpose.
REFERENCE_ROWS: dict[str, tuple[str, str, str]] = {
    "sample_run": ("real run", "1 Hz", "yes"),
    "dev_fields_run": ("real run", "1 Hz", "yes"),
    "wrist_ppg_run": ("real run", "1 Hz, 81 s and 11 s pauses", "yes"),
    "strap_run_hrv": ("real run", "1 Hz, 229 s pause", "yes"),
    "hilly_run_8k_fr945": ("real run", "1 Hz, 82 s pause", "yes"),
    "hilly_long_run_17k_fr945": ("real run", "1 Hz", "yes"),
    "strap_cool_down_walk": ("real walk", "1 Hz", "walk only"),
    "strap_hrv_sample_run": ("HRV capture", "1 Hz", "no"),
    "strap_hrv_capture": ("HRV capture", "6 s steps", "no"),
    "wrist_ppg_hrv_snapshot": ("HRV capture", "6 s steps", "no"),
    "strap_health_snapshot": ("health snapshot", "1 Hz", "no"),
    "strap_health_snapshot_hrv": ("health snapshot", "1 Hz", "no"),
    "sample_health_snapshot": ("health snapshot", "1 Hz", "no"),
}

#: The fixtures that may carry position values: the seven files committed with them before the
#: F014 stripping, read from the decoded files. Every other fixture is stripped with ``STRIPPER``
#: before it is committed; a new file joins this list only after the user rules that its track may
#: be published. The list is exact both ways: a listed file that decodes without positions fails.
POSITIONS_ALLOWED = frozenset({
    "sample_run",
    "dev_fields_run",
    "wrist_ppg_run",
    "strap_run_hrv",
    "strap_cool_down_walk",
    "strap_hrv_sample_run",
    "wrist_ppg_hrv_snapshot",
})
STRIPPER = "tests/support/strip_fit_positions.py"

#: The record step that names each recording interval, and the gap above which a 1 Hz step is a
#: pause or a dropout (a stepped file adds its step). A file whose steps have no majority is named
#: ``VARIABLE_STEPS``, which no mode cell uses.
INTERVALS = {1.0: "1 Hz", 6.0: "6 s steps"}
VARIABLE_STEPS = "variable steps"
PAUSE_MIN_GAP_S = 5.0


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


def _rows_by_file(text: str | None = None) -> dict[str, Row]:
    rows = parse_table(_readme_text() if text is None else text)
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
    interval: str
    pauses: tuple[int, ...]
    dropouts: tuple[int, ...]


def _mode_from(timestamps: list, timer_events: list[tuple[object, str]]) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    """``(interval, pauses, dropouts)`` from record timestamps and timer events, in time order.

    The interval names the record step that more than half of the steps share (``INTERVALS``).
    When no step has that majority the interval is ``VARIABLE_STEPS``, which no mode cell uses, so
    a file of mixed steps (smart recording) cannot read as 1 Hz. A gap is a record step over
    ``PAUSE_MIN_GAP_S`` in a 1 Hz or variable-step file, and over the step plus ``PAUSE_MIN_GAP_S``
    in a stepped file. Each gap is a pause when a timer ``stop`` or ``stop_all`` sits at its first record and a
    timer ``start`` at its last, and a dropout otherwise.
    """
    steps = [(b - a).total_seconds() for a, b in pairwise(timestamps)]
    common, count = Counter(steps).most_common(1)[0]
    if 2 * count <= len(steps):
        interval, gap_limit = VARIABLE_STEPS, PAUSE_MIN_GAP_S
    else:
        interval = INTERVALS.get(common, f"{common:g} s steps")
        gap_limit = PAUSE_MIN_GAP_S if interval == "1 Hz" else common + PAUSE_MIN_GAP_S
    stops = {t for t, kind in timer_events if kind in ("stop", "stop_all")}
    starts = {t for t, kind in timer_events if kind == "start"}
    pauses, dropouts = [], []
    for a, b in pairwise(timestamps):
        gap = (b - a).total_seconds()
        if gap > gap_limit:
            (pauses if a in stops and b in starts else dropouts).append(round(gap))
    return interval, tuple(pauses), tuple(dropouts)


@cache
def decode(stem: str) -> Decoded:
    position_fields: set[str] = set()
    creator = None
    sensors: set[tuple[str, object]] = set()
    timestamps: list = []
    timer_events: list[tuple[object, str]] = []
    with fitdecode.FitReader(str(FIXTURES / f"{stem}.fit")) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "record":
                timestamp = frame.get_value("timestamp", fallback=None)
                if timestamp is not None:
                    timestamps.append(timestamp)
            elif frame.name == "event" and frame.get_value("event", fallback=None) == "timer":
                timer_events.append((frame.get_value("timestamp", fallback=None),
                                     frame.get_value("event_type", fallback=None)))
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
    assert len(timestamps) >= 2, f"{stem}.fit: fewer than two timed records"
    interval, pauses, dropouts = _mode_from(timestamps, timer_events)
    return Decoded(tuple(sorted(position_fields)), creator, frozenset(sensors), interval, pauses, dropouts)


def parse_mode(cell: str) -> tuple[str, tuple[int, ...]]:
    """``(interval, pause lengths in s)`` from a mode cell such as ``1 Hz, 81 s and 11 s pauses``."""
    interval, _, rest = cell.partition(", ")
    return interval, tuple(int(n) for n in re.findall(r"(\d+) s\b", rest))


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


def reference_problems(row: Row) -> list[str]:
    """Disagreements between the row and ``REFERENCE_ROWS``, each naming the file and column."""
    if row.file not in REFERENCE_ROWS:
        return [f"{row.file}: no reference row in REFERENCE_ROWS"]
    expected = dict(zip(("kind", "mode", "run_proof"), REFERENCE_ROWS[row.file]))
    print(f"[slice compared] {row.file}: kind/mode/run proof row "
          f"{(row.kind, row.mode, row.run_proof)} reference {tuple(expected.values())}")
    return [f"{row.file}: {column} says {getattr(row, column)!r} but the reference row says {value!r}"
            for column, value in expected.items() if getattr(row, column) != value]


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
    stated_mode = parse_mode(row.mode)
    print(f"[slice compared] {stem}: mode row {stated_mode[0]!r} pauses {list(stated_mode[1])} "
          f"decoded {decoded.interval!r} pauses {list(decoded.pauses)} dropouts {list(decoded.dropouts)}")
    if decoded.dropouts or stated_mode != (decoded.interval, decoded.pauses):
        problems.append(
            f"{stem}: mode says {row.mode!r} but the decoded file has interval {decoded.interval!r}, "
            f"pauses {list(decoded.pauses)} s and gaps over {PAUSE_MIN_GAP_S:g} s with no timer pair "
            f"{list(decoded.dropouts)} s")
    return problems


def positions_problems(stem: str) -> list[str]:
    """A fixture outside ``POSITIONS_ALLOWED`` whose decoded file carries position values."""
    fields = decode(stem).position_fields
    print(f"[slice compared] {stem}: {len(fields)} position field(s), "
          f"on the allow-list {stem in POSITIONS_ALLOWED}")
    if not fields or stem in POSITIONS_ALLOWED:
        return []
    return [(f"{stem}.fit carries {len(fields)} position field(s) {list(fields)[:4]} and is not in "
             f"POSITIONS_ALLOWED: strip it with {STRIPPER} before committing it. Only the user can "
             "rule that its track may be published; add it to the list only after that ruling.")]


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
    problems = reference_problems(rows[stem])
    assert not problems, problems
    problems = decoded_problems(rows[stem])
    assert not problems, problems


@pytest.mark.parametrize("stem", _on_disk())
def test_a_fixture_with_positions_is_on_the_allow_list(stem: str) -> None:
    """A new fixture with a GPS track fails until it is stripped or the user rules it may stay."""
    problems = positions_problems(stem)
    assert not problems, problems


def test_the_positions_allow_list_names_only_fixtures_that_carry_positions() -> None:
    """The list stays exact: a listed file that is removed or stripped leaves the list too."""
    carrying = sorted(stem for stem in _on_disk() if decode(stem).position_fields)
    print(f"[slice compared] decoded with positions {carrying}; allowed {sorted(POSITIONS_ALLOWED)}")
    assert carrying == sorted(POSITIONS_ALLOWED)


def test_a_planted_fixture_with_positions_fails_and_points_at_the_stripper(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A byte copy of sample_run under a new name, in a scratch fixture dir, is refused."""
    (tmp_path / "planted_run.fit").write_bytes((FIXTURES / "sample_run.fit").read_bytes())
    monkeypatch.setattr(sys.modules[__name__], "FIXTURES", tmp_path)
    try:
        problems = positions_problems("planted_run")
    finally:
        decode.cache_clear()
    assert len(problems) == 1, problems
    assert problems[0].startswith("planted_run.fit carries ")
    assert STRIPPER in problems[0] and "user" in problems[0]


def test_the_readme_says_new_fixtures_are_stripped() -> None:
    prose = re.sub(r"\s+", " ", _readme_text())
    assert STRIPPER in prose and "unless the user rules otherwise" in prose, (
        f"the README does not say new fixtures are stripped with {STRIPPER}")


def test_the_reference_rows_name_every_fixture() -> None:
    """``REFERENCE_ROWS`` covers the directory, so no fixture escapes the literal check."""
    assert sorted(REFERENCE_ROWS) == _on_disk()


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


def test_a_file_named_by_two_rows_is_named() -> None:
    """A second row for one file would let the later row silently replace the earlier one."""
    text = _readme_text()
    row = next(line for line in text.splitlines() if line.startswith("| sample_run |"))
    with pytest.raises(AssertionError, match=r"provenance rows named more than once: \['sample_run'\]"):
        _rows_by_file(text.replace(row, f"{row}\n{row}", 1))


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


@pytest.mark.parametrize(
    ("stem", "value"),
    [
        ("wrist_ppg_run", "1 Hz, 81 s pause"),
        ("wrist_ppg_run", "1 Hz"),
        ("hilly_run_8k_fr945", "6 s steps"),
        ("hilly_run_8k_fr945", "1 Hz"),
        ("strap_hrv_capture", "1 Hz"),
        ("hilly_long_run_17k_fr945", "1 Hz, 30 s pause"),
    ],
)
def test_a_mode_disagreeing_with_the_decoded_file_is_named(stem: str, value: str) -> None:
    row = replace(_real_rows()[stem], mode=value)
    problems = decoded_problems(row)
    assert len(problems) == 1 and problems[0].startswith(f"{stem}: mode says")


def test_a_gap_with_no_timer_pair_is_a_dropout_not_a_pause() -> None:
    t0 = datetime(2026, 3, 1, tzinfo=UTC)
    stamps = [t0 + timedelta(seconds=s) for s in (0, 1, 2, 12, 13, 14)]
    assert _mode_from(stamps, []) == ("1 Hz", (), (10,))
    assert _mode_from(stamps, [(stamps[2], "stop_all"), (stamps[3], "start")]) == ("1 Hz", (10,), ())


def _stamps(*seconds: float) -> list[datetime]:
    t0 = datetime(2026, 3, 1, tzinfo=UTC)
    return [t0 + timedelta(seconds=s) for s in seconds]


@pytest.mark.parametrize(
    "timer_events",
    [
        pytest.param([(2, "stop_all")], id="stop-only"),
        pytest.param([(2, "stop")], id="plain-stop-only"),
        pytest.param([(12, "start")], id="start-only"),
        pytest.param([(12, "stop_all"), (2, "start")], id="pair-reversed"),
    ],
)
def test_a_gap_with_only_one_side_of_a_timer_pair_is_a_dropout(timer_events: list) -> None:
    """A pause needs both events: a stop at the gap's first record and a start at its last."""
    stamps = _stamps(0, 1, 2, 12, 13, 14)
    events = [(_stamps(at)[0], kind) for at, kind in timer_events]
    assert _mode_from(stamps, events) == ("1 Hz", (), (10,))


def test_a_decoded_dropout_fails_a_mode_cell_that_otherwise_agrees(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dropout branch of ``decoded_problems``: interval and pauses agree, a dropout alone fails."""
    real = decode("sample_run")
    assert real.dropouts == ()
    monkeypatch.setattr(sys.modules[__name__], "decode", lambda stem: replace(real, dropouts=(10,)))
    problems = decoded_problems(_real_rows()["sample_run"])
    assert len(problems) == 1 and problems[0].startswith("sample_run: mode says"), problems
    assert "[10] s" in problems[0]


@pytest.mark.parametrize(
    ("seconds", "interval"),
    [
        # Smart-like: 1 s is the commonest step but not a majority, and no gap is over 5 s.
        pytest.param((0, 1, 2, 3, 5, 8, 12, 13, 15, 18, 22), "variable steps", id="smart-like-mixed-1-to-5-s"),
        # Exactly half the steps are 1 s: half is not a majority.
        pytest.param((0, 1, 2, 4, 7), "variable steps", id="half-is-not-a-majority"),
        pytest.param((0, 1, 2, 3, 5), "1 Hz", id="three-of-four-is-a-majority"),
        pytest.param((0, 6, 12, 18, 20, 21), "6 s steps", id="six-s-majority"),
    ],
)
def test_the_interval_is_named_only_by_a_majority_of_the_steps(seconds: tuple, interval: str) -> None:
    assert _mode_from(_stamps(*seconds), [])[0] == interval


@pytest.mark.parametrize(
    ("seconds", "timer_events", "expected"),
    [
        pytest.param((0, 1, 2, 3, 5, 8, 12, 13, 15, 18, 22, 40), [], ("variable steps", (), (18,)),
                     id="18-s-gap-no-timer-pair-is-a-dropout"),
        pytest.param((0, 1, 2, 3, 5, 8, 12, 13, 15, 18, 22, 40), [(22, "stop_all"), (40, "start")],
                     ("variable steps", (18,), ()), id="18-s-gap-with-a-timer-pair-is-a-pause"),
        pytest.param((0, 1, 2, 3, 5, 8, 12, 13, 15, 18, 22, 28), [], ("variable steps", (), (6,)),
                     id="6-s-gap-is-over-5"),
        pytest.param((0, 1, 2, 3, 5, 8, 12, 13, 15, 18, 22), [], ("variable steps", (), ()),
                     id="no-gap-over-5"),
    ],
)
def test_a_variable_step_file_gets_the_dropout_check_over_5_s(
        seconds: tuple, timer_events: list, expected: tuple) -> None:
    """A file with no majority step has no step to add, so its gap limit is the 1 Hz 5 s."""
    events = [(_stamps(at)[0], kind) for at, kind in timer_events]
    assert _mode_from(_stamps(*seconds), events) == expected


def test_a_variable_step_file_fails_against_every_readme_mode() -> None:
    stamps = _stamps(0, 1, 2, 3, 5, 8, 12, 13, 15, 18, 22)
    derived = _mode_from(stamps, [])
    assert derived[0] not in {parse_mode(row.mode)[0] for row in _real_rows().values()}


@pytest.mark.parametrize(
    ("seconds", "timer_events", "expected"),
    [
        pytest.param((0, 6, 12, 18, 38, 44, 50), [], ("6 s steps", (), (20,)), id="20-s-gap-no-timer-pair"),
        pytest.param((0, 6, 12, 18, 38, 44, 50), [(18, "stop_all"), (38, "start")], ("6 s steps", (20,), ()),
                     id="20-s-gap-with-a-timer-pair"),
        pytest.param((0, 6, 12, 18, 29, 35, 41), [], ("6 s steps", (), ()), id="11-s-gap-is-step-plus-5-not-over"),
        pytest.param((0, 6, 12, 18, 30, 36, 42), [], ("6 s steps", (), (12,)), id="12-s-gap-is-over-step-plus-5"),
    ],
)
def test_a_stepped_file_gets_the_dropout_check_over_step_plus_5_s(
        seconds: tuple, timer_events: list, expected: tuple) -> None:
    events = [(_stamps(at)[0], kind) for at, kind in timer_events]
    assert _mode_from(_stamps(*seconds), events) == expected


@pytest.mark.parametrize(
    ("seconds", "timer_events", "expected"),
    [
        pytest.param((0, 1, 2, 3, 9, 10, 11, 12), [], ("1 Hz", (), (6,)), id="6-s-gap-no-timer-pair-is-a-dropout"),
        pytest.param((0, 1, 2, 3, 9, 10, 11, 12), [(3, "stop_all"), (9, "start")], ("1 Hz", (6,), ()),
                     id="6-s-gap-with-a-timer-pair-is-a-pause"),
        pytest.param((0, 1, 2, 3, 8, 9, 10, 11), [], ("1 Hz", (), ()), id="5-s-gap-is-not-over-5"),
    ],
)
def test_a_1_hz_file_gets_the_dropout_check_over_5_s_not_step_plus_5(
        seconds: tuple, timer_events: list, expected: tuple) -> None:
    """In a 1 Hz file the gap limit is 5 s, not the step plus 5 s: a 6 s gap is over it."""
    events = [(_stamps(at)[0], kind) for at, kind in timer_events]
    assert _mode_from(_stamps(*seconds), events) == expected


@pytest.mark.parametrize(
    ("stem", "column", "value"),
    [
        ("strap_hrv_sample_run", "kind", "real run"),
        ("strap_hrv_sample_run", "run_proof", "yes"),
        ("wrist_ppg_hrv_snapshot", "kind", "health snapshot"),
        ("strap_cool_down_walk", "run_proof", "yes"),
        ("wrist_ppg_run", "mode", "1 Hz"),
    ],
)
def test_a_row_disagreeing_with_the_reference_is_named(stem: str, column: str, value: str) -> None:
    row = replace(_real_rows()[stem], **{column: value})
    problems = reference_problems(row)
    assert len(problems) == 1 and problems[0].startswith(f"{stem}: {column} says")
