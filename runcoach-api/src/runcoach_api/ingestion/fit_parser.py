"""FIT file decoding: header pre-check + strict ``fitdecode`` parse.

``decode(raw)`` returns the flat list of ``fitdecode.FitDataMessage``
frames found in the file (definition/header/CRC frames are dropped --
callers only ever need the data messages). Two things are deliberate
here, not incidental:

1. The ``.FIT`` signature at byte offset 8 is checked *before* handing
   the bytes to ``fitdecode`` at all. This is the seam later tasks
   (corrupt-file vs. not-a-FIT-file rejection) build on: bytes that
   fail this check never reach the parser and raise
   ``NotAFitFileError``; bytes that pass but fail to parse raise
   ``FitParseFailure`` instead.
2. ``fitdecode.FitReader`` is opened with ``check_crc=CrcCheck.RAISE``
   and ``error_handling=ErrorHandling.RAISE`` explicitly. Both default
   to lenient modes (``CrcCheck.WARN`` / ``ErrorHandling.WARN``) that
   tolerate malformed data by design -- the opposite of what a
   validating ingestion pipeline needs.
"""

from __future__ import annotations

import io

import fitdecode

from runcoach_api.ingestion.exceptions import FitParseFailure, NotAFitFileError

_FIT_MAGIC = b".FIT"
_MAGIC_OFFSET = 8


def decode(raw: bytes) -> list[fitdecode.FitDataMessage]:
    """Decode FIT ``raw`` bytes into a list of data-message frames.

    Raises:
        NotAFitFileError: ``raw`` is too short or lacks the ``.FIT``
            header signature at byte offset 8.
        FitParseFailure: the bytes carry a valid FIT header but
            ``fitdecode`` fails to parse the body (bad CRC, malformed
            definition/data records, etc.).
    """
    magic = raw[_MAGIC_OFFSET : _MAGIC_OFFSET + len(_FIT_MAGIC)]
    if magic != _FIT_MAGIC:
        raise NotAFitFileError(
            f"missing '.FIT' header signature at byte offset {_MAGIC_OFFSET}"
        )

    messages: list[fitdecode.FitDataMessage] = []
    try:
        with fitdecode.FitReader(
            io.BytesIO(raw),
            check_crc=fitdecode.CrcCheck.RAISE,
            error_handling=fitdecode.ErrorHandling.RAISE,
        ) as reader:
            for frame in reader:
                if isinstance(frame, fitdecode.FitDataMessage):
                    messages.append(frame)
    except (
        fitdecode.FitCRCError,
        fitdecode.FitEOFError,
        fitdecode.FitParseError,
    ) as exc:
        raise FitParseFailure(str(exc)) from exc

    return messages
