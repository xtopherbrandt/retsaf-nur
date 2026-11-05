"""Read a Strava row's activity file out of the archive and list its ``session`` starts (F030
reference section 2 item 5). Pure: no store, no file system, no HTTP.

- ``file_kind(filename)``: ``"fit"``, ``"gpx"`` or ``"tcx"`` by the name's extension (any case),
  one trailing ``.gz`` stripped first; ``None`` for anything else.
- ``read_activity_file(zf, prefix, filename)``: the bytes of the entry named ``prefix + filename``,
  or a ``FileError``. The name is a label: it is compared as text with each ``ZipInfo.filename``
  (directory entries skipped, the first of two same-named entries taken), never joined to a path
  or normalised, so ``..``, an absolute path or a back slash names no entry and opens nothing on
  disk. No match is ``file_not_in_archive``. A name ending in ``.gz`` (any case) is decompressed in
  memory. Either way at most ``archive.FIT_ENTRY_CAP + 1`` bytes are read (the cap read at call
  time); more than the cap is a ``FileError`` with status 413 and ``POST /sessions``' message. An
  error the read raises (``archive._ENTRY_READ_ERRORS``: a bad gzip header, a stream that does not
  inflate or is cut off, a corrupt or encrypted zip entry) is a ``FileError`` naming it; the
  errors are caught around the read alone.
- ``session_starts(fit_bytes)``: the ``start_time`` of every ``session`` message in file order, as
  a UTC ``datetime`` (``isoformat()`` gives the store's ``+00:00`` spelling), ``None`` for a
  session whose start is absent or invalid; ``[]`` for a file with no ``session`` message. The
  file is decoded as ``POST /sessions`` decodes it (``fit_parser.decode``: CRC checked, decode
  ceilings enforced); a file that does not decode is a ``FileError`` naming the error, with status
  413 when a decode ceiling was passed.
"""

from __future__ import annotations

import gzip
import zipfile
from datetime import UTC, datetime
from typing import Literal

from runcoach_api.imports import archive
from runcoach_api.imports.strava_types import FileError
from runcoach_api.ingestion import fit_parser
from runcoach_api.ingestion.exceptions import FitParseFailure, NotAFitFileError, TooManyRecordsError

FileKind = Literal["fit", "gpx", "tcx"]

# The reasons a file fails with.
FILE_NOT_IN_ARCHIVE = "file_not_in_archive"
FILE_TOO_LARGE = "file_too_large"
FILE_NOT_READ = "file_not_read"
FIT_NOT_DECODED = "fit_not_decoded"

_KINDS: dict[str, FileKind] = {".fit": "fit", ".gpx": "gpx", ".tcx": "tcx"}
_GZ = ".gz"


def file_kind(filename: str) -> FileKind | None:
    """The activity file's kind by extension, one trailing ``.gz`` stripped; ``None`` otherwise."""
    name = filename.lower().removesuffix(_GZ)
    for suffix, kind in _KINDS.items():
        if name.endswith(suffix):
            return kind
    return None


def _entry(zf: zipfile.ZipFile, name: str) -> zipfile.ZipInfo | None:
    """The first non-directory entry whose name is exactly ``name``."""
    if not name:
        return None
    for info in zf.infolist():
        if info.filename == name and not info.is_dir():
            return info
    return None


def _error_name(exc: BaseException) -> str:
    """The error's class name, module-qualified unless built in (``zlib.error``, ``EOFError``)."""
    kind = type(exc)
    return kind.__name__ if kind.__module__ == "builtins" else f"{kind.__module__}.{kind.__qualname__}"


def read_activity_file(zf: zipfile.ZipFile, prefix: str, filename: str) -> bytes | FileError:
    """The bytes of the entry ``prefix + filename`` under the per-file cap (module docstring)."""
    info = _entry(zf, prefix + filename) if filename else None
    if info is None:
        return FileError(FILE_NOT_IN_ARCHIVE)
    cap = archive.FIT_ENTRY_CAP
    try:
        with zf.open(info) as handle:
            if filename.lower().endswith(_GZ):
                with gzip.GzipFile(fileobj=handle) as inflated:
                    data = inflated.read(cap + 1)
            else:
                data = handle.read(cap + 1)
    except archive._ENTRY_READ_ERRORS as exc:
        return FileError(FILE_NOT_READ, detail=f"{_error_name(exc)}: {exc}")
    if len(data) > cap:
        return FileError(FILE_TOO_LARGE, status=413, detail=f"upload exceeds {cap} byte limit")
    return data


def _utc(value: object) -> datetime | None:
    """A decoded ``start_time`` as a UTC datetime; anything but a datetime (absent, invalid) is None."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def session_starts(fit_bytes: bytes) -> list[datetime | None] | FileError:
    """Every ``session`` message's ``start_time`` in file order (module docstring)."""
    try:
        messages = fit_parser.decode(fit_bytes)
    except TooManyRecordsError as exc:
        return FileError(FIT_NOT_DECODED, status=413, detail=f"{type(exc).__name__}: {exc}")
    except (FitParseFailure, NotAFitFileError) as exc:
        return FileError(FIT_NOT_DECODED, detail=f"{type(exc).__name__}: {exc}")
    return [
        _utc(message.get_value("start_time", fallback=None))
        for message in messages
        if message.name == "session"
    ]
