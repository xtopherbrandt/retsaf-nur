"""``imports.archive.scan``: read, screen and order an archive's FIT files.

The F020 reference's section 2 steps 2-5, below the job and the HTTP route:
no store is opened here. The material is the section 9 archive that
``tests/support/import_archive.py`` builds in ``tmp_path`` from corpus
fixtures; each test adds the entries its row needs through ``extra_inner`` /
``extra_outer`` or builds a small zip of its own.

- **Screening by content (F020 AC2).** The inner zip sits under a folder name
  no Garmin export uses and is still opened; monitoring_b and the loose outer
  monitoring_daily are ``non_activity``; TCX, JSON and the nested zip are
  ``not_fit`` and the nested zip is not opened; the corrupt inner zip is an
  ``unreadable_archives`` entry with a reason.
- **Bounds (F020 AC9).** An inner zip expanding past the (lowered) cap is
  unreadable and no more than the cap is ever written; a FIT entry over the
  (lowered) cap is a 413 ``failed`` candidate carrying the ``POST /sessions``
  message; hostile entry names create no path outside ``work_dir``; ``.FIT``
  is screened; an encrypted entry is reported, not raised.
- **Order (F020 AC4 at reader level).** Activities oldest first by
  ``time_created``, ties by label then archive position, undated after dated,
  and a ``file_id``-unreadable candidate after every activity in archive
  order; the 2119-stamped activity comes after the 2016-2026 ones.
"""

from __future__ import annotations

import importlib.util
import io
import os
import struct
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from runcoach_api.imports import archive

_SPEC = importlib.util.spec_from_file_location(
    "import_archive", Path(__file__).parent / "support" / "import_archive.py"
)
assert _SPEC is not None and _SPEC.loader is not None
import_archive = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(import_archive)

IA = import_archive

FILE_TYPE_ACTIVITY = 4
FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)


def _fit_seconds(year: int, month: int = 6, day: int = 1) -> int:
    return int((datetime(year, month, day, tzinfo=UTC) - FIT_EPOCH).total_seconds())


def _activity(year: int, **kwargs) -> bytes:
    return IA.minimal_fit(FILE_TYPE_ACTIVITY, time_created=_fit_seconds(year, **kwargs))


def _zip(entries: list[tuple[str, bytes]], **kwargs) -> bytes:
    buffer = io.BytesIO()
    kwargs.setdefault("compression", zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(buffer, "w", **kwargs) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buffer.getvalue()


def _write_zip(path: Path, entries: list[tuple[str, bytes]], **kwargs) -> Path:
    path.write_bytes(_zip(entries, **kwargs))
    return path


def _tree(root: Path) -> set[str]:
    return {str(p) for p in root.rglob("*")}


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def test_an_inner_zip_under_an_unknown_folder_is_screened_by_content(tmp_path: Path, work_dir: Path) -> None:
    """F020 AC2: the folder name is never matched; every entry is screened by what it holds."""
    path = IA.build_archive(tmp_path)
    seen: list[int] = []
    scan = archive.scan(path, work_dir, progress=seen.append)

    inner = IA.INNER_ZIP
    labels = [a.label for a in scan.activities]
    print("activities:", labels)
    print("non_activity:", scan.non_activity, "not_fit:", scan.not_fit)
    print("unreadable:", scan.unreadable_archives, "failed:", scan.failed)

    expected_activities = {
        f"{inner}!{name}"
        for name in (
            "sample_run.fit",
            "strap_run_hrv.fit",
            "strap_hrv_capture.fit",
            "strap_hrv_sample_run.fit",
            "sample_health_snapshot.fit",
            IA.CYCLING_RUN,
            IA.NO_HR_RUN,
            IA.TRUNCATED,
        )
    }
    assert set(labels) == expected_activities
    assert len(labels) == len(expected_activities)
    assert scan.non_activity == {"monitoring_b": 1, "monitoring_daily": 1}
    assert scan.not_fit == {"tcx": 1, "json": 1, "zip": 1}
    assert [u.label for u in scan.unreadable_archives] == [IA.CORRUPT_ZIP]
    assert scan.unreadable_archives[0].reason
    assert scan.failed == []
    # 9 FIT candidates in the inner zip plus the loose outer one.
    assert scan.fit_files_scanned == 10
    assert seen == list(range(1, 11))
    # Each activity's bytes sit under work_dir, byte for byte, under a generated name.
    inner_bytes = dict(IA.inner_entries())
    for activity in scan.activities:
        assert activity.path.parent == work_dir
        assert activity.label.split("!")[-1] not in activity.path.name
        assert activity.path.read_bytes() == inner_bytes[activity.label.split("!", 1)[1]]
    # No inner zip copy is left behind: only the activities' files remain.
    assert sorted(work_dir.iterdir()) == sorted(a.path for a in scan.activities)


def test_the_nested_zip_is_counted_and_never_opened(tmp_path: Path, work_dir: Path, monkeypatch) -> None:
    """A zip inside the inner zip is ``not_fit`` ``zip``: its ``sample_run.fit`` is never a candidate."""
    opened: list[str] = []
    real = zipfile.ZipFile

    class Spy(real):  # type: ignore[misc, valid-type]
        def __init__(self, file, *args, **kwargs):
            opened.append(str(getattr(file, "name", file)))
            super().__init__(file, *args, **kwargs)

    path = IA.build_archive(tmp_path)
    monkeypatch.setattr(archive.zipfile, "ZipFile", Spy)
    scan = archive.scan(path, work_dir)
    print("opened:", opened)
    assert scan.not_fit["zip"] == 1
    # The outer archive, the inner zip and the corrupt one: three opens, no nested zip.
    assert len(opened) == 3
    assert not any(a.label.endswith("nested.zip!sample_run.fit") for a in scan.activities)


def test_a_loose_outer_fit_and_an_upper_case_extension_are_screened(tmp_path: Path, work_dir: Path) -> None:
    """``.FIT`` and ``.Zip`` match in any case, at either level."""
    upper_inner = _zip([("RUN_2018.FIT", _activity(2018)), ("mon.Fit", IA.minimal_fit(32))])
    path = IA.build_archive(
        tmp_path,
        extra_outer=[("LOOSE_RUN.FIT", _activity(2017)), ("Other/Part9.ZIP", upper_inner), ("notes.TXT", b"x")],
    )
    scan = archive.scan(path, work_dir)
    labels = [a.label for a in scan.activities]
    print(labels, scan.non_activity, scan.not_fit)
    assert "LOOSE_RUN.FIT" in labels
    assert "Other/Part9.ZIP!RUN_2018.FIT" in labels
    assert scan.non_activity["monitoring_b"] == 2
    assert scan.not_fit["txt"] == 1


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_activities_are_ordered_oldest_first_by_time_created(tmp_path: Path, work_dir: Path) -> None:
    """Newest-first listing comes out oldest first; ties by label then position; undated last."""
    # Newer than every section 9 activity, so the no-HR run stays the oldest.
    tie = _activity(2030, month=3)
    extra = [
        ("b_tie.fit", tie),
        ("a_tie.fit", tie),
        ("a_tie.fit", tie),
        ("undated.fit", IA.minimal_fit(FILE_TYPE_ACTIVITY, time_created=None)),
        ("invalid_stamp.fit", IA.minimal_fit(FILE_TYPE_ACTIVITY, time_created=0xFFFFFFFF)),
    ]
    path = IA.build_archive(tmp_path, newest_first=True, extra_inner=extra)
    scan = archive.scan(path, work_dir)
    rows = [(a.label.split("!")[-1], a.time_created, a.position) for a in scan.activities]
    for row in rows:
        print(row)

    dated = [r for r in rows if r[1] is not None]
    assert [r[1] for r in dated] == sorted(r[1] for r in dated)
    # The no-HR run is the oldest activity in the archive.
    assert rows[0][0] == IA.NO_HR_RUN
    # Equal stamps: label first, then archive position.
    ties = [r for r in rows if r[0] in ("a_tie.fit", "b_tie.fit")]
    assert [r[0] for r in ties] == ["a_tie.fit", "a_tie.fit", "b_tie.fit"]
    assert ties[0][2] < ties[1][2]
    # No time_created (absent, or the invalid sentinel): after every dated activity, then by label.
    assert [r[0] for r in rows[-2:]] == ["invalid_stamp.fit", "undated.fit"]
    assert all(r[1] is None for r in rows[-2:])


def test_the_2119_stamp_comes_after_the_2016_to_2026_rows(tmp_path: Path, work_dir: Path) -> None:
    """The export's manual entry stamped 2119 is processed last among dated activities, not in 2017."""
    extra = [
        ("y2119.fit", _activity(2119, month=1)),
        ("y2016.fit", _activity(2016)),
        ("y2026.fit", _activity(2026)),
        ("epoch.fit", IA.minimal_fit(FILE_TYPE_ACTIVITY, time_created=0)),
    ]
    path = IA.build_archive(tmp_path, newest_first=True, extra_inner=extra)
    scan = archive.scan(path, work_dir)
    names = [a.label.split("!")[-1] for a in scan.activities]
    print(names)
    assert names[0] == "epoch.fit"
    assert scan.activities[0].time_created_utc == FIT_EPOCH
    assert names[-1] == "y2119.fit"
    assert scan.activities[-1].time_created_utc.year == 2119
    assert names.index("y2016.fit") < names.index("y2026.fit") < names.index("y2119.fit")


def test_an_unreadable_file_id_row_comes_after_every_activity(tmp_path: Path, work_dir: Path) -> None:
    """``file_id``-unreadable candidates are ``failed``, after every activity, in archive order."""
    # Unreadable entries sit first, between and inside: archive position alone would interleave them.
    path = _write_zip(
        tmp_path / "a.zip",
        [
            ("first_garbage.fit", b"this is not a FIT file at all"),
            ("run_2019.fit", _activity(2019)),
            ("p.zip", _zip([("garbage_inner.fit", b"\x0e\x10garbage"), ("in_2018.fit", _activity(2018))])),
            ("empty.fit", b""),
            ("run_2017.fit", _activity(2017)),
        ],
    )
    scan = archive.scan(path, work_dir)
    order = scan.processing_order()
    for row in order:
        print(type(row).__name__, row.label, row.position, getattr(row, "detail", ""))
    assert [(type(row).__name__, row.label) for row in order] == [
        ("Activity", "run_2017.fit"),
        ("Activity", "p.zip!in_2018.fit"),
        ("Activity", "run_2019.fit"),
        ("Failed", "first_garbage.fit"),
        ("Failed", "p.zip!garbage_inner.fit"),
        ("Failed", "empty.fit"),
    ]
    assert scan.fit_files_scanned == 6
    assert all(row.detail for row in scan.failed)
    assert all(row.status is None for row in scan.failed)
    # A failed candidate writes nothing to work_dir.
    assert len(list(work_dir.iterdir())) == len(scan.activities)


def test_a_fit_without_a_file_id_type_is_failed_not_dropped(tmp_path: Path, work_dir: Path) -> None:
    """A ``file_id`` with no usable ``type`` cannot be told from an activity: it is ``failed``, not counted away."""
    path = _write_zip(tmp_path / "a.zip", [("typeless.fit", IA.minimal_fit(0xFF))])
    scan = archive.scan(path, work_dir)
    print(scan.failed, scan.non_activity)
    assert [f.label for f in scan.failed] == ["typeless.fit"]
    assert "type" in scan.failed[0].detail
    assert scan.non_activity == {}


def test_a_fit_with_no_file_id_message_is_failed(tmp_path: Path, work_dir: Path) -> None:
    """A well-formed FIT whose only message is a ``record`` (global 20): no ``file_id`` to screen by."""
    from fitdecode.utils import compute_crc

    body = bytearray(IA.minimal_fit(FILE_TYPE_ACTIVITY)[:-2])
    struct.pack_into("<H", body, 14 + 3, 20)  # the definition's global message number
    data = bytes(body) + struct.pack("<H", compute_crc(bytes(body)))
    path = _write_zip(tmp_path / "a.zip", [("no_file_id.fit", data)])
    scan = archive.scan(path, work_dir)
    print(scan.failed)
    assert [(f.label, f.status) for f in scan.failed] == [("no_file_id.fit", None)]
    assert "no file_id" in scan.failed[0].detail
    assert scan.activities == [] and scan.non_activity == {}


def test_a_garmin_private_type_is_counted_by_its_number(tmp_path: Path, work_dir: Path) -> None:
    path = _write_zip(tmp_path / "a.zip", [("hrv_status.fit", IA.minimal_fit(44)), ("x.fit", IA.minimal_fit(44))])
    scan = archive.scan(path, work_dir)
    assert scan.non_activity == {"44": 2}
    assert scan.activities == [] and scan.failed == []


def test_an_inner_zip_past_its_cap_is_unreadable_and_stops_writing(
    tmp_path: Path, work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F020 AC9: a small compressed inner zip expanding past the cap is unreadable; at most the cap is written."""
    # The cap is the section 9 inner zip's own size: that one sits exactly at it and is read.
    with zipfile.ZipFile(IA.build_archive(tmp_path, name="probe.zip")) as zf:
        cap = zf.getinfo(IA.INNER_ZIP).file_size
    (tmp_path / "probe.zip").unlink()
    monkeypatch.setattr(archive, "INNER_ZIP_CAP", cap)
    # Stored, not deflated: the inner zip itself is 4 MiB, and the outer deflates it to a few KiB.
    bomb_inner = _zip(
        [("zeros.bin", b"\0" * (4 * 1024 * 1024)), ("run.fit", _activity(2019))], compression=zipfile.ZIP_STORED
    )
    path = IA.build_archive(tmp_path, extra_outer=[("Bomb/Part1.zip", bomb_inner)])
    with zipfile.ZipFile(path) as zf:
        compressed = zf.getinfo("Bomb/Part1.zip").compress_size
        declared = zf.getinfo("Bomb/Part1.zip").file_size
    print("bomb compressed", compressed, "declared", declared, "cap", cap)
    assert declared > cap

    written: dict[str, int] = {}
    real_open = open

    class Counting:
        def __init__(self, handle, name):
            self._handle, self._name = handle, name

        def write(self, data):
            written[self._name] = written.get(self._name, 0) + len(data)
            return self._handle.write(data)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._handle.close()

        def __getattr__(self, attr):
            return getattr(self._handle, attr)

    def counting_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        return Counting(handle, str(file)) if "w" in mode else handle

    monkeypatch.setattr(archive, "open", counting_open, raising=False)
    scan = archive.scan(path, work_dir)
    print("written:", written)
    print("unreadable:", scan.unreadable_archives)

    assert [u.label for u in scan.unreadable_archives] == [IA.CORRUPT_ZIP, "Bomb/Part1.zip"]
    assert str(cap) in scan.unreadable_archives[1].reason
    assert written, "the copy went through the module's open()"
    assert max(written.values()) <= cap
    # Nothing from the bomb was screened, the other inner zip still was, and its copy is gone.
    assert not any(a.label.startswith("Bomb/") for a in scan.activities)
    assert len(scan.activities) == 8
    assert sorted(work_dir.iterdir()) == sorted(a.path for a in scan.activities)


def test_an_inner_zip_at_exactly_its_cap_is_read(tmp_path: Path, work_dir: Path, monkeypatch) -> None:
    inner = _zip([("run.fit", _activity(2019))])
    monkeypatch.setattr(archive, "INNER_ZIP_CAP", len(inner))
    path = _write_zip(tmp_path / "a.zip", [("p.zip", inner)])
    scan = archive.scan(path, work_dir)
    assert scan.unreadable_archives == []
    assert [a.label for a in scan.activities] == ["p.zip!run.fit"]


def test_a_fit_entry_over_its_cap_is_a_413_failed_candidate(tmp_path: Path, work_dir: Path, monkeypatch) -> None:
    """F020 AC9: a FIT entry over the (lowered) cap fails with 413 and the ``POST /sessions`` message."""
    fit = _activity(2019)
    monkeypatch.setattr(archive, "FIT_ENTRY_CAP", len(fit))
    # Every corpus file is larger than this cap, so the archive is built here, not from section 9.
    path = _write_zip(tmp_path / "a.zip", [("p.zip", _zip([("big.fit", fit + b"\0")])), ("at_cap.fit", fit)])
    scan = archive.scan(path, work_dir)
    print(scan.failed)
    assert [(f.label, f.status) for f in scan.failed] == [("p.zip!big.fit", 413)]
    big = next(f for f in scan.failed if f.label.endswith("big.fit"))
    assert big.detail == f"upload exceeds {len(fit)} byte limit"
    assert "at_cap.fit" in [a.label for a in scan.activities]


def test_the_fit_entry_cap_is_the_one_file_upload_cap() -> None:
    from runcoach_api import main

    assert archive.FIT_ENTRY_CAP == main.MAX_UPLOAD_BYTES == 50 * 1024 * 1024
    assert archive.INNER_ZIP_CAP == 1024 ** 3


def _rename_entry(data: bytes, old: bytes, new: bytes) -> bytes:
    """Swap an entry name's bytes (same length) in the local header and the central directory."""
    assert len(old) == len(new) and data.count(old) == 2
    return data.replace(old, new)


def test_an_entry_name_never_reaches_the_file_system(tmp_path: Path, work_dir: Path) -> None:
    """F020 AC9: ``..``, drive letters and non-UTF-8 bytes in names create no path outside ``work_dir``."""
    root = tmp_path / "root"
    root.mkdir()
    hostile = [
        ("../../escape_up.fit", _activity(2018)),
        ("..\\..\\escape_back.fit", _activity(2018, month=7)),
        ("C:/escape_drive.fit", _activity(2018, month=8)),
        ("C:\\escape_drive_back.fit", _activity(2018, month=9)),
        ("/abs/escape_abs.fit", _activity(2018, month=10)),
        ("NAME_XX.fit", _activity(2018, month=11)),
        ("../inner_escape.zip", _zip([("../../deeper.fit", _activity(2018, month=12))])),
    ]
    raw = _rename_entry(_zip(hostile), b"NAME_XX", b"NAME_\x82\xff")
    path = root / "hostile.zip"
    path.write_bytes(raw)
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    print("entry names as zipfile reads them:", names)
    assert any("\x82" not in n and n.startswith("NAME_") and n != "NAME_XX.fit" for n in names)

    before = _tree(tmp_path)
    scan = archive.scan(path, work_dir)
    new_paths = sorted(_tree(tmp_path) - before)
    print("new paths:", new_paths)
    print("labels:", [a.label for a in scan.activities])
    assert new_paths and all(Path(p).parent == work_dir for p in new_paths)
    # Where a name used as a path would have landed: none of them holds the entry's base name.
    cwd = Path.cwd()
    landing = {work_dir, work_dir.parent, work_dir.parent.parent, cwd, cwd.parent, cwd.parent.parent}
    landing |= {Path("C:/"), Path("/abs"), Path(cwd.anchor) / "abs"}
    bases = ("escape_up.fit", "escape_back.fit", "escape_drive.fit", "escape_drive_back.fit", "escape_abs.fit")
    bases += ("deeper.fit", "inner_escape.zip")
    hits = [d / b for d in landing for b in bases if (d / b).exists()]
    print("landing dirs checked:", sorted(map(str, landing)), "hits:", hits)
    assert hits == []
    assert len(scan.activities) == 7
    assert "../inner_escape.zip!../../deeper.fit" in [a.label for a in scan.activities]
    for activity in scan.activities:
        assert activity.path.parent == work_dir
        assert ".." not in activity.path.name and ":" not in activity.path.name


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_duplicate_names_are_each_visited_by_their_own_entry(tmp_path: Path, work_dir: Path) -> None:
    """Two entries with one name: both are read by ``ZipInfo``, not the last one twice."""
    first, second = _activity(2017), _activity(2018)
    path = IA.build_archive(tmp_path, extra_outer=[("dup.fit", first), ("dup.fit", second)])
    scan = archive.scan(path, work_dir)
    dups = [a for a in scan.activities if a.label == "dup.fit"]
    assert [a.path.read_bytes() for a in dups] == [first, second]
    assert dups[0].path != dups[1].path


def _encrypt_flag(data: bytes, name: bytes) -> bytes:
    """Set general-purpose bit 0 (encrypted) on one entry in both headers, as a password-protected zip carries."""
    out = bytearray(data)
    for signature, flag_offset, name_offset in ((b"PK\x03\x04", 6, 30), (b"PK\x01\x02", 8, 46)):
        start = 0
        while (at := out.find(signature, start)) >= 0:
            length = struct.unpack_from("<H", out, at + (26 if signature == b"PK\x03\x04" else 28))[0]
            if bytes(out[at + name_offset : at + name_offset + length]) == name:
                flags = struct.unpack_from("<H", out, at + flag_offset)[0]
                struct.pack_into("<H", out, at + flag_offset, flags | 0x1)
            start = at + 4
    return bytes(out)


def test_an_encrypted_entry_is_reported_not_raised(tmp_path: Path, work_dir: Path) -> None:
    raw = _zip(
        [
            ("locked.fit", _activity(2018)),
            ("locked.zip", _zip([("in.fit", _activity(2019))])),
            ("open.fit", _activity(2020)),
        ]
    )
    raw = _encrypt_flag(_encrypt_flag(raw, b"locked.fit"), b"locked.zip")
    path = tmp_path / "enc.zip"
    path.write_bytes(raw)
    scan = archive.scan(path, work_dir)
    print(scan.failed, scan.unreadable_archives)
    assert [f.label for f in scan.failed] == ["locked.fit"]
    assert "encrypted" in scan.failed[0].detail
    assert [(u.label, "encrypted" in u.reason) for u in scan.unreadable_archives] == [("locked.zip", True)]
    assert [a.label for a in scan.activities] == ["open.fit"]


@pytest.mark.parametrize("entry", ["run.fit", "part.zip"])
def test_a_work_dir_write_failure_raises(
    tmp_path: Path, work_dir: Path, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """A read error of one entry is reported, but a failing write to ``work_dir`` (an activity's
    spill, or an inner zip's copy) is outside per-file handling and raises."""
    contents = {"run.fit": _activity(2019), "part.zip": _zip([("in.fit", _activity(2020))])}
    path = _write_zip(tmp_path / "a.zip", [(entry, contents[entry])])
    real_open = open

    def full_disk_open(file, mode="r", *args, **kwargs):
        if "w" in mode:
            raise OSError(28, "No space left on device")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(archive, "open", full_disk_open, raising=False)
    with pytest.raises(OSError, match="No space left on device"):
        archive.scan(path, work_dir)


def test_a_truncated_inner_zip_is_unreadable_and_the_rest_is_read(tmp_path: Path, work_dir: Path) -> None:
    inner = _zip([("a.fit", _activity(2018)), ("b.fit", _activity(2019))])
    path = IA.build_archive(tmp_path, extra_outer=[("cut/Part.zip", inner[: len(inner) // 2])])
    scan = archive.scan(path, work_dir)
    print(scan.unreadable_archives)
    assert [u.label for u in scan.unreadable_archives] == [IA.CORRUPT_ZIP, "cut/Part.zip"]
    assert len(scan.activities) == 8


def test_an_empty_or_non_fit_only_archive_scans_to_nothing(tmp_path: Path, work_dir: Path) -> None:
    empty = _write_zip(tmp_path / "empty.zip", [])
    scan = archive.scan(empty, work_dir)
    assert (scan.activities, scan.failed, scan.non_activity, scan.not_fit, scan.unreadable_archives) == (
        [],
        [],
        {},
        {},
        [],
    )
    assert scan.fit_files_scanned == 0
    other = _write_zip(
        tmp_path / "other.zip",
        [("a/b.tcx", b"x"), ("README", b"x"), ("folder/", b""), (".hidden", b"x"), ("x.GPX", b"x")],
    )
    scan = archive.scan(other, work_dir)
    assert scan.not_fit == {"tcx": 1, "(none)": 2, "gpx": 1}
    assert scan.fit_files_scanned == 0


def test_a_zip64_archive_is_read(tmp_path: Path, work_dir: Path) -> None:
    inner = _zip([("in.fit", _activity(2019))])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for name, data in (("run.fit", _activity(2018)), ("p.zip", inner)):
            with zf.open(name, "w", force_zip64=True) as fh:
                fh.write(data)
    path = tmp_path / "z64.zip"
    path.write_bytes(buffer.getvalue())
    scan = archive.scan(path, work_dir)
    assert [a.label for a in scan.activities] == ["run.fit", "p.zip!in.fit"]


def test_the_scan_leaves_the_archive_closed(tmp_path: Path, work_dir: Path) -> None:
    """Windows cannot delete an open file: after a scan the archive and every activity file unlink."""
    path = IA.build_archive(tmp_path)
    scan = archive.scan(path, work_dir)
    path.unlink()
    for activity in scan.activities:
        activity.path.unlink()
    assert not os.listdir(work_dir)
