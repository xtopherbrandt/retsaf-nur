"""``tests/support/fit_patch.py``: patched FIT bytes that ``fitdecode`` still accepts.

The oracle throughout is ``fitdecode`` re-decoding the patched bytes with
``CrcCheck.RAISE``, which checks both the 14-byte header's CRC and the
trailing file CRC. No ``runcoach_api`` code judges a patch, except the one
smoke upload at the end, whose subject is the upload path itself.

Patched bytes stay in memory. Nothing here writes into ``tests/fixtures/``.

- **The walk.** Normal definitions, developer-data definitions (bit 0x20)
  and the 14-byte header; the walk's own count of developer-data definitions
  in ``dev_fields_run.fit`` matches ``fitdecode``'s. Compressed-timestamp
  headers and sources with a bad CRC are refused.
- **Remove.** ``zones_target`` (7) is removed: none decodes, every other
  message keeps its count, and the header's data size and CRC are rewritten.
- **Set.** ``zones_target.max_heart_rate``, ``session.start_time`` (on the
  file with developer fields) and two ``user_profile`` fields are set; the
  decoded raw values match and no other field value moves.
- **Unknown targets.** A message number the file does not hold, or a field
  its definition does not carry, raises.
- **Smoke upload.** The ``dev_fields_run`` patched to ``sample_run``'s start
  time is accepted by ``POST /sessions``.

Field identities, counts and the raw values the tests set are printed; no
position value is ever read.
"""

from __future__ import annotations

import collections
import importlib.util
import io
import itertools
import struct
from pathlib import Path

import fitdecode
import pytest
from fastapi.testclient import TestClient
from fitdecode.utils import compute_crc
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"

_SPEC = importlib.util.spec_from_file_location(
    "fit_patch", Path(__file__).parent / "support" / "fit_patch.py"
)
assert _SPEC is not None and _SPEC.loader is not None
fit_patch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fit_patch)

USER_PROFILE, ZONES_TARGET, SESSION = 3, 7, 18


def _raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _decode(data: bytes) -> tuple[list, list]:
    """``(headers, data messages)`` with both CRCs checked; raises on any mismatch."""
    headers, messages = [], []
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitHeader):
                headers.append(frame)
            elif isinstance(frame, fitdecode.FitDataMessage):
                messages.append(frame)
    return headers, messages


def _counts(messages: list) -> collections.Counter:
    return collections.Counter(m.global_mesg_num for m in messages)


def _field_values(messages: list) -> list[tuple[int, list[tuple[str, object]]]]:
    """Every data message as ``(global number, [(field key, raw value)])``, dev fields included."""
    out = []
    for m in messages:
        fields = []
        for f in m.fields:
            dev = getattr(f.field_def, "dev_data_index", None) if f.field_def is not None else None
            key = f"dev{dev}/{f.def_num}" if dev is not None else str(f.def_num)
            fields.append((key, f.raw_value))
        out.append((m.global_mesg_num, fields))
    return out


def _assert_header_rewritten(data: bytes) -> None:
    """The header's data size covers exactly the records, and its CRC is the CRC of bytes 0-11."""
    assert data[0] == 14
    assert data[0] + struct.unpack_from("<I", data, 4)[0] + 2 == len(data)
    assert struct.unpack_from("<H", data, 12)[0] == compute_crc(data, start=0, end=12)


# ---------------------------------------------------------------------------
# the walk
# ---------------------------------------------------------------------------


def test_walk_counts_dev_fields_definitions_as_fitdecode_does() -> None:
    raw = _raw("dev_fields_run.fit")
    expected = 0
    with fitdecode.FitReader(io.BytesIO(raw), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDefinitionMessage) and frame.dev_field_defs:
                expected += 1
    records = fit_patch.walk(raw)
    dev_definitions = [r for r in records if r.is_definition and r.definition.dev_fields]
    print(f"developer-data definitions: walk {len(dev_definitions)}, fitdecode {expected}")
    assert expected == 5
    assert len(dev_definitions) == expected
    data_count = sum(1 for r in records if not r.is_definition)
    _, messages = _decode(raw)
    assert data_count == len(messages)
    # The records tile the data section exactly.
    assert records[0].offset == raw[0]
    for a, b in itertools.pairwise(records):
        assert a.offset + a.length == b.offset
    assert records[-1].offset + records[-1].length == len(raw) - 2


def test_walk_refuses_a_compressed_timestamp_header() -> None:
    raw = bytearray(_raw("sample_run.fit")[:14])
    raw += bytes([0x80])
    struct.pack_into("<I", raw, 4, 1)
    struct.pack_into("<H", raw, 12, compute_crc(raw, start=0, end=12))
    raw += struct.pack("<H", compute_crc(raw, start=0, end=len(raw)))
    with pytest.raises(fit_patch.FitPatchError, match="compressed-timestamp"):
        fit_patch.walk(bytes(raw))


def test_walk_refuses_a_source_with_a_bad_file_crc() -> None:
    raw = bytearray(_raw("sample_run.fit"))
    raw[-1] ^= 0xFF
    with pytest.raises(fit_patch.FitPatchError, match="CRC"):
        fit_patch.walk(bytes(raw))


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["strap_run_hrv.fit", "dev_fields_run.fit"], ids=["strap_run_hrv", "dev_fields_run"]
)
def test_remove_zones_target_redecodes_with_valid_crc(name: str) -> None:
    raw = _raw(name)
    _, before = _decode(raw)
    assert _counts(before)[ZONES_TARGET] >= 1

    patched = fit_patch.remove_messages(raw, ZONES_TARGET)

    _assert_header_rewritten(patched)
    assert len(patched) < len(raw)
    headers, after = _decode(patched)
    assert headers[0].crc_matched is True
    expected = _counts(before)
    del expected[ZONES_TARGET]
    print(f"{name}: {len(before)} messages before, {len(after)} after")
    assert _counts(after) == expected
    # The surviving messages are the same messages, field for field.
    kept = [m for m in _field_values(before) if m[0] != ZONES_TARGET]
    assert _field_values(after) == kept


def test_remove_an_unknown_message_number_raises() -> None:
    with pytest.raises(fit_patch.FitPatchError, match="65000"):
        fit_patch.remove_messages(_raw("sample_run.fit"), 65000)


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


def _assert_only_field_moved(before: list, after: list, global_num: int, changes: dict[int, int]) -> None:
    """Exactly the first ``global_num`` message's ``changes`` differ; every other raw value is as decoded before."""
    b, a = _field_values(before), _field_values(after)
    assert len(a) == len(b)
    first = next(i for i, m in enumerate(b) if m[0] == global_num)
    for i, (mb, ma) in enumerate(zip(b, a, strict=True)):
        if i != first:
            assert ma == mb
            continue
        assert ma[0] == mb[0]
        for (kb, vb), (ka, va) in zip(mb[1], ma[1], strict=True):
            assert ka == kb
            if kb in {str(n) for n in changes}:
                assert va == changes[int(kb)], (kb, va)
            else:
                assert va == vb, kb


def test_set_zones_target_max_heart_rate_on_hilly_run() -> None:
    raw = _raw("hilly_run_8k_fr945.fit")
    _, before = _decode(raw)

    patched = fit_patch.set_field(raw, ZONES_TARGET, 1, 191)

    _assert_header_rewritten(patched)
    assert len(patched) == len(raw)
    _, after = _decode(patched)
    _assert_only_field_moved(before, after, ZONES_TARGET, {1: 191})
    # One field byte moves, plus the trailing file CRC; the header is untouched.
    moved = [i for i, (x, y) in enumerate(zip(raw, patched, strict=True)) if x != y]
    print(f"bytes moved: {len(moved)}")
    assert len([i for i in moved if i < len(raw) - 2]) == 1
    assert all(i >= raw[0] for i in moved)


def test_set_session_start_time_on_dev_fields_run() -> None:
    _, sample = _decode(_raw("sample_run.fit"))
    target = next(m for m in sample if m.global_mesg_num == SESSION).get_raw_value("start_time")
    raw = _raw("dev_fields_run.fit")
    _, before = _decode(raw)
    assert next(m for m in before if m.global_mesg_num == SESSION).get_raw_value("start_time") != target

    patched = fit_patch.set_field(raw, SESSION, 2, target)

    _assert_header_rewritten(patched)
    _, after = _decode(patched)
    print(f"session.start_time raw now {target}")
    _assert_only_field_moved(before, after, SESSION, {2: target})
    dev_values = sum(1 for _, fields in _field_values(after) for k, _ in fields if k.startswith("dev"))
    assert dev_values > 0


def test_set_user_profile_gender_and_activity_class() -> None:
    raw = _raw("sample_run.fit")
    _, before = _decode(raw)

    patched = fit_patch.set_field(raw, USER_PROFILE, 1, 0)
    patched = fit_patch.set_field(patched, USER_PROFILE, 17, 0x80 + 50)

    _assert_header_rewritten(patched)
    _, after = _decode(patched)
    profile = next(m for m in after if m.global_mesg_num == USER_PROFILE)
    print(
        f"gender raw {profile.get_raw_value('gender')}, activity_class raw {profile.get_raw_value('activity_class')}"
    )
    assert profile.get_raw_value("gender") == 0
    assert profile.get_raw_value("activity_class") == 0x80 + 50
    _assert_only_field_moved(before, after, USER_PROFILE, {1: 0, 17: 0x80 + 50})


def test_set_respects_a_big_endian_definition() -> None:
    """A uint16 written into a big-endian definition decodes as the value, not its byte swap."""
    raw = _raw("sample_run.fit")
    flipped = fit_patch.with_big_endian_definitions(raw, USER_PROFILE)
    _, before = _decode(flipped)
    weight = next(m for m in before if m.global_mesg_num == USER_PROFILE).get_raw_value("weight")

    patched = fit_patch.set_field(flipped, USER_PROFILE, 4, 0x0102)

    _, after = _decode(patched)
    assert next(m for m in after if m.global_mesg_num == USER_PROFILE).get_raw_value("weight") == 0x0102
    assert weight != 0x0102
    _assert_only_field_moved(before, after, USER_PROFILE, {4: 0x0102})


@pytest.mark.parametrize(
    ("global_num", "field_num", "match"),
    [(65000, 1, "65000"), (ZONES_TARGET, 250, "250")],
    ids=["unknown_message", "unknown_field"],
)
def test_set_an_unknown_target_raises(global_num: int, field_num: int, match: str) -> None:
    with pytest.raises(fit_patch.FitPatchError, match=match):
        fit_patch.set_field(_raw("sample_run.fit"), global_num, field_num, 1)


def test_set_a_value_too_wide_for_the_field_raises() -> None:
    with pytest.raises(fit_patch.FitPatchError, match="does not fit"):
        fit_patch.set_field(_raw("sample_run.fit"), ZONES_TARGET, 1, 256)


# ---------------------------------------------------------------------------
# smoke upload
# ---------------------------------------------------------------------------


def test_patched_dev_fields_run_uploads_with_201(post_fit_bytes) -> None:
    """The start time moves to ``sample_run``'s; the records stay in 2024, so they sit
    about 67.7 M s before the session start. The upload is still accepted."""
    _, sample = _decode(_raw("sample_run.fit"))
    target = next(m for m in sample if m.global_mesg_num == SESSION).get_raw_value("start_time")
    patched = fit_patch.set_field(_raw("dev_fields_run.fit"), SESSION, 2, target)

    with TestClient(app) as client:
        response = post_fit_bytes(client, "dev_fields_run_patched.fit", patched)
    print(f"POST /sessions -> {response.status_code}")
    assert response.status_code == 201, response.text
