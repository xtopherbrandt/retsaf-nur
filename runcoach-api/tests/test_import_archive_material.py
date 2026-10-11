"""``tests/support/import_archive.py``: the test archive every F020 import test builds.

The material is the F020 reference's section 9 archive, built at run time in
``tmp_path`` from corpus fixtures; nothing is written into ``tests/fixtures/``.
The oracle is ``fitdecode`` (and ``zipfile``) reading back what was built, not
any ``runcoach_api`` import code.

- **Minimal FIT.** ``minimal_fit(32)`` decodes with both CRCs checked to one
  ``file_id`` whose ``type`` is ``monitoring_b``.
- **The archive.** ``build_archive`` lists exactly the section 9 entries
  (printed): the inner zip under a folder name no importer knows, the five
  corpus files (two real runs, two HRV captures and a health snapshot, as the
  fixtures README's provenance table names them), the cycling copy, the no-HR
  copy, the monitoring_b FIT, a TCX, a JSON, the truncated FIT, the nested
  zip, the corrupt inner zip and the loose outer ``.fit``.
- **The copies.** The cycling copy's session sport is ``cycling``; the no-HR
  copy carries no ``user_profile`` or ``zones_target`` and is the oldest
  activity by ``time_created`` and by session ``start_time``; every activity's
  ``(source_device, start_time)`` is distinct; the truncated FIT does not decode.
- **Options.** ``newest_first`` lists the activities by ``time_created``
  descending; extra entries land at the level asked for, duplicates included.
- **The AC4 premise.** ``sample_run`` carries ``max_hr_bpm`` 188 through
  ``profile_values.extract``, so a newer run in the archive has a max HR the
  no-HR copy could wrongly borrow.
"""

from __future__ import annotations

import importlib.util
import io
import zipfile
from pathlib import Path

import fitdecode
import pytest
from runcoach_api.ingestion import fit_parser, mapping, profile_values

FIXTURES = Path(__file__).parent / "fixtures"

_SPEC = importlib.util.spec_from_file_location(
    "import_archive", Path(__file__).parent / "support" / "import_archive.py"
)
assert _SPEC is not None and _SPEC.loader is not None
import_archive = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(import_archive)

IA = import_archive


def _messages(data: bytes) -> list[fitdecode.FitDataMessage]:
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        return [frame for frame in reader if isinstance(frame, fitdecode.FitDataMessage)]


def _first(messages: list, name: str):
    return next(m for m in messages if m.name == name)


def _inner_entries(outer: zipfile.ZipFile, name: str) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
        return {info.filename: inner.read(info) for info in inner.infolist()}


# ---------------------------------------------------------------------------
# minimal FIT
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("file_type", "type_name"), [(32, "monitoring_b"), (28, "monitoring_daily"), (4, "activity")])
def test_a_minimal_monitoring_b_file_decodes_with_its_type(file_type: int, type_name: str) -> None:
    data = IA.minimal_fit(file_type)
    messages = _messages(data)
    print(f"minimal_fit({file_type}): {len(data)} bytes, {[(m.name, m.get_value('type')) for m in messages]}")
    assert [m.name for m in messages] == ["file_id"]
    assert messages[0].get_value("type") == type_name
    assert messages[0].get_raw_value("time_created") == IA.MINIMAL_TIME_CREATED


def test_a_minimal_fit_without_time_created_omits_it() -> None:
    messages = _messages(IA.minimal_fit(4, time_created=None))
    assert not messages[0].has_field("time_created")


# ---------------------------------------------------------------------------
# the archive
# ---------------------------------------------------------------------------

EXPECTED_INNER = {
    "sample_run.fit",
    "strap_run_hrv.fit",
    "strap_hrv_capture.fit",
    "sample_health_snapshot.fit",
    "strap_hrv_sample_run.fit",
    IA.CYCLING_RUN,
    IA.NO_HR_RUN,
    IA.MONITORING_B,
    IA.TCX,
    IA.JSON,
    IA.TRUNCATED,
    IA.NESTED_ZIP,
}


def test_the_section_9_archive_holds_every_listed_entry(tmp_path: Path) -> None:
    path = IA.build_archive(tmp_path)
    assert path.parent == tmp_path and path.is_file()
    with zipfile.ZipFile(path) as outer:
        outer_names = [info.filename for info in outer.infolist()]
        print("outer entries:", outer_names)
        assert sorted(outer_names) == sorted([IA.INNER_ZIP, IA.CORRUPT_ZIP, IA.LOOSE_FIT])
        inner = _inner_entries(outer, IA.INNER_ZIP)
        print("inner entries:", list(inner))
        assert set(inner) == EXPECTED_INNER and len(inner) == len(EXPECTED_INNER)
        with pytest.raises(zipfile.BadZipFile):
            zipfile.ZipFile(io.BytesIO(outer.read(IA.CORRUPT_ZIP)))
        loose = _messages(outer.read(IA.LOOSE_FIT))
    print("labels:", IA.labels(path))
    # The inner zip's folder is one no Garmin export uses and no importer can know.
    assert IA.INNER_ZIP.rsplit("/", 1)[0] not in ("DI_CONNECT/DI-Connect-Uploaded-Files", "")
    assert _first(loose, "file_id").get_value("type") == "monitoring_daily"

    # The corpus files are copied byte for byte.
    for name in ("sample_run", "strap_run_hrv", "strap_hrv_capture", "sample_health_snapshot", "strap_hrv_sample_run"):
        assert inner[f"{name}.fit"] == (FIXTURES / f"{name}.fit").read_bytes()
    assert _first(_messages(inner[IA.MONITORING_B]), "file_id").get_value("type") == "monitoring_b"
    assert inner[IA.TCX].startswith(b"<?xml") and b"TrainingCenterDatabase" in inner[IA.TCX]
    assert inner[IA.JSON].startswith(b"{")
    with zipfile.ZipFile(io.BytesIO(inner[IA.NESTED_ZIP])) as nested:
        assert nested.namelist() == ["sample_run.fit"]
    with pytest.raises(fit_parser.FitParseFailure):
        fit_parser.decode(inner[IA.TRUNCATED])


def test_the_copies_are_re_dated_and_patched(tmp_path: Path) -> None:
    with zipfile.ZipFile(IA.build_archive(tmp_path)) as outer:
        inner = _inner_entries(outer, IA.INNER_ZIP)

    cycling = _messages(inner[IA.CYCLING_RUN])
    assert _first(cycling, "session").get_value("sport") == "cycling"
    no_hr = _messages(inner[IA.NO_HR_RUN])
    assert not {"user_profile", "zones_target"} & {m.name for m in no_hr}
    assert set(profile_values.extract(no_hr).values()) == {None}

    activities = {
        name: data for name, data in inner.items()
        if name.endswith(".fit") and name not in (IA.MONITORING_B, IA.TRUNCATED)
    }
    keys = {}
    created = {}
    for name, data in activities.items():
        session, _ = mapping.to_canonical(fit_parser.decode(data))
        keys[name] = (session.source_device, session.start_time)
        created[name] = _first(_messages(data), "file_id").get_raw_value("time_created")
    for name in sorted(keys, key=keys.get):
        print(f"{name}: time_created {created[name]}, key {keys[name]}")
    assert len(set(keys.values())) == len(keys)
    assert min(created, key=created.get) == IA.NO_HR_RUN
    assert min(keys, key=lambda n: keys[n][1]) == IA.NO_HR_RUN
    assert sorted(created.values()).count(created[IA.NO_HR_RUN]) == 1


def test_newest_first_lists_the_activities_by_time_created_descending(tmp_path: Path) -> None:
    with zipfile.ZipFile(IA.build_archive(tmp_path, newest_first=True)) as outer:
        inner = _inner_entries(outer, IA.INNER_ZIP)
    order = []
    for name, data in inner.items():
        if not name.endswith(".fit"):
            continue
        with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.DISABLED) as reader:
            file_id = next(f for f in reader if isinstance(f, fitdecode.FitDataMessage) and f.name == "file_id")
        if file_id.get_value("type") == "activity":
            order.append((name, file_id.get_raw_value("time_created")))
    print("activities as listed:", order)
    assert len(order) == 8
    assert [t for _, t in order] == sorted((t for _, t in order), reverse=True)
    assert order[-1][0] == IA.NO_HR_RUN
    # The listed order is not already oldest first, so processing in it is a real perturbation.
    assert [t for _, t in order] != sorted(t for _, t in order)


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_extra_entries_land_at_the_level_asked_for(tmp_path: Path) -> None:
    path = IA.build_archive(
        tmp_path,
        name="extra.zip",
        extra_inner=[("more/run.FIT", b"inner-1"), ("more/run.FIT", b"inner-2")],
        extra_outer=[("notes.txt", b"outer")],
    )
    assert path.name == "extra.zip"
    with zipfile.ZipFile(path) as outer:
        assert outer.read("notes.txt") == b"outer"
        with zipfile.ZipFile(io.BytesIO(outer.read(IA.INNER_ZIP))) as inner:
            extra = [(i.filename, inner.read(i)) for i in inner.infolist() if i.filename.startswith("more/")]
    assert extra == [("more/run.FIT", b"inner-1"), ("more/run.FIT", b"inner-2")]


# ---------------------------------------------------------------------------
# the AC4 premise
# ---------------------------------------------------------------------------


def test_sample_run_carries_max_hr_188() -> None:
    """``sample_run`` is a real run (README provenance row: run proof yes)."""
    extracted = profile_values.extract(fit_parser.decode((FIXTURES / "sample_run.fit").read_bytes()))
    print(f"sample_run profile values: {extracted}")
    assert extracted["max_hr_bpm"] == 188
