"""T021: reject a corrupt/malformed FIT file with ``FitParseFailure`` /
HTTP 400 -- distinct from ``NotAFitFileError`` (no ``.FIT`` signature at
all, a different task's scope, not exercised here).

Fixtures are derived from the real, user-supplied FIT data at
``tests/fixtures/sample_run.fit`` -- per ``.claude/rules/project-testing.md``
this exercises real ``fitdecode`` parsing failure modes (bad CRC,
truncation) rather than a fabricated FIT-like byte string. Both
fixtures retain the real ``.FIT`` header signature at byte offset 8,
so they reach ``fitdecode`` and fail there, not at the pre-parse magic
check.

Scoped exclusively to this task's behavior. No other test file should
be created or reused for it (see T019's task notes on the
shared-filename collision this avoids).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from runcoach_api.ingestion.exceptions import FitParseFailure
from runcoach_api.ingestion.fit_parser import decode
from runcoach_api.main import app

FIXTURE = Path(__file__).parent / "fixtures" / "sample_run.fit"


def _real_fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def _corrupted_crc_bytes() -> bytes:
    """The real fixture with its trailing CRC byte flipped.

    The ``.FIT`` header signature (offset 8) is untouched -- only the
    footer CRC is corrupted -- so this exercises ``fitdecode``'s
    ``FitCRCError`` path (``check_crc=CrcCheck.RAISE``), not the
    pre-parse magic-byte check.
    """
    raw = bytearray(_real_fixture_bytes())
    raw[-1] ^= 0xFF
    return bytes(raw)


def _truncated_bytes() -> bytes:
    """The real fixture cut well before its end (mid-body).

    Still carries the real ``.FIT`` header, so it reaches ``fitdecode``
    and fails there (an EOF/parse error while reading records), not at
    the magic-byte check.
    """
    raw = _real_fixture_bytes()
    return raw[: len(raw) // 2]


# ---------------------------------------------------------------------------
# decode() unit-level behavior
# ---------------------------------------------------------------------------


def test_decode_corrupted_crc_raises_fit_parse_failure() -> None:
    try:
        decode(_corrupted_crc_bytes())
    except FitParseFailure:
        pass
    else:
        raise AssertionError("expected FitParseFailure for corrupted CRC bytes")


def test_decode_truncated_file_raises_fit_parse_failure() -> None:
    try:
        decode(_truncated_bytes())
    except FitParseFailure:
        pass
    else:
        raise AssertionError("expected FitParseFailure for truncated FIT bytes")


# ---------------------------------------------------------------------------
# HTTP boundary: POST /sessions with a corrupt upload -> clean 400
# ---------------------------------------------------------------------------


def test_corrupted_upload_returns_400_without_raw_traceback() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            files={"file": ("corrupt.fit", _corrupted_crc_bytes())},
        )

    assert response.status_code == 400
    assert "Traceback" not in response.text
    assert "File \"" not in response.text


def test_truncated_upload_returns_400_without_raw_traceback() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            files={"file": ("truncated.fit", _truncated_bytes())},
        )

    assert response.status_code == 400
    assert "Traceback" not in response.text
    assert "File \"" not in response.text
