"""T053: FIT ingest is bounded by *what it decodes*, not only by bytes.

The 50MB upload cap bounds the compressed-on-the-wire size of an
upload; it does not bound what those bytes decode into. ``fit_parser``
closes that with three ceilings, all enforced *inside* ``decode()``'s
per-frame loop:

* ``MAX_RECORD_MESSAGES`` -- ``record`` messages (T053, original).
* ``MAX_DATA_MESSAGES`` -- every data message, whatever its name.
* ``MAX_RR_BEATS`` -- beats carried by ``hrv`` (#78) messages.

The latter two close a sprint-003 review finding: T053's bound was
scoped to ``record`` messages only, so a file made entirely of ``hrv``
messages -- the carrier this sprint's resting-HRV feature actually
consumes -- passed the byte cap and the record ceiling while being
counted by nothing. At ~2 bytes per packed beat, an unbounded beat
series is a tiny upload.

Both added ceilings are **volume backstops**, not CPU-time budgets.
The beats do feed ``rr_reconstruction``, whose baseline scan is O(n^2)
in the number of runs and which runs twice per ingest from a sync
endpoint -- but that curve is only steep on adversarial input (every
beat its own run), and pricing the ceiling off it would reject honest
training data: at the corpus's own 2.316 beats/sec, a 3-hour long run
is 25,016 beats. ``MAX_RR_BEATS`` is therefore set past any plausible
activity (~18h) and the quadratic is fixed separately, in IDEA-008.
``test_the_beat_ceiling_clears_a_projected_multi_hour_session`` is
what holds that line.

Three properties carry the weight here, and all three are asserted:

1. **Every guard short-circuits.** A ceiling that only trips after the
   whole message list has been materialised bounds nothing -- the
   memory is already spent by then. The ``..._short_circuits_...``
   tests drive ``decode()`` against a frame stream that raises if it is
   pulled even one frame past the point where the ceiling is exceeded,
   so a "count afterwards" implementation cannot pass them.
2. **No ceiling fires on real data.** All six fixtures in the corpus
   are decoded at the shipped ceilings, and their record, data-message
   and beat counts are asserted to sit well below them.
3. **The bounds are on the right units.** Beats, not ``hrv`` messages
   (one packed message carries a whole array), and real beats, not
   array slots (real ``hrv`` arrays are ``None``-padded).

Over-ceiling streams are synthesised (a monkeypatched low ceiling, or
a generated frame stream) rather than committed as a huge fixture --
generating a real 100k-record FIT file is impractical and the task
file explicitly forbids committing one.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import fitdecode
import pytest
from fastapi.testclient import TestClient

from runcoach_api.ingestion import fit_parser
from runcoach_api.ingestion.exceptions import TooManyRecordsError
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"

ALL_FIXTURES = [
    "dev_fields_run.fit",
    "sample_health_snapshot.fit",
    "sample_run.fit",
    "strap_health_snapshot.fit",
    "strap_hrv_sample_run.fit",
    "wrist_ppg_run.fit",
]

# The largest real fixture in the corpus, by record count -- the
# calibration point for the ceiling's headroom (task file: 3,118).
LARGEST_FIXTURE = "dev_fields_run.fit"


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _record_count(name: str) -> int:
    return sum(1 for m in fit_parser.decode(_fixture_bytes(name)) if m.name == "record")


def _beat_count(messages) -> int:
    """Beats carried by ``hrv`` messages, counted independently of
    ``fit_parser`` -- the same numeric-slot rule
    ``rr_reconstruction._hrv_candidates`` applies when it turns those
    slots into the series the O(n^2) baseline scan then walks.
    """
    beats = 0
    for message in messages:
        if message.name != "hrv":
            continue
        values = message.get_value("time", fallback=None)
        if values is None:
            continue
        if not isinstance(values, (tuple, list)):
            values = (values,)
        beats += sum(1 for value in values if isinstance(value, (int, float)))
    return beats


def _fake_named_frame(name: str) -> MagicMock:
    frame = MagicMock(spec=fitdecode.FitDataMessage)
    frame.name = name
    return frame


def _fake_hrv_frame_values(time_value) -> MagicMock:
    """An ``hrv`` frame whose ``time`` field yields ``time_value``.

    ``None`` stands for "no ``time`` field at all"; a tuple stands for
    the packed array a real ``hrv`` message carries (``None`` members
    are the fixed-width array's unused sentinel slots); a bare float
    stands for the single-element case ``fitdecode`` returns as a
    scalar.
    """
    frame = MagicMock(spec=fitdecode.FitDataMessage)
    frame.name = "hrv"
    frame.get_value = (
        lambda key, fallback=None, _v=time_value: _v if key == "time" else fallback
    )
    return frame


def _fake_hrv_frame(beats: int) -> MagicMock:
    return _fake_hrv_frame_values(tuple(0.8 for _ in range(beats)))


def _fake_record_frame() -> MagicMock:
    """A stand-in that satisfies ``isinstance(frame, FitDataMessage)``.

    ``MagicMock(spec=...)`` passes isinstance against the spec'd class,
    which is what ``decode()``'s frame filter tests.
    """
    frame = MagicMock(spec=fitdecode.FitDataMessage)
    frame.name = "record"
    return frame


class _FakeReader:
    """Context-manager stand-in for ``fitdecode.FitReader``.

    Iterating it pulls lazily from ``frames``; nothing is materialised
    up front, exactly as the real reader streams.
    """

    def __init__(self, frames) -> None:
        self._frames = frames

    def __enter__(self) -> "_FakeReader":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def __iter__(self):
        return iter(self._frames)


def _install_fake_reader(monkeypatch: pytest.MonkeyPatch, frames) -> None:
    monkeypatch.setattr(
        fit_parser.fitdecode, "FitReader", lambda *a, **k: _FakeReader(frames)
    )


class _PulledTooFar(Exception):
    """Raised by the endless streams below when ``decode()`` reads on
    past the frame that should have tripped a ceiling."""


def _drive_endless_stream(
    monkeypatch: pytest.MonkeyPatch, limit: int, make_frame
) -> list[int]:
    """Drive ``decode()`` against a stream that never ends, where frame
    N is the Nth unit of whatever ceiling is under test.

    An implementation that decoded the whole stream and counted
    afterwards would pull frame ``limit + 2`` and blow up with
    ``_PulledTooFar`` instead of raising ``TooManyRecordsError`` --
    so this can only return if the guard fires inside the frame loop.
    Returns the frame numbers actually pulled.
    """
    pulled: list[int] = []

    def endless_frames():
        index = 0
        while True:
            index += 1
            if index > limit + 1:
                raise _PulledTooFar(
                    f"decode() pulled frame {index}: the guard did not short-circuit"
                )
            pulled.append(index)
            yield make_frame()

    _install_fake_reader(monkeypatch, endless_frames())

    with pytest.raises(TooManyRecordsError):
        fit_parser.decode(_HEADER_ONLY)

    return pulled


# A byte prefix that clears decode()'s ``.FIT`` magic pre-check, so the
# fake reader (not the header guard) is what the test exercises.
_HEADER_ONLY = b"\x00" * fit_parser._MAGIC_OFFSET + fit_parser._FIT_MAGIC


def test_ceiling_is_one_hundred_thousand() -> None:
    assert fit_parser.MAX_RECORD_MESSAGES == 100_000


def test_the_two_added_ceilings_are_the_shipped_numbers() -> None:
    """Pinned so a later edit has to argue with the reasoning in
    ``fit_parser``'s comments rather than quietly slide them."""
    assert fit_parser.MAX_DATA_MESSAGES == 500_000
    assert fit_parser.MAX_RR_BEATS == 150_000


def test_over_ceiling_stream_raises_too_many_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fit_parser, "MAX_RECORD_MESSAGES", 10)
    frames = [_fake_record_frame() for _ in range(11)]
    _install_fake_reader(monkeypatch, frames)

    with pytest.raises(TooManyRecordsError) as excinfo:
        fit_parser.decode(_HEADER_ONLY)

    assert excinfo.value.limit == 10
    assert excinfo.value.count == 11


def test_stream_exactly_at_ceiling_parses_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fit_parser, "MAX_RECORD_MESSAGES", 10)
    frames = [_fake_record_frame() for _ in range(10)]
    _install_fake_reader(monkeypatch, frames)

    assert len(fit_parser.decode(_HEADER_ONLY)) == 10


def test_guard_short_circuits_during_decode_rather_than_counting_afterwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ceiling must abandon the parse mid-stream.

    The generator below raises if it is asked for a frame beyond the
    one that trips the ceiling. An implementation that decoded the
    whole stream and counted afterwards would pull frame 12 (and every
    frame after it) and blow up with ``_PulledTooFar`` instead of
    raising ``TooManyRecordsError`` -- so this test can only pass if
    the guard fires inside the per-frame loop.
    """
    limit = 10
    monkeypatch.setattr(fit_parser, "MAX_RECORD_MESSAGES", limit)
    pulled = _drive_endless_stream(monkeypatch, limit, _fake_record_frame)

    assert pulled == list(range(1, limit + 2))


def test_only_record_messages_count_toward_the_record_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``MAX_RECORD_MESSAGES`` stays scoped to ``record`` messages.

    The data-message and beat ceilings added alongside it do not widen
    this one: non-record frames still do not count toward it. What
    changed is that they are no longer counted by *nothing* -- see
    ``test_non_record_messages_are_bounded_by_the_data_message_ceiling``.
    """
    monkeypatch.setattr(fit_parser, "MAX_RECORD_MESSAGES", 3)
    frames = [_fake_named_frame("device_info") for _ in range(20)]
    frames.extend(_fake_record_frame() for _ in range(3))
    _install_fake_reader(monkeypatch, frames)

    assert len(fit_parser.decode(_HEADER_ONLY)) == 23


def test_non_record_messages_are_bounded_by_the_data_message_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The NEW contract, replacing the record-only scope note.

    A file made entirely of non-``record`` data messages used to clear
    every guard in the parser: under the byte cap, under the record
    ceiling, counted by nothing. ``MAX_DATA_MESSAGES`` bounds the list
    ``decode()`` actually materialises, whatever the messages are named.
    """
    monkeypatch.setattr(fit_parser, "MAX_DATA_MESSAGES", 10)
    frames = [_fake_named_frame("device_info") for _ in range(11)]
    _install_fake_reader(monkeypatch, frames)

    with pytest.raises(TooManyRecordsError) as excinfo:
        fit_parser.decode(_HEADER_ONLY)

    assert excinfo.value.limit == 10
    assert excinfo.value.count == 11


def test_stream_exactly_at_the_data_message_ceiling_parses_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fit_parser, "MAX_DATA_MESSAGES", 10)
    _install_fake_reader(
        monkeypatch, [_fake_named_frame("device_info") for _ in range(10)]
    )

    assert len(fit_parser.decode(_HEADER_ONLY)) == 10


def test_data_message_guard_short_circuits_during_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = 10
    monkeypatch.setattr(fit_parser, "MAX_DATA_MESSAGES", limit)
    pulled = _drive_endless_stream(
        monkeypatch, limit, lambda: _fake_named_frame("device_info")
    )

    assert pulled == list(range(1, limit + 2))


def test_all_hrv_stream_raises_rather_than_decoding_unbounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The finding this bound exists for.

    RR beats ride on ``hrv`` (#78) messages, and a packed ``hrv``
    message costs ~2 bytes per beat -- so ~100k beats is a ~200KB
    upload, well under both the 50MB cap and the record ceiling. Those
    beats feed ``rr_reconstruction``, whose baseline scan is O(n^2) in
    the number of runs, and ``reconstruct()`` runs twice per ingest on
    a sync (threadpool-pinning) request. ``MAX_RR_BEATS`` bounds the
    beats before they are ever handed on.
    """
    monkeypatch.setattr(fit_parser, "MAX_RR_BEATS", 10)
    # Deliberately zero ``record`` messages and only 6 data messages:
    # neither the record ceiling nor a message-count bound sees this.
    frames = [_fake_hrv_frame(2) for _ in range(6)]
    _install_fake_reader(monkeypatch, frames)

    with pytest.raises(TooManyRecordsError) as excinfo:
        fit_parser.decode(_HEADER_ONLY)

    assert excinfo.value.limit == 10
    assert excinfo.value.count == 12


def test_hrv_stream_exactly_at_the_beat_ceiling_parses_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fit_parser, "MAX_RR_BEATS", 10)
    _install_fake_reader(monkeypatch, [_fake_hrv_frame(2) for _ in range(5)])

    assert len(fit_parser.decode(_HEADER_ONLY)) == 5


def test_beat_guard_short_circuits_during_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One beat per message, so frame N carries beat N: an
    implementation that counted beats after materialising the stream
    would pull past the trip point and raise ``_PulledTooFar``.
    """
    limit = 10
    monkeypatch.setattr(fit_parser, "MAX_RR_BEATS", limit)
    pulled = _drive_endless_stream(monkeypatch, limit, lambda: _fake_hrv_frame(1))

    assert pulled == list(range(1, limit + 2))


def test_a_single_packed_hrv_message_can_trip_the_beat_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message-count bound alone would not close the finding: one
    legal ``hrv`` message carries a whole packed array of beats.
    """
    monkeypatch.setattr(fit_parser, "MAX_RR_BEATS", 10)
    _install_fake_reader(monkeypatch, [_fake_hrv_frame(11)])

    with pytest.raises(TooManyRecordsError) as excinfo:
        fit_parser.decode(_HEADER_ONLY)

    assert excinfo.value.count == 11


def test_scalar_hrv_time_counts_as_one_beat(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fitdecode`` returns a scalar, not a tuple, when an array field
    carries exactly one element (``rr_reconstruction._hrv_candidates``
    handles the same shape). The counter must not choke on it.
    """
    monkeypatch.setattr(fit_parser, "MAX_RR_BEATS", 2)
    _install_fake_reader(monkeypatch, [_fake_hrv_frame_values(0.8) for _ in range(3)])

    with pytest.raises(TooManyRecordsError) as excinfo:
        fit_parser.decode(_HEADER_ONLY)

    assert excinfo.value.count == 3


def test_empty_sentinel_slots_do_not_count_as_beats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real ``hrv`` messages pad their fixed-width ``time`` array with
    ``None`` sentinels -- ``dev_fields_run.fit`` carries 15,635 slots
    for 7,220 real beats. Counting slots would more than halve the
    effective headroom, and ``rr_reconstruction`` discards them, so the
    bound counts the beats that actually reach the amplifier.
    """
    monkeypatch.setattr(fit_parser, "MAX_RR_BEATS", 5)
    # 4 real beats spread over 20 array slots: under the ceiling by the
    # beat rule, four times over it by a slot-counting one.
    frames = [_fake_hrv_frame_values((0.8, None, None, None, None)) for _ in range(4)]
    _install_fake_reader(monkeypatch, frames)

    assert len(fit_parser.decode(_HEADER_ONLY)) == 4


def test_hrv_message_without_a_time_field_counts_no_beats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fit_parser, "MAX_RR_BEATS", 1)
    _install_fake_reader(monkeypatch, [_fake_hrv_frame_values(None) for _ in range(10)])

    assert len(fit_parser.decode(_HEADER_ONLY)) == 10


def test_error_carries_count_and_limit_as_data() -> None:
    """Both numbers must survive into the message, in the right roles.

    Asserting a shared prefix (``"100" in str(exc)``) is satisfied by
    either number alone -- and by a message that swapped them -- so the
    full formatted string is asserted instead.
    """
    exc = TooManyRecordsError(count=100_001, limit=100_000)
    assert exc.count == 100_001
    assert exc.limit == 100_000
    assert str(exc) == (
        "FIT file carries more than 100000 record messages "
        "(reached 100001); refusing to decode further"
    )


@pytest.mark.parametrize("fixture_name", ALL_FIXTURES)
def test_real_fixture_still_decodes_under_the_shipped_ceiling(fixture_name: str) -> None:
    """No monkeypatching: the real ceilings, the real files."""
    messages = fit_parser.decode(_fixture_bytes(fixture_name))
    records = sum(1 for m in messages if m.name == "record")
    assert records < fit_parser.MAX_RECORD_MESSAGES
    assert len(messages) < fit_parser.MAX_DATA_MESSAGES
    assert _beat_count(messages) < fit_parser.MAX_RR_BEATS
    assert messages


def test_the_beat_ceiling_clears_a_projected_multi_hour_session() -> None:
    """What ``MAX_RR_BEATS`` costs, in hours of training.

    The corpus's RR-heaviest file is ``dev_fields_run.fit`` (3,127
    ``hrv`` messages, 7,220 real beats, 15,635 array slots) -- *not*
    ``strap_hrv_sample_run.fit``, which carries 156. But clearing a
    52-minute fixture is a weak claim for a marathon-focused system, so
    the fixture is used for its *rate* instead: 7,220 beats over its
    1Hz record stream is ~2.3 beats/sec (~139 bpm), and the ceiling has
    to clear a long run projected at that rate.

    At 2.316 beats/sec: 3h = 25,016 beats, 4h = 33,355, 5h = 41,693.
    ``MAX_RR_BEATS`` = 150,000 is ~18 hours -- past any plausible
    single activity, ultras included. Anyone lowering this number is
    choosing a maximum workout duration; this test says which one.
    """
    messages = fit_parser.decode(_fixture_bytes(LARGEST_FIXTURE))
    beats = _beat_count(messages)
    seconds = sum(1 for m in messages if m.name == "record")  # 1Hz stream
    beats_per_second = beats / seconds

    assert beats > 7_000  # calibration: the corpus's RR-heaviest file
    assert 2.0 < beats_per_second < 2.6  # ~139 bpm

    four_hour_session = beats_per_second * 4 * 3600
    assert four_hour_session > 33_000  # sanity: the projection is real
    assert four_hour_session < fit_parser.MAX_RR_BEATS


def test_largest_fixture_has_orders_of_magnitude_of_headroom() -> None:
    records = _record_count(LARGEST_FIXTURE)
    assert records > 3_000  # calibration: the corpus's biggest file
    assert records * 20 < fit_parser.MAX_RECORD_MESSAGES


def test_over_ceiling_upload_surfaces_as_413(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rejection, not a data-quality finding -- and the same semantic
    bucket as the 50MB byte cap, which also returns 413."""
    monkeypatch.setattr(fit_parser, "MAX_RECORD_MESSAGES", 5)

    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            files={"file": (LARGEST_FIXTURE, _fixture_bytes(LARGEST_FIXTURE))},
        )

    assert response.status_code == 413
    assert "record" in response.text.lower()


def test_under_ceiling_upload_still_succeeds() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            files={"file": ("sample_run.fit", _fixture_bytes("sample_run.fit"))},
        )

    assert response.status_code == 201, response.text
