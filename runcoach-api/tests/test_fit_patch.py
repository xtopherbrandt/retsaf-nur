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
- **Strings.** ``set_string_field`` blanks ``session.sport_profile_name`` to
  NUL bytes (``fitdecode`` decodes it to ``None``), writes spaces into it and
  a short name into ``workout.wkt_name``, NUL-padded to the field's size; no
  other field value moves. A non-string field, a value that does not fit
  and a value holding a NUL byte are refused.
- **Unknown targets.** A message number the file does not hold, or a field
  its definition does not carry, raises.
- **Decimation.** ``decimate_records`` keeps the first record and every k-th
  after it (the tail after the last kept one, fewer than k records, is
  dropped), and the kept records are the original's, field for field; every
  non-record message keeps its count and bytes, the ``session`` message
  included. ``decimate_records_randomly`` keeps records 6 to 8 s apart on
  ``sample_run`` (every kept-to-kept step at least 6 s and at most 8 s plus
  the file's own largest irregular step of 2 s, all three spacings drawn,
  the first record kept, the last kept one under 8 s before the last), is the
  same set for the same seed and a different set for another, and never
  fabricates a record. A step under 1
  and reversed or zero spacing bounds are refused.
- **Shift.** ``shift_timestamps`` moves every time field (``timestamp`` on
  every message, ``file_id.time_created``, the session and lap
  ``start_time`` and every other ``date_time`` field) by exactly the offset
  and no other field value; the copy's ``(source_device, start_time)``
  differs from its source's. A shift out of the ``uint32`` range and a
  source with a bad CRC are refused.
- **The Nth message.** ``set_field_at`` sets the intensity of the third
  ``workout_step`` of ``sample_run`` and moves no other field of any message,
  the other three steps included; index 0 gives ``set_field``'s bytes; a
  big-endian definition is honoured; an index past the last message, or
  below 0, is refused.
- **Duplicate.** ``duplicate_message`` inserts a byte copy of the second
  ``workout_step`` right after it: the copy decodes with both CRCs checked,
  field for field equal to its source, and every other message keeps its
  place and values. An index past the last message is refused.
- **Repeat workout.** ``tests/support/repeat_workout.py`` builds five steps
  from ``sample_run``'s four (warm-up, active 600 s, recovery 120 s with an
  open target, repeat from step 1 three times, cool-down); raw ``fitdecode``
  reads check each patched raw value, the resolved ``duration_step`` and
  ``repeat_steps`` of the repeat step, ``num_valid_steps`` 5, and that every
  lap is left as written.
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
# strings
# ---------------------------------------------------------------------------

WORKOUT = 26
SPORT_PROFILE_NAME, WKT_NAME = 110, 8


def _first(messages: list, global_num: int):
    return next(m for m in messages if m.global_mesg_num == global_num)


def test_blank_string_field_decodes_to_none_and_moves_nothing_else() -> None:
    raw = _raw("wrist_ppg_run.fit")
    _, before = _decode(raw)
    assert _first(before, SESSION).get_value("sport_profile_name") == "Run"

    patched = fit_patch.set_string_field(raw, SESSION, SPORT_PROFILE_NAME, None)

    _assert_header_rewritten(patched)
    assert len(patched) == len(raw)
    _, after = _decode(patched)
    profile = _first(after, SESSION)
    print(f"sport_profile_name raw now {profile.get_raw_value('sport_profile_name')!r}")
    assert profile.get_value("sport_profile_name", fallback="<missing>") is None
    _assert_only_field_moved(before, after, SESSION, {SPORT_PROFILE_NAME: None})
    # The field's bytes are all NUL; the field is 128 bytes in every fixture.
    record = next(
        r for r in fit_patch.walk(patched) if not r.is_definition and r.definition.global_num == SESSION
    )
    offset, field = fit_patch._field_offset(record, SPORT_PROFILE_NAME)
    assert field.size == 128
    assert patched[offset : offset + field.size] == b"\x00" * field.size


def test_spaces_written_into_a_string_field_decode_as_spaces() -> None:
    raw = _raw("wrist_ppg_run.fit")
    _, before = _decode(raw)

    patched = fit_patch.set_string_field(raw, SESSION, SPORT_PROFILE_NAME, "   ")

    _, after = _decode(patched)
    print(f"sport_profile_name now {_first(after, SESSION).get_value('sport_profile_name')!r}")
    assert _first(after, SESSION).get_value("sport_profile_name") == "   "
    _assert_only_field_moved(before, after, SESSION, {SPORT_PROFILE_NAME: "   "})


def test_a_name_written_into_wkt_name_is_nul_padded_to_the_field_size() -> None:
    raw = _raw("sample_run.fit")
    _, before = _decode(raw)
    assert _first(before, WORKOUT).get_value("wkt_name") == "Easy Run"

    patched = fit_patch.set_string_field(raw, WORKOUT, WKT_NAME, "Tempo")

    assert len(patched) == len(raw)
    _, after = _decode(patched)
    print(f"wkt_name now {_first(after, WORKOUT).get_value('wkt_name')!r}")
    assert _first(after, WORKOUT).get_value("wkt_name") == "Tempo"
    _assert_only_field_moved(before, after, WORKOUT, {WKT_NAME: "Tempo"})
    record = next(
        r for r in fit_patch.walk(patched) if not r.is_definition and r.definition.global_num == WORKOUT
    )
    offset, field = fit_patch._field_offset(record, WKT_NAME)
    assert patched[offset : offset + field.size] == b"Tempo" + b"\x00" * (field.size - 5)


def test_a_string_that_fills_the_field_exactly_is_refused() -> None:
    """The terminating NUL needs a byte of its own; 32 bytes of name in a 32-byte field is refused."""
    with pytest.raises(fit_patch.FitPatchError, match="does not fit"):
        fit_patch.set_string_field(_raw("sample_run.fit"), WORKOUT, WKT_NAME, "x" * 32)
    patched = fit_patch.set_string_field(_raw("sample_run.fit"), WORKOUT, WKT_NAME, "x" * 31)
    _, after = _decode(patched)
    assert _first(after, WORKOUT).get_value("wkt_name") == "x" * 31


def test_a_multibyte_name_is_measured_in_bytes() -> None:
    patched = fit_patch.set_string_field(_raw("sample_run.fit"), WORKOUT, WKT_NAME, "Ééé")
    _, after = _decode(patched)
    assert _first(after, WORKOUT).get_value("wkt_name") == "Ééé"
    with pytest.raises(fit_patch.FitPatchError, match="does not fit"):
        fit_patch.set_string_field(_raw("sample_run.fit"), WORKOUT, WKT_NAME, "É" * 16)


def test_a_value_holding_a_nul_byte_is_refused() -> None:
    with pytest.raises(fit_patch.FitPatchError, match="NUL"):
        fit_patch.set_string_field(_raw("sample_run.fit"), WORKOUT, WKT_NAME, "Easy\x00Run")


def test_set_string_field_refuses_a_non_string_field() -> None:
    with pytest.raises(fit_patch.FitPatchError, match="not a string"):
        fit_patch.set_string_field(_raw("sample_run.fit"), ZONES_TARGET, 1, None)


def test_set_string_field_refuses_a_non_string_value() -> None:
    with pytest.raises(fit_patch.FitPatchError, match="str or None"):
        fit_patch.set_string_field(_raw("sample_run.fit"), WORKOUT, WKT_NAME, 5)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# decimation
# ---------------------------------------------------------------------------

RECORD = 20


def _record_timestamps(messages: list) -> list[int]:
    return [m.get_raw_value("timestamp") for m in messages if m.global_mesg_num == RECORD]


def _records_only(values: list[tuple[int, list]]) -> list[tuple[int, list]]:
    return [m for m in values if m[0] == RECORD]


def _non_records(values: list[tuple[int, list]]) -> list[tuple[int, list]]:
    return [m for m in values if m[0] != RECORD]


@pytest.mark.parametrize("every", [1, 7], ids=["every_1", "every_7"])
def test_decimate_keeps_the_first_and_every_kth_record(every: int) -> None:
    raw = _raw("sample_run.fit")
    _, before = _decode(raw)
    records_before = _records_only(_field_values(before))

    patched = fit_patch.decimate_records(raw, every)

    _assert_header_rewritten(patched)
    _, after = _decode(patched)
    expected_positions = list(range(0, len(records_before), every))
    print(f"sample_run every {every}: {len(records_before)} records -> {len(_record_timestamps(after))}, "
          f"expected {len(expected_positions)}, last kept position {expected_positions[-1]} of {len(records_before) - 1}")
    assert expected_positions[-1] <= len(records_before) - 1 < expected_positions[-1] + every
    assert _records_only(_field_values(after)) == [records_before[i] for i in expected_positions]
    assert _non_records(_field_values(after)) == _non_records(_field_values(before))
    if every == 1:
        assert patched == raw


def test_decimate_randomly_spaces_kept_records_six_to_eight_seconds_apart() -> None:
    raw = _raw("sample_run.fit")
    _, before = _decode(raw)
    stamps_before = _record_timestamps(before)
    # sample_run is 1 Hz but for one 2 s and one 3 s step, so a kept-to-kept step can overshoot the
    # drawn spacing by at most 2 s (the first record at or past the spacing is kept).
    overshoot = max(b - a for a, b in itertools.pairwise(stamps_before)) - 1
    assert overshoot == 2

    patched = fit_patch.decimate_records_randomly(raw, 6, 8, seed=1)
    again = fit_patch.decimate_records_randomly(raw, 6, 8, seed=1)
    other = fit_patch.decimate_records_randomly(raw, 6, 8, seed=2)

    _assert_header_rewritten(patched)
    _, after = _decode(patched)
    stamps = _record_timestamps(after)
    steps = collections.Counter(b - a for a, b in itertools.pairwise(stamps))
    print(f"sample_run seed 1: {len(stamps_before)} records -> {len(stamps)}; steps {dict(sorted(steps.items()))}; "
          f"last kept {stamps_before[-1] - stamps[-1]} s before the last record")
    assert stamps[0] == stamps_before[0]
    assert 0 <= stamps_before[-1] - stamps[-1] < 8
    assert min(steps) >= 6 and max(steps) <= 8 + overshoot
    assert {6, 7, 8} <= set(steps)
    assert sum(v for k, v in steps.items() if k > 8) <= 2
    kept = _records_only(_field_values(after))
    assert all(m in _records_only(_field_values(before)) for m in kept)
    assert _non_records(_field_values(after)) == _non_records(_field_values(before))
    assert again == patched
    assert _record_timestamps(_decode(other)[1]) != stamps


@pytest.mark.parametrize(
    ("call", "match"),
    [
        (lambda raw: fit_patch.decimate_records(raw, 0), "at least 1"),
        (lambda raw: fit_patch.decimate_records_randomly(raw, 8, 6, 1), "low_s <= high_s"),
        (lambda raw: fit_patch.decimate_records_randomly(raw, 0, 6, 1), "low_s <= high_s"),
    ],
    ids=["step_0", "reversed_bounds", "zero_low"],
)
def test_decimation_refuses_a_bad_step_or_bounds(call, match: str) -> None:
    with pytest.raises(fit_patch.FitPatchError, match=match):
        call(_raw("sample_run.fit"))


# ---------------------------------------------------------------------------
# shift
# ---------------------------------------------------------------------------

LAP = 19
DAY_S = 86_400
# The profile types of an absolute time in FIT seconds; ``fitdecode`` decodes
# their raw value as the integer stored.
_TIME_TYPES = ("date_time", "local_date_time")


def _is_time_field(field) -> bool:
    return field.def_num == fit_patch.TIMESTAMP_FIELD or getattr(field.type, "name", None) in _TIME_TYPES


@pytest.mark.parametrize(
    ("name", "seconds"),
    [("sample_run.fit", -400 * DAY_S), ("strap_hrv_capture.fit", 3 * DAY_S + 17), ("dev_fields_run.fit", DAY_S)],
)
def test_a_shifted_copy_decodes_and_moves_every_timestamp(name: str, seconds: int) -> None:
    """Every time field (``timestamp`` on every message, ``file_id.time_created``, the session
    and lap ``start_time`` and the other ``date_time`` fields) moves by exactly ``seconds``;
    no other field value moves, and both CRCs check."""
    from runcoach_api.ingestion import fit_parser, mapping

    raw = _raw(name)
    shifted = fit_patch.shift_timestamps(raw, seconds)
    _assert_header_rewritten(shifted)
    _, before = _decode(raw)
    _, after = _decode(shifted)
    assert len(after) == len(before)
    moved: collections.Counter = collections.Counter()
    moved_253 = 0
    for old, new in zip(before, after, strict=True):
        assert new.global_mesg_num == old.global_mesg_num
        for f_old, f_new in zip(old.fields, new.fields, strict=True):
            assert f_new.def_num == f_old.def_num
            if _is_time_field(f_old) and f_old.raw_value is not None:
                assert f_new.raw_value == f_old.raw_value + seconds, (old.name, f_old.name)
                moved[(old.name, f_old.name)] += 1
                moved_253 += f_old.def_num == fit_patch.TIMESTAMP_FIELD
            else:
                assert f_new.raw_value == f_old.raw_value, (old.name, f_old.name)
    print(f"{name} shifted by {seconds} s; moved: {sorted(moved.items())}")
    counts = _counts(before)
    assert moved[("file_id", "time_created")] == 1
    assert moved[("session", "start_time")] == counts[SESSION] >= 1
    assert moved[("lap", "start_time")] == counts[LAP] >= 1
    assert moved[("record", "timestamp")] == counts[fit_patch.RECORD_MESSAGE] >= 1
    # Field 253 moved on every message that carries a valid one, known to the profile or not.
    assert moved_253 == sum(
        1 for m in before if m.has_field(fit_patch.TIMESTAMP_FIELD) and m.get_raw_value(fit_patch.TIMESTAMP_FIELD) is not None
    )

    source, _ = mapping.to_canonical(fit_parser.decode(raw))
    copy, _ = mapping.to_canonical(fit_parser.decode(shifted))
    print(f"source ({source.source_device}, {source.start_time}); copy ({copy.source_device}, {copy.start_time})")
    assert copy.source_device == source.source_device
    assert (copy.source_device, copy.start_time) != (source.source_device, source.start_time)


@pytest.mark.parametrize("seconds", [-(1 << 32), 1 << 32])
def test_a_shift_out_of_the_uint32_range_is_refused(seconds: int) -> None:
    with pytest.raises(fit_patch.FitPatchError, match="out of range"):
        fit_patch.shift_timestamps(_raw("sample_run.fit"), seconds)


def test_a_shift_refuses_a_source_with_a_bad_crc() -> None:
    raw = bytearray(_raw("sample_run.fit"))
    raw[-1] ^= 0xFF
    with pytest.raises(fit_patch.FitPatchError, match="CRC"):
        fit_patch.shift_timestamps(bytes(raw), DAY_S)


# ---------------------------------------------------------------------------
# the Nth message, a duplicate, the repeat workout
# ---------------------------------------------------------------------------

WORKOUT, WORKOUT_STEP, LAP = 26, 27, 19
STEP_INTENSITY = 7



def _repeat_workout_module():
    """``tests/support/repeat_workout.py``, loaded by path as the other support modules are."""
    spec = importlib.util.spec_from_file_location(
        "repeat_workout", Path(__file__).parent / "support" / "repeat_workout.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _positions(values: list, global_num: int) -> list[int]:
    """The list positions of the data messages of ``global_num``."""
    return [i for i, (g, _) in enumerate(values) if g == global_num]


def _changed(old: list, new: list) -> list[tuple[int, str, object, object]]:
    """``(message position, field key, old raw, new raw)`` for every field whose raw value moved."""
    assert len(old) == len(new)
    out = []
    for i, ((g_old, f_old), (g_new, f_new)) in enumerate(zip(old, new, strict=True)):
        assert g_old == g_new
        assert [k for k, _ in f_old] == [k for k, _ in f_new]
        out += [(i, k, a, b) for (k, a), (_, b) in zip(f_old, f_new, strict=True) if a != b]
    return out


def test_set_field_at_patches_only_the_nth_workout_step() -> None:
    raw = _raw("sample_run.fit")
    patched = fit_patch.set_field_at(raw, WORKOUT_STEP, 2, STEP_INTENSITY, 4)
    _assert_header_rewritten(patched)
    _, before = _decode(raw)
    _, after = _decode(patched)
    old, new = _field_values(before), _field_values(after)
    steps = _positions(old, WORKOUT_STEP)
    changed = _changed(old, new)
    print(f"workout_step positions {steps}; changed {changed}")
    assert len(steps) == 4
    assert changed == [(steps[2], str(STEP_INTENSITY), 0, 4)]
    # Index 0 is the first message: the same bytes set_field writes.
    assert fit_patch.set_field_at(raw, WORKOUT_STEP, 0, STEP_INTENSITY, 4) == fit_patch.set_field(
        raw, WORKOUT_STEP, STEP_INTENSITY, 4
    )
    # A big-endian definition is written big-endian at the Nth message too.
    swapped = fit_patch.with_big_endian_definitions(raw, WORKOUT_STEP)
    _, big = _decode(fit_patch.set_field_at(swapped, WORKOUT_STEP, 3, 2, 600000))
    durations = [m.get_raw_value(2) for m in big if m.global_mesg_num == WORKOUT_STEP]
    print(f"big-endian step durations {durations}")
    assert durations == [300000, 1200000, 900000, 600000]
    for index in (4, -1):
        with pytest.raises(fit_patch.FitPatchError, match=f"index {index}"):
            fit_patch.set_field_at(raw, WORKOUT_STEP, index, STEP_INTENSITY, 4)


def test_duplicate_message_inserts_a_decodable_copy() -> None:
    raw = _raw("sample_run.fit")
    copied = fit_patch.duplicate_message(raw, WORKOUT_STEP, 1)
    _assert_header_rewritten(copied)
    assert len(copied) > len(raw)
    _, before = _decode(raw)
    _, after = _decode(copied)
    old, new = _field_values(before), _field_values(after)
    at = _positions(old, WORKOUT_STEP)[1]
    print(f"counts before {sorted(_counts(before).items())}, after {sorted(_counts(after).items())}")
    assert _counts(after) - _counts(before) == collections.Counter({WORKOUT_STEP: 1})
    assert _counts(before) - _counts(after) == collections.Counter()
    assert new == old[: at + 1] + [old[at]] + old[at + 1 :]
    # The copy is the source's bytes, record header included.
    steps = [r for r in fit_patch.walk(copied) if not r.is_definition and r.definition.global_num == WORKOUT_STEP]
    first, second = steps[1], steps[2]
    assert second.offset == first.offset + first.length
    assert copied[first.offset : first.offset + first.length] == copied[second.offset : second.offset + second.length]
    with pytest.raises(fit_patch.FitPatchError, match="index 4"):
        fit_patch.duplicate_message(raw, WORKOUT_STEP, 4)


def test_repeat_workout_decodes_five_steps_with_a_repeat_from_step_1_three_times() -> None:
    built = _repeat_workout_module().build_repeat_workout()
    headers, messages = _decode(built)
    assert headers[0].header_size == 14
    steps = [m for m in messages if m.global_mesg_num == WORKOUT_STEP]
    rows = [
        tuple(m.get_raw_value(n) for n in (254, 7, 1, 2, 3, 4)) for m in steps
    ]
    print("message_index, intensity, duration_type, duration_value, target_type, target_value")
    for row in rows:
        print(row)
    # Raw values, written by hand from the FIT profile: intensity 2 warmup, 0 active, 4 recovery,
    # 3 cooldown; duration_type 0 time (ms), 6 repeat_until_steps_cmplt; target_type 2 open.
    # Step 1 keeps its speed target (type 0); step 3 is the first copy of the cool-down, so its
    # intensity and open target stay as the cool-down wrote them.
    assert rows == [
        (0, 2, 0, 300000, 2, 0),
        (1, 0, 0, 600000, 0, 0),
        (2, 4, 0, 120000, 2, 0),
        (3, 3, 6, 1, 2, 3),
        (4, 3, 0, 300000, 2, 0),
    ]
    # fitdecode resolves the repeat step's subfields from duration_type 6.
    repeat = steps[3]
    assert repeat.get_value("duration_type") == "repeat_until_steps_cmplt"
    assert repeat.get_value("duration_step") == 1
    assert repeat.get_value("repeat_steps") == 3
    assert steps[2].get_value("intensity") == "recovery"
    assert steps[2].get_value("target_type") == "open"
    workout = next(m for m in messages if m.global_mesg_num == WORKOUT)
    assert workout.get_raw_value(6) == 5
    # The laps are left as written.
    _, source = _decode(_raw("sample_run.fit"))
    laps = [v for g, v in _field_values(messages) if g == LAP]
    assert laps == [v for g, v in _field_values(source) if g == LAP]
    assert len(laps) == 10


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
