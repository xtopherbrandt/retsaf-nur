"""The types a Strava archive import shares between its reader, its file reader, its matching rules
and its job (F030 reference sections 2-4). Pure: no I/O, no store.

- ``CsvRow``: one parsed ``activities.csv`` row, every cell kept in ``columns``.
- ``FileError``: an activity file that could not be read or decoded, with its reason, the HTTP
  status a size cap answers (413) and a detail.
- ``Decision``: what the matching rules decided for one row.
- The outcome, match and warning names, so every module spells them from one definition. The
  report schema's ``ImportOutcome`` holds every outcome named here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Row outcomes (reference section 3). ``stored``, ``already_stored`` and ``failed`` are the names
# the Garmin import already reports; the others are the Strava rows'.
ATTACHED = "attached"
ATTACHED_BY_FILE_START = "attached_by_file_start"
STORED = "stored"
STORED_RETYPED = "stored_retyped"
ALREADY_STORED = "already_stored"
AMBIGUOUS = "ambiguous"
MULTISPORT_NOT_STORED = "multisport_not_stored"
NOT_FIT = "not_fit"
NO_FILE = "no_file"
NOT_RUNNING = "not_running"
FAILED = "failed"
UNCHANGED = "unchanged"
UPDATED = "updated"
DELETED_IN_STRAVA = "deleted_in_strava"

# Every outcome a Strava row can report, in the order the report counts them.
STRAVA_OUTCOMES = (
    ATTACHED,
    ATTACHED_BY_FILE_START,
    STORED,
    STORED_RETYPED,
    ALREADY_STORED,
    AMBIGUOUS,
    MULTISPORT_NOT_STORED,
    NOT_FIT,
    NO_FILE,
    NOT_RUNNING,
    FAILED,
    UNCHANGED,
    UPDATED,
    DELETED_IN_STRAVA,
)

# How a row's link was found (``strava_activities.match``; null when the row links nothing).
MATCH_CSV_START = "csv_start"
MATCH_FILE_START = "file_start"
MATCH_STORED = "stored"

# The one warning a linked row can carry; it never changes the outcome.
DISTANCE_DIFFERS = "distance_differs"

# The header names of the two free-text cells the store keeps in their own columns.
DESCRIPTION_COLUMN = "Activity Description"
PRIVATE_NOTE_COLUMN = "Activity Private Note"


@dataclass(frozen=True)
class CsvRow:
    """One ``activities.csv`` row that parsed (reference section 2.3-2.4).

    ``columns`` holds every cell as the CSV's text in header order (``""`` for an empty cell), a
    repeated header name's later copies keyed ``<name> (2)``, ``<name> (3)``. The other fields are
    the cells the rules read, converted: ``start_time`` is ``Activity Date`` as UTC ISO 8601 with
    ``+00:00`` (the store's spelling), ``distance_m`` is ``Distance (2)`` in metres, ``None`` when
    empty or ``0.0``. ``line`` is the physical line of the CSV the row starts on."""

    line: int
    activity_id: int
    start_time: str
    activity_type: str
    name: str
    filename: str
    distance_m: float | None
    columns: dict[str, str]

    @property
    def description(self) -> str | None:
        """The ``Activity Description`` cell, or ``None`` when the header has no such column."""
        return self.columns.get(DESCRIPTION_COLUMN)

    @property
    def private_note(self) -> str | None:
        """The ``Activity Private Note`` cell, or ``None`` when the header has no such column."""
        return self.columns.get(PRIVATE_NOTE_COLUMN)


@dataclass(frozen=True)
class FileError:
    """An activity file that was not read: ``reason`` names why (``file_not_in_archive``,
    ``no_session_message``, the error a read or decode raised), ``status`` is the HTTP status a
    size cap answers (413) or ``None``, ``detail`` any further text."""

    reason: str
    status: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class Decision:
    """What the matching rules decided for one row.

    ``outcome`` is one of ``STRAVA_OUTCOMES``; ``session_id`` the session the row links (``None``
    when it links nothing); ``match`` one of the ``MATCH_*`` names or ``None``; ``warnings`` the
    row's warnings (``DISTANCE_DIFFERS``); ``detail`` the report's text (``candidates: <id>, <id>``
    for an ambiguous row, the reason for a failed one); ``store`` is true when the row's file must
    be stored as a Strava-only run, which then decides the stored outcome."""

    outcome: str
    session_id: str | None = None
    match: str | None = None
    warnings: tuple[str, ...] = field(default=())
    detail: str | None = None
    store: bool = False
