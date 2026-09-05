"""T053: FIT ingest is bounded by *record count*, not only by upload bytes.

The 50MB upload cap bounds the compressed-on-the-wire size of an
upload; it does not bound how many ``record`` messages those bytes
decode into. ``fit_parser.MAX_RECORD_MESSAGES`` closes that with a
ceiling enforced *inside* ``decode()``'s per-frame loop.

Two properties carry the weight here, and both are asserted below:

1. **The guard short-circuits.** A ceiling that only trips after the
   whole message list has been materialised bounds nothing -- the
   memory is already spent by then. ``test_guard_short_circuits_...``
   drives ``decode()`` against a frame stream that raises if it is
   pulled even one frame past the point where the ceiling is exceeded,
   so a "count afterwards" implementation cannot pass it.
2. **The ceiling never fires on real data.** All six fixtures in the
   corpus are decoded at the shipped ceiling and their record counts
   asserted to sit orders of magnitude below it.

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


# A byte prefix that clears decode()'s ``.FIT`` magic pre-check, so the
# fake reader (not the header guard) is what the test exercises.
_HEADER_ONLY = b"\x00" * fit_parser._MAGIC_OFFSET + fit_parser._FIT_MAGIC


def test_ceiling_is_one_hundred_thousand() -> None:
    assert fit_parser.MAX_RECORD_MESSAGES == 100_000


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
    pulled: list[int] = []

    class _PulledTooFar(Exception):
        pass

    def endless_frames():
        i = 0
        while True:
            i += 1
            if i > limit + 1:
                raise _PulledTooFar(
                    f"decode() pulled frame {i}: the guard did not short-circuit"
                )
            pulled.append(i)
            yield _fake_record_frame()

    _install_fake_reader(monkeypatch, endless_frames())

    with pytest.raises(TooManyRecordsError):
        fit_parser.decode(_HEADER_ONLY)

    assert pulled == list(range(1, limit + 2))


def test_only_record_messages_count_toward_the_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IDEA-004 scopes the bound to ``record`` messages; non-record
    frames must not trip it (they are counted by no other guard --
    a deliberate, documented residual gap in the task file)."""
    monkeypatch.setattr(fit_parser, "MAX_RECORD_MESSAGES", 3)
    frames = []
    for _ in range(20):
        other = MagicMock(spec=fitdecode.FitDataMessage)
        other.name = "device_info"
        frames.append(other)
    frames.extend(_fake_record_frame() for _ in range(3))
    _install_fake_reader(monkeypatch, frames)

    assert len(fit_parser.decode(_HEADER_ONLY)) == 23


def test_error_carries_count_and_limit_as_data() -> None:
    exc = TooManyRecordsError(count=100_001, limit=100_000)
    assert exc.count == 100_001
    assert exc.limit == 100_000
    assert "100" in str(exc)


@pytest.mark.parametrize("fixture_name", ALL_FIXTURES)
def test_real_fixture_still_decodes_under_the_shipped_ceiling(fixture_name: str) -> None:
    """No monkeypatching: the real ceiling, the real files."""
    messages = fit_parser.decode(_fixture_bytes(fixture_name))
    records = sum(1 for m in messages if m.name == "record")
    assert records < fit_parser.MAX_RECORD_MESSAGES
    assert messages


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
