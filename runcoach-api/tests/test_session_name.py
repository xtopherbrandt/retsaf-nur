"""F019 AC2, the upload half: the session name is saved in ``sessions.name`` at upload.

The name is the FIT ``workout.wkt_name``, else ``session.sport_profile_name``;
an empty or whitespace-only value falls through to the next source, and the
column is NULL when neither has one (user rulings 2026-10-08 and 2026-10-09).
The rule lives once, in ``metrics.session_list.session_name``, so the
read-side fallback for a session stored before the column existed shares it.

**Upload rows** drive real bytes through ``POST /sessions`` and read the
column back from the isolated store, never from a route: ``GET /sessions/{id}``
is unchanged and its body must not gain ``name``. The expected names were read
from the decoded fixtures (``fitdecode``, 2026-10-10): three files carry a
``workout`` message, the rest name the profile. ``strap_hrv_capture`` is an
HRV capture and is cited for its name only (``fixtures/README.md``). The
null row patches ``sample_run`` through ``tests/support/fit_patch.py``: the
``workout`` message removed and the profile name blanked to NUL bytes, which
``fitdecode`` decodes to ``None``.

**The probe table** attacks ``session_name`` directly: the empty string and
spaces fall through, a zero-width space (``U+200B``, for which ``str.isspace``
is False) is a name, a non-blank name is stored verbatim, unstripped, and a
value that is not a string is no name and falls through.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.main import app
from runcoach_api.metrics.session_list import session_name

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"

WORKOUT_MESSAGE = 26
SESSION_MESSAGE = 18
SPORT_PROFILE_NAME_FIELD = 110


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")


def _upload(client: TestClient, name: str, raw: bytes | None = None) -> str:
    response = client.post("/sessions", files={"file": (name, raw or (FIXTURES / name).read_bytes())})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _stored_name(session_id: str) -> str | None:
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT name FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None, f"no session {session_id}"
    return row["name"]


# --- the upload path ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected", "source"),
    [
        ("sample_run.fit", "Easy Run", "workout"),
        ("strap_run_hrv.fit", "Long Easy Run", "workout"),
        ("hilly_run_8k_fr945.fit", "Easy Run", "workout"),
        ("wrist_ppg_run.fit", "Run", "profile"),
        ("strap_hrv_capture.fit", "HRV Snapshot", "profile"),
    ],
    ids=["sample_run", "strap_run_hrv", "hilly_run_8k_fr945", "wrist_ppg_run", "strap_hrv_capture"],
)
def test_upload_saves_the_workout_name_else_the_profile_name(name: str, expected: str, source: str) -> None:
    with TestClient(app) as client:
        session_id = _upload(client, name)
        detail = client.get(f"/sessions/{session_id}")
    stored = _stored_name(session_id)
    print(f"{name}: sessions.name = {stored!r} (from the {source})")
    assert stored == expected
    assert detail.status_code == 200, detail.text
    assert "name" not in detail.json()


def test_a_file_with_no_workout_and_a_blank_profile_name_stores_null() -> None:
    raw = (FIXTURES / "sample_run.fit").read_bytes()
    patched = fit_patch.remove_messages(raw, WORKOUT_MESSAGE)
    patched = fit_patch.set_string_field(patched, SESSION_MESSAGE, SPORT_PROFILE_NAME_FIELD, None)

    with TestClient(app) as client:
        session_id = _upload(client, "sample_run_unnamed.fit", patched)
    stored = _stored_name(session_id)
    print(f"patched sample_run: sessions.name = {stored!r}")
    assert stored is None


def test_a_whitespace_profile_name_falls_through_to_null_at_upload() -> None:
    """The whitespace rule reaches the store: spaces in the profile name, no workout, NULL."""
    raw = (FIXTURES / "wrist_ppg_run.fit").read_bytes()
    patched = fit_patch.set_string_field(raw, SESSION_MESSAGE, SPORT_PROFILE_NAME_FIELD, "   ")

    with TestClient(app) as client:
        session_id = _upload(client, "wrist_ppg_run_blank.fit", patched)
    stored = _stored_name(session_id)
    print(f"patched wrist_ppg_run: sessions.name = {stored!r}")
    assert stored is None


def test_a_blank_workout_name_falls_through_to_the_profile_name_at_upload() -> None:
    raw = (FIXTURES / "sample_run.fit").read_bytes()
    patched = fit_patch.set_string_field(raw, WORKOUT_MESSAGE, 8, None)

    with TestClient(app) as client:
        session_id = _upload(client, "sample_run_blank_workout.fit", patched)
    stored = _stored_name(session_id)
    print(f"sample_run with the workout name blanked: sessions.name = {stored!r}")
    assert stored == "Run"


# --- the probe table for session_name -------------------------------------------------------------


@pytest.mark.parametrize(
    ("workout_name", "profile_name", "expected"),
    [
        ("", "Run", "Run"),
        ("   ", "Run", "Run"),
        (" ", "Run", "Run"),
        (None, "Run", "Run"),
        ("Easy Run", "", "Easy Run"),
        ("Easy Run", None, "Easy Run"),
        ("", "", None),
        ("   ", " ", None),
        (None, None, None),
        ("\t\n", "\r", None),
    ],
    ids=[
        "empty_workout",
        "spaces_workout",
        "one_space_workout",
        "no_workout",
        "empty_profile",
        "no_profile",
        "both_empty",
        "both_spaces",
        "both_absent",
        "other_whitespace",
    ],
)
def test_a_blank_name_falls_through_to_the_next_source(workout_name, profile_name, expected) -> None:
    result = session_name(workout_name, profile_name)
    print(f"session_name({workout_name!r}, {profile_name!r}) = {result!r}")
    assert result == expected


def test_a_zero_width_space_is_a_name() -> None:
    assert not "\u200b".isspace()
    assert session_name("\u200b", "Run") == "\u200b"
    assert session_name(None, "\u200b") == "\u200b"


@pytest.mark.parametrize(
    "name",
    [" Easy Run ", "Easy  Run", "\tEasy Run\n", "Easy Run" + " " * 40, "Ééé"],
    ids=["padded", "double_space", "tab_and_newline", "trailing_run", "non_ascii"],
)
def test_a_non_blank_name_is_stored_verbatim_unstripped(name: str) -> None:
    assert session_name(name, "Run") == name
    assert session_name(None, name) == name


def test_the_workout_name_wins_over_the_profile_name() -> None:
    assert session_name("Long Easy Run", "Run") == "Long Easy Run"


@pytest.mark.parametrize("bad", [1, b"Run", ("Run",), 0.0, True], ids=["int", "bytes", "tuple", "float", "bool"])
def test_a_non_string_value_is_not_a_name_and_falls_through(bad) -> None:
    """A fake or malformed message can hand ``to_canonical`` a non-string profile name, and the
    undeclared-capture note records it as read; the name rule neither refuses it nor stores it."""
    assert session_name(bad, None) is None
    assert session_name(None, bad) is None
    assert session_name(bad, "Run") == "Run"
    assert session_name("Easy Run", bad) == "Easy Run"
