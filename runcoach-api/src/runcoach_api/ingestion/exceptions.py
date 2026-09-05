"""Typed exceptions raised by the ingestion pipeline.

``pipeline.py`` and its stub modules only ever raise these (pure-function
-raises pattern, mirroring ``config.py``) -- translation to an HTTP
status happens exclusively at the ``main.py`` boundary.
"""

from __future__ import annotations


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
    """Raised when a FIT file carries more ``record`` messages than
    ``fit_parser.MAX_RECORD_MESSAGES`` allows.

    The 50MB upload cap bounds an upload's *bytes*; it does not bound
    how many ``record`` messages those bytes decode into. A file of
    unusually small per-record messages can sit well under the byte cap
    while carrying far more records than any realistic activity, so the
    record count is bounded separately -- and enforced *during* decode,
    since a ceiling that trips only after the full list is materialised
    would have already spent the memory it exists to bound.

    Carries the observed ``count`` and the ``limit`` it exceeded as
    data, the same shape ``DuplicateSessionError`` uses for
    ``existing_session_id``, so the ``main.py`` boundary can report both
    without re-deriving them.
    """

    def __init__(self, count: int, limit: int) -> None:
        super().__init__(
            f"FIT file carries more than {limit} record messages "
            f"(reached {count}); refusing to decode further"
        )
        self.count = count
        self.limit = limit
