"""FIT file decoding: header pre-check + strict ``fitdecode`` parse.

``decode(raw)`` returns the flat list of ``fitdecode.FitDataMessage``
frames found in the file (definition/header/CRC frames are dropped --
callers only ever need the data messages). Three things are deliberate
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
3. Three ceilings -- ``MAX_RECORD_MESSAGES``, ``MAX_DATA_MESSAGES``
   and ``MAX_RR_BEATS`` -- are enforced *inside* the frame loop, not
   after it. The 50MB upload cap bounds an upload's bytes; it bounds
   neither what those bytes decode into nor what the decoded messages
   cost downstream, and a ceiling checked after the loop has already
   spent the memory it exists to bound. Each constant's own comment
   carries its calibration.
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

# Ceiling on *all* data messages decoded from a single FIT file.
#
# ``MAX_RECORD_MESSAGES`` bounds only frames named ``record``. That
# scope was deliberate (IDEA-004) but left the list ``decode()``
# actually materialises unbounded: a file made entirely of any other
# data message -- ``hrv``, ``device_info``, anything -- cleared the
# 50MB byte cap, cleared the record ceiling, and was counted by
# nothing. The smallest legal data message is a few bytes on the wire
# and a ``FitDataMessage`` object with its field list in memory, so
# 50MB of them is millions of objects and gigabytes of heap.
#
#   largest fixture   12,817 data messages (``dev_fields_run.fit``,
#                     of which 3,118 are ``record``) -- 39x headroom.
#   record ceiling    100,000 records at the corpus's ~4.1 messages
#                     per record is ~410,000 data messages, so a
#                     record-shaped file still meets MAX_RECORD_MESSAGES
#                     first: this is a backstop for the shapes that
#                     ceiling cannot see, not a replacement for it.
MAX_DATA_MESSAGES = 500_000

# Ceiling on RR beats carried by ``hrv`` (#78) messages.
#
# This is the bound that closes the sprint-003 review finding: beats
# ride on ``hrv`` messages, which ``MAX_RECORD_MESSAGES`` cannot see,
# and ~2 bytes per packed beat means a very large beat series is a
# very small upload. It is a **memory/volume backstop**, sized so that
# no legitimate activity is ever rejected -- *not* a CPU-time budget.
#
# Sizing, from the corpus's own beat rate. ``dev_fields_run.fit``
# carries 7,220 real beats over 3,118 seconds of 1Hz records = 2.316
# beats/sec (~139 bpm), so at that rate:
#
#   1h -> ~8,340 | 2h -> ~16,670 | 3h -> ~25,010 | 4h -> ~33,340
#   5h -> ~41,680 | 18h -> ~150,000
#
# This is a marathon-focused coaching system: a 3-hour strap-paired
# long run is a core session, not an edge case, and an ultra is a
# planned-for one. 150,000 beats is ~18 hours at that rate and ~21x
# the RR-heaviest fixture, so the ceiling sits past any plausible
# single activity while still bounding what ``decode()`` will
# materialise and hand on.
#
# What this ceiling deliberately does NOT do. ``rr_reconstruction``
# turns these beats into a series whose baseline scan
# (``_find_baseline_index``) is O(n^2) in the number of *runs*, and
# ``reconstruct()`` runs twice per ingest (``mapping._infer_hr_source``
# and ``pipeline.ingest_fit_bytes``) on a sync endpoint that pins an
# anyio threadpool worker for its whole duration. Measured here on
# adversarial alternating beats -- every beat its own run, the O(n^2)
# worst case -- 8,000 -> 11.2s, 16,000 -> 64.0s, 20,000 -> 88.1s; a
# hostile series at *this* ceiling still costs roughly an hour of CPU.
#
# That cost is accepted under the local-first, single-athlete threat
# model: the only uploader is the athlete, on their own machine,
# against their own data. Real RR series cost nothing like it -- they
# do not alternate every beat, so they hold few runs: the 7,220-beat
# fixture reconstructs in 0.04s. Choosing a ceiling from the hostile
# curve would have bound the honest case (20,000 beats rejects a
# 3-hour long run) while the hostile case stayed expensive anyway.
#
# The actual fix for the quadratic is IDEA-008: linearise
# ``_find_baseline_index`` and memoize the double ``reconstruct()``.
# Until that lands, this bound caps volume, not time.
MAX_RR_BEATS = 150_000


def _hrv_beat_count(frame: fitdecode.FitDataMessage) -> int:
    """Beats carried by one ``hrv`` message's ``time`` array.

    Counts exactly what ``rr_reconstruction._hrv_candidates`` will
    later keep, and for the same two reasons:

    * ``fitdecode`` returns a scalar rather than a tuple when an array
      field carries exactly one element, and a single-slot ``hrv``
      message is legal FIT.
    * A real ``hrv`` array is fixed-width and ``None``-padded --
      ``dev_fields_run.fit`` carries 15,635 slots for 7,220 real beats
      -- so counting slots would more than halve the effective
      headroom while bounding a cost nothing downstream pays.
    """
    time_values = frame.get_value("time", fallback=None)
    if time_values is None:
        return 0
    if not isinstance(time_values, (tuple, list)):
        time_values = (time_values,)
    return sum(1 for value in time_values if isinstance(value, (int, float)))


def decode(raw: bytes) -> list[fitdecode.FitDataMessage]:
    """Decode FIT ``raw`` bytes into a list of data-message frames.

    Raises:
        NotAFitFileError: ``raw`` is too short or lacks the ``.FIT``
            header signature at byte offset 8.
        FitParseFailure: the bytes carry a valid FIT header but
            ``fitdecode`` fails to parse the body (bad CRC, malformed
            definition/data records, etc.).
        TooManyRecordsError: the stream passes one of the three
            ceilings -- more than ``MAX_RECORD_MESSAGES`` ``record``
            messages, more than ``MAX_DATA_MESSAGES`` data messages of
            any name, or more than ``MAX_RR_BEATS`` beats carried by
            ``hrv`` messages. All three are raised from inside the
            frame loop, the moment the ceiling is passed -- the reader
            is abandoned there and the remaining frames are never
            pulled, so a hostile file costs only the frames read up to
            that point.

            One exception type covers all three deliberately: they are
            one semantic bucket ("this upload is too large"), they all
            map to the same 413 at the ``main.py`` boundary, and a
            sibling exception would need a handler there that this
            change is not scoped to add. ``count``/``limit`` still
            carry the exact numbers.
    """
    magic = raw[_MAGIC_OFFSET : _MAGIC_OFFSET + len(_FIT_MAGIC)]
    if magic != _FIT_MAGIC:
        raise NotAFitFileError(
            f"missing '.FIT' header signature at byte offset {_MAGIC_OFFSET}"
        )

    messages: list[fitdecode.FitDataMessage] = []
    record_count = 0
    data_message_count = 0
    beat_count = 0
    try:
        with fitdecode.FitReader(
            io.BytesIO(raw),
            check_crc=fitdecode.CrcCheck.RAISE,
            error_handling=fitdecode.ErrorHandling.RAISE,
        ) as reader:
            for frame in reader:
                if isinstance(frame, fitdecode.FitDataMessage):
                    data_message_count += 1
                    if data_message_count > MAX_DATA_MESSAGES:
                        raise TooManyRecordsError(
                            count=data_message_count, limit=MAX_DATA_MESSAGES
                        )
                    if frame.name == "record":
                        record_count += 1
                        if record_count > MAX_RECORD_MESSAGES:
                            raise TooManyRecordsError(
                                count=record_count, limit=MAX_RECORD_MESSAGES
                            )
                    elif frame.name == "hrv":
                        beat_count += _hrv_beat_count(frame)
                        if beat_count > MAX_RR_BEATS:
                            raise TooManyRecordsError(
                                count=beat_count, limit=MAX_RR_BEATS
                            )
                    messages.append(frame)
    except (
        fitdecode.FitCRCError,
        fitdecode.FitEOFError,
        fitdecode.FitParseError,
    ) as exc:
        raise FitParseFailure(str(exc)) from exc

    return messages
