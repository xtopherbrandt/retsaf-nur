"""``tests/support/fit_patch.py`` record-range helpers: blank a field on, or drop, a span of records.

The oracle is ``fitdecode`` re-decoding the patched bytes with ``CrcCheck.RAISE``,
as in ``test_fit_patch.py``; no ``runcoach_api`` code judges a patch. Spans are
seconds after the first record message, ``[start_s, end_s)``.

- **Blank.** ``heart_rate`` (field 3) blanked on ``[600, 1200)`` of ``sample_run``:
  exactly 600 records decode with no heart rate, they are the records whose
  timestamps fall in the span, every other record field is unchanged and every
  other message is byte-identical.
- **Drop.** ``[1500, 1560)`` dropped: 60 fewer records, the rest identical in
  order, the session's ``total_timer_time`` and every lap untouched.
- **Refusals.** A field the record definition does not carry, a span no record
  falls in, and an empty span raise ``FitPatchError``.
- **The fixture on disk** is compared by SHA-256 after every test.

Patched bytes stay in memory. Nothing here writes into ``tests/fixtures/``.
"""

from __future__ import annotations

import collections
import hashlib
import importlib.util
import io
import struct
from pathlib import Path

import fitdecode
import pytest
from fitdecode.utils import compute_crc

FIXTURES = Path(__file__).parent / "fixtures"

_SPEC = importlib.util.spec_from_file_location(
    "fit_patch", Path(__file__).parent / "support" / "fit_patch.py"
)
assert _SPEC is not None and _SPEC.loader is not None
fit_patch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fit_patch)

RECORD, LAP, SESSION = 20, 19, 18
HEART_RATE, TIMESTAMP = 3, 253
SAMPLE_RUN = FIXTURES / "sample_run.fit"
SAMPLE_RUN_SHA256 = hashlib.sha256(SAMPLE_RUN.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def fixture_unchanged_on_disk():
    yield
    assert hashlib.sha256(SAMPLE_RUN.read_bytes()).hexdigest() == SAMPLE_RUN_SHA256


def _decode(data: bytes) -> list:
    """Every data message, both CRCs checked; raises on any mismatch."""
    messages = []
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage):
                messages.append(frame)
    return messages


def _raw_fields(message) -> tuple[tuple[int, object], ...]:
    return tuple((f.def_num, f.raw_value) for f in message.fields)


def _by_number(messages: list, global_num: int) -> list:
    return [m for m in messages if m.global_mesg_num == global_num]


def _others(messages: list, global_num: int) -> list[tuple[int, tuple]]:
    return [(m.global_mesg_num, _raw_fields(m)) for m in messages if m.global_mesg_num != global_num]


def _seconds_after_first(records: list) -> list[float]:
    first = records[0].get_value("timestamp")
    return [(r.get_value("timestamp") - first).total_seconds() for r in records]


def _assert_header_rewritten(data: bytes) -> None:
    assert data[0] == 14
    assert data[0] + struct.unpack_from("<I", data, 4)[0] + 2 == len(data)
    assert struct.unpack_from("<H", data, 12)[0] == compute_crc(data, start=0, end=12)


# ---------------------------------------------------------------------------
# blank
# ---------------------------------------------------------------------------


def test_blank_record_field_blanks_ten_minutes_of_heart_rate() -> None:
    raw = SAMPLE_RUN.read_bytes()
    patched = fit_patch.blank_record_field(raw, HEART_RATE, 600, 1200)
    _assert_header_rewritten(patched)

    before, after = _decode(raw), _decode(patched)
    assert collections.Counter(m.global_mesg_num for m in after) == collections.Counter(
        m.global_mesg_num for m in before
    )
    assert _others(after, RECORD) == _others(before, RECORD)

    old, new = _by_number(before, RECORD), _by_number(after, RECORD)
    assert len(new) == len(old) == 2817
    seconds = _seconds_after_first(old)
    in_span = [600 <= s < 1200 for s in seconds]
    blanked = [r.get_value("heart_rate") is None for r in new]
    print(f"records {len(new)}, blanked {sum(blanked)}, in span {sum(in_span)}")
    assert sum(blanked) == 600
    assert blanked == in_span
    assert all(r.get_value("heart_rate") is not None for r in old)
    for o, n, hit in zip(old, new, in_span, strict=True):
        if hit:
            # fitdecode reads the invalid 0xFF as None; the byte itself is checked below.
            expected = tuple((k, None if k == HEART_RATE else v) for k, v in _raw_fields(o))
            assert _raw_fields(n) == expected
        else:
            assert _raw_fields(n) == _raw_fields(o)
    walked = [r for r in fit_patch.walk(patched) if not r.is_definition and r.definition.global_num == RECORD]
    assert len(walked) == len(new)
    hr_bytes = []
    for r in walked:
        offset = r.offset + 1
        for field in r.definition.fields:
            if field.num == HEART_RATE:
                hr_bytes.append(patched[offset])
                break
            offset += field.size
    assert [b == 0xFF for b in hr_bytes] == in_span


def test_blank_record_field_refuses_a_field_the_record_does_not_carry() -> None:
    with pytest.raises(fit_patch.FitPatchError, match="field 99"):
        fit_patch.blank_record_field(SAMPLE_RUN.read_bytes(), 99, 600, 1200)


@pytest.mark.parametrize(
    "span", [(1_000_000, 1_000_060), (600, 600), (700, 600)], ids=["after_end", "empty", "reversed"]
)
def test_blank_record_field_refuses_a_span_with_no_record(span: tuple[int, int]) -> None:
    with pytest.raises(fit_patch.FitPatchError, match="no record"):
        fit_patch.blank_record_field(SAMPLE_RUN.read_bytes(), HEART_RATE, *span)


# ---------------------------------------------------------------------------
# drop
# ---------------------------------------------------------------------------


def test_drop_records_removes_sixty_seconds_and_keeps_total_timer_time() -> None:
    raw = SAMPLE_RUN.read_bytes()
    patched = fit_patch.drop_records(raw, 1500, 1560)
    _assert_header_rewritten(patched)

    before, after = _decode(raw), _decode(patched)
    assert _others(after, RECORD) == _others(before, RECORD)
    assert len(_by_number(after, LAP)) == len(_by_number(before, LAP)) >= 1

    old, new = _by_number(before, RECORD), _by_number(after, RECORD)
    seconds = _seconds_after_first(old)
    kept = [_raw_fields(o) for o, s in zip(old, seconds, strict=True) if not 1500 <= s < 1560]
    print(f"records before {len(old)}, after {len(new)}, dropped {len(old) - len(new)}")
    assert len(old) - len(new) == 60
    assert [_raw_fields(n) for n in new] == kept
    new_seconds = _seconds_after_first(new)
    assert not any(1500 <= s < 1560 for s in new_seconds)

    (old_session,), (new_session,) = _by_number(before, SESSION), _by_number(after, SESSION)
    timer = new_session.get_value("total_timer_time")
    print(f"session total_timer_time before {old_session.get_value('total_timer_time')}, after {timer}")
    assert timer == old_session.get_value("total_timer_time") == pytest.approx(2816.667)
    assert _raw_fields(new_session) == _raw_fields(old_session)


def test_drop_records_refuses_a_span_with_no_record() -> None:
    with pytest.raises(fit_patch.FitPatchError, match="no record"):
        fit_patch.drop_records(SAMPLE_RUN.read_bytes(), 1_000_000, 1_000_060)
