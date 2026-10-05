"""Overwrite every position value in a FIT file, keeping every other byte.

    uv run --package runcoach-api python runcoach-api/tests/support/strip_fit_positions.py SRC DST

Writes DST from SRC. The two hilly fixtures were made with this script from
the athlete's originals, which stay outside the repository: their GPS tracks
are not part of the corpus.

**What counts as a position value.** Any field whose profile name ends in
``_lat`` or ``_long``, in any message (record 0/1, lap 3-6, session 3, 4,
29-32, 38, 39, split 21-24, and the rest of the profile), plus lap fields
27-30, which the FR945 writes and the profile leaves unnamed but which hold
semicircle values on the original track.

**How.** A byte-level walk, not a re-encode. The walk keeps the current
definition for each local message number, because the files redefine local
numbers repeatedly. Each position value's 4 bytes are overwritten with the
sint32 invalid value ``0x7FFFFFFF`` in the definition's byte order. The
14- or 12-byte header is left untouched, and the trailing file CRC is
recomputed with ``fitdecode.utils.compute_crc``. Nothing else moves.

The walk refuses (exit 2, naming the reason) what it does not need to
handle and cannot check: compressed-timestamp headers, developer fields,
a position field that is not a whole number of 4-byte elements, or a data
size that does not match the header. It never prints a field value.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from fitdecode import profile
from fitdecode.utils import compute_crc

#: sint32 invalid (FIT base type 0x85).
INVALID_SINT32 = 0x7FFFFFFF

#: Lap fields the profile leaves unnamed that carry positions on the FR945.
UNNAMED_POSITION_FIELDS: dict[int, frozenset[int]] = {19: frozenset({27, 28, 29, 30})}


def _named_position_fields() -> dict[int, frozenset[int]]:
    out: dict[int, set[int]] = {}
    for global_num, mesg_type in profile.MESSAGE_TYPES.items():
        for field_num, field in mesg_type.fields.items():
            if field.name.endswith("_lat") or field.name.endswith("_long"):
                out.setdefault(global_num, set()).add(field_num)
    return {g: frozenset(f) for g, f in out.items()}


def position_fields() -> dict[int, frozenset[int]]:
    """Global message number -> field numbers stripped by this script."""
    fields = {g: set(f) for g, f in _named_position_fields().items()}
    for global_num, nums in UNNAMED_POSITION_FIELDS.items():
        fields.setdefault(global_num, set()).update(nums)
    return {g: frozenset(f) for g, f in fields.items()}


class StripError(Exception):
    """The file holds a shape this walk does not rewrite safely."""


def strip_positions(data: bytes) -> tuple[bytes, list[tuple[int, int]]]:
    """Return the stripped bytes and the ``(offset, length)`` windows overwritten."""
    targets = position_fields()
    buf = bytearray(data)
    header_size = buf[0]
    if header_size not in (12, 14):
        raise StripError(f"header size {header_size}")
    if bytes(buf[8:12]) != b".FIT":
        raise StripError("no .FIT signature")
    data_size = struct.unpack_from("<I", buf, 4)[0]
    end = header_size + data_size
    if end + 2 != len(buf):
        raise StripError(f"data size {data_size} + header {header_size} + 2 != file length {len(buf)}")

    # local number -> (big_endian, global number, [(field_num, size)])
    definitions: dict[int, tuple[bool, int, list[tuple[int, int]]]] = {}
    windows: list[tuple[int, int]] = []
    pos = header_size
    while pos < end:
        record_header = buf[pos]
        pos += 1
        if record_header & 0x80:
            raise StripError(f"compressed-timestamp header at offset {pos - 1}")
        local = record_header & 0x0F
        if record_header & 0x40:
            if record_header & 0x20:
                raise StripError(f"developer-data definition at offset {pos - 1}")
            big_endian = buf[pos + 1] == 1
            global_num = struct.unpack_from(">H" if big_endian else "<H", buf, pos + 2)[0]
            n_fields = buf[pos + 4]
            pos += 5
            fields = []
            for _ in range(n_fields):
                fields.append((buf[pos], buf[pos + 1]))
                pos += 3
            definitions[local] = (big_endian, global_num, fields)
            continue
        if local not in definitions:
            raise StripError(f"data message for undefined local {local} at offset {pos - 1}")
        big_endian, global_num, fields = definitions[local]
        wanted = targets.get(global_num, frozenset())
        for field_num, size in fields:
            if field_num in wanted:
                if size % 4:
                    raise StripError(f"position field {global_num}/{field_num} has size {size}")
                for elem in range(pos, pos + size, 4):
                    struct.pack_into(">i" if big_endian else "<i", buf, elem, INVALID_SINT32)
                    windows.append((elem, 4))
            pos += size
    if pos != end:
        raise StripError(f"walk ended at {pos}, data ends at {end}")

    struct.pack_into("<H", buf, end, compute_crc(buf, start=0, end=end))
    return bytes(buf), windows


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: strip_fit_positions.py SRC DST", file=sys.stderr)
        return 2
    src, dst = Path(argv[1]), Path(argv[2])
    try:
        stripped, windows = strip_positions(src.read_bytes())
    except StripError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    dst.write_bytes(stripped)
    print(f"{dst.name}: {len(windows)} position values overwritten")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
