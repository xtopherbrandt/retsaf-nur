"""F020: ``POST /imports`` carries a whole Garmin export archive, far larger
than one FIT file, so ``ContentLengthLimitMiddleware`` gives ``/imports`` (and
``/imports/``) its own ceiling, ``IMPORT_MAX_BYTES``, while every other path
keeps ``MAX_UPLOAD_BYTES``.

Each test goes through ``TestClient(app)`` -- the real assembled app -- and
declares a large ``Content-Length`` over a tiny real body, so no gigabyte is
ever sent. The middleware reads both constants at call time, so the tests that
need a cap below the 64 KB framing allowance's reach lower the constants with
``monkeypatch`` rather than declare a gigabyte-plus length.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from runcoach_api import main
from runcoach_api.main import (
    IMPORT_MAX_BYTES,
    MAX_UPLOAD_BYTES,
    _MULTIPART_FRAMING_ALLOWANCE_BYTES,
    app,
)

TINY_BODY = b"tiny-body-nowhere-near-the-declared-length"


def _post_declaring(path: str, declared: int):
    with TestClient(app) as client:
        return client.post(path, headers={"content-length": str(declared)}, content=TINY_BODY)


def test_the_import_cap_is_one_gibibyte_and_above_the_session_cap() -> None:
    assert IMPORT_MAX_BYTES == 1024 * 1024 * 1024
    assert IMPORT_MAX_BYTES > MAX_UPLOAD_BYTES


def test_a_declared_length_over_50mb_on_imports_passes_the_middleware() -> None:
    declared = MAX_UPLOAD_BYTES + _MULTIPART_FRAMING_ALLOWANCE_BYTES + 1
    assert declared < IMPORT_MAX_BYTES

    response = _post_declaring("/imports", declared)

    # The middleware let it through; whatever answers (no route, a route that
    # rejects the tiny body) is not the middleware's 413.
    assert response.status_code != 413
    assert "byte limit" not in response.text


def test_a_declared_length_just_under_the_import_ceiling_passes_the_middleware() -> None:
    declared = IMPORT_MAX_BYTES + _MULTIPART_FRAMING_ALLOWANCE_BYTES

    response = _post_declaring("/imports", declared)

    assert response.status_code != 413


def test_a_declared_length_over_the_import_cap_is_413_naming_it() -> None:
    declared = IMPORT_MAX_BYTES + _MULTIPART_FRAMING_ALLOWANCE_BYTES + 1

    response = _post_declaring("/imports", declared)

    assert response.status_code == 413
    assert response.text == f"upload exceeds {IMPORT_MAX_BYTES} byte limit"


def test_the_trailing_slash_path_gets_the_import_ceiling() -> None:
    over_session_cap = MAX_UPLOAD_BYTES + _MULTIPART_FRAMING_ALLOWANCE_BYTES + 1
    over_import_cap = IMPORT_MAX_BYTES + _MULTIPART_FRAMING_ALLOWANCE_BYTES + 1

    passed = _post_declaring("/imports/", over_session_cap)
    refused = _post_declaring("/imports/", over_import_cap)

    assert passed.status_code != 413
    assert refused.status_code == 413
    assert refused.text == f"upload exceeds {IMPORT_MAX_BYTES} byte limit"


def test_sessions_still_refuses_over_50mb() -> None:
    declared = MAX_UPLOAD_BYTES + _MULTIPART_FRAMING_ALLOWANCE_BYTES + 1

    response = _post_declaring("/sessions", declared)

    assert response.status_code == 413
    assert response.text == f"upload exceeds {MAX_UPLOAD_BYTES} byte limit"


@pytest.mark.parametrize("path", ["/import", "/imports/x", "/importsx", "/me/imports"])
def test_a_neighbouring_path_keeps_the_session_ceiling(path: str) -> None:
    declared = MAX_UPLOAD_BYTES + _MULTIPART_FRAMING_ALLOWANCE_BYTES + 1

    response = _post_declaring(path, declared)

    assert response.status_code == 413
    assert response.text == f"upload exceeds {MAX_UPLOAD_BYTES} byte limit"


def test_the_ceilings_are_read_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # The middleware stack is built once per app; lowering the module
    # constants afterwards must still change what it refuses.
    monkeypatch.setattr(main, "IMPORT_MAX_BYTES", 1000)
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)
    over_import = 1000 + _MULTIPART_FRAMING_ALLOWANCE_BYTES + 1
    over_session = 10 + _MULTIPART_FRAMING_ALLOWANCE_BYTES + 1

    imports = _post_declaring("/imports", over_import)
    sessions = _post_declaring("/sessions", over_session)

    assert imports.status_code == 413
    assert imports.text == "upload exceeds 1000 byte limit"
    assert sessions.status_code == 413
    assert sessions.text == "upload exceeds 10 byte limit"
