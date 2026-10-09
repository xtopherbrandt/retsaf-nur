"""Patch FIT bytes in memory and return bytes that still decode with every CRC checked.

Loaded by tests with ``importlib.util.spec_from_file_location``, as the other
``tests/support`` modules are. Every function takes the file's bytes and
returns new bytes; nothing here writes a file, and a patched file is never
written into ``tests/fixtures/``.

- ``walk(data)`` lists the records of the data section: normal definitions,
  developer-data definitions (record header bit 0x20, whose developer field
  bytes follow the normal fields in each data message) and data messages,
  after a 12- or 14-byte header. It keeps the current definition for each
  local message number, because files redefine local numbers repeatedly.
- ``set_field(data, global_num, field_num, value)`` overwrites one field of
  the **first** data message of ``global_num`` with an unsigned raw value of
  the field's size (1, 2 or 4 bytes), in the definition's byte order.
- ``remove_messages(data, global_num)`` drops every data message of
  ``global_num``. Its definitions stay; a definition with no data is valid FIT.
- ``finalize(buf)`` rewrites the header data size (bytes 4-7), the header
  CRC (bytes 12-13, 14-byte headers only) and the trailing file CRC, with
  ``fitdecode.utils.compute_crc``. ``set_field`` and ``remove_messages`` call
  it before returning.
- ``with_big_endian_definitions(data, global_num)`` rewrites every definition
  of ``global_num`` as big-endian and byte-swaps its data, so the endianness
  branch of ``set_field`` can be exercised: no fixture writes big-endian.
- ``blank_record_field(data, field_num, start_s, end_s)`` writes the FIT
  invalid value (the one ``fitdecode`` decodes to ``None``: 0xFF for a
  ``uint8`` such as ``heart_rate``) into ``field_num`` of every ``record``
  (20) data message whose ``timestamp`` (253) lies in ``[start_s, end_s)``
  seconds after the first record message's timestamp.
- ``drop_records(data, start_s, end_s)`` drops every ``record`` data message
  in that span. Every other message, the ``session`` and ``lap`` messages
  included, keeps its bytes, so ``total_timer_time`` is unchanged.

Refused with ``FitPatchError``: a source whose file CRC does not match (so a
corrupted file is never laundered into a CRC-valid one), a compressed-
timestamp record header (no fixture has one), a header that is not 12 or 14
bytes, a record that runs past the data, a data size that does not match the
file length, a message number the file holds no data message for, a field
the first message's definition does not carry, a value that does not fit
the field, a record span that holds no record message (an empty or reversed
span included), a record message without a timestamp, and a record message
in the span whose definition does not carry the field to blank.

**A moved start time leaves the records where they were.** Setting
``session.start_time`` on ``dev_fields_run.fit`` to ``sample_run.fit``'s start
time moves the session from 2024 to 2026 and leaves every record timestamp in
2024, so the records decode at about -67.7 M s from the session start. That
is expected of this patch, not a fault in it.
"""

from __future__ import annotations

import math
import struct
from typing import NamedTuple

from fitdecode.types import BASE_TYPES
from fitdecode.utils import compute_crc


class FitPatchError(Exception):
    """The file, or the patch asked of it, is outside what this module rewrites safely."""


RECORD_MESSAGE = 20
TIMESTAMP_FIELD = 253

# The FIT invalid raw value of each integer base type (FIT SDK base-type table);
# ``fitdecode.types.BASE_TYPES`` decodes exactly these to ``None``. Floats are
# invalid as NaN and a string as zero bytes; both are handled in ``_invalid_bytes``.
_INVALID_INT = {
    0x00: 0xFF,  # enum
    0x01: 0x7F,  # sint8
    0x02: 0xFF,  # uint8
    0x83: 0x7FFF,  # sint16
    0x84: 0xFFFF,  # uint16
    0x85: 0x7FFFFFFF,  # sint32
    0x86: 0xFFFFFFFF,  # uint32
    0x0A: 0,  # uint8z
    0x8B: 0,  # uint16z
    0x8C: 0,  # uint32z
    0x0D: 0xFF,  # byte
    0x8E: 0x7FFFFFFFFFFFFFFF,  # sint64
    0x8F: 0xFFFFFFFFFFFFFFFF,  # uint64
    0x90: 0,  # uint64z
}


class FieldDef(NamedTuple):
    num: int
    size: int
    base_type: int


class DevFieldDef(NamedTuple):
    num: int
    size: int
    dev_data_index: int


class Definition(NamedTuple):
    local: int
    big_endian: bool
    global_num: int
    fields: tuple[FieldDef, ...]
    dev_fields: tuple[DevFieldDef, ...]

    @property
    def data_size(self) -> int:
        return sum(f.size for f in self.fields) + sum(f.size for f in self.dev_fields)


class Record(NamedTuple):
    """One record of the data section: ``offset`` is its record-header byte, ``length`` includes it."""

    offset: int
    length: int
    is_definition: bool
    definition: Definition


def _check_source(data: bytes) -> tuple[int, int]:
    """``(header size, data end)`` of a well-formed source with a matching file CRC."""
    if len(data) < 14:
        raise FitPatchError(f"file of {len(data)} bytes is too short for a FIT header")
    header_size = data[0]
    if header_size not in (12, 14):
        raise FitPatchError(f"header size {header_size}")
    if bytes(data[8:12]) != b".FIT":
        raise FitPatchError("no .FIT signature")
    data_size = struct.unpack_from("<I", data, 4)[0]
    end = header_size + data_size
    if end + 2 != len(data):
        raise FitPatchError(f"data size {data_size} + header {header_size} + 2 != file length {len(data)}")
    if struct.unpack_from("<H", data, end)[0] != compute_crc(data, start=0, end=end):
        raise FitPatchError("source file CRC mismatch")
    return header_size, end


def walk(data: bytes) -> list[Record]:
    """Every record of the data section, in file order."""
    header_size, end = _check_source(data)

    def need(start: int, length: int, what: str) -> None:
        if start + length > end:
            raise FitPatchError(f"{what} at offset {start} runs past the data end {end}")

    definitions: dict[int, Definition] = {}
    records: list[Record] = []
    pos = header_size
    while pos < end:
        start = pos
        record_header = data[pos]
        pos += 1
        if record_header & 0x80:
            raise FitPatchError(f"compressed-timestamp header at offset {start}")
        local = record_header & 0x0F
        if record_header & 0x40:
            need(pos, 5, "definition")
            big_endian = data[pos + 1] == 1
            global_num = struct.unpack_from(">H" if big_endian else "<H", data, pos + 2)[0]
            n_fields = data[pos + 4]
            pos += 5
            need(pos, 3 * n_fields, "field definitions")
            fields = tuple(
                FieldDef(data[p], data[p + 1], data[p + 2]) for p in range(pos, pos + 3 * n_fields, 3)
            )
            pos += 3 * n_fields
            dev_fields: tuple[DevFieldDef, ...] = ()
            if record_header & 0x20:
                need(pos, 1, "developer field count")
                n_dev = data[pos]
                pos += 1
                need(pos, 3 * n_dev, "developer field definitions")
                dev_fields = tuple(
                    DevFieldDef(data[p], data[p + 1], data[p + 2]) for p in range(pos, pos + 3 * n_dev, 3)
                )
                pos += 3 * n_dev
            definition = Definition(local, big_endian, global_num, fields, dev_fields)
            definitions[local] = definition
            records.append(Record(start, pos - start, True, definition))
            continue
        if local not in definitions:
            raise FitPatchError(f"data message for undefined local {local} at offset {start}")
        definition = definitions[local]
        need(pos, definition.data_size, "data message")
        pos += definition.data_size
        records.append(Record(start, pos - start, False, definition))
    if pos != end:
        raise FitPatchError(f"walk ended at {pos}, data ends at {end}")
    return records


def finalize(buf: bytearray) -> bytes:
    """Rewrite the header data size, the header CRC and the file CRC of ``buf``.

    ``buf`` holds the header, the data section and two trailing CRC bytes
    whose old value is ignored.
    """
    header_size = buf[0]
    end = len(buf) - 2
    struct.pack_into("<I", buf, 4, end - header_size)
    if header_size == 14:
        struct.pack_into("<H", buf, 12, compute_crc(buf, start=0, end=12))
    struct.pack_into("<H", buf, end, compute_crc(buf, start=0, end=end))
    return bytes(buf)


def _data_records(records: list[Record], global_num: int) -> list[Record]:
    found = [r for r in records if not r.is_definition and r.definition.global_num == global_num]
    if not found:
        raise FitPatchError(f"no data message of global message number {global_num}")
    return found


def set_field(data: bytes, global_num: int, field_num: int, value: int) -> bytes:
    """Set ``field_num`` of the first ``global_num`` data message to the unsigned raw ``value``."""
    record = _data_records(walk(data), global_num)[0]
    definition = record.definition
    offset = record.offset + 1
    for field in definition.fields:
        if field.num == field_num:
            break
        offset += field.size
    else:
        raise FitPatchError(f"message {global_num} carries no field {field_num}")
    formats = {1: "B", 2: "H", 4: "I"}
    if field.size not in formats:
        raise FitPatchError(f"field {global_num}/{field_num} has size {field.size}, not 1, 2 or 4")
    if not 0 <= value < 1 << (8 * field.size):
        raise FitPatchError(
            f"value {value} does not fit field {global_num}/{field_num} of {field.size} bytes"
        )
    buf = bytearray(data)
    struct.pack_into((">" if definition.big_endian else "<") + formats[field.size], buf, offset, value)
    return finalize(buf)


def remove_messages(data: bytes, global_num: int) -> bytes:
    """Drop every data message of ``global_num``; every other record keeps its bytes."""
    records = walk(data)
    dropped = {r.offset for r in _data_records(records, global_num)}
    header_size = data[0]
    buf = bytearray(data[:header_size])
    for r in records:
        if r.offset not in dropped:
            buf += data[r.offset : r.offset + r.length]
    buf += b"\x00\x00"
    return finalize(buf)


def _field_offset(record: Record, field_num: int) -> tuple[int, FieldDef]:
    """``(absolute byte offset, definition)`` of ``field_num`` in a data message."""
    offset = record.offset + 1
    for field in record.definition.fields:
        if field.num == field_num:
            return offset, field
        offset += field.size
    raise FitPatchError(
        f"message {record.definition.global_num} at offset {record.offset} carries no field {field_num}"
    )


def _invalid_bytes(field: FieldDef, big_endian: bool) -> bytes:
    """The bytes of ``field`` with every element set to its base type's FIT invalid value."""
    base = BASE_TYPES.get(field.base_type)
    if base is None:
        raise FitPatchError(f"field {field.num} has unknown base type {field.base_type:#x}")
    order = ">" if big_endian else "<"
    if field.base_type in _INVALID_INT:
        element = struct.pack(order + base.fmt, _INVALID_INT[field.base_type])
    elif base.fmt in ("f", "d"):
        element = struct.pack(order + base.fmt, math.nan)
    elif base.fmt == "s":
        element = b"\x00"
    else:
        raise FitPatchError(f"field {field.num} has base type {base.name}; no invalid value is known here")
    if field.size % len(element):
        raise FitPatchError(f"field {field.num} of {field.size} bytes is not whole {base.name} elements")
    return element * (field.size // len(element))


def _span(data: bytes, start_s: float, end_s: float) -> tuple[list[Record], list[Record]]:
    """``(every record of the walk, the record data messages in the span)``.

    The span is ``[start_s, end_s)`` seconds after the first record message's
    timestamp (field 253, a ``uint32`` of FIT seconds), in file order.
    """
    records = walk(data)
    found = []
    first: int | None = None
    for record in _data_records(records, RECORD_MESSAGE):
        offset, field = _field_offset(record, TIMESTAMP_FIELD)
        if field.size != 4:
            raise FitPatchError(f"record at offset {record.offset} has a timestamp of {field.size} bytes")
        order = ">" if record.definition.big_endian else "<"
        timestamp = struct.unpack_from(order + "I", data, offset)[0]
        if first is None:
            first = timestamp
        if start_s <= timestamp - first < end_s:
            found.append(record)
    if not found:
        raise FitPatchError(f"no record message in [{start_s}, {end_s}) s after the first record")
    return records, found


def blank_record_field(data: bytes, field_num: int, start_s: float, end_s: float) -> bytes:
    """Set ``field_num`` to its FIT invalid value on every record message in the span."""
    _, in_span = _span(data, start_s, end_s)
    buf = bytearray(data)
    for record in in_span:
        offset, field = _field_offset(record, field_num)
        invalid = _invalid_bytes(field, record.definition.big_endian)
        buf[offset : offset + field.size] = invalid
    return finalize(buf)


def drop_records(data: bytes, start_s: float, end_s: float) -> bytes:
    """Drop every record message in the span; every other record keeps its bytes."""
    records, in_span = _span(data, start_s, end_s)
    dropped = {r.offset for r in in_span}
    header_size = data[0]
    buf = bytearray(data[:header_size])
    for r in records:
        if r.offset not in dropped:
            buf += data[r.offset : r.offset + r.length]
    buf += b"\x00\x00"
    return finalize(buf)


def with_big_endian_definitions(data: bytes, global_num: int) -> bytes:
    """Rewrite every definition of ``global_num`` as big-endian and byte-swap its data messages.

    Each normal field is swapped element by element at its base type's size;
    developer fields carry no base type in the definition and are refused.
    """
    buf = bytearray(data)
    for r in walk(data):
        d = r.definition
        if d.global_num != global_num or d.big_endian:
            continue
        if d.dev_fields:
            raise FitPatchError(
                f"message {global_num} has developer fields; their element size is unknown here"
            )
        if r.is_definition:
            buf[r.offset + 2] = 1
            struct.pack_into(">H", buf, r.offset + 3, global_num)
            continue
        pos = r.offset + 1
        for field in d.fields:
            base = BASE_TYPES.get(field.base_type)
            if base is None:
                raise FitPatchError(
                    f"field {global_num}/{field.num} has unknown base type {field.base_type:#x}"
                )
            step = base.size if field.size % base.size == 0 else 1
            for elem in range(pos, pos + field.size, step):
                buf[elem : elem + step] = buf[elem : elem + step][::-1]
            pos += field.size
    return finalize(buf)
