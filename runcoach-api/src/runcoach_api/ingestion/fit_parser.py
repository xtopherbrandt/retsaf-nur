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

from runcoach_api.ingestion.exceptions import (
    FitParseFailure,
    NotAFitFileError,
    TooManyRecordsError,
)

_FIT_MAGIC = b".FIT"
_MAGIC_OFFSET = 8

# Ceiling on ``record`` messages accepted from a single FIT file.
#
#   base    21,600 records = 6 h x 3600 s at 1Hz -- F003's NFR ("single
#                            athlete, realistic activity durations up to
#                            several hours at 1Hz").
#   ceiling 100,000 records ~ 28 h at 1Hz, ~4.6x the base.
#
# The 4.6x multiple is this codebase's own engineering judgement, not
# something the domain spec derives; it is logged as such in F003's
# Decision Log. 100,000 sits far beyond any single running activity --
# including a 100-mile ultra -- while still being a real bound: the
# largest fixture in the corpus (``dev_fields_run.fit``) carries ~3,118
# records, so the ceiling keeps ~32x headroom over anything real.
#
# Enforced inside ``decode()``'s per-frame loop rather than in
# ``quality_gates``: by the time the gates run, ``mapping.to_canonical``
# has already materialised the full record list and the memory this
# bound exists to protect is already spent.
MAX_RECORD_MESSAGES = 100_000


def decode(raw: bytes) -> list[fitdecode.FitDataMessage]:
    """Decode FIT ``raw`` bytes into a list of data-message frames.

    Raises:
        NotAFitFileError: ``raw`` is too short or lacks the ``.FIT``
            header signature at byte offset 8.
        FitParseFailure: the bytes carry a valid FIT header but
            ``fitdecode`` fails to parse the body (bad CRC, malformed
            definition/data records, etc.).
        TooManyRecordsError: the stream carries more than
            ``MAX_RECORD_MESSAGES`` ``record`` messages. Raised from
            inside the frame loop, the moment the ceiling is passed --
            the reader is abandoned there and the remaining frames are
            never pulled, so a hostile file costs only the frames read
            up to that point.
    """
    magic = raw[_MAGIC_OFFSET : _MAGIC_OFFSET + len(_FIT_MAGIC)]
    if magic != _FIT_MAGIC:
        raise NotAFitFileError(
            f"missing '.FIT' header signature at byte offset {_MAGIC_OFFSET}"
        )

    messages: list[fitdecode.FitDataMessage] = []
    record_count = 0
    try:
        with fitdecode.FitReader(
            io.BytesIO(raw),
            check_crc=fitdecode.CrcCheck.RAISE,
            error_handling=fitdecode.ErrorHandling.RAISE,
        ) as reader:
            for frame in reader:
                if isinstance(frame, fitdecode.FitDataMessage):
                    if frame.name == "record":
                        record_count += 1
                        if record_count > MAX_RECORD_MESSAGES:
                            raise TooManyRecordsError(
                                count=record_count, limit=MAX_RECORD_MESSAGES
                            )
                    messages.append(frame)
    except (
        fitdecode.FitCRCError,
        fitdecode.FitEOFError,
        fitdecode.FitParseError,
    ) as exc:
        raise FitParseFailure(str(exc)) from exc

    return messages
