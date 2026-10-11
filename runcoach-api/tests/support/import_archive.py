"""Build the F020 reference's section 9 test archive at run time, from corpus fixtures.

Loaded by tests with ``importlib.util.spec_from_file_location``, as the other
``tests/support`` modules are. Nothing here writes into ``tests/fixtures/``:
``build_archive`` writes one zip into the directory it is given (a test's
``tmp_path``), and every patched copy exists only inside it.

- ``minimal_fit(file_type, *, time_created=MINIMAL_TIME_CREATED)`` is a
  hand-built FIT file holding one ``file_id`` message (``type``,
  ``manufacturer`` Garmin and, unless ``None``, ``time_created``), with a
  14-byte header and both CRCs, so ``file_id.type`` can be any value,
  monitoring_b (32) included.
- ``build_archive(directory, **options)`` writes the outer zip and returns
  its path. Its entries, in this order:

  - ``INNER_ZIP``: the inner zip, under a folder name no Garmin export uses,
    so an importer that finds it must screen by content. It holds the corpus
    files ``sample_run`` and ``strap_run_hrv`` (real runs), ``strap_hrv_capture``
    and ``strap_hrv_sample_run`` (HRV captures; the second recorded on the Run
    profile and not run proof) and ``sample_health_snapshot`` (a health
    snapshot), byte for byte; ``CYCLING_RUN``, ``sample_run`` with
    ``session.sport`` set to cycling and re-dated ``CYCLING_SHIFT_S`` later;
    ``NO_HR_RUN``, ``sample_run`` without its ``user_profile`` (3) and
    ``zones_target`` (7) messages, re-dated to ``NO_HR_MARGIN_S`` before the
    oldest other activity's ``time_created``; ``MONITORING_B``,
    ``minimal_fit(32)``; ``TCX`` and ``JSON``, small text files;
    ``TRUNCATED``, ``strap_run_hrv`` re-dated ``TRUNCATED_SHIFT_S`` later and
    cut to half its length (its ``file_id`` still reads; the file does not
    decode); and ``NESTED_ZIP``, a zip holding ``sample_run.fit``.
  - ``CORRUPT_ZIP``: an entry named like an inner zip whose bytes are not a zip.
  - ``LOOSE_FIT``: ``minimal_fit(28)`` (monitoring_daily) at the outer level.

  Options: ``newest_first=True`` lists the inner zip's activity FIT entries
  (those whose ``file_id.type`` is activity) by ``time_created`` descending,
  ahead of the other inner entries, which keep their order; by default the
  inner entries are in the order above. ``extra_inner`` and ``extra_outer``
  are ``(name, bytes)`` pairs appended to the inner or outer zip after the
  entries above, duplicate names allowed. ``name`` is the outer zip's file name.
  The section 9 entries are built once per process and listing order
  (``inner_entries``); each call writes a new zip.
- ``labels(path)`` lists an archive the way the reference labels it: an
  outer entry by its path and an entry of a readable inner zip as
  ``<outer path>!<inner path>``.

Re-dating uses ``fit_patch.shift_timestamps``, so each copy's
``(source_device, start_time)`` differs from its source run's and an
importer does not see it as already stored.
"""

from __future__ import annotations

import functools
import importlib.util
import io
import struct
import zipfile
from collections.abc import Iterable
from pathlib import Path

import fitdecode
from fitdecode.utils import compute_crc

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

_SPEC = importlib.util.spec_from_file_location("fit_patch", Path(__file__).resolve().parent / "fit_patch.py")
assert _SPEC is not None and _SPEC.loader is not None
fit_patch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fit_patch)

USER_PROFILE, ZONES_TARGET, SESSION = 3, 7, 18
SESSION_SPORT_FIELD = 5
SPORT_CYCLING = 2
FILE_TYPE_MONITORING_B, FILE_TYPE_MONITORING_DAILY = 32, 28
GARMIN = 1
DAY_S = 86_400

# 2024-11-08T18:13:20Z in FIT seconds (since 1989-12-31T00:00:00Z).
MINIMAL_TIME_CREATED = 1_100_000_000

CYCLING_SHIFT_S = 7 * DAY_S
TRUNCATED_SHIFT_S = 2 * DAY_S
NO_HR_MARGIN_S = 30 * DAY_S

INNER_ZIP = "DI_CONNECT/Unseen-Folder-Q7/UploadedFiles_0-_Part1.zip"
CORRUPT_ZIP = "DI_CONNECT/Unseen-Folder-Q7/UploadedFiles_1-_Part2.zip"
LOOSE_FIT = "loose_monitoring_daily.fit"

CORPUS = ("sample_run", "strap_run_hrv", "strap_hrv_capture", "sample_health_snapshot", "strap_hrv_sample_run")
CYCLING_RUN = "sample_run_cycling.fit"
NO_HR_RUN = "sample_run_no_hr.fit"
MONITORING_B = "monitoring_b.fit"
TCX = "activity.tcx"
JSON = "summary.json"
TRUNCATED = "strap_run_hrv_truncated.fit"
NESTED_ZIP = "nested.zip"

_TCX_BYTES = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">\n'
    b"  <Activities/>\n"
    b"</TrainingCenterDatabase>\n"
)
_JSON_BYTES = b'{"summarizedActivitiesExport": []}\n'


def minimal_fit(file_type: int, *, time_created: int | None = MINIMAL_TIME_CREATED) -> bytes:
    """A FIT file of one ``file_id`` message whose ``type`` is ``file_type``."""
    # (field number, size, base type): enum, uint16, uint32.
    fields = [(0, 1, 0x00), (1, 2, 0x84)]
    values = struct.pack("<BH", file_type, GARMIN)
    if time_created is not None:
        fields.append((4, 4, 0x86))
        values += struct.pack("<I", time_created)
    definition = struct.pack("<BBBHB", 0x40, 0, 0, 0, len(fields))
    definition += b"".join(struct.pack("<BBB", *f) for f in fields)
    body = definition + b"\x00" + values
    header = bytearray(struct.pack("<BBHI4sH", 14, 0x20, 2132, len(body), b".FIT", 0))
    struct.pack_into("<H", header, 12, compute_crc(header, start=0, end=12))
    data = bytes(header) + body
    return data + struct.pack("<H", compute_crc(data))


def _fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.fit").read_bytes()


def _file_id(data: bytes) -> tuple[str | int | None, int | None]:
    """``(file_id.type, file_id.time_created raw)`` read with CRC checking off, or ``(None, None)``."""
    try:
        with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.DISABLED) as reader:
            for frame in reader:
                if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "file_id":
                    return frame.get_value("type", fallback=None), frame.get_raw_value("time_created", fallback=None)
    except fitdecode.FitError:
        pass
    return None, None


def _zip_bytes(entries: Iterable[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def inner_entries(*, newest_first: bool = False) -> list[tuple[str, bytes]]:
    """The inner zip's section 9 entries, in listing order."""
    return list(_inner_entries(newest_first))


@functools.cache
def _inner_entries(newest_first: bool) -> tuple[tuple[str, bytes], ...]:
    """Built once per process and listing order: every value is immutable ``bytes``."""
    sample_run = _fixture("sample_run")
    entries = [(f"{name}.fit", _fixture(name)) for name in CORPUS]
    oldest = min(_file_id(data)[1] for _, data in entries)
    no_hr = fit_patch.remove_messages(fit_patch.remove_messages(sample_run, USER_PROFILE), ZONES_TARGET)
    no_hr_shift = oldest - NO_HR_MARGIN_S - _file_id(sample_run)[1]
    truncated = fit_patch.shift_timestamps(_fixture("strap_run_hrv"), TRUNCATED_SHIFT_S)
    entries += [
        (
            CYCLING_RUN,
            fit_patch.shift_timestamps(
                fit_patch.set_field(sample_run, SESSION, SESSION_SPORT_FIELD, SPORT_CYCLING), CYCLING_SHIFT_S
            ),
        ),
        (NO_HR_RUN, fit_patch.shift_timestamps(no_hr, no_hr_shift)),
        (MONITORING_B, minimal_fit(FILE_TYPE_MONITORING_B)),
        (TCX, _TCX_BYTES),
        (JSON, _JSON_BYTES),
        (TRUNCATED, truncated[: len(truncated) // 2]),
        (NESTED_ZIP, _zip_bytes([("sample_run.fit", sample_run)])),
    ]
    if newest_first:
        dated = {name: _file_id(data) for name, data in entries}
        activities = [e for e in entries if dated[e[0]][0] == "activity"]
        others = [e for e in entries if dated[e[0]][0] != "activity"]
        activities.sort(key=lambda e: dated[e[0]][1] or 0, reverse=True)
        entries = activities + others
    return tuple(entries)


def build_archive(
    directory: Path,
    *,
    newest_first: bool = False,
    extra_inner: Iterable[tuple[str, bytes]] = (),
    extra_outer: Iterable[tuple[str, bytes]] = (),
    name: str = "garmin_export.zip",
) -> Path:
    """Write the section 9 archive into ``directory`` and return its path."""
    inner = _zip_bytes([*inner_entries(newest_first=newest_first), *extra_inner])
    corrupt = b"PK\x03\x04" + b"this inner zip is corrupt" * 8
    path = Path(directory) / name
    path.write_bytes(
        _zip_bytes(
            [
                (INNER_ZIP, inner),
                (CORRUPT_ZIP, corrupt),
                (LOOSE_FIT, minimal_fit(FILE_TYPE_MONITORING_DAILY)),
                *extra_outer,
            ]
        )
    )
    return path


def labels(path: Path) -> list[str]:
    """Every entry label of the archive at ``path``: outer paths, and ``outer!inner`` for readable inner zips."""
    out = []
    with zipfile.ZipFile(path) as outer:
        for info in outer.infolist():
            out.append(info.filename)
            if not info.filename.lower().endswith(".zip"):
                continue
            try:
                with zipfile.ZipFile(io.BytesIO(outer.read(info))) as inner:
                    out += [f"{info.filename}!{i.filename}" for i in inner.infolist()]
            except zipfile.BadZipFile:
                pass
    return out
