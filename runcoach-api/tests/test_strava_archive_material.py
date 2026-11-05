"""``tests/support/strava_archive.py``: the Strava archives every F030 import test builds.

The material is the F030 reference's section 6, built at run time in
``tmp_path`` from corpus fixtures; nothing is written into ``tests/fixtures/``.
The oracles are ``fitdecode``, ``gzip``, ``csv`` and ``zipfile`` reading back
what was built, plus ``mapping.to_canonical`` for the store's view of a file
(its ``start_time``, ``source_device`` and ``distance_m``), never any Strava
import code.

- **Constructed FIT files.** The cycling copy reads ``cycling`` in both
  ``session.sport`` and ``sport.sport`` with both CRCs checked; the two-session
  copy decodes two sessions, the no-session copy none; the unstarted copy has
  no session start while its canonical start is its source's.
- **The CSV.** The header is the real export's 103 cells with the five repeated
  names at their real positions; cells holding LF, CRLF and a quote, the
  12:xx AM and PM dates and the short row read back intact; the BOM and the
  header without ``Distance (2)`` are options.
- **The catalogue.** One case per outcome of reference section 3 and per named
  failure; the 1 % cases are exact (printed: ``s``, ``d`` and the rule's two
  sides); each stored file is a distinct ``(source_device, start_time)``.
- **The archive.** The three layouts, the explicit directory entries, the
  gzipped fixture runs, the corrupt ``.gz`` and the bomb.

``sample_run`` is the only source file: a real run (README provenance row: run
proof yes).
"""

from __future__ import annotations

import csv
import functools
import gzip
import importlib.util
import io
import zipfile
import zlib
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import NamedTuple

import fitdecode
import pytest
from runcoach_api.ingestion import fit_parser, mapping

FIXTURES = Path(__file__).parent / "fixtures"

_SPEC = importlib.util.spec_from_file_location("strava_archive", Path(__file__).parent / "support" / "strava_archive.py")
assert _SPEC is not None and _SPEC.loader is not None
strava_archive = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(strava_archive)

SA = strava_archive
SAMPLE = (FIXTURES / "sample_run.fit").read_bytes()


def _messages(data: bytes) -> list[fitdecode.FitDataMessage]:
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        return [frame for frame in reader if isinstance(frame, fitdecode.FitDataMessage)]


def _named(data: bytes, name: str) -> list[fitdecode.FitDataMessage]:
    return [m for m in _messages(data) if m.name == name]


class _Stored(NamedTuple):
    source_device: str
    start_time: str
    distance_m: float | None


@functools.cache
def _canonical(data: bytes) -> _Stored:
    """The store's view of ``data``: its dedup key and ``summary["distance_m"]``, as the pipeline maps them."""
    session, _ = mapping.to_canonical(fit_parser.decode(data))
    return _Stored(session.source_device, session.start_time, session.summary.get("distance_m"))


def _read_csv(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))


# ---------------------------------------------------------------------------
# constructed FIT files
# ---------------------------------------------------------------------------


def test_cycling_copy_has_session_and_sport_cycling_and_a_valid_crc() -> None:
    copy = SA.cycling_copy(SAMPLE)
    sessions, sports = _named(copy, "session"), _named(copy, "sport")
    print("cycling copy: session sport", [s.get_value("sport") for s in sessions],
          "sport.sport", [s.get_value("sport") for s in sports])
    assert [s.get_value("sport") for s in sessions] == ["cycling"]
    assert [s.get_value("sport") for s in sports] == ["cycling"]
    # The source said running in both, so the copy's reading is the patch's doing.
    assert [s.get_value("sport") for s in _named(SAMPLE, "session") + _named(SAMPLE, "sport")] == ["running"] * 2
    assert len(copy) == len(SAMPLE)
    assert _canonical(copy).start_time == _canonical(SAMPLE).start_time


def test_two_session_copy_decodes_two_sessions() -> None:
    copy = SA.two_session_copy(SAMPLE)
    sessions = _named(copy, "session")
    print("two-session copy:", [(s.get_value("start_time"), s.get_value("sport")) for s in sessions])
    assert len(sessions) == 2 and len(_named(SAMPLE, "session")) == 1
    assert sessions[0].get_value("start_time") == sessions[1].get_value("start_time")


def test_no_session_copy_decodes_no_session() -> None:
    copy = SA.no_session_copy(SAMPLE)
    names = {m.name for m in _messages(copy)}
    print("no-session copy messages:", sorted(names))
    assert "session" not in names and "record" in names and "file_id" in names


def test_unstarted_copy_has_no_session_start_but_its_canonical_start() -> None:
    copy = SA.unstarted_copy(SAMPLE)
    assert [s.get_value("start_time") for s in _named(copy, "session")] == [None]
    print("unstarted copy canonical start:", _canonical(copy).start_time)
    assert (_canonical(copy).source_device, _canonical(copy).start_time) == (
        _canonical(SAMPLE).source_device, _canonical(SAMPLE).start_time)


def test_distance_and_device_patches_change_only_their_field() -> None:
    patched = SA.other_device_copy(SA.with_distance(SAMPLE, 1_010_000))
    source, copy = _canonical(SAMPLE), _canonical(patched)
    print(f"source {source.source_device} {source.distance_m}; copy {copy.source_device} {copy.distance_m}")
    assert copy.distance_m == 10_100.0
    assert copy.start_time == source.start_time and copy.source_device != source.source_device


# ---------------------------------------------------------------------------
# the CSV
# ---------------------------------------------------------------------------


def test_csv_header_repeats_the_five_names() -> None:
    header = _read_csv(SA.activities_csv([]))[0]
    assert len(header) == 103 and tuple(header) == SA.FULL_HEADER
    repeats = {name: [i for i, h in enumerate(header) if h == name] for name in set(header) if header.count(name) > 1}
    print("repeated names:", sorted(repeats.items()))
    assert repeats == {"Elapsed Time": [5, 15], "Distance": [6, 17], "Max Heart Rate": [7, 30],
                       "Relative Effort": [8, 37], "Commute": [9, 50]}
    assert [SA.KEYS[i] for i in (6, 17, 37)] == ["Distance", "Distance (2)", "Relative Effort (2)"]
    without = SA.header_without("Distance (2)")
    assert len(without) == 102 and without.count("Distance") == 1 and without.index("Distance") == 6
    assert SA.activities_csv([], bom=True).startswith(b"\xef\xbb\xbfActivity ID,")
    assert SA.activities_csv([]).endswith(b"Media\n")
    assert SA.activities_csv([], lineterminator="\r\n").endswith(b"Media\r\n")
    with pytest.raises(KeyError):
        SA.row(**{"Distance (3)": "1"})


def test_csv_holds_12_am_12_pm_crlf_quote_and_short_rows() -> None:
    cases = {c.key: c for c in SA.OUTCOME_ROWS}
    picked = [cases[k] for k in ("twelve_am", "twelve_pm", "lf_cell", "crlf_cell", "quote_cell", "failed_short_row")]
    data = SA.activities_csv([c.cells for c in picked])
    rows = _read_csv(data)[1:]
    by_id = {r[0]: r for r in rows}
    for r in rows:
        print(len(r), r[:4], repr(r[4][:30]), repr(r[10][:30]))
    am, pm = (by_id[cases[k].cells["Activity ID"]] for k in ("twelve_am", "twelve_pm"))
    assert am[1].endswith(" 12:34:21 AM") and pm[1].endswith(" 12:34:21 PM")
    for date, stored_name in ((am[1], "twelve_am.fit"), (pm[1], "twelve_pm.fit")):
        stored = dict(SA.stored_files())[stored_name]
        parsed = datetime.strptime(date, "%b %d, %Y, %I:%M:%S %p").replace(tzinfo=UTC)
        assert parsed.isoformat() == _canonical(stored).start_time
    assert by_id[cases["lf_cell"].cells["Activity ID"]][4] == "Bailed due to calf problems.\nWalked home."
    assert by_id[cases["crlf_cell"].cells["Activity ID"]][10] == "Foot pain\r\nblew up"
    assert b"Foot pain\r\nblew up" in data
    assert by_id[cases["quote_cell"].cells["Activity ID"]][2] == 'The "easy" ride, mostly'
    assert b'"The ""easy"" ride, mostly"' in data
    short = [r for r in rows if len(r) != 103]
    assert len(short) == 1 and len(short[0]) == 12 and short[0][2] == "short row"
    assert SA.strava_date(datetime(2022, 4, 18, 14, 26, 14, tzinfo=UTC)) == SA.DEFAULT_DATE


# ---------------------------------------------------------------------------
# the catalogue
# ---------------------------------------------------------------------------


def test_csv_has_a_row_for_every_outcome() -> None:
    cases = SA.OUTCOME_ROWS
    for c in cases:
        print(f"{c.key}: {c.outcome} {c.failure or ''} file={c.file[0] if c.file else None} "
              f"stored={[n for n, _ in c.stored]} link={c.link} warnings={c.warnings}")
    assert {c.outcome for c in cases} == set(SA.OUTCOMES)
    assert {c.failure for c in cases if c.outcome == "failed"} == set(SA.FAILURES)
    assert all((c.failure is None) == (c.outcome != "failed") for c in cases)
    assert len({c.key for c in cases}) == len(cases)
    # Every row reads back from the CSV, in catalogue order.
    rows = _read_csv(SA.activities_csv([c.cells for c in cases]))[1:]
    assert len(rows) == len(cases)
    ids = [c.cells["Activity ID"] if isinstance(c.cells, dict) else c.cells[0] for c in cases]
    assert [r[0] for r in rows] == ids
    # Only the duplicate-ID case repeats an ID; only the bad-ID case's does not parse.
    assert len(ids) - len(set(ids)) == 1
    dup = SA.by_key("failed_duplicate_activity_id").cells["Activity ID"]
    assert ids.count(dup) == 2 and ids.index(dup) < len(ids) - 1 - ids[::-1].index(dup)
    # Every named link and every stored file exists, and stored files are distinct sessions.
    stored = SA.stored_files()
    names = [n for n, _ in stored]
    assert len(set(names)) == len(names)
    assert {c.link for c in cases if c.link} <= set(names)
    keys = [(_canonical(d).source_device, _canonical(d).start_time) for _, d in stored]
    assert len(set(keys)) == len(keys)
    # A file a Run row must decode (or store) has no session at its CSV start unless the case says so.
    starts = {k[1] for k in keys}
    for key in ("stored", "stored_retyped", "multisport_not_stored", "failed_no_session_message"):
        c = SA.by_key(key)
        csv_start = datetime.strptime(c.cells["Activity Date"], "%b %d, %Y, %I:%M:%S %p").replace(tzinfo=UTC)
        assert csv_start.isoformat() not in starts, key


def test_the_one_percent_cases_are_exact() -> None:
    stored = dict(SA.stored_files())
    rows = []
    for key in ("attached", "attached_distance_differs", "attached_at_exactly_one_percent",
                "attached_just_past_one_percent", "attached_zero_distance", "attached_tiebreak",
                "ambiguous_neither_within", "ambiguous_both_within", "ambiguous_zero_distance"):
        c = SA.by_key(key)
        d = float(c.cells["Distance (2)"])
        csv_start = datetime.strptime(c.cells["Activity Date"], "%b %d, %Y, %I:%M:%S %p").replace(tzinfo=UTC)
        sessions = [_canonical(stored[n]) for n, _ in c.stored]
        within = [d != 0 and abs(s.distance_m - d) <= 0.01 * d for s in sessions]
        rows.append((key, d, [s.distance_m for s in sessions], within))
        print(key, d, [(s.distance_m, abs(s.distance_m - d), 0.01 * d) for s in sessions], within)
        assert all(s.start_time == csv_start.isoformat() for s in sessions)
        if c.outcome == "attached":
            assert within.count(True) == (0 if d == 0 or "differs" in key or "past" in key else 1)
            if len(sessions) == 2:
                assert c.link == c.stored[within.index(True)][0]
            # A warning is carried by a linked row whose session is not within 1 %, d non-zero.
            assert ("distance_differs" in c.warnings) == (d != 0 and not any(within))
        else:
            assert len(sessions) == 2 and within.count(True) != 1 and c.link is None and not c.warnings
    one = SA.by_key("attached_at_exactly_one_percent")
    s = _canonical(stored[one.link]).distance_m
    assert Fraction(s) == Fraction(101, 100) * Fraction(one.cells["Distance (2)"])
    tie = SA.by_key("attached_tiebreak")
    assert [_canonical(stored[n]).distance_m for n, _ in tie.stored] == [13_712.0, 3_090.0]
    assert tie.cells["Distance (2)"] == "13711.6"


# ---------------------------------------------------------------------------
# the archive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", SA.LAYOUTS)
def test_archive_variants_root_folder_and_macosx(tmp_path: Path, layout: str) -> None:
    path = SA.case_zip(tmp_path / f"{layout}.zip", layout=layout)
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        print(layout, names[:4], "...", names[-4:])
        prefix = "" if layout == "root" else f"{SA.FOLDER}/"
        assert f"{prefix}activities.csv" in names
        assert f"{prefix}activities/" in names and f"{prefix}media/" in names
        assert zf.getinfo(f"{prefix}activities/").is_dir()
        top = {n.split("/", 1)[0] + ("/" if "/" in n else "") for n in names}
        if layout == "root":
            assert "activities.csv" in top and SA.FOLDER + "/" not in top
        elif layout == "folder":
            assert top == {f"{SA.FOLDER}/"}
        else:
            assert top == {f"{SA.FOLDER}/", "__MACOSX/"}
            assert f"__MACOSX/{SA.FOLDER}/._activities.csv" in names
        rows = _read_csv(zf.read(f"{prefix}activities.csv"))[1:]
        assert len(rows) == len(SA.OUTCOME_ROWS)
        # Every Filename is root-relative and resolves under the prefix, except the missing one.
        for c in SA.OUTCOME_ROWS:
            if c.file is not None:
                assert c.file[0].startswith("activities/")
                assert zf.read(prefix + c.file[0]) == c.file[1]
        missing = SA.by_key("failed_file_not_in_archive").cells["Filename"]
        assert prefix + missing not in names
    with zipfile.ZipFile(SA.strava_zip(tmp_path / "bom.zip", [], header=SA.header_without("Distance (2)"),
                                       bom=True, extra=[("notes.txt", b"x")])) as zf:
        assert zf.read("activities.csv").startswith(b"\xef\xbb\xbf")
        assert len(_read_csv(zf.read("activities.csv"))[0]) == 102
        assert zf.read("notes.txt") == b"x"


def test_activity_files_are_gzipped_fixture_runs() -> None:
    for key in ("attached", "stored", "attached_by_file_start"):
        c = SA.by_key(key)
        fit = gzip.decompress(c.file[1])
        session = _canonical(fit)
        print(key, c.file[0], len(c.file[1]), "->", len(fit), session.start_time, session.source_device)
        assert c.file[0].endswith(".fit.gz")
        assert _canonical(SAMPLE).source_device == session.source_device
        # A re-dated copy of sample_run: same record count, other start.
        assert len(_named(fit, "record")) == len(_named(SAMPLE, "record"))
        assert session.start_time != _canonical(SAMPLE).start_time
    retyped = gzip.decompress(SA.by_key("stored_retyped").file[1])
    assert [s.get_value("sport") for s in _named(retyped, "session")] == ["cycling"]
    assert len(_named(gzip.decompress(SA.by_key("multisport_not_stored").file[1]), "session")) == 2
    assert not _named(gzip.decompress(SA.by_key("failed_no_session_message").file[1]), "session")
    with pytest.raises(fit_parser.FitParseFailure):
        fit_parser.decode(gzip.decompress(SA.by_key("failed_fit_does_not_decode").file[1]))
    for key in ("not_fit_gpx", "not_fit_tcx"):
        assert gzip.decompress(SA.by_key(key).file[1]).startswith(b"<?xml")
    with pytest.raises((OSError, EOFError, zlib.error)):
        gzip.decompress(SA.by_key("failed_corrupt_gz").file[1])
    bomb = SA.by_key("failed_over_cap")
    assert bomb.cap == SA.GZ_BOMB_CAP and len(bomb.file[1]) < SA.GZ_BOMB_CAP // 100
    with gzip.GzipFile(fileobj=io.BytesIO(bomb.file[1])) as stream:
        assert len(stream.read(SA.GZ_BOMB_CAP + 1)) == SA.GZ_BOMB_CAP + 1
    assert len(gzip.decompress(bomb.file[1])) == SA.GZ_BOMB_EXPANDED
