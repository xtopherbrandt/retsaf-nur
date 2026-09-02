"""T027: reject a non-FIT file upload with ``NotAFitFileError`` / HTTP 400 --
distinct from ``FitParseFailure`` (a real ``.FIT``-signed but corrupt body,
T021's scope, not exercised here beyond the regression check below).

The critical requirement under test: the ``.FIT`` header-magic check in
``fit_parser.decode()`` must run *before* the CRC-wrapped
``fitdecode.FitReader`` call, so garbage-with-no-header and
corrupt-but-signed-FIT stay mechanically distinct failure modes.

Scoped exclusively to this task's behavior. No other test file should be
appended to for it (see T019/T021's task notes on the shared-filename
collision this avoids).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from runcoach_api.ingestion.exceptions import FitParseFailure, NotAFitFileError
from runcoach_api.ingestion.fit_parser import decode
from runcoach_api.main import app

FIXTURE = Path(__file__).parent / "fixtures" / "sample_run.fit"

_GARBAGE = b"not a fit file at all, just garbage bytes padded out"


def _real_fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def _corrupted_crc_bytes() -> bytes:
    """The real fixture with its trailing CRC byte flipped.

    Mirrors ``test_fit_parser_corrupt.py``'s corruption technique exactly:
    the ``.FIT`` header signature (offset 8) is untouched, only the footer
    CRC is corrupted -- so this must still reach ``fitdecode`` and raise
    ``FitParseFailure``, not get misrouted to ``NotAFitFileError`` by this
    task's ordering fix.
    """
    raw = bytearray(_real_fixture_bytes())
    raw[-1] ^= 0xFF
    return bytes(raw)


# ---------------------------------------------------------------------------
# decode() unit-level behavior
# ---------------------------------------------------------------------------


def test_decode_garbage_bytes_raises_not_a_fit_file_error() -> None:
    try:
        decode(_GARBAGE)
    except NotAFitFileError:
        pass
    else:
        raise AssertionError("expected NotAFitFileError for garbage bytes")


def test_decode_corrupted_crc_still_raises_fit_parse_failure_not_misrouted() -> None:
    """Regression check: the ordering fix must not misroute a real,
    header-intact-but-corrupt-body FIT file into NotAFitFileError."""
    try:
        decode(_corrupted_crc_bytes())
    except FitParseFailure:
        pass
    except NotAFitFileError:
        raise AssertionError(
            "corrupt-but-signed FIT bytes were misrouted to NotAFitFileError"
        ) from None
    else:
        raise AssertionError("expected FitParseFailure for corrupted CRC bytes")


# ---------------------------------------------------------------------------
# HTTP boundary: POST /sessions with a non-FIT upload -> clean 400
# ---------------------------------------------------------------------------


def test_non_fit_upload_returns_400_mentioning_not_a_valid_fit_file() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            files={"file": ("garbage.txt", _GARBAGE)},
        )

    assert response.status_code == 400
    assert "not a valid FIT file" in response.text
