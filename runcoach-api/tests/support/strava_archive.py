"""Build Strava bulk-export archives at run time, for the F030 import tests.

Loaded by tests with ``importlib.util.spec_from_file_location``, as the other
``tests/support`` modules are. Nothing here writes into ``tests/fixtures/``:
every builder returns bytes, or writes one zip into the directory it is given
(a test's ``tmp_path``).

The real export (``export_1347737.zip``, the athlete's) sets the shapes copied
here: ``activities.csv`` has 103 header cells, five names repeat (``FULL_HEADER``),
``Activity Date`` reads ``Apr 18, 2022, 2:26:14 PM`` in UTC, rows end with LF
(3,327 record ends, none CRLF; a CR occurs only inside quoted cells), and the
zip lists explicit directory entries (``activities/``, ``media/``).

- ``keys_of(header)``: the column keys of a header, a repeated name's later
  copies keyed ``<name> (2)``, ``<name> (3)`` (F030 reference section 2.3).
- ``row(**cells)``: one CSV row as a dict keyed by ``KEYS``, every cell ``""``
  except ``Activity Date`` (``DEFAULT_DATE``) and the cells given. An unknown
  key raises ``KeyError``.
- ``strava_date(dt)``: a UTC instant in the export's ``Activity Date`` form.
- ``activities_csv(rows, *, header=FULL_HEADER, bom=False, lineterminator="\\n")``:
  the CSV bytes. A dict row is written in ``keys_of(header)`` order (a key the
  header lacks is dropped); any other row is written as its cells, so a short
  row is a short list. ``header_without(key)`` drops one column by its key.
- Activity files: ``gz(data)``; ``cycling_copy(fit)`` sets ``session.sport``
  and ``sport.sport`` to cycling; ``two_session_copy(fit)`` repeats the first
  ``session`` message; ``no_session_copy(fit)`` drops every ``session``
  message; ``unstarted_copy(fit)`` sets ``session.start_time`` to its invalid
  value, so the file's own session start is unreadable while the canonical
  start (the earliest record) stays the source's; ``with_distance(fit, cm)``
  sets ``session.total_distance``; ``other_device_copy(fit)`` sets
  ``file_id.product`` to ``OTHER_PRODUCT`` (device ``3476``), so the copy
  stores beside its source on one second. Every patch goes through
  ``fit_patch``, which rewrites the header and both CRCs.
  ``CORRUPT_GZ`` is a gzip header followed by bytes that do not inflate;
  ``gz_bomb(expanded=GZ_BOMB_EXPANDED)`` inflates to ``expanded`` zero bytes,
  past ``GZ_BOMB_CAP``, the per-file cap a test sets in place of the 50 MB one.
- ``strava_zip(path, rows, files, *, layout="root", folder=FOLDER, ...)``: the
  archive with ``activities.csv``, ``activities/`` and ``media/`` at the root
  (``"root"``), under one folder (``"folder"``), or under one folder with a
  ``__MACOSX/`` tree beside it (``"macosx"``). ``files`` are
  ``(Filename, bytes)`` pairs, placed under the same prefix; ``extra`` entries
  are appended at the archive's root, ``header``, ``bom`` and
  ``lineterminator`` pass to ``activities_csv``.
- ``OUTCOME_ROWS``: the catalogue, one ``Case`` per outcome of reference
  section 3 (each named ``failed`` cause its own case), plus 12:xx AM and PM
  rows, cells holding LF, CRLF and a quote, and a short row. Each case carries
  its CSV row, its archive file, the FIT files a test uploads with
  ``POST /sessions`` before importing (``stored``), and what a first import of
  the whole catalogue gives it. Built once per loaded copy of this module on
  first use: about 35 s on the development machine, nearly all of it
  ``fit_patch``'s CRC passes over the re-dated copies.
  ``case_zip(path, cases, **kw)`` writes the cases into one archive;
  ``stored_files(cases)`` lists what to upload first.

Every activity file is a copy of ``sample_run`` (README provenance: real run,
run proof yes) re-dated by whole days with ``fit_patch.shift_timestamps``, so
no two cases share a second unless a case asks for it.
"""

from __future__ import annotations

import csv
import functools
import gzip
import importlib.util
import io
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import fitdecode

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

_SPEC = importlib.util.spec_from_file_location("fit_patch", Path(__file__).resolve().parent / "fit_patch.py")
assert _SPEC is not None and _SPEC.loader is not None
fit_patch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fit_patch)

# FIT global message and field numbers.
FILE_ID, SPORT, SESSION = 0, 12, 18
FILE_ID_PRODUCT = 2
SPORT_SPORT = 0
SESSION_START_TIME, SESSION_SPORT, SESSION_TOTAL_DISTANCE = 2, 5, 9
SPORT_CYCLING = 2
UINT32_INVALID = 0xFFFFFFFF
OTHER_PRODUCT = 3476

DAY_S = 86_400

# The header of the real export, in its order (103 cells).
FULL_HEADER: tuple[str, ...] = (
    "Activity ID", "Activity Date", "Activity Name", "Activity Type", "Activity Description",
    "Elapsed Time", "Distance", "Max Heart Rate", "Relative Effort", "Commute",
    "Activity Private Note", "Activity Gear", "Filename", "Athlete Weight", "Bike Weight",
    "Elapsed Time", "Moving Time", "Distance", "Max Speed", "Average Speed",
    "Elevation Gain", "Elevation Loss", "Elevation Low", "Elevation High", "Max Grade",
    "Average Grade", "Average Positive Grade", "Average Negative Grade", "Max Cadence", "Average Cadence",
    "Max Heart Rate", "Average Heart Rate", "Max Watts", "Average Watts", "Calories",
    "Max Temperature", "Average Temperature", "Relative Effort", "Total Work", "Number of Runs",
    "Uphill Time", "Downhill Time", "Other Time", "Perceived Exertion", "Type",
    "Start Time", "Weighted Average Power", "Power Count", "Prefer Perceived Exertion",
    "Perceived Relative Effort",
    "Commute", "Total Weight Lifted", "From Upload", "Grade Adjusted Distance", "Weather Observation Time",
    "Weather Condition", "Weather Temperature", "Apparent Temperature", "Dewpoint", "Humidity",
    "Weather Pressure", "Wind Speed", "Wind Gust", "Wind Bearing", "Precipitation Intensity",
    "Sunrise Time", "Sunset Time", "Moon Phase", "Bike", "Gear",
    "Precipitation Probability", "Precipitation Type", "Cloud Cover", "Weather Visibility", "UV Index",
    "Weather Ozone", "Jump Count", "Total Grit", "Average Flow", "Flagged",
    "Average Elapsed Speed", "Dirt Distance", "Newly Explored Distance", "Newly Explored Dirt Distance",
    "Activity Count",
    "Total Steps", "Carbon Saved", "Pool Length", "Training Load", "Intensity",
    "Average Grade Adjusted Pace", "Timer Time", "Total Cycles", "Recovery", "With Pet",
    "Competition", "Long Run", "For a Cause", "With Kid", "Downhill Distance",
    "Total Sets", "Total Reps", "Media",
)
REPEATED = ("Elapsed Time", "Distance", "Max Heart Rate", "Relative Effort", "Commute")
DEFAULT_DATE = "Apr 18, 2022, 2:26:14 PM"
FOLDER = "export_1347737"


def keys_of(header: Sequence[str]) -> list[str]:
    """The column keys of ``header``: a repeated name's later copies keyed ``<name> (2)``, ``(3)``."""
    seen: dict[str, int] = {}
    keys = []
    for name in header:
        seen[name] = seen.get(name, 0) + 1
        keys.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return keys


KEYS: tuple[str, ...] = tuple(keys_of(FULL_HEADER))


def header_without(key: str, header: Sequence[str] = FULL_HEADER) -> tuple[str, ...]:
    """``header`` without the column keyed ``key`` (``"Distance (2)"`` drops the second ``Distance``)."""
    keys = keys_of(header)
    if key not in keys:
        raise KeyError(key)
    drop = keys.index(key)
    return tuple(name for i, name in enumerate(header) if i != drop)


def row(**cells: str) -> dict[str, str]:
    """One CSV row keyed by ``KEYS``: every cell ``""`` but ``Activity Date`` and the cells given."""
    unknown = set(cells) - set(KEYS)
    if unknown:
        raise KeyError(f"not a column key: {sorted(unknown)}")
    out = dict.fromkeys(KEYS, "")
    out["Activity Date"] = DEFAULT_DATE
    out.update(cells)
    return out


def strava_date(dt: datetime) -> str:
    """``dt`` (UTC) as the export writes ``Activity Date``: ``Apr 18, 2022, 2:26:14 PM``."""
    dt = dt.astimezone(UTC)
    hour = dt.hour % 12 or 12
    return f"{dt:%b} {dt.day}, {dt.year}, {hour}:{dt:%M:%S} {'AM' if dt.hour < 12 else 'PM'}"


def activities_csv(
    rows: Iterable[Mapping[str, str] | Sequence[str]],
    *,
    header: Sequence[str] = FULL_HEADER,
    bom: bool = False,
    lineterminator: str = "\n",
) -> bytes:
    """``activities.csv`` bytes: ``header`` then ``rows``, quoted as ``csv.writer`` quotes them."""
    keys = keys_of(header)
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator=lineterminator)
    writer.writerow(header)
    for r in rows:
        writer.writerow([r.get(k, "") for k in keys] if isinstance(r, Mapping) else list(r))
    return (b"\xef\xbb\xbf" if bom else b"") + text.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# activity files
# ---------------------------------------------------------------------------


def fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.fit").read_bytes()


def gz(data: bytes) -> bytes:
    """``data`` gzipped as the export stores ``.fit.gz`` (mtime 0, so the bytes are reproducible)."""
    return gzip.compress(data, mtime=0)


def cycling_copy(fit: bytes) -> bytes:
    """``fit`` with ``session.sport`` and ``sport.sport`` set to cycling."""
    return fit_patch.set_field(fit_patch.set_field(fit, SESSION, SESSION_SPORT, SPORT_CYCLING), SPORT, SPORT_SPORT, SPORT_CYCLING)


def two_session_copy(fit: bytes) -> bytes:
    """``fit`` with its first ``session`` message written twice."""
    return fit_patch.duplicate_message(fit, SESSION, 0)


def no_session_copy(fit: bytes) -> bytes:
    """``fit`` without any ``session`` message."""
    return fit_patch.remove_messages(fit, SESSION)


def unstarted_copy(fit: bytes) -> bytes:
    """``fit`` with ``session.start_time`` set to the FIT invalid value."""
    return fit_patch.set_field(fit, SESSION, SESSION_START_TIME, UINT32_INVALID)


def with_distance(fit: bytes, centimetres: int) -> bytes:
    """``fit`` with ``session.total_distance`` set to ``centimetres`` (scale 100, so metres = cm / 100)."""
    return fit_patch.set_field(fit, SESSION, SESSION_TOTAL_DISTANCE, centimetres)


def other_device_copy(fit: bytes) -> bytes:
    """``fit`` with ``file_id.product`` set to ``OTHER_PRODUCT``, so it stores beside ``fit`` on one second."""
    return fit_patch.set_field(fit, FILE_ID, FILE_ID_PRODUCT, OTHER_PRODUCT)


def shifted(fit: bytes, seconds: int) -> bytes:
    """``fit`` with every time field moved by ``seconds``."""
    return fit_patch.shift_timestamps(fit, seconds)


def session_start(fit: bytes) -> datetime:
    """The first ``session`` message's ``start_time``, read by ``fitdecode`` with the CRC checked."""
    with fitdecode.FitReader(io.BytesIO(fit), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "session":
                return frame.get_value("start_time")
    raise ValueError("no session message")


CORRUPT_GZ = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff" + b"these bytes do not inflate" * 4
GZ_BOMB_CAP = 1 << 20
GZ_BOMB_EXPANDED = 4 << 20


def gz_bomb(expanded: int = GZ_BOMB_EXPANDED) -> bytes:
    """A small ``.gz`` that inflates to ``expanded`` zero bytes."""
    return gzip.compress(bytes(expanded), mtime=0)


# ---------------------------------------------------------------------------
# the archive
# ---------------------------------------------------------------------------

LAYOUTS = ("root", "folder", "macosx")
_APPLEDOUBLE = b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        " + bytes(8)


PROFILE_NAME = "profile.csv"
# The first columns of the athlete's profile.csv header (12 in all on the 2026-10-24 export).
PROFILE_HEADER = ("Athlete ID", "Email Address", "First Name", "Last Name", "Sex", "Description")


def profile_csv(athlete_id: str, *, bom: bool = False) -> bytes:
    """``profile.csv`` with ``PROFILE_HEADER`` and one row whose ``Athlete ID`` is ``athlete_id``."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(PROFILE_HEADER)
    writer.writerow([athlete_id, "runner@example.com", "Test", "Runner", "M", ""])
    return (b"\xef\xbb\xbf" if bom else b"") + out.getvalue().encode("utf-8")


def strava_zip(
    path: Path,
    rows: Iterable[Mapping[str, str] | Sequence[str]],
    files: Iterable[tuple[str, bytes]] = (),
    *,
    layout: str = "root",
    folder: str = FOLDER,
    header: Sequence[str] = FULL_HEADER,
    bom: bool = False,
    lineterminator: str = "\n",
    extra: Iterable[tuple[str, bytes]] = (),
    profile: str | bytes | None = None,
) -> Path:
    """Write a Strava archive at ``path`` and return it. ``profile``: none written when ``None``;
    a ``str`` writes ``profile.csv`` beside ``activities.csv`` as the athlete's export holds it (a
    header starting ``Athlete ID`` and one row holding that id); ``bytes`` are written as the file."""
    if layout not in LAYOUTS:
        raise ValueError(f"layout {layout!r} is not one of {LAYOUTS}")
    prefix = "" if layout == "root" else f"{folder}/"
    csv_bytes = activities_csv(rows, header=header, bom=bom, lineterminator=lineterminator)
    path = Path(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        if prefix:
            archive.writestr(prefix, b"")
        archive.writestr(f"{prefix}activities/", b"")
        for name, data in files:
            archive.writestr(f"{prefix}{name}", data)
        archive.writestr(f"{prefix}media/", b"")
        archive.writestr(f"{prefix}activities.csv", csv_bytes)
        if profile is not None:
            archive.writestr(f"{prefix}{PROFILE_NAME}", profile_csv(profile) if isinstance(profile, str) else profile)
        if layout == "macosx":
            archive.writestr("__MACOSX/", b"")
            archive.writestr(f"__MACOSX/{folder}/", b"")
            archive.writestr(f"__MACOSX/{folder}/._activities.csv", _APPLEDOUBLE)
        for name, data in extra:
            archive.writestr(name, data)
    return path


# ---------------------------------------------------------------------------
# the catalogue
# ---------------------------------------------------------------------------

OUTCOMES = (
    "attached",
    "attached_by_file_start",
    "stored",
    "stored_retyped",
    "already_stored",
    "ambiguous",
    "multisport_not_stored",
    "failed",
    "not_fit",
    "no_file",
    "not_running",
)
FAILURES = (
    "file_not_in_archive",
    "no_session_message",
    "corrupt_gz",
    "over_cap",
    "fit_does_not_decode",
    "short_row",
    "bad_activity_date",
    "bad_activity_id",
    "duplicate_activity_id",
)


class Case(NamedTuple):
    """One catalogue row and what a first import of the whole catalogue gives it.

    ``cells`` is the CSV row (a dict keyed by ``KEYS``, or a list for a short
    row); ``file`` the archive entry ``(Filename, bytes)`` or ``None``;
    ``stored`` the ``(name, FIT bytes)`` a test uploads before importing;
    ``link`` the name, among every case's ``stored``, of the session the row
    links to; ``failure`` the named cause of a ``failed`` row; ``cap`` the
    per-file cap the test must set for the outcome to hold (``None``: the
    default).
    """

    key: str
    outcome: str
    cells: Mapping[str, str] | tuple[str, ...]
    file: tuple[str, bytes] | None = None
    stored: tuple[tuple[str, bytes], ...] = ()
    link: str | None = None
    warnings: tuple[str, ...] = ()
    failure: str | None = None
    cap: int | None = None
    match: str | None = None


FIRST_ID = 9_000_000_001
_BASE_SHIFT_DAYS = 30


def _metres(cm: int) -> str:
    return f"{cm / 100:.1f}"


@functools.cache
def outcome_rows() -> tuple[Case, ...]:
    """The catalogue: every case, in CSV order (the order the import must not rely on)."""
    sample = fixture("sample_run")
    base_start = session_start(sample)
    cases: list[Case] = []
    counter = iter(range(1, 1000))

    def base(cm: int | None = None, *, extra_s: int = 0) -> tuple[bytes, datetime]:
        """A ``sample_run`` copy on its own day (and ``cm`` centimetres when given), with its start."""
        shift = -(_BASE_SHIFT_DAYS + next(counter)) * DAY_S + extra_s
        data = shifted(sample, shift)
        if cm is not None:
            data = with_distance(data, cm)
        return data, base_start + timedelta(seconds=shift)

    def when() -> datetime:
        """A start on its own day, for a row whose file is not a re-dated ``sample_run``."""
        return base_start - timedelta(days=_BASE_SHIFT_DAYS + next(counter))

    def add(key: str, outcome: str, *, activity_id: int | None = None, file_ext: str = ".fit.gz",
            file_bytes: bytes | None = None, **kw: object) -> int:
        aid = activity_id if activity_id is not None else FIRST_ID + len(cases)
        cells = dict(kw.pop("cells"))  # type: ignore[arg-type]
        cells.setdefault("Activity ID", str(aid))
        cells.setdefault("Activity Name", key.replace("_", " "))
        cells.setdefault("Activity Type", "Run")
        file = None
        if file_bytes is not None:
            name = cells.setdefault("Filename", f"activities/{aid}{file_ext}")
            file = (name, file_bytes)
        cases.append(Case(key=key, outcome=outcome, cells=row(**cells), file=file, **kw))  # type: ignore[arg-type]
        return aid

    def dated(start: datetime, d: str = "") -> dict[str, str]:
        return {"Activity Date": strava_date(start), "Distance (2)": d}

    # Step 1: one candidate at the CSV start.
    fit, start = base(1_000_000)
    add("attached", "attached", cells=dated(start, "10000.0"), file_bytes=gz(fit),
        stored=(("attached.fit", fit),), link="attached.fit", match="csv_start")
    fit, start = base(1_020_000)
    add("attached_distance_differs", "attached", cells=dated(start, "10000.0"), file_bytes=gz(fit),
        stored=(("differs.fit", fit),), link="differs.fit", warnings=("distance_differs",), match="csv_start")
    fit, start = base(1_010_000)
    add("attached_at_exactly_one_percent", "attached", cells=dated(start, "10000.0"), file_bytes=gz(fit),
        stored=(("one_percent.fit", fit),), link="one_percent.fit", match="csv_start")
    fit, start = base(1_010_001)
    add("attached_just_past_one_percent", "attached", cells=dated(start, "10000.0"), file_bytes=gz(fit),
        stored=(("past_one_percent.fit", fit),), link="past_one_percent.fit",
        warnings=("distance_differs",), match="csv_start")
    fit, start = base(1_000_000)
    add("attached_zero_distance", "attached", cells=dated(start, "0.0"), file_bytes=gz(fit),
        stored=(("zero_distance.fit", fit),), link="zero_distance.fit", match="csv_start")

    # Step 1: two candidates on one second (the 2024-07-05 shape and its ambiguous neighbours).
    def pair(key: str, outcome: str, cm: tuple[int, int], d: str, link: int | None) -> None:
        first, start = base(cm[0])
        second = with_distance(other_device_copy(first), cm[1])
        names = (f"{key}_fr945.fit", f"{key}_3476.fit")
        add(key, outcome, cells=dated(start, d), file_bytes=gz(first),
            stored=((names[0], first), (names[1], second)),
            link=None if link is None else names[link], match=None if link is None else "csv_start")

    pair("attached_tiebreak", "attached", (1_371_200, 309_000), "13711.6", 0)
    pair("ambiguous_neither_within", "ambiguous", (1_200_000, 309_000), "10000.0", None)
    pair("ambiguous_both_within", "ambiguous", (1_000_000, 1_005_000), "10000.0", None)
    pair("ambiguous_zero_distance", "ambiguous", (1_000_000, 309_000), "0.0", None)

    # One row per session: the second row on a linked session's second.
    fit, start = base()
    add("attached_first_of_two", "attached", cells=dated(start), file_bytes=gz(fit),
        stored=(("first_of_two.fit", fit),), link="first_of_two.fit", match="csv_start")
    add("ambiguous_already_linked", "ambiguous", cells=dated(start), file_bytes=gz(fit))

    # 12-hour clock: a stored session at 00:34:21 UTC, and one at 12:34:21 UTC.
    fit, start = base(extra_s=-12 * 3600)
    add("twelve_am", "attached", cells=dated(start), file_bytes=gz(fit),
        stored=(("twelve_am.fit", fit),), link="twelve_am.fit", match="csv_start")
    fit, start = base()
    add("twelve_pm", "attached", cells=dated(start), file_bytes=gz(fit),
        stored=(("twelve_pm.fit", fit),), link="twelve_pm.fit", match="csv_start")

    # Step 2: the CSV start moved 50 minutes off the file's start.
    fit, start = base()
    add("attached_by_file_start", "attached_by_file_start", cells=dated(start + timedelta(minutes=50)),
        file_bytes=gz(fit), stored=(("by_file_start.fit", fit),), link="by_file_start.fit", match="file_start")
    fit, start = base()
    add("multisport_not_stored", "multisport_not_stored", cells=dated(start), file_bytes=gz(two_session_copy(fit)))
    fit, start = base()
    add("failed_no_session_message", "failed", cells=dated(start), file_bytes=gz(no_session_copy(fit)),
        failure="no_session_message")

    # Step 3: stored, retyped, already stored.
    fit, start = base()
    add("stored", "stored", cells=dated(start), file_bytes=gz(fit), match="stored")
    fit, start = base()
    add("stored_retyped", "stored_retyped", cells=dated(start), file_bytes=gz(cycling_copy(fit)), match="stored")
    fit, start = base()
    add("already_stored", "already_stored", cells=dated(start + timedelta(minutes=50)),
        file_bytes=gz(unstarted_copy(fit)), stored=(("already_stored.fit", fit),), link="already_stored.fit",
        match="stored")

    # Step 4 and a Ride row's file, never decoded.
    fit, start = base()
    add("not_running_cycling_file", "not_running", cells={**dated(start), "Activity Type": "Ride"},
        file_bytes=gz(cycling_copy(fit)))
    fit, start = base()
    add("not_running_undecodable_file", "not_running", cells={**dated(start), "Activity Type": "Ride"},
        file_bytes=b"these bytes are not a FIT file")
    ride_start = when()
    ride_id = add("not_running", "not_running", cells={**dated(ride_start), "Activity Type": "Ride"})
    fit, start = base()
    add("not_fit_gpx", "not_fit", cells=dated(start), file_ext=".gpx.gz",
        file_bytes=gz(b'<?xml version="1.0"?><gpx version="1.1"></gpx>\n'))
    fit, start = base()
    add("not_fit_tcx", "not_fit", cells=dated(start), file_ext=".tcx.gz",
        file_bytes=gz(b'<?xml version="1.0"?><TrainingCenterDatabase/>\n'))
    start = when()
    add("no_file", "no_file", cells=dated(start))

    # Failed rows, one per named cause.
    start = when()
    add("failed_file_not_in_archive", "failed",
        cells={**dated(start), "Filename": "activities/missing.fit.gz"}, failure="file_not_in_archive")
    start = when()
    add("failed_corrupt_gz", "failed", cells=dated(start), file_bytes=CORRUPT_GZ, failure="corrupt_gz")
    start = when()
    add("failed_over_cap", "failed", cells=dated(start), file_bytes=gz_bomb(), failure="over_cap", cap=GZ_BOMB_CAP)
    fit, start = base()
    add("failed_fit_does_not_decode", "failed", cells=dated(start), file_bytes=gz(fit[: len(fit) // 2]),
        failure="fit_does_not_decode")
    start = when()
    add("failed_bad_activity_date", "failed",
        cells={"Activity Date": "18 avr. 2022 14:26:14"}, failure="bad_activity_date")
    add("failed_bad_activity_id", "failed", activity_id=0,
        cells={**dated(start), "Activity ID": "not-an-id"}, failure="bad_activity_id")
    # The same Activity ID an hour after the ``not_running`` row: rows run oldest first, so this
    # later row is the one failed.
    add("failed_duplicate_activity_id", "failed", activity_id=ride_id,
        cells={**dated(ride_start + timedelta(hours=1)), "Activity Type": "Ride"}, failure="duplicate_activity_id")

    # Cells the CSV must carry intact (on non-running rows with no file: their outcome is fixed).
    start = when()
    add("lf_cell", "not_running", cells={**dated(start), "Activity Type": "Ride",
                                          "Activity Description": "Bailed due to calf problems.\nWalked home."})
    start = when()
    add("crlf_cell", "not_running", cells={**dated(start), "Activity Type": "Ride",
                                            "Activity Private Note": "Foot pain\r\nblew up"})
    start = when()
    add("quote_cell", "not_running", cells={**dated(start), "Activity Type": "Ride",
                                             "Activity Name": 'The "easy" ride, mostly'})
    start = when()
    short = tuple(row(**{**dated(start), "Activity ID": str(FIRST_ID + len(cases)),
                         "Activity Name": "short row", "Activity Type": "Run"}).values())[:12]
    cases.append(Case(key="failed_short_row", outcome="failed", cells=short, failure="short_row"))
    return tuple(cases)


def __getattr__(name: str) -> object:
    if name == "OUTCOME_ROWS":
        return outcome_rows()
    raise AttributeError(name)


def case_zip(path: Path, cases: Iterable[Case] | None = None, **kw: object) -> Path:
    """``strava_zip`` of ``cases`` (default: the whole catalogue): their rows and their files."""
    cases = tuple(outcome_rows() if cases is None else cases)
    return strava_zip(path, [c.cells for c in cases], [c.file for c in cases if c.file is not None], **kw)  # type: ignore[arg-type]


def stored_files(cases: Iterable[Case] | None = None) -> list[tuple[str, bytes]]:
    """Every ``(name, FIT bytes)`` the ``cases`` need stored before the import, in catalogue order."""
    cases = outcome_rows() if cases is None else cases
    return [s for c in cases for s in c.stored]


def by_key(key: str) -> Case:
    return next(c for c in outcome_rows() if c.key == key)
