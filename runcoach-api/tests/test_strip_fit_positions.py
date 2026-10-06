"""``tests/support/strip_fit_positions.py``: what it refuses, and what one strip changes.

The stripper made the two hilly fixtures from the athlete's originals, which
stay outside the repository, so its own behaviour is the only evidence of how
those files were written. These tests run it on copies of ``sample_run.fit``
in ``tmp_path``; no committed fixture is ever written.

- **Refusals.** A source whose file CRC does not match is refused, so a
  corrupted original is never laundered into a CRC-valid output. A definition
  that runs past the data is refused as a ``StripError``, not a bare
  ``struct.error`` or ``IndexError``. The command line refuses (exit 2) a
  destination that resolves to the source, and one that already exists.
- **The round trip.** One strip of ``sample_run.fit``: the output decodes
  with the CRC checked, holds no non-null position field, changes no byte
  outside a position field or the trailing CRC, and keeps the header. The
  position field bytes are located by the test's own byte walk, not by the
  windows the stripper reports.

Field identities and counts only are printed, never a value.
"""

from __future__ import annotations

import importlib.util
import shutil
import struct
from pathlib import Path

import fitdecode
import pytest
from fitdecode.utils import compute_crc

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = "sample_run.fit"

_SPEC = importlib.util.spec_from_file_location(
    "strip_fit_positions", Path(__file__).parent / "support" / "strip_fit_positions.py"
)
assert _SPEC is not None and _SPEC.loader is not None
strip_fit_positions = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(strip_fit_positions)

StripError = strip_fit_positions.StripError
POSITION_FIELDS: dict[int, frozenset[int]] = strip_fit_positions.position_fields()


@pytest.fixture
def source_copy(tmp_path: Path) -> Path:
    copy = tmp_path / SOURCE
    shutil.copyfile(FIXTURES / SOURCE, copy)
    return copy


def _field_spans(data: bytes):
    """Yield ``(global_num, field_num, offset, size)`` for every field of every data message.

    The test's own walk: it shares nothing with the stripper's and reads every field.
    """
    header_size = data[0]
    end = header_size + struct.unpack_from("<I", data, 4)[0]
    definitions: dict[int, tuple[int, list[tuple[int, int]]]] = {}
    pos = header_size
    while pos < end:
        record_header = data[pos]
        pos += 1
        local = record_header & 0x0F
        if record_header & 0x40:
            big_endian = data[pos + 1] == 1
            global_num = struct.unpack_from(">H" if big_endian else "<H", data, pos + 2)[0]
            count = data[pos + 4]
            definitions[local] = (global_num, [(data[pos + 5 + 3 * k], data[pos + 6 + 3 * k]) for k in range(count)])
            pos += 5 + 3 * count
            continue
        global_num, fields = definitions[local]
        for field_num, size in fields:
            yield global_num, field_num, pos, size
            pos += size
    assert pos == end, f"walk ended at {pos}, data ends at {end}"


def _position_counts(path: Path) -> tuple[int, int]:
    """``(position fields decoded, non-null among them)``, with the file CRC checked."""
    checked = present = 0
    with fitdecode.FitReader(str(path), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            wanted = POSITION_FIELDS.get(frame.global_mesg_num, frozenset())
            for field in frame.fields:
                if field.def_num in wanted:
                    checked += 1
                    present += field.value is not None
    return checked, present


def _with_crc(buf: bytearray) -> bytes:
    end = buf[0] + struct.unpack_from("<I", buf, 4)[0]
    struct.pack_into("<H", buf, end, compute_crc(buf, start=0, end=end))
    return bytes(buf)


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------


def test_a_source_with_a_bad_crc_is_refused(source_copy: Path) -> None:
    """One bit flipped in a data record's field, the CRC left as it was: refused, never laundered."""
    data = bytearray(source_copy.read_bytes())
    # The first field of the first data message: inside a record, not the header, not the CRC.
    _, _, offset, size = next(span for span in _field_spans(bytes(data)) if span[3] > 0)
    end = data[0] + struct.unpack_from("<I", data, 4)[0]
    assert data[0] <= offset < end and size > 0
    data[offset] ^= 0x01
    source_copy.write_bytes(bytes(data))
    print(f"[slice compared] flipped bit 0 at offset {offset} of {len(data)} (data ends at {end})")

    with pytest.raises(StripError, match="source CRC mismatch"):
        strip_fit_positions.strip_positions(bytes(data))


def test_a_definition_past_the_data_is_a_strip_error() -> None:
    """A definition that claims more fields than the data holds: a StripError, not struct.error or IndexError."""
    # Record header 0x40 (definition, local 0), reserved, little-endian, global 20, 5 fields, one field given.
    body = bytes([0x40, 0x00, 0x00, 20, 0x00, 5, 0x00, 0x04, 0x85])
    header = bytearray(14)
    header[0], header[1] = 14, 0x20
    struct.pack_into("<I", header, 4, len(body))
    header[8:12] = b".FIT"
    data = _with_crc(bytearray(bytes(header) + body + b"\x00\x00"))

    with pytest.raises(StripError, match="runs past the data end"):
        strip_fit_positions.strip_positions(data)


def test_the_cli_refuses_to_write_over_its_source(source_copy: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = source_copy.read_bytes()
    same = source_copy.parent / "sub" / ".." / source_copy.name
    (source_copy.parent / "sub").mkdir()

    code = strip_fit_positions.main(["strip_fit_positions.py", str(source_copy), str(same)])

    err = capsys.readouterr().err
    print(f"[slice compared] exit {code}; stderr {err.strip()!r}")
    assert code == 2
    assert "is the source" in err
    assert source_copy.read_bytes() == before


def test_the_cli_refuses_to_write_over_an_existing_file(
    source_copy: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dst = tmp_path / "existing.fit"
    dst.write_bytes(b"keep me")

    code = strip_fit_positions.main(["strip_fit_positions.py", str(source_copy), str(dst)])

    err = capsys.readouterr().err
    print(f"[slice compared] exit {code}; stderr {err.strip()!r}")
    assert code == 2
    assert "already exists" in err
    assert dst.read_bytes() == b"keep me"


def test_the_cli_writes_a_new_destination(source_copy: Path, tmp_path: Path) -> None:
    """The control for the two refusals: a fresh destination is written, with exit 0."""
    dst = tmp_path / "stripped.fit"
    assert strip_fit_positions.main(["strip_fit_positions.py", str(source_copy), str(dst)]) == 0
    assert dst.read_bytes() == strip_fit_positions.strip_positions(source_copy.read_bytes())[0]


# ---------------------------------------------------------------------------
# the round trip
# ---------------------------------------------------------------------------


def test_one_strip_changes_only_position_bytes_and_the_crc(source_copy: Path, tmp_path: Path) -> None:
    original = source_copy.read_bytes()
    stripped, windows = strip_fit_positions.strip_positions(original)
    out = tmp_path / "stripped.fit"
    out.write_bytes(stripped)

    checked_before, present_before = _position_counts(source_copy)
    checked_after, present_after = _position_counts(out)

    header_size = original[0]
    end = header_size + struct.unpack_from("<I", original, 4)[0]
    allowed = {end, end + 1}
    position_spans = 0
    for global_num, field_num, offset, size in _field_spans(original):
        if field_num in POSITION_FIELDS.get(global_num, frozenset()):
            position_spans += 1
            allowed.update(range(offset, offset + size))
    changed = [i for i, (a, b) in enumerate(zip(original, stripped, strict=True)) if a != b]
    outside = [i for i in changed if i not in allowed]
    # Counts only: never a value.
    print(
        f"[slice compared] {SOURCE}: position fields decoded {checked_before} -> {checked_after}, "
        f"non-null {present_before} -> {present_after}; {position_spans} position spans by the test's walk, "
        f"{len(windows)} windows reported; {len(changed)} bytes changed, {len(outside)} outside the spans and CRC"
    )

    assert len(stripped) == len(original)
    assert present_before > 0, "the source holds no position value, so the strip proves nothing"
    assert checked_after == checked_before > 0
    assert present_after == 0
    assert changed, "the strip changed no byte"
    assert not outside, f"{len(outside)} changed bytes outside every position field and the CRC, first {outside[:5]}"
    assert stripped[:header_size] == original[:header_size]
