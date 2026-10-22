"""Read, screen and order the FIT files of an export archive (F020 reference section 2, steps 2-5).

``scan(archive_path, work_dir)`` opens no store and makes no HTTP call. It
visits every entry of the outer zip and of each inner zip one level down,
screens each FIT candidate by its ``file_id`` alone, and returns the
activities in processing order with their bytes copied to ``work_dir``.

- **Visiting.** Entries are visited by ``ZipInfo``, never by name: with two
  entries of one name, ``ZipFile.open(name)`` returns the last one twice. An
  entry whose name ends in ``.zip`` (any case) at the outer level is an inner
  archive: it is copied to ``work_dir`` reading at most ``INNER_ZIP_CAP``
  bytes of actual content (not the size its header declares), opened, visited,
  then closed and deleted before the next entry (Windows cannot delete an open
  file). A ``.zip`` inside an inner zip is counted under ``not_fit`` as ``zip``
  and never opened. A ``.fit`` (any case) at either level is a FIT candidate;
  any other entry is counted under ``not_fit`` by its lower-cased extension,
  ``(none)`` without one. Directory entries are ignored.
- **Names are labels only.** An outer entry is labelled by its path, an inner
  entry by ``<outer path>!<inner path>``. No name reaches a file system call:
  every file written under ``work_dir`` has a generated name, so ``..``, a
  drive letter or non-UTF-8 bytes in an entry name create nothing outside it.
- **Screening.** A candidate is read into memory, stopping at
  ``FIT_ENTRY_CAP + 1`` bytes; over the cap it is a ``Failed`` candidate with
  the status and message ``POST /sessions`` gives a file over its cap. Its
  ``file_id`` is read with CRC checking disabled, and decoding stops there. A
  ``file_id.type`` of ``HRV_STATUS_FILE_TYPE`` is a night: an ``Activity``
  of ``kind`` ``"night"``, spilled, ordered and counted with the activities.
  Any other ``file_id.type`` than ``activity`` is counted under ``non_activity``
  by the decoder's type name, or by the number as a string for a Garmin-private
  type. A ``file_id`` that cannot be read, or has no usable ``type``, makes a
  ``Failed`` candidate (it may be an activity) with ``status`` None. That
  ``file_id`` decision is the public ``screen_file_id(data)``, so a caller
  screening one file applies the same rule.
- **Order.** ``time_created`` ascending, ties by label, then by archive
  position; an activity without ``time_created`` (absent, or the invalid
  sentinel) comes after every dated one. ``Scan.processing_order()`` puts the
  failed candidates after every activity, in archive order.
- **Reported, never raised.** An encrypted entry, an entry whose compressed
  stream is corrupt (a bzip2 or LZMA stream included), an inner zip that does
  not open (``BadZipFile``, truncated), or one that passes its cap is reported
  in ``failed`` or ``unreadable_archives``. Only the outer archive failing to
  open as a zip, or a write to ``work_dir`` failing, raises: those are errors
  outside per-file handling, so the read errors are caught around each read
  alone, never around a write.
"""

from __future__ import annotations

import io
import lzma
import posixpath
import zipfile
import zlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import fitdecode

# 1 GiB of actual bytes per inner zip copy; tests lower it.
INNER_ZIP_CAP = 1024**3
# The cap of one-file upload (``POST /sessions``); tests lower it.
FIT_ENTRY_CAP = 50 * 1024 * 1024
_COPY_CHUNK = 1024 * 1024

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)
_ENCRYPTED = 0x1
# Errors reading one entry's bytes out of a zip: corrupt data (a corrupt bzip2
# stream raises OSError, a corrupt LZMA stream LZMAError), a bad CRC, an
# unsupported compression method, an encrypted entry or a cut-off stream. They
# are caught around reads only: OSError is also what a failing write raises.
_ENTRY_READ_ERRORS = (
    zipfile.BadZipFile,
    zlib.error,
    lzma.LZMAError,
    OSError,
    RuntimeError,
    NotImplementedError,
    EOFError,
    ValueError,
)


@dataclass(frozen=True)
class Activity:
    """An activity FIT file, or an HRV_STATUS file (``kind`` ``"night"``), its bytes at ``path`` under ``work_dir``."""

    label: str
    time_created: int | None  # FIT seconds since FIT_EPOCH, as file_id carries it
    path: Path
    position: int
    kind: Literal["activity", "night"] = "activity"

    @property
    def time_created_utc(self) -> datetime | None:
        if self.time_created is None:
            return None
        return FIT_EPOCH + timedelta(seconds=self.time_created)


@dataclass(frozen=True)
class Failed:
    """A FIT candidate that cannot be screened: ``status`` is what ``POST /sessions`` returns, else None."""

    label: str
    status: int | None
    detail: str
    position: int


@dataclass(frozen=True)
class UnreadableArchive:
    label: str
    reason: str


@dataclass
class Scan:
    activities: list[Activity] = field(default_factory=list)
    failed: list[Failed] = field(default_factory=list)
    non_activity: dict[str, int] = field(default_factory=dict)
    not_fit: dict[str, int] = field(default_factory=dict)
    unreadable_archives: list[UnreadableArchive] = field(default_factory=list)
    fit_files_scanned: int = 0

    def processing_order(self) -> list[Activity | Failed]:
        """Every activity in order, then every failed candidate in archive order."""
        return [*self.activities, *self.failed]


def _extension(name: str) -> str:
    """The lower-cased extension of an entry name's last component, ``(none)`` without one (string work only)."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    ext = posixpath.splitext(base)[1]
    return ext[1:].lower() if ext else "(none)"


HRV_STATUS_FILE_TYPE = 68
"""``file_id.type`` of the watch's HRV_STATUS file, a number the FIT profile fitdecode ships does not name."""


@dataclass(frozen=True)
class FileIdScreen:
    """What a FIT file's ``file_id`` says it is, before any decoding past ``file_id``.

    ``outcome`` is ``"activity"`` (``time_created`` is the raw value, None when
    absent or the invalid sentinel), ``"hrv_status"`` (``file_id.type``
    ``HRV_STATUS_FILE_TYPE``, the watch's overnight HRV file; ``time_created``
    as for an activity), ``"non_activity"`` (``file_type`` is the label the
    archive import counts: the decoder's type name, or the number as a string
    for a Garmin-private type) or ``"failed"`` (``detail`` says why; the file
    may still be an activity).
    """

    outcome: Literal["activity", "hrv_status", "non_activity", "failed"]
    time_created: int | None = None
    file_type: str | None = None
    detail: str | None = None


def _read_file_id(data: bytes) -> tuple[str | int | None, int | None]:
    """``(file_id.type, raw time_created)``, reading no further than ``file_id``; raises if it cannot be read."""
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.DISABLED) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "file_id":
                return (
                    frame.get_value("type", fallback=None),
                    frame.get_raw_value("time_created", fallback=None),
                )
    raise LookupError("no file_id message")


def screen_file_id(data: bytes) -> FileIdScreen:
    """Screen one FIT file's bytes by its ``file_id`` alone, with CRC checking off; never raises.

    The rule the archive import applies to every FIT candidate: a ``file_id``
    that cannot be read, or carries no usable ``type``, fails; type
    ``HRV_STATUS_FILE_TYPE`` is an ``hrv_status``; any other ``type`` than
    ``activity`` is a non-activity; otherwise an activity.
    """
    try:
        file_type, time_created = _read_file_id(data)
    except Exception as exc:  # noqa: BLE001 -- any decoder error fails this file only
        return FileIdScreen("failed", detail=f"file_id could not be read: {type(exc).__name__}: {exc}")
    if file_type is None:
        return FileIdScreen("failed", detail="file_id carries no type")
    if file_type == HRV_STATUS_FILE_TYPE:
        return FileIdScreen("hrv_status", time_created=time_created)
    if file_type != "activity":
        return FileIdScreen("non_activity", file_type=str(file_type))
    return FileIdScreen("activity", time_created=time_created)


class _Scanner:
    def __init__(self, work_dir: Path, progress: Callable[[int], None] | None) -> None:
        self.work_dir = work_dir
        self.progress = progress
        self.position = 0
        self.written = 0
        self.scan = Scan()
        self.non_activity: Counter[str] = Counter()
        self.not_fit: Counter[str] = Counter()

    def _next_name(self, suffix: str) -> Path:
        self.written += 1
        return self.work_dir / f"{self.written:06d}{suffix}"

    def _fail(self, label: str, position: int, status: int | None, detail: str) -> None:
        self.scan.failed.append(Failed(label, status, detail, position))

    def visit(self, zf: zipfile.ZipFile, prefix: str, *, inner: bool) -> None:
        for info in zf.infolist():
            if info.is_dir():
                continue
            position = self.position
            self.position += 1
            label = f"{prefix}{info.filename}"
            ext = _extension(info.filename)
            if ext == "fit":
                self._candidate(zf, info, label, position)
            elif ext == "zip" and not inner:
                self._inner_zip(zf, info, label)
            else:
                self.not_fit[ext] += 1

    def _candidate(self, zf: zipfile.ZipFile, info: zipfile.ZipInfo, label: str, position: int) -> None:
        self.scan.fit_files_scanned += 1
        try:
            self._screen(zf, info, label, position)
        finally:
            if self.progress is not None:
                self.progress(self.scan.fit_files_scanned)

    def _screen(self, zf: zipfile.ZipFile, info: zipfile.ZipInfo, label: str, position: int) -> None:
        if info.flag_bits & _ENCRYPTED:
            self._fail(label, position, None, "entry is encrypted")
            return
        cap = FIT_ENTRY_CAP
        try:
            with zf.open(info) as handle:
                data = handle.read(cap + 1)
        except _ENTRY_READ_ERRORS as exc:
            self._fail(label, position, None, f"entry could not be read: {type(exc).__name__}: {exc}")
            return
        if len(data) > cap:
            self._fail(label, position, 413, f"upload exceeds {cap} byte limit")
            return
        screened = screen_file_id(data)
        if screened.outcome == "failed":
            self._fail(label, position, None, screened.detail or "")
            return
        if screened.outcome == "non_activity":
            self.non_activity[screened.file_type or ""] += 1
            return
        # An HRV_STATUS file is spilled and ordered with the activities, as a night.
        kind: Literal["activity", "night"] = "night" if screened.outcome == "hrv_status" else "activity"
        path = self._next_name(".fit")
        with open(path, "wb") as out:
            out.write(data)
        self.scan.activities.append(Activity(label, screened.time_created, path, position, kind))

    def _inner_zip(self, zf: zipfile.ZipFile, info: zipfile.ZipInfo, label: str) -> None:
        if info.flag_bits & _ENCRYPTED:
            self.scan.unreadable_archives.append(UnreadableArchive(label, "entry is encrypted"))
            return
        path = self._next_name(".zip")
        try:
            reason = self._copy_capped(zf, info, path)
            if reason is None:
                try:
                    inner = zipfile.ZipFile(path)
                except _ENTRY_READ_ERRORS as exc:
                    reason = f"not a readable zip: {type(exc).__name__}: {exc}"
                else:
                    # Visiting writes activities to work_dir, so OSError is not caught here.
                    try:
                        with inner:
                            self.visit(inner, f"{label}!", inner=True)
                    except (zipfile.BadZipFile, zlib.error, NotImplementedError, EOFError, ValueError) as exc:
                        reason = f"not a readable zip: {type(exc).__name__}: {exc}"
            if reason is not None:
                self.scan.unreadable_archives.append(UnreadableArchive(label, reason))
        finally:
            path.unlink(missing_ok=True)

    def _copy_capped(self, zf: zipfile.ZipFile, info: zipfile.ZipInfo, path: Path) -> str | None:
        """Copy one inner zip, writing at most ``INNER_ZIP_CAP`` bytes; a reason when it cannot be used.

        A read error is the reason; a write to ``path`` that fails raises.
        """
        cap = INNER_ZIP_CAP
        total = 0
        try:
            source = zf.open(info)
        except _ENTRY_READ_ERRORS as exc:
            return f"could not be read: {type(exc).__name__}: {exc}"
        with source, open(path, "wb") as out:
            while True:
                try:
                    chunk = source.read(min(_COPY_CHUNK, cap + 1 - total))
                except _ENTRY_READ_ERRORS as exc:
                    return f"could not be read: {type(exc).__name__}: {exc}"
                if not chunk:
                    return None
                if total + len(chunk) > cap:
                    return f"expands past the {cap} byte limit"
                out.write(chunk)
                total += len(chunk)


def _order_key(activity: Activity) -> tuple[bool, int, str, int]:
    return (activity.time_created is None, activity.time_created or 0, activity.label, activity.position)


def scan(archive_path: Path, work_dir: Path, *, progress: Callable[[int], None] | None = None) -> Scan:
    """Screen every FIT file of ``archive_path``; activities' bytes are written under ``work_dir``.

    ``progress(n)`` is called after each FIT candidate with the running
    ``fit_files_scanned``. Raises ``zipfile.BadZipFile`` when the outer archive
    is not a zip.
    """
    scanner = _Scanner(Path(work_dir), progress)
    with zipfile.ZipFile(archive_path) as outer:
        scanner.visit(outer, "", inner=False)
    result = scanner.scan
    result.activities.sort(key=_order_key)
    result.non_activity = dict(scanner.non_activity)
    result.not_fit = dict(scanner.not_fit)
    return result
