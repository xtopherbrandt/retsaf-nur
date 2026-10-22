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

import importlib.util
import struct
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runcoach_api import db
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


def _fit_crc(data: bytes) -> int:
    """The FIT CRC-16 over ``data``."""
    table = (0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
             0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400)
    crc = 0
    for byte in data:
        for nibble in (byte & 0xF, (byte >> 4) & 0xF):
            tmp = table[crc & 0xF]
            crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ table[nibble]
    return crc


def _zero_size_field_bytes() -> bytes:
    """A FIT whose one definition (``file_id``) declares a field of size 0, then one data message.

    fitdecode accepts the definition and raises a plain ``ValueError('size')`` reading the data
    message; no ``date_time`` field is involved.
    """
    definition = bytes([0x40, 0x00, 0x00]) + struct.pack("<H", 0) + bytes([1, 0, 0, 0x00])
    body = definition + bytes([0x00])
    header = struct.pack("<BBHI4s", 12, 0x10, 2093, len(body), b".FIT")
    raw = header + body
    return raw + struct.pack("<H", _fit_crc(raw))


def test_a_decoder_value_error_is_not_reported_as_a_date_time_fault() -> None:
    with pytest.raises(FitParseFailure) as caught:
        decode(_zero_size_field_bytes())
    message = str(caught.value)
    assert isinstance(caught.value.__cause__, ValueError)
    assert "date_time" not in message
    assert "size" in message


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


# ---------------------------------------------------------------------------
# A date_time field beyond time_t: fitdecode's timestamp conversion raises
# OverflowError or OSError, and the decode wrapper turns it into the same
# FitParseFailure / 400 a truncated file gets.
# ---------------------------------------------------------------------------

SUPPORT = Path(__file__).parent / "support"
SESSION, START_TIME = 18, 2
BEYOND_TIME_T = (2**40, 2**63 + 5)


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _session_start_widened_to(value: int) -> bytes:
    """The real fixture with ``session.start_time`` redeclared uint64 and set to ``value``."""
    fit_patch = _load_support("fit_patch")
    raw = fit_patch.widen_to_uint64(_real_fixture_bytes(), SESSION, START_TIME)
    return fit_patch.set_field(raw, SESSION, START_TIME, value)


def _session_count() -> int:
    conn = db.get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        conn.close()


def _lap_rows(session_id: str) -> list[tuple]:
    conn = db.get_connection()
    try:
        return conn.execute(
            "SELECT * FROM laps WHERE session_id = ? ORDER BY lap_index", (session_id,)
        ).fetchall()
    finally:
        conn.close()


def _assert_unparseable_400(response) -> None:
    assert response.status_code == 400, response.text
    assert "could not be parsed" in response.text
    assert "date_time" in response.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize("value", BEYOND_TIME_T)
def test_a_date_time_beyond_time_t_is_a_400_on_upload(value: int) -> None:
    raw = _session_start_widened_to(value)
    with TestClient(app) as client:
        plain = client.post("/sessions", files={"file": ("widened.fit", raw)})
        _assert_unparseable_400(plain)
        with_switch = client.post(
            "/sessions",
            files={"file": ("widened.fit", raw)},
            data={"runs_and_captures_only": "true"},
        )
        _assert_unparseable_400(with_switch)
        assert _session_count() == 0


@pytest.mark.parametrize("value", BEYOND_TIME_T)
def test_a_date_time_beyond_time_t_is_a_400_on_attach_laps(value: int) -> None:
    with TestClient(app) as client:
        stored = client.post("/sessions", files={"file": ("sample_run.fit", _real_fixture_bytes())})
        assert stored.status_code == 201, stored.text
        session_id = stored.json()["session_id"]
        before = _lap_rows(session_id)
        assert before

        response = client.put(
            f"/sessions/{session_id}/laps",
            files={"file": ("widened.fit", _session_start_widened_to(value))},
        )
        _assert_unparseable_400(response)
        assert _lap_rows(session_id) == before
