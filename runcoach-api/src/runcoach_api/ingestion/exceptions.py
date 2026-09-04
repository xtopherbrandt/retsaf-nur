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


class OversizedUploadError(Exception):
    """Raised when the upload exceeds the configured size limit."""


class MissingSportError(Exception):
    """Raised when no ``sport`` can be determined from the FIT file.

    ``sessions.sport`` is ``NOT NULL`` (spec §2.2.1 enum field); a file
    with neither a ``session`` nor a ``sport`` message (e.g. a watch
    that died mid-activity) leaves ``mapping.to_canonical`` with no
    value to map. Raised in ``mapping.py`` -- before a ``Session`` with
    an invalid ``sport`` is ever constructed -- rather than letting the
    ``NOT NULL`` violation surface at ``db.persist`` time as an
    unhandled 500 (T034 item 2).
    """
