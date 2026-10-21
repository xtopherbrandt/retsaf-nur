"""``imports.archive.screen_file_id``: F020's ``file_id`` screen as one public function.

The screen is the decision the archive reader makes on each FIT candidate's
bytes: an activity (with its raw ``time_created``), a non-activity counted by
its type name or, for a Garmin-private type, by the number as a string, or a
failure with the detail text F020 reports. The archive reader's own pins
(``test_import_archive_reader.py``, ``test_import_probe_table.py``) guard that
the reader's outputs do not change.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from runcoach_api.imports import archive
from runcoach_api.imports.archive import FileIdScreen, screen_file_id

_SPEC = importlib.util.spec_from_file_location(
    "import_archive", Path(__file__).parent / "support" / "import_archive.py"
)
assert _SPEC is not None and _SPEC.loader is not None
import_archive = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(import_archive)
IA = import_archive


def test_public_screen_names_activity_non_activity_and_fails_typeless_and_non_fit() -> None:
    run = IA._fixture("sample_run")
    expected_type, expected_time = IA._file_id(run)
    assert expected_type == "activity" and expected_time is not None
    assert screen_file_id(run) == FileIdScreen("activity", time_created=expected_time)

    # A dated minimal activity carries its raw time_created through unchanged.
    dated = screen_file_id(IA.minimal_fit(4, time_created=IA.MINIMAL_TIME_CREATED))
    assert dated == FileIdScreen("activity", time_created=IA.MINIMAL_TIME_CREATED)
    assert screen_file_id(IA.minimal_fit(4, time_created=None)) == FileIdScreen("activity", time_created=None)

    assert screen_file_id(IA.minimal_fit(32)) == FileIdScreen("non_activity", file_type="monitoring_b")
    for number in (44, 49):
        assert screen_file_id(IA.minimal_fit(number)) == FileIdScreen("non_activity", file_type=str(number))

    assert screen_file_id(IA.minimal_fit(0xFF)) == FileIdScreen("failed", detail="file_id carries no type")

    text = screen_file_id(b"this is not a FIT file at all\n")
    assert text.outcome == "failed"
    assert text.detail is not None and text.detail.startswith("file_id could not be read: ")
    assert text.time_created is None and text.file_type is None

    empty = screen_file_id(b"")
    assert empty == FileIdScreen("failed", detail="file_id could not be read: LookupError: no file_id message")


def test_type_68_is_hrv_status_with_its_time_created() -> None:
    """A file_id of type 68 (HRV_STATUS) is the screen's own outcome, carrying its raw time_created."""
    night = screen_file_id(IA.minimal_fit(68, time_created=IA.MINIMAL_TIME_CREATED))
    assert night == FileIdScreen("hrv_status", time_created=IA.MINIMAL_TIME_CREATED)
    assert night.file_type is None and night.detail is None
    assert screen_file_id(IA.minimal_fit(68, time_created=None)) == FileIdScreen("hrv_status", time_created=None)


def test_the_screen_has_three_outcomes_over_activity_hrv_status_and_monitoring() -> None:
    """A real run, a type-68 file and a monitoring_b file are the three outcomes, one each."""
    run = IA._fixture("sample_run")
    night = IA.minimal_fit(68, time_created=IA.MINIMAL_TIME_CREATED)
    monitoring = IA.minimal_fit(32)
    outcomes = [screen_file_id(data).outcome for data in (run, night, monitoring)]
    assert outcomes == ["activity", "hrv_status", "non_activity"]
    assert len(set(outcomes)) == 3


def test_archive_reader_screens_through_the_public_function(tmp_path: Path, monkeypatch) -> None:
    """A monitoring file the real screen counts as non-activity becomes an activity when the screen says so."""
    zip_path = tmp_path / "one.zip"
    zip_path.write_bytes(IA._zip_bytes([("a.fit", IA.minimal_fit(32))]))
    work = tmp_path / "work"
    work.mkdir()

    real = archive.scan(zip_path, work)
    assert real.non_activity == {"monitoring_b": 1} and real.activities == []

    seen: list[bytes] = []

    def fake(data: bytes) -> FileIdScreen:
        seen.append(data)
        return FileIdScreen("activity", time_created=7)

    monkeypatch.setattr(archive, "screen_file_id", fake)
    faked = archive.scan(zip_path, work)
    assert seen == [IA.minimal_fit(32)]
    assert faked.non_activity == {} and [a.time_created for a in faked.activities] == [7]
