"""The HRV_STATUS file type and the night's ``activity_tag``, each defined once.

``ingestion/overnight.py`` defines ``HRV_STATUS_FILE_TYPE`` (``file_id.type`` 68)
and ``OVERNIGHT_TAG`` (``overnight_hrv``); every API module imports them. The CLI
is a separate install that cannot import the API package, so it keeps its own
copies (``commands/imports.py``'s ``NIGHT_TAG`` and the tag inside
``commands/sync.py``'s ``RANGE_TAGS``); the first test ties them to the API's, as
the ``*_cli_against_app`` tests tie the CLI to the served app.

The night screen also admits the type by name: fitdecode 0.11.0 (the newest
release) names no file type 68, but a later profile that did would hand the
screen a string, and ``HRV_STATUS_FILE_TYPE_NAME`` is the name it would carry.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.imports import archive
from runcoach_api.imports.archive import FileIdScreen, screen_file_id
from runcoach_api.ingestion import overnight
from runcoach_api.main import app

pytest.importorskip("runcoach_cli")

API_SRC = Path(overnight.__file__).resolve().parents[1]
DEFINING_FILE = API_SRC / "ingestion" / "overnight.py"
NIGHTS = Path(__file__).parent / "fixtures" / "nights"

_SPEC = importlib.util.spec_from_file_location(
    "import_archive", Path(__file__).parent / "support" / "import_archive.py"
)
assert _SPEC is not None and _SPEC.loader is not None
IA = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(IA)

# A bare 68 on a line that names a file type (``file_type``, ``FILE_TYPE``,
# ``filetype``), or compared with ``==``: a second definition or a hard-coded test.
_TYPE_68 = re.compile(r"(?i)(file_?type.*\b68\b|\b68\b.*file_?type|==\s*68\b)")
# The tag as a quoted string literal (docstrings spell it in double backticks,
# and the ``overnight_hrv_values`` table name does not close the quote).
_TAG_LITERAL = re.compile(r"""['"]overnight_hrv['"]""")

# Read from a decode of ``nights/night_recent.fit`` (``test_overnight_upload.py``).
RECENT_ID = "f866f4fe46b0a62f032a1a0f1c3fd435"


def _hits(pattern: re.Pattern[str]) -> list[str]:
    hits = []
    for path in sorted(API_SRC.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(API_SRC).as_posix()}:{number}: {line.strip()}")
    return hits


def test_the_cli_constants_equal_the_api_constants() -> None:
    from runcoach_cli.commands import imports as cli_imports
    from runcoach_cli.commands import sync as cli_sync

    assert overnight.HRV_STATUS_FILE_TYPE == 68
    assert overnight.OVERNIGHT_TAG == "overnight_hrv"
    assert cli_imports.NIGHT_TAG == overnight.OVERNIGHT_TAG
    assert overnight.OVERNIGHT_TAG in cli_sync.RANGE_TAGS
    # The API modules that use them hold the overnight module's objects, not copies.
    assert archive.HRV_STATUS_FILE_TYPE is overnight.HRV_STATUS_FILE_TYPE
    assert db.OVERNIGHT_TAG is overnight.OVERNIGHT_TAG


def test_type_68_has_one_definition_in_the_api() -> None:
    type_hits = _hits(_TYPE_68)
    tag_hits = _hits(_TAG_LITERAL)
    print("type 68 hits:", *type_hits, sep="\n  ")
    print("tag literal hits:", *tag_hits, sep="\n  ")
    assert type_hits == [hit for hit in type_hits if hit.startswith("ingestion/overnight.py:")]
    assert [hit.split(": ", 1)[1] for hit in type_hits] == ["HRV_STATUS_FILE_TYPE = 68"]
    assert [hit.split(": ", 1)[1] for hit in tag_hits] == ['OVERNIGHT_TAG = "overnight_hrv"']
    assert all(hit.startswith("ingestion/overnight.py:") for hit in tag_hits)


def test_a_named_hrv_status_file_type_is_still_a_night(monkeypatch: pytest.MonkeyPatch) -> None:
    real = archive._read_file_id
    raw = (NIGHTS / "night_recent.fit").read_bytes()
    number, time_created = real(raw)
    assert number == overnight.HRV_STATUS_FILE_TYPE

    def named(data: bytes) -> tuple[str | int | None, int | None]:
        _, stamp = real(data)
        return "hrv_status", stamp

    monkeypatch.setattr(archive, "_read_file_id", named)
    assert overnight.HRV_STATUS_FILE_TYPE_NAME == "hrv_status"
    assert screen_file_id(raw) == FileIdScreen("hrv_status", time_created=time_created)
    # Another name is still a non-activity counted by that name.
    monkeypatch.setattr(archive, "_read_file_id", lambda data: ("hrv", 7))
    assert screen_file_id(raw) == FileIdScreen("non_activity", file_type="hrv")

    # The named file goes through the upload route as a night.
    monkeypatch.setattr(archive, "_read_file_id", named)
    with TestClient(app) as client:
        response = client.post("/sessions", files={"file": ("upload.fit", raw)})
    assert response.status_code == 201, response.text
    assert response.json() == {"session_id": RECENT_ID, "quality_flags": []}
    conn = db.get_connection()
    try:
        tag = conn.execute("SELECT activity_tag FROM sessions WHERE session_id = ?", (RECENT_ID,)).fetchone()
    finally:
        conn.close()
    assert tuple(tag) == (overnight.OVERNIGHT_TAG,)
