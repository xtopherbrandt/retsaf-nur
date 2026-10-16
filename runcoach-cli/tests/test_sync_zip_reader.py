"""The zip entry reader ``sync.read_zip_entries`` and ``sync._upload_zip`` built on it.

The reader is pure: it takes a download's bytes and yields, in archive order, each entry's
``ZipEntry`` (its ``ZipInfo`` and bytes) or a ``ZipFailure`` (a code, the detail sync prints and
whether it is temporary). A whole-download failure carries no ``info``.

Adversarial table (what was fed, what came out):

| input | result |
|---|---|
| 0 entries | snapshot: nothing; activity: ``zip_empty``, temporary |
| 1 and 64 entries | every entry yielded |
| 65 entries | one ``zip_too_many_entries``, permanent, no entry read |
| entries totalling exactly 200 MB (4 x 50 MB of deflated zeros) | every entry yielded |
| one byte over 200 MB | one ``zip_over_budget``, permanent, no entry read |
| a name ending in "/" holding data | ``zip_directory_with_data``, permanent; the rest go on |
| a name ending in "/" holding nothing | skipped, as a folder |
| a stored and a deflated entry | both yielded |
| bzip2, LZMA, encrypted, over the entry cap, corrupt, not a zip, a web page | today's failures |

The budget counts the sizes the central directory declares: ``zipfile`` never returns more bytes
for an entry than its declared size, so the declared total bounds what is read.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from typing import Any

import pytest
from runcoach_cli.commands import sync

DAY = date(2026, 10, 10)
MB = 1024 * 1024


def _zip(entries: list[tuple[str, bytes]], compress_type: int = zipfile.ZIP_STORED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compress_type) as archive:
        for name, data in entries:
            info = zipfile.ZipInfo("placeholder")
            info.filename = name  # set after construction, so a backslash or a trailing "/" is kept
            info.compress_type = compress_type
            archive.writestr(info, data)
    return buffer.getvalue()


def _mixed_methods() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.fit", b"ACT-bz", compress_type=zipfile.ZIP_BZIP2)
        archive.writestr("b.fit", b"ACT-xz", compress_type=zipfile.ZIP_LZMA)
        archive.writestr("c.fit", b"ACT-c", compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("d.fit", b"ACT-d", compress_type=zipfile.ZIP_STORED)
    return buffer.getvalue()


def _encrypted() -> bytes:
    raw = bytearray(_zip([("a.fit", b"ACT-enc"), ("b.fit", b"ACT-b")]))
    raw[raw.find(b"PK\x03\x04") + 6] |= 0x01
    raw[raw.find(b"PK\x01\x02") + 8] |= 0x01
    return bytes(raw)


def _corrupt() -> bytes:
    return _zip([("a.fit", b"ACT-a" * 20), ("b.fit", b"ACT-b")]).replace(b"ACT-aACT-a", b"XXXXXXXXXX", 1)


def _read(data: bytes, *, activity: bool = True) -> list[Any]:
    return list(sync.read_zip_entries(data, activity=activity))


def _entries(reads: list[Any]) -> list[tuple[str, bytes]]:
    return [(r.info.filename, r.data) for r in reads if isinstance(r, sync.ZipEntry)]


def _failures(reads: list[Any]) -> list[tuple[str | None, str, bool]]:
    return [
        (None if r.info is None else r.info.filename, r.code, r.retry)
        for r in reads
        if isinstance(r, sync.ZipFailure)
    ]


class _Recorder:
    """Stands in for ``_Uploader``: records what ``_upload_zip`` uploads and fails."""

    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes]] = []
        self.fails: list[tuple[str, str, bool]] = []

    def upload(self, day: date, item: str, name: str, activity_id: int | None, data: bytes) -> None:
        self.uploads.append((item, data))

    def fail(self, day: date, item: str, activity_id: int | None, detail: str, *, retry: bool) -> None:
        self.fails.append((item, detail, retry))


def _upload(data: bytes, activity_id: int | None = 101) -> _Recorder:
    recorder = _Recorder()
    sync._upload_zip(recorder, DAY, "activity 101", data, activity_id)  # type: ignore[arg-type]
    return recorder


# --- the entry count ---------------------------------------------------------------------------


def _n_entries(n: int) -> bytes:
    return _zip([(f"{i:03d}.fit", b"x") for i in range(n)])


def test_a_zip_over_the_entry_cap_fails_permanent() -> None:
    assert sync.ZIP_ENTRY_LIMIT == 64
    data = _n_entries(65)

    reads = _read(data)

    assert _failures(reads) == [(None, "zip_too_many_entries", False)]
    assert _entries(reads) == []
    recorder = _upload(data)
    assert recorder.uploads == []
    assert recorder.fails == [
        ("activity 101", "the zip holds 65 entries, over the 64 sync reads, and none was uploaded", False)
    ]


@pytest.mark.parametrize("n", [1, 64])
def test_a_zip_at_or_under_the_entry_cap_is_read(n: int) -> None:
    reads = _read(_n_entries(n))

    assert _failures(reads) == []
    assert len(_entries(reads)) == n


def test_an_empty_zip_fails_for_an_activity_and_is_nothing_for_a_snapshot() -> None:
    assert _read(_zip([]), activity=False) == []
    assert _failures(_read(_zip([]))) == [(None, "zip_empty", True)]


# --- the byte budget ---------------------------------------------------------------------------


def _zeros(sizes: list[int]) -> bytes:
    zeros = bytes(max(sizes))
    return _zip([(f"{i}.fit", zeros[:size]) for i, size in enumerate(sizes)], zipfile.ZIP_DEFLATED)


def test_a_zip_over_the_byte_budget_fails_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    assert sync.FIT_ENTRY_CAP == 50 * MB and sync.ZIP_BYTE_BUDGET == 200 * MB
    data = _zeros([50 * MB, 50 * MB, 50 * MB, 50 * MB, 1])  # 200 MB and one byte, each under the cap
    assert len(data) < 1 * MB
    opened: list[str] = []
    real_open = zipfile.ZipFile.open

    def spying_open(self, name, *args, **kwargs):
        opened.append(getattr(name, "filename", name))
        return real_open(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", spying_open)

    reads = _read(data)

    assert _failures(reads) == [(None, "zip_over_budget", False)]
    assert opened == []
    recorder = _upload(data)
    assert recorder.uploads == []
    assert recorder.fails == [
        ("activity 101", "the zip's entries hold over 200 MB in total, and none was uploaded", False)
    ]


def test_a_zip_at_the_byte_budget_is_read() -> None:
    data = _zeros([50 * MB, 50 * MB, 50 * MB, 50 * MB])  # exactly 200 MB

    sizes = []
    for read in sync.read_zip_entries(data, activity=True):
        assert isinstance(read, sync.ZipEntry), read
        sizes.append(len(read.data))

    assert sizes == [50 * MB] * 4


# --- directory entries -------------------------------------------------------------------------


def test_a_directory_entry_with_data_fails() -> None:
    data = _zip([("dir/", b"ACT-hidden"), ("empty/", b""), ("a.fit", b"ACT-a")])

    reads = _read(data)

    assert _failures(reads) == [("dir/", "zip_directory_with_data", False)]
    assert _entries(reads) == [("a.fit", b"ACT-a")]
    recorder = _upload(data)
    assert recorder.uploads == [("activity 101: a.fit", b"ACT-a")]
    assert recorder.fails == [
        (
            "activity 101: dir/",
            "the entry is named as a folder but holds data, which sync does not read",
            False,
        )
    ]


def test_a_zip_of_empty_directories_alone_holds_no_file() -> None:
    assert _failures(_read(_zip([("empty/", b"")]))) == [(None, "zip_empty", True)]


# --- the reader and _upload_zip agree on today's hostile cases ---------------------------------

# name -> (download, the entries expected uploaded, the failures expected (item suffix, retry)).
HOSTILE = {
    "non-fit-and-fit": (
        _zip([("notes.txt", b"NOTFIT hello"), ("101.fit", b"ACT-101")]),
        [b"NOTFIT hello", b"ACT-101"],
        [],
    ),
    "over-and-at-cap": (
        _zip([("big.fit", b"B" * 65), ("cap.fit", b"C" * 64)]),
        [b"C" * 64],
        [(": big.fit", False)],
    ),
    "dot-dot-slash": (_zip([("../x.fit", b"ACT-dotdot")]), [b"ACT-dotdot"], []),
    "drive-and-absolute": (
        _zip([("C:\\x.fit", b"SNAP-drive"), ("/abs/y.fit", b"SNAP-abs")]),
        [b"SNAP-drive", b"SNAP-abs"],
        [],
    ),
    "corrupt-entry": (_corrupt(), [b"ACT-b"], [(": a.fit", True)]),
    "bzip2-lzma-deflated-stored": (
        _mixed_methods(),
        [b"ACT-c", b"ACT-d"],
        [(": a.fit", False), (": b.fit", False)],
    ),
    "encrypted": (_encrypted(), [b"ACT-b"], [(": a.fit", False)]),
    "empty": (_zip([]), [], [("", True)]),
    "not-a-zip": (b"\x00\x01garbage", [], [("", True)]),
    "truncated-zip": (b"PK\x03\x04truncated", [], [("", True)]),
}


@pytest.mark.parametrize("case", HOSTILE.values(), ids=HOSTILE.keys())
def test_the_entry_reader_yields_the_same_entries_upload_zip_uploaded(
    case: tuple[bytes, list[bytes], list[tuple[str, bool]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync, "FIT_ENTRY_CAP", 64)
    data, uploaded, failed = case

    reads = _read(data)
    recorder = _upload(data)

    assert [entry for _, entry in _entries(reads)] == [entry for _, entry in recorder.uploads] == uploaded
    assert [
        ("" if r.info is None else f": {r.info.filename}", r.detail, r.retry)
        for r in reads
        if isinstance(r, sync.ZipFailure)
    ] == [(item.removeprefix("activity 101"), detail, retry) for item, detail, retry in recorder.fails]
    assert [(item.removeprefix("activity 101"), retry) for item, _, retry in recorder.fails] == failed


def test_a_web_page_is_a_failure_to_the_reader_and_a_stop_to_upload_zip() -> None:
    page = b"\xef\xbb\xbf  <!doctype html><html>login</html>"

    assert _failures(_read(page)) == [(None, "web_page", True)]
    with pytest.raises(sync.SyncStop, match="web page"):
        _upload(page)
