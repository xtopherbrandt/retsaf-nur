"""A Strava row's activity file is read from the archive by name under the per-file cap, and its
``session`` starts are decoded in file order (F030 reference section 2 item 5; AC3, AC8).

``strava_files`` is pure: every test writes a small zip into ``tmp_path`` with
``strava_archive.strava_zip`` and opens it with ``zipfile``. The cap is ``archive.FIT_ENTRY_CAP``
read at call time; the over-cap tests lower it to ``GZ_BOMB_CAP`` (1 MB) with ``monkeypatch``.
"At most cap + 1 bytes read" is measured by wrapping the streams the module reads: the zip entry
(a counting ``ZipFile.open``) and the decompressed stream (a counting ``gzip.GzipFile``).

Every activity file is ``sample_run`` (README provenance: real run, run proof yes) or a copy
patched by ``strava_archive``.
"""

from __future__ import annotations

import gzip
import importlib.util
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from runcoach_api.imports import archive, strava_files
from runcoach_api.imports.strava_types import FileError

_SPEC = importlib.util.spec_from_file_location(
    "strava_archive", Path(__file__).resolve().parent / "support" / "strava_archive.py"
)
assert _SPEC is not None and _SPEC.loader is not None
sa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sa)

FIT = sa.fixture("sample_run")
START = sa.session_start(FIT)
NAME = "activities/123.fit"
GZ_NAME = "activities/123.fit.gz"


def _zip(tmp_path: Path, files: list[tuple[str, bytes]], layout: str = "root") -> zipfile.ZipFile:
    path = sa.strava_zip(tmp_path / f"export-{layout}.zip", [], files, layout=layout)
    return zipfile.ZipFile(path)


def _prefix(layout: str) -> str:
    return "" if layout == "root" else f"{sa.FOLDER}/"


class _CountingStream:
    """A read-only stream that counts the bytes its reads return."""

    def __init__(self, inner, counts: list[int]) -> None:
        self._inner = inner
        self._counts = counts

    def read(self, size: int = -1) -> bytes:
        data = self._inner.read(size)
        self._counts.append(len(data))
        return data

    def readinto(self, buffer) -> int:
        n = self._inner.readinto(buffer)
        self._counts.append(n or 0)
        return n

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        self._inner.close()


def _counting_gzip(monkeypatch: pytest.MonkeyPatch) -> tuple[list[int], list[int]]:
    """Patch ``gzip.GzipFile`` so every ``read`` records the size asked and the bytes returned."""
    asked: list[int] = []
    returned: list[int] = []
    real = gzip.GzipFile

    class CountingGzipFile(real):  # type: ignore[misc, valid-type]
        def read(self, size: int = -1) -> bytes:
            asked.append(size)
            data = super().read(size)
            returned.append(len(data))
            return data

    monkeypatch.setattr(gzip, "GzipFile", CountingGzipFile)
    return asked, returned


# ---------------------------------------------------------------------------
# reading by name, under the cap
# ---------------------------------------------------------------------------


def test_a_gz_past_the_cap_fails_413_reading_at_most_cap_plus_one(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "FIT_ENTRY_CAP", sa.GZ_BOMB_CAP)
    asked, returned = _counting_gzip(monkeypatch)
    with _zip(tmp_path, [(GZ_NAME, sa.gz_bomb())]) as zf:
        result = strava_files.read_activity_file(zf, "", GZ_NAME)
    assert isinstance(result, FileError)
    assert result.status == 413
    assert str(sa.GZ_BOMB_CAP) in result.detail
    # One bounded read of cap + 1 decompressed bytes, never an unbounded read() of the 4 MB.
    assert asked == [sa.GZ_BOMB_CAP + 1]
    assert sum(returned) == sa.GZ_BOMB_CAP + 1
    assert sa.GZ_BOMB_EXPANDED > sa.GZ_BOMB_CAP + 1


def test_a_gz_at_the_cap_is_read_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "FIT_ENTRY_CAP", sa.GZ_BOMB_CAP)
    with _zip(tmp_path, [(GZ_NAME, sa.gz_bomb(sa.GZ_BOMB_CAP))]) as zf:
        result = strava_files.read_activity_file(zf, "", GZ_NAME)
    assert result == bytes(sa.GZ_BOMB_CAP)


def test_a_plain_fit_past_the_cap_fails_413_reading_at_most_cap_plus_one(tmp_path, monkeypatch):
    cap = len(FIT) // 4
    monkeypatch.setattr(archive, "FIT_ENTRY_CAP", cap)
    counts: list[int] = []
    with _zip(tmp_path, [(NAME, FIT)]) as zf:
        real_open = zf.open
        monkeypatch.setattr(zf, "open", lambda info, *a, **k: _CountingStream(real_open(info, *a, **k), counts))
        result = strava_files.read_activity_file(zf, "", NAME)
    assert isinstance(result, FileError)
    assert result.status == 413
    assert sum(counts) == cap + 1


def test_the_cap_is_read_at_call_time(tmp_path, monkeypatch):
    with _zip(tmp_path, [(NAME, FIT)]) as zf:
        assert strava_files.read_activity_file(zf, "", NAME) == FIT
        monkeypatch.setattr(archive, "FIT_ENTRY_CAP", len(FIT))
        assert strava_files.read_activity_file(zf, "", NAME) == FIT
        monkeypatch.setattr(archive, "FIT_ENTRY_CAP", len(FIT) - 1)
        result = strava_files.read_activity_file(zf, "", NAME)
    assert isinstance(result, FileError) and result.status == 413


def test_a_corrupt_gz_fails_naming_the_error(tmp_path):
    with _zip(tmp_path, [(GZ_NAME, sa.CORRUPT_GZ)]) as zf:
        result = strava_files.read_activity_file(zf, "", GZ_NAME)
    assert isinstance(result, FileError)
    assert result.status is None
    assert result.reason == strava_files.FILE_NOT_READ
    # The error the read raised is named, with its message.
    assert result.detail.startswith("zlib.error: Error -3 while decompressing data")


@pytest.mark.parametrize(
    "data, error",
    [
        (b"not a gzip stream at all", "gzip.BadGzipFile: Not a gzipped file"),
        (sa.gz(FIT)[:-20], "EOFError: Compressed file ended"),
        (sa.CORRUPT_GZ, "zlib.error: "),
    ],
    ids=["bad-magic", "cut-off", "does-not-inflate"],
)
def test_each_gz_read_error_is_a_file_error_naming_it(tmp_path, data, error):
    with _zip(tmp_path, [(GZ_NAME, data)]) as zf:
        result = strava_files.read_activity_file(zf, "", GZ_NAME)
    assert isinstance(result, FileError)
    assert result.reason == strava_files.FILE_NOT_READ
    assert result.status is None
    assert result.detail.startswith(error)


def test_a_corrupt_zip_entry_is_a_file_error_naming_it(tmp_path):
    # A stored entry whose bytes no longer match its CRC: the zip read raises BadZipFile.
    path = tmp_path / "bad-crc.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as out:
        out.writestr(NAME, FIT)
    raw = bytearray(path.read_bytes())
    at = raw.find(FIT[:64])
    raw[at + 100] ^= 0xFF
    path.write_bytes(bytes(raw))
    with zipfile.ZipFile(path) as zf:
        result = strava_files.read_activity_file(zf, "", NAME)
    assert isinstance(result, FileError)
    assert result.reason == strava_files.FILE_NOT_READ
    assert result.detail.startswith("zipfile.BadZipFile: Bad CRC-32")


def test_a_filename_naming_no_entry_is_file_not_in_archive(tmp_path):
    with _zip(tmp_path, [(NAME, FIT)]) as zf:
        for name in ("activities/999.fit", "", "activities/", "activities/123.FIT", "123.fit"):
            result = strava_files.read_activity_file(zf, "", name)
            assert result == FileError("file_not_in_archive"), name


def test_a_filename_is_a_label_never_a_path(tmp_path):
    outside = tmp_path / "outside.fit"
    outside.write_bytes(FIT)
    with _zip(tmp_path, [(NAME, FIT)], layout="folder") as zf:
        prefix = _prefix("folder")
        # An absolute path, a parent step and a back-slashed spelling name no entry: nothing is
        # joined, normalised or opened on disk.
        for name in (str(outside), outside.as_posix(), f"../{sa.FOLDER}/{NAME}", "activities\\123.fit",
                     f"./{NAME}", f"activities/../{NAME}"):
            assert strava_files.read_activity_file(zf, prefix, name) == FileError("file_not_in_archive"), name
        # The prefix is prepended to the label as text, exactly once.
        assert strava_files.read_activity_file(zf, prefix, NAME) == FIT
        assert strava_files.read_activity_file(zf, "", NAME) == FileError("file_not_in_archive")
    assert outside.read_bytes() == FIT


@pytest.mark.parametrize("layout", ["root", "folder"])
def test_a_plain_fit_and_a_gz_fit_read_alike(tmp_path, layout):
    with _zip(tmp_path, [(NAME, FIT), (GZ_NAME, sa.gz(FIT))], layout=layout) as zf:
        plain = strava_files.read_activity_file(zf, _prefix(layout), NAME)
        gzipped = strava_files.read_activity_file(zf, _prefix(layout), GZ_NAME)
    assert plain == FIT
    assert gzipped == FIT
    assert strava_files.session_starts(plain) == strava_files.session_starts(gzipped) == [START]


def test_of_two_same_named_entries_the_first_is_read(tmp_path):
    path = tmp_path / "twice.zip"
    with zipfile.ZipFile(path, "w") as out:
        out.writestr(NAME, FIT)
        with pytest.warns(UserWarning, match="Duplicate name"):
            out.writestr(NAME, b"the second copy")
    with zipfile.ZipFile(path) as zf:
        assert strava_files.read_activity_file(zf, "", NAME) == FIT


def test_an_upper_case_gz_suffix_is_decompressed(tmp_path):
    with _zip(tmp_path, [("activities/123.FIT.GZ", sa.gz(FIT))]) as zf:
        assert strava_files.read_activity_file(zf, "", "activities/123.FIT.GZ") == FIT


# ---------------------------------------------------------------------------
# session starts
# ---------------------------------------------------------------------------


def _fit_seconds(dt: datetime) -> int:
    return int((dt - archive.FIT_EPOCH).total_seconds())


def test_session_starts_are_listed_in_file_order():
    starts = strava_files.session_starts(FIT)
    assert starts == [START]
    (start,) = starts
    assert start.tzinfo is not None and start.utcoffset().total_seconds() == 0
    # The store's spelling, as ``strava_csv`` gives ``start_time``.
    assert len(start.isoformat()) == 25 and start.isoformat().endswith("+00:00")
    two = sa.two_session_copy(FIT)
    assert strava_files.session_starts(two) == [START, START]
    # The second session moved an hour earlier, then the first: the list follows the file, not time.
    earlier = datetime.fromtimestamp(START.timestamp() - 3600, UTC)
    second_earlier = sa.fit_patch.set_field_at(two, sa.SESSION, 1, sa.SESSION_START_TIME, _fit_seconds(earlier))
    assert strava_files.session_starts(second_earlier) == [START, earlier]
    first_earlier = sa.fit_patch.set_field_at(two, sa.SESSION, 0, sa.SESSION_START_TIME, _fit_seconds(earlier))
    assert strava_files.session_starts(first_earlier) == [earlier, START]
    # One unreadable start keeps its place.
    second_unread = sa.fit_patch.set_field_at(two, sa.SESSION, 1, sa.SESSION_START_TIME, sa.UINT32_INVALID)
    assert strava_files.session_starts(second_unread) == [START, None]


def test_a_session_with_an_unreadable_start_lists_none():
    assert strava_files.session_starts(sa.unstarted_copy(FIT)) == [None]
    assert strava_files.session_starts(sa.two_session_copy(sa.unstarted_copy(FIT))) == [None, None]


def test_a_fit_with_no_session_message_lists_none():
    assert strava_files.session_starts(sa.no_session_copy(FIT)) == []


@pytest.mark.parametrize(
    "data",
    [b"", b"definitely not a FIT file", FIT[: len(FIT) // 2], FIT[:-1] + bytes([FIT[-1] ^ 0xFF])],
    ids=["empty", "not-fit", "cut-off", "bad-crc"],
)
def test_a_fit_that_does_not_decode_is_a_file_error(data):
    result = strava_files.session_starts(data)
    assert isinstance(result, FileError)
    assert result.reason == strava_files.FIT_NOT_DECODED
    assert result.detail


def test_a_fit_past_a_decode_ceiling_is_a_413(monkeypatch):
    from runcoach_api.ingestion import fit_parser

    monkeypatch.setattr(fit_parser, "MAX_RECORD_MESSAGES", 10)
    result = strava_files.session_starts(FIT)
    assert isinstance(result, FileError)
    assert result.status == 413
    assert result.reason == strava_files.FIT_NOT_DECODED


# ---------------------------------------------------------------------------
# file kinds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, kind",
    [
        ("activities/1.fit", "fit"),
        ("activities/1.fit.gz", "fit"),
        ("activities/1.FIT.GZ", "fit"),
        ("activities/1.gpx", "gpx"),
        ("activities/1.gpx.gz", "gpx"),
        ("activities/1.tcx", "tcx"),
        ("activities/1.TCX.gz", "tcx"),
        ("activities/1.gz", None),
        ("activities/1.csv", None),
        ("activities/1", None),
        ("", None),
        ("activities/1.fit.zip", None),
        ("activities/1.gz.fit", "fit"),
        ("activities/1.fitgz", None),
    ],
)
def test_gpx_and_tcx_are_classified_by_extension(name, kind):
    assert strava_files.file_kind(name) == kind
