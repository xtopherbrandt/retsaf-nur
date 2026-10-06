"""The two hilly fixtures carry no position value.

``hilly_run_8k_fr945.fit`` and ``hilly_long_run_17k_fr945.fit`` are the
athlete's own runs with every position value overwritten by
``tests/support/strip_fit_positions.py``: the GPS tracks stay out of the
repository. A position value is any field whose profile name ends in
``_lat`` or ``_long``, in any message, plus lap fields 27-30, which the
FR945 writes unnamed and which held semicircle values on the original
track.

Every message is decoded with the file CRC checked, and each position
field must decode as absent (the sint32 invalid value ``0x7FFFFFFF``
decodes to ``None``). The fields are found by number, not by name, so an
unnamed lap field and a named one are checked the same way. Each file must
also still upload with 201.

The originals live outside the repository, so the byte-diff and
bounding-box checks made when the files were written are build-time
evidence, recorded in the commit that added them.

**A field-agnostic check.** The checks above test the stripper's own field
set, so a position held in a field the set does not name (an unknown
message, a vendor field) would pass them. The last check does not use the
set. It walks every data message of each stripped file at the byte level,
known and unknown messages alike, and reads every element of every field
whose base type is 4 bytes wide (sint32, uint32, uint32z, float32) as a
sint32. A message fails when one element falls inside the latitude range and
another inside the longitude range of a box: ``sample_run.fit``'s own track
(the athlete's area), widened by 1 degree on every side. The box is computed
at test time from that file's decoded record positions and never printed;
failures name the message and field numbers and count them, never a value.
A planted pair inside the box, written into a copy of a stripped file with
the CRC recomputed, must fail the same check. The plant targets are fixed
message and field numbers whose base type the test asserts, located by a
byte walk of the test's own that reads every field whatever its type, so
the scanner under test never chooses what it is tested on: one pair each in
sint32, uint32, uint32z and float32 fields, one in the second and fourth
elements of one uint32 array field, and one just outside the track's own
bounding box but inside the 1-degree margin.
"""

from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

import fitdecode
import pytest
from fastapi.testclient import TestClient
from fitdecode import profile
from fitdecode.utils import compute_crc
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
HILLY_FIXTURES = ("hilly_run_8k_fr945.fit", "hilly_long_run_17k_fr945.fit")
BOX_SOURCE = "sample_run.fit"
SEMICIRCLES_PER_DEGREE = 2**31 / 180
#: FIT base types 4 bytes wide, by their low five bits: sint32, uint32, float32, uint32z.
FOUR_BYTE_BASE_TYPES = frozenset({0x05, 0x06, 0x08, 0x0C})

_SPEC = importlib.util.spec_from_file_location(
    "strip_fit_positions", Path(__file__).parent / "support" / "strip_fit_positions.py"
)
assert _SPEC is not None and _SPEC.loader is not None
strip_fit_positions = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(strip_fit_positions)

POSITION_FIELDS: dict[int, frozenset[int]] = strip_fit_positions.position_fields()


def test_the_position_field_set_holds_the_named_and_the_unnamed_lap_fields() -> None:
    """The set the test checks is the set the task names: record 0/1, lap 3-6
    and 27-30, session 3, 4, 29-32, 38, 39, split 21-24."""
    assert {0, 1} <= POSITION_FIELDS[20]
    assert {3, 4, 5, 6, 27, 28, 29, 30} <= POSITION_FIELDS[19]
    assert {3, 4, 29, 30, 31, 32, 38, 39} <= POSITION_FIELDS[18]
    assert {21, 22, 23, 24} <= POSITION_FIELDS[312]


@pytest.mark.parametrize("fixture_name", HILLY_FIXTURES)
def test_no_position_value_remains(fixture_name: str) -> None:
    path = FIXTURES / fixture_name
    checked = 0
    present: list[str] = []
    with fitdecode.FitReader(path, check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            wanted = POSITION_FIELDS.get(frame.global_mesg_num, frozenset())
            for field in frame.fields:
                if field.def_num in wanted:
                    checked += 1
                    if field.value is not None:
                        present.append(f"{frame.name}/{field.def_num}")
    # Counts and field identities only: never a value.
    assert checked > 0, f"{fixture_name}: no position field was decoded at all"
    assert not present, f"{fixture_name}: {len(present)} position values remain, first {present[:5]}"


@pytest.mark.parametrize("fixture_name", HILLY_FIXTURES)
def test_the_stripped_fixture_uploads(fixture_name: str, post_fit) -> None:
    with TestClient(app) as client:
        response = post_fit(client, fixture_name)
    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# the field-agnostic check: no lat/long-shaped pair inside the athlete's area
# ---------------------------------------------------------------------------


def _four_byte_elements(data: bytes):
    """Yield ``(global_num, [(field_num, element_index, offset, big_endian)])`` per data message.

    A byte-level walk that keeps the current definition of each local message number. Every
    field whose base type is 4 bytes wide contributes each of its 4-byte elements. The walk
    refuses what it cannot read safely (compressed-timestamp headers, developer fields), so a
    file it cannot scan fails rather than passes.
    """
    header_size = data[0]
    end = header_size + struct.unpack_from("<I", data, 4)[0]
    definitions: dict[int, tuple[bool, int, list[tuple[int, int, int]]]] = {}
    pos = header_size
    while pos < end:
        record_header = data[pos]
        pos += 1
        assert not record_header & 0x80, f"compressed-timestamp header at offset {pos - 1}"
        local = record_header & 0x0F
        if record_header & 0x40:
            assert not record_header & 0x20, f"developer-data definition at offset {pos - 1}"
            big_endian = data[pos + 1] == 1
            global_num = struct.unpack_from(">H" if big_endian else "<H", data, pos + 2)[0]
            n_fields = data[pos + 4]
            pos += 5
            fields = []
            for _ in range(n_fields):
                fields.append((data[pos], data[pos + 1], data[pos + 2]))
                pos += 3
            definitions[local] = (big_endian, global_num, fields)
            continue
        big_endian, global_num, fields = definitions[local]
        elements = []
        for field_num, size, base_type in fields:
            if base_type & 0x1F in FOUR_BYTE_BASE_TYPES and size % 4 == 0:
                for index, offset in enumerate(range(pos, pos + size, 4)):
                    elements.append((field_num, index, offset, big_endian))
            pos += size
        yield global_num, elements
    assert pos == end, f"walk ended at {pos}, data ends at {end}"


def _sint32(data: bytes, offset: int, big_endian: bool) -> int:
    return struct.unpack_from(">i" if big_endian else "<i", data, offset)[0]


def _track_extent() -> tuple[tuple[int, int], tuple[int, int]]:
    """``((lat_min, lat_max), (lon_min, lon_max))`` in semicircles: sample_run's own track."""
    lats, lons = [], []
    with fitdecode.FitReader(str(FIXTURES / BOX_SOURCE)) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "record":
                lat = frame.get_value("position_lat", fallback=None)
                lon = frame.get_value("position_long", fallback=None)
                if lat is not None and lon is not None:
                    lats.append(lat)
                    lons.append(lon)
    assert len(lats) > 100, f"{BOX_SOURCE}: only {len(lats)} positioned records to build the box from"
    return (min(lats), max(lats)), (min(lons), max(lons))


def _area_box() -> tuple[tuple[float, float], tuple[float, float]]:
    """``((lat_lo, lat_hi), (lon_lo, lon_hi))`` in semicircles: sample_run's track, widened by 1 degree."""
    (lat_min, lat_max), (lon_min, lon_max) = _track_extent()
    widen = SEMICIRCLES_PER_DEGREE
    return (lat_min - widen, lat_max + widen), (lon_min - widen, lon_max + widen)


def _pair_key(global_num: int, lat_field: int, lon_field: int) -> str:
    name = profile.MESSAGE_TYPES[global_num].name if global_num in profile.MESSAGE_TYPES else "unknown"
    return f"{name} ({global_num}) fields {lat_field}/{lon_field}"


def _pairs_in_box(data: bytes, box) -> tuple[int, dict[str, int]]:
    """``(elements scanned, {message/fields identity: messages})`` for messages holding a pair in the box."""
    (lat_lo, lat_hi), (lon_lo, lon_hi) = box
    scanned = 0
    hits: dict[str, int] = {}
    for global_num, elements in _four_byte_elements(data):
        scanned += len(elements)
        values = [(field_num, index, _sint32(data, offset, big_endian))
                  for field_num, index, offset, big_endian in elements]
        in_lat = [(f, i) for f, i, v in values if lat_lo <= v <= lat_hi]
        in_lon = [(f, i) for f, i, v in values if lon_lo <= v <= lon_hi]
        pairs = {(a[0], b[0]) for a in in_lat for b in in_lon if a != b}
        for lat_field, lon_field in sorted(pairs):
            key = _pair_key(global_num, lat_field, lon_field)
            hits[key] = hits.get(key, 0) + 1
    return scanned, hits


@pytest.mark.parametrize("fixture_name", HILLY_FIXTURES)
def test_no_lat_long_shaped_pair_lies_in_the_athletes_area(fixture_name: str) -> None:
    scanned, hits = _pairs_in_box((FIXTURES / fixture_name).read_bytes(), _area_box())
    # Identities and counts only: never a value, never the box.
    print(f"[slice compared] {fixture_name}: {scanned} four-byte elements scanned, {len(hits)} pair identities in the box")
    assert scanned > 0, f"{fixture_name}: no four-byte element was scanned"
    assert not hits, f"{fixture_name}: lat/long-shaped pairs inside the area box: {hits}"


def test_the_check_sees_the_unstripped_track_it_was_built_from() -> None:
    """A positive control: ``sample_run.fit`` keeps its track, so its record positions are hits."""
    _, hits = _pairs_in_box((FIXTURES / BOX_SOURCE).read_bytes(), _area_box())
    print(f"[slice compared] {BOX_SOURCE}: pair identities in the box {sorted(hits)}")
    assert hits.get("record (20) fields 0/1", 0) > 100, sorted(hits)


# The plant targets are fixed by number, not chosen by the scanner under test. Each base type is the
# low five bits of the FIT base-type byte, written out here rather than read from
# FOUR_BYTE_BASE_TYPES, and asserted against the file before anything is planted.
SINT32, UINT32, FLOAT32, UINT32Z = 0x05, 0x06, 0x08, 0x0C

#: id -> (global message, (lat field, element), (lon field, element), base type, where the pair lies).
#: Targets in the first message of that number in ``hilly_run_8k_fr945.fit``.
PLANTS: dict[str, tuple[int, tuple[int, int], tuple[int, int], int, str]] = {
    "sint32-unknown-message": (140, (5, 0), (6, 0), SINT32, "centre"),
    "uint32-unknown-message": (113, (2, 0), (3, 0), UINT32, "centre"),
    "uint32z-device-info": (23, (3, 0), (24, 0), UINT32Z, "centre"),
    "float32-session": (18, (181, 0), (187, 0), FLOAT32, "centre"),
    "uint32-array-non-first-elements": (216, (2, 1), (2, 3), UINT32, "centre"),
    "sint32-in-the-1-degree-margin": (140, (2, 0), (3, 0), SINT32, "margin"),
}


def _fields_by_message(data: bytes):
    """Yield ``(global_num, {field_num: (offset, size, base_type, big_endian)})`` per data message.

    The test's own byte walk for choosing plant targets: it reads every field, whatever its base
    type or size, and shares nothing with the scanner's base-type set or element split.
    """
    header_size = data[0]
    end = header_size + struct.unpack_from("<I", data, 4)[0]
    definitions: dict[int, tuple[bool, int, list[tuple[int, int, int]]]] = {}
    pos = header_size
    while pos < end:
        record_header = data[pos]
        pos += 1
        local = record_header & 0x0F
        if record_header & 0x40:
            big_endian = data[pos + 1] == 1
            global_num = struct.unpack_from(">H" if big_endian else "<H", data, pos + 2)[0]
            count = data[pos + 4]
            definitions[local] = (big_endian, global_num,
                                  [tuple(data[pos + 5 + 3 * k: pos + 8 + 3 * k]) for k in range(count)])
            pos += 5 + 3 * count
            continue
        big_endian, global_num, fields = definitions[local]
        located = {}
        for field_num, size, base_type in fields:
            located[field_num] = (pos, size, base_type, big_endian)
            pos += size
        yield global_num, located


def _plant_offset(target: tuple[int, int, int, bool], element: int, base_type: int) -> tuple[int, bool]:
    offset, size, found_type, big_endian = target
    assert found_type & 0x1F == base_type, f"base type {found_type:#04x}, expected {base_type:#04x}"
    assert size >= 4 * (element + 1), f"field of {size} bytes has no element {element}"
    return offset + 4 * element, big_endian


@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_a_planted_pair_fails_the_check(plant: str, tmp_path: Path) -> None:
    """The check's failing branch, once per base type, array element and box edge it must cover."""
    global_num, (lat_field, lat_element), (lon_field, lon_element), base_type, where = PLANTS[plant]
    box = _area_box()
    if where == "centre":
        (lat_lo, lat_hi), (lon_lo, lon_hi) = box
        lat, lon = (lat_lo + lat_hi) / 2, (lon_lo + lon_hi) / 2
    else:
        # Half a degree past the track's own north-east corner: outside the track, inside the margin.
        (_, lat_max), (_, lon_max) = _track_extent()
        lat, lon = lat_max + SEMICIRCLES_PER_DEGREE / 2, lon_max + SEMICIRCLES_PER_DEGREE / 2
    data = bytearray((FIXTURES / HILLY_FIXTURES[0]).read_bytes())
    fields = next(located for number, located in _fields_by_message(bytes(data)) if number == global_num)
    assert lat_field in fields and lon_field in fields, f"message {global_num} lacks field {lat_field} or {lon_field}"
    key = _pair_key(global_num, lat_field, lon_field)
    assert key not in _pairs_in_box(bytes(data), box)[1], f"{key} is in the box before the plant"

    for (field_num, element), value in (((lat_field, lat_element), lat), ((lon_field, lon_element), lon)):
        offset, big_endian = _plant_offset(fields[field_num], element, base_type)
        struct.pack_into(">i" if big_endian else "<i", data, offset, int(value))
    end = data[0] + struct.unpack_from("<I", data, 4)[0]
    struct.pack_into("<H", data, end, compute_crc(data, start=0, end=end))
    planted = tmp_path / "planted.fit"
    planted.write_bytes(bytes(data))
    with fitdecode.FitReader(str(planted), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        assert sum(1 for _ in reader) > 0

    _, hits = _pairs_in_box(bytes(data), box)
    # Identities and counts only: never a value.
    print(f"[slice compared] {plant}: planted into {key} elements {lat_element}/{lon_element}; "
          f"{len(hits)} pair identities in the box: {sorted(hits)}")
    assert key in hits, sorted(hits)
