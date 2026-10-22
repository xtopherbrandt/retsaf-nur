"""Typed exceptions raised by the ingestion pipeline.

``pipeline.py`` and its stub modules only ever raise these (pure-function
-raises pattern, mirroring ``config.py``) -- translation to an HTTP
status happens exclusively at the ``main.py`` boundary.
"""

from __future__ import annotations

import sqlite3


class NotAFitFileError(Exception):
    """Raised when the uploaded bytes are not a FIT file at all."""


class FitParseFailure(Exception):
    """Raised when the uploaded bytes look like a FIT file but fail to parse."""


class DuplicateSessionError(Exception):
    """Raised when the session has already been ingested.

    Carries the ``existing_session_id`` so the caller can report which
    prior session this upload collides with.
    """

    def __init__(self, existing_session_id: str) -> None:
        super().__init__(existing_session_id)
        self.existing_session_id = existing_session_id


class MissingCanonicalFieldError(Exception):
    """Raised when a required canonical field has no derivable value.

    Generalizes what were previously two class-per-field exceptions
    (``MissingSportError`` / ``MissingStartTimeError``): both fired in
    ``mapping.to_canonical`` when a NOT NULL canonical column --
    ``sessions.sport`` (spec §2.2.1 enum field; T034 item 2) or
    ``sessions.start_time`` (needed by ``derive_session_id()``, which
    hashes ``(source_device, start_time)`` per commit 8f7e488 / T032;
    falling back to ``datetime.now()`` would silently reintroduce the
    wall-clock non-determinism T032 closed) -- had nothing to map,
    before a ``Session`` was ever constructed, rather than letting a
    ``NOT NULL`` violation surface at ``db.persist`` time as an
    unhandled 500. Structurally identical per-field raises are
    collapsed into one exception carrying ``field`` as data, the same
    shape ``DuplicateSessionError`` (``db.py``) uses for
    ``existing_session_id`` -- one class, translated at one call site,
    rather than one class per field.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


class TooManyRecordsError(Exception):
    """Raised when a FIT file overruns one of ``fit_parser``'s three
    decode ceilings: ``MAX_RECORD_MESSAGES``, ``MAX_DATA_MESSAGES`` or
    ``MAX_RR_BEATS``.

    The 50MB upload cap bounds an upload's *bytes*; it does not bound
    how many messages -- or how many beats -- those bytes decode into. A
    file of unusually small per-record messages can sit well under the
    byte cap while carrying far more records than any realistic
    activity, and a packed ``hrv`` message costs ~2 bytes per beat, so
    each quantity is bounded separately -- and enforced *during* decode,
    since a ceiling that trips only after the full list is materialised
    would have already spent the memory it exists to bound.

    **One class, three ceilings, three nouns.** All three raise this
    same type on purpose: "this upload is too large" is one semantic
    bucket, so ``main.py`` keeps one ``except`` clause and one 413, and
    the exception type stays stable for callers. What must *not* be
    shared is the noun. A strap-paired workout that trips
    ``MAX_RR_BEATS`` may carry no ``record`` messages at all, so telling
    its uploader they had too many "record messages" names a quantity
    that is zero in their file and gives them nothing to act on.
    ``unit`` is the overflowing quantity's plural name, supplied by the
    ceiling that tripped and rendered both here and in the 413 body.

    Carries the observed ``count``, the ``limit`` it exceeded and that
    ``unit`` as data, the same shape ``DuplicateSessionError`` uses for
    ``existing_session_id``, so the ``main.py`` boundary can report all
    three without re-deriving them.
    """

    def __init__(self, count: int, limit: int, unit: str = "record messages") -> None:
        super().__init__(
            f"FIT file carries more than {limit} {unit} "
            f"(reached {count}); refusing to decode further"
        )
        self.count = count
        self.limit = limit
        self.unit = unit


class OvernightSeriesError(Exception):
    """Raised when an HRV_STATUS file's structure refuses the whole night.

    ``ingestion.overnight.map_night`` raises it for two ``hrv_value``
    messages at one timestamp (the series' primary key is the timestamp,
    so two values there cannot both be stored and neither is the right
    one to keep) and for a second ``hrv_status_summary`` message (a night
    has one summary; two leave the file's average undefined). A fault the
    decoder itself refuses, a series truncated mid-message, a
    ``date_time`` out of range or another field value it cannot read, is
    ``FitParseFailure`` as on every route.
    Nothing is written for a file that raises this.
    """


LOCKED_DETAIL = "database is locked"


def http_status_detail(exc: BaseException) -> tuple[int | None, str]:
    """``(status, detail)`` for an exception raised while ingesting one file.

    For each typed exception above, the status and detail ``POST /sessions``
    answers with, so the one-file route and an archive import report a file
    the same way. Anything else has no HTTP status (``None``): a store held
    locked past the busy timeout (``sqlite3.OperationalError`` "database is
    locked") gives that message alone, so a re-run knows to pick the file up;
    any other exception gives its type and message.
    """
    if isinstance(exc, NotAFitFileError):
        return 400, f"not a valid FIT file: {exc}"
    if isinstance(exc, FitParseFailure):
        return 400, f"file could not be parsed: {exc}"
    if isinstance(exc, TooManyRecordsError):
        return 413, f"upload exceeds the limit of {exc.limit} {exc.unit} (reached {exc.count})"
    if isinstance(exc, MissingCanonicalFieldError):
        return 400, f"{exc.field} could not be determined: {exc}"
    if isinstance(exc, OvernightSeriesError):
        return 400, f"overnight HRV file refused: {exc}"
    if isinstance(exc, DuplicateSessionError):
        return 409, f"already ingested as session {exc.existing_session_id}"
    if isinstance(exc, sqlite3.OperationalError) and str(exc) == LOCKED_DETAIL:
        return None, LOCKED_DETAIL
    return None, f"{type(exc).__name__}: {exc}"
