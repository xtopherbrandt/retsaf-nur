"""F021 AC2 and AC4's source rule: ``hr_history.borrow`` picks the HR setting in effect on a run's date.

The contract table ``_CONTRACT`` below was written from F021's AC2, AC4 and Negative Class text
(spec/features/F021-dated-hr-settings.md) and the seam rows the feature lists, BEFORE
``hr_history.py`` existed; it was not derived from the implementation. Each row builds its own
files, entries and F016 anchor, so no row depends on another's store.

The rule under test (F021 AC2): among stored files carrying the field whose ``start_time`` is at or
before the run's (max and threshold: ``running`` sessions only; equal start times by
``upload_order``) and dated entries whose ``effective_from`` is on or before the run's local date
in ``athlete_timezone``, the one with the latest local date wins; a file and an entry on the same
local date: the file wins. With none, F016's effective anchor, ``out_of_period`` true only when
that anchor is a file dated after the run. AC4: the anchor's own file is reported as ``anchor``.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

import pytest

from runcoach_api import db, hr_history, profile

UTC = ZoneInfo("UTC")
LA = ZoneInfo("America/Los_Angeles")
AKL = ZoneInfo("Pacific/Auckland")

BLOCK_KEYS = {
    "value",
    "source",
    "session_id",
    "anchor_version",
    "anchor_unavailable",
    "entry_id",
    "effective_from",
    "out_of_period",
}


# --- builders ---------------------------------------------------------------------------------


def _file(session_id: str, start_time: str, value: Any, *, upload_order: int = 1, sport: str = "running"):
    return (session_id, start_time, upload_order, sport, value)


def _entry(entry_id: int, effective_from: str, value: int):
    return (entry_id, effective_from, value)


def _resolved(field: str, files, patch_value: Any = None) -> profile.FieldValue:
    """F016's resolution of ``field`` over the same stored files, plus an undated ``PATCH /me`` entry."""
    sessions = [
        {"session_id": s, "start_time": t, "upload_order": u, "sport": sp, field: v}
        for (s, t, u, sp, v) in files
    ]
    entries = [] if patch_value is None else [
        {"entry_id": 1, "field": field, "value": patch_value, "set_at": "2026-01-01T00:00:00+00:00"}
    ]
    return profile.resolve(sessions, entries)[field]


def _anchor(resolved: profile.FieldValue, *, version: int = 1, reason: str | None = None) -> profile.Anchor:
    if reason is None and resolved.source is None:
        reason = profile.MISSING
    if reason is not None:
        return profile.Anchor(
            value=None, source=None, session_id=None, recorded_at=None, version=None,
            entered_value=resolved.entered_value, reason=reason,
        )
    return profile.Anchor(
        value=resolved.value, source=resolved.source, session_id=resolved.session_id,
        recorded_at=resolved.recorded_at, version=version, entered_value=resolved.entered_value, reason=None,
    )


def _borrow(field, run_start, files, entries=(), *, zone=UTC, patch_value=None, anchor_reason=None):
    resolved = _resolved(field, files, patch_value)
    anchor = _anchor(resolved, reason=anchor_reason)
    return hr_history.borrow(field, run_start, zone, list(files), list(entries), resolved, anchor), anchor


def _anchor_block(anchor: profile.Anchor, *, out_of_period: bool) -> dict:
    """The block F017 saves today (``db._from_anchor``), widened by the three new keys at their defaults."""
    return {**db._from_anchor(anchor), "entry_id": None, "effective_from": None, "out_of_period": out_of_period}


def _history(value, entry_id, effective_from) -> dict:
    return {
        "value": value, "source": "history", "session_id": None, "anchor_version": None,
        "anchor_unavailable": None, "entry_id": entry_id, "effective_from": effective_from,
        "out_of_period": False,
    }


def _dated_file(value, session_id) -> dict:
    return {
        "value": value, "source": "session_file_dated", "session_id": session_id, "anchor_version": None,
        "anchor_unavailable": None, "entry_id": None, "effective_from": None, "out_of_period": False,
    }


MAX = "max_hr_bpm"
REST = "resting_hr_bpm"
THR = "threshold_hr_bpm"

F2023 = _file("s2023", "2023-03-01T08:00:00+00:00", 188)
F2025 = _file("s2025", "2025-02-01T08:00:00+00:00", 186)


# --- F021 AC2 seam rows -----------------------------------------------------------------------


def test_dated_entries_give_195_in_2018_and_191_in_2022():
    entries = [_entry(1, "2016-01-01", 195), _entry(2, "2021-01-01", 191)]
    block_2018, _ = _borrow(MAX, "2018-06-01T07:00:00+00:00", [], entries)
    block_2022, _ = _borrow(MAX, "2022-06-01T07:00:00+00:00", [], entries)
    assert block_2018 == _history(195, 1, "2016-01-01")
    assert block_2022 == _history(191, 2, "2021-01-01")


def test_no_earlier_source_falls_back_to_the_anchor_out_of_period():
    block, anchor = _borrow(MAX, "2018-06-01T07:00:00+00:00", [F2023])
    assert anchor.value == 188 and anchor.source == "fit"
    assert block == _anchor_block(anchor, out_of_period=True)
    assert block["value"] == 188 and block["source"] == "anchor"

    with_entry, _ = _borrow(MAX, "2018-06-01T07:00:00+00:00", [F2023], [_entry(1, "2016-01-01", 195)])
    assert with_entry == _history(195, 1, "2016-01-01")


def test_the_latest_date_wins_entry_over_an_earlier_file():
    block, _ = _borrow(MAX, "2024-04-01T07:00:00+00:00", [F2023], [_entry(4, "2023-06-01", 191)])
    assert block == _history(191, 4, "2023-06-01")


def test_an_undated_patch_entry_is_the_anchor_not_out_of_period():
    block, anchor = _borrow(MAX, "2018-06-01T07:00:00+00:00", [], patch_value=190)
    assert anchor.source == "entered"
    assert block == _anchor_block(anchor, out_of_period=False)
    assert block["value"] == 190 and block["source"] == "anchor"


def test_a_file_and_an_entry_on_one_local_date_the_file_wins():
    entries = [_entry(7, "2023-03-01", 191)]
    # The 2023 file is not the anchor's (a 2025 file is): the file wins as a dated file.
    block, _ = _borrow(MAX, "2024-04-01T07:00:00+00:00", [F2023, F2025], entries)
    assert block == _dated_file(188, "s2023")
    # The 2023 file is the anchor's own file: it wins and is reported as the anchor.
    own, anchor = _borrow(MAX, "2024-04-01T07:00:00+00:00", [F2023], entries)
    assert own == _anchor_block(anchor, out_of_period=False)
    assert own["value"] == 188


# --- F021 AC4 ---------------------------------------------------------------------------------


def test_the_anchors_own_file_is_reported_as_anchor():
    block, anchor = _borrow(MAX, "2024-04-01T07:00:00+00:00", [F2023])
    assert anchor.session_id == "s2023"
    assert set(block) == BLOCK_KEYS
    # Exactly the block F017 saves today, plus the new keys at their defaults (F021 AC3).
    assert {k: block[k] for k in db._from_anchor(anchor)} == db._from_anchor(anchor)
    assert block == _anchor_block(anchor, out_of_period=False)
    assert block["anchor_version"] == 1


# --- the analyst's probe rows -----------------------------------------------------------------


def test_a_file_later_the_same_local_day_than_the_run_is_not_a_candidate():
    later_same_day = _file("s_pm", "2024-05-01T18:00:00+00:00", 190, upload_order=9)
    block, anchor = _borrow(MAX, "2024-05-01T07:00:00+00:00", [F2023, later_same_day])
    assert anchor.session_id == "s_pm"
    assert block == _dated_file(188, "s2023")


def test_a_file_starting_at_the_runs_own_start_time_is_a_candidate():
    run = "2024-05-01T07:00:00+00:00"
    same_start = _file("s_same", run, 183, upload_order=4)
    block, _ = _borrow(MAX, run, [F2023, same_start, F2025])
    assert block == _dated_file(183, "s_same")


@pytest.mark.parametrize("hi_first", [True, False], ids=["higher-listed-first", "higher-listed-last"])
def test_equal_start_times_go_to_the_higher_upload_order(hi_first):
    t = "2023-09-01T08:00:00+00:00"
    pair = [_file("s_hi", t, 187, upload_order=5), _file("s_lo", t, 186, upload_order=3)]
    files = [*(pair if hi_first else pair[::-1]), F2025]
    block, _ = _borrow(MAX, "2024-04-01T07:00:00+00:00", files)
    assert block == _dated_file(187, "s_hi")


@pytest.mark.parametrize("field", [MAX, THR])
def test_a_non_running_file_is_ignored_for_max_and_threshold(field):
    walk = _file("s_walk", "2023-06-01T08:00:00+00:00", 200, sport="walking")
    run_file = _file("s_run", "2023-01-01T08:00:00+00:00", 170)
    block, _ = _borrow(field, "2024-04-01T07:00:00+00:00", [run_file, walk, F2025])
    assert block == _dated_file(170, "s_run")


def test_a_non_running_file_is_used_for_resting():
    walk = _file("s_walk", "2023-06-01T08:00:00+00:00", 50, sport="walking")
    run_file = _file("s_run", "2023-01-01T08:00:00+00:00", 48)
    later = _file("s2025", "2025-02-01T08:00:00+00:00", 46)
    block, _ = _borrow(REST, "2024-04-01T07:00:00+00:00", [run_file, walk, later])
    assert block == _dated_file(50, "s_walk")


def test_a_file_without_the_field_is_not_a_candidate():
    empty = _file("s_none", "2024-01-01T08:00:00+00:00", None)
    block, _ = _borrow(MAX, "2024-04-01T07:00:00+00:00", [F2023, empty, F2025])
    assert block == _dated_file(188, "s2023")


def test_an_entry_dated_the_runs_own_date_applies():
    entries = [_entry(1, "2024-01-01", 180), _entry(2, "2024-04-01", 185)]
    block, _ = _borrow(MAX, "2024-04-01T07:00:00+00:00", [], entries)
    assert block == _history(185, 2, "2024-04-01")


def test_an_entry_dated_after_the_run_does_not_apply():
    entries = [_entry(1, "2024-01-01", 180), _entry(2, "2024-04-02", 185)]
    block, _ = _borrow(MAX, "2024-04-01T23:00:00+00:00", [], entries)
    assert block == _history(180, 1, "2024-01-01")


_ENTRIES_JUNE = [_entry(1, "2024-01-01", 180), _entry(2, "2024-06-02", 185)]


@pytest.mark.parametrize(
    ("zone", "run_start", "expected"),
    [
        # 23:30 PDT on 2024-06-01 is 06:30 UTC on 2024-06-02: the run's date is 2024-06-01.
        (LA, "2024-06-02T06:30:00+00:00", _history(180, 1, "2024-01-01")),
        (UTC, "2024-06-02T06:30:00+00:00", _history(185, 2, "2024-06-02")),
        # 23:30 NZST on 2024-06-01 is 11:30 UTC the same day: the run's date is 2024-06-01.
        (AKL, "2024-06-01T11:30:00+00:00", _history(180, 1, "2024-01-01")),
        # 00:30 NZST on 2024-06-02 is 12:30 UTC on 2024-06-01: the run's date is 2024-06-02.
        (AKL, "2024-06-01T12:30:00+00:00", _history(185, 2, "2024-06-02")),
        (UTC, "2024-06-01T12:30:00+00:00", _history(180, 1, "2024-01-01")),
    ],
    ids=["la-2330-local", "utc-same-instant", "akl-2330-local", "akl-0030-next-local-day", "utc-0030-akl-instant"],
)
def test_the_runs_date_is_its_local_date_in_the_zone(zone, run_start, expected):
    block, _ = _borrow(MAX, run_start, [], _ENTRIES_JUNE, zone=zone)
    assert block == expected


@pytest.mark.parametrize(
    ("zone", "expected"),
    [
        # In Auckland the file starts 01:00 on 2024-06-02, the entry's date: the file wins.
        (AKL, _dated_file(184, "s_akl")),
        # In UTC the file is dated 2024-06-01, before the entry: the entry wins.
        (UTC, _history(185, 2, "2024-06-02")),
    ],
    ids=["akl-same-local-date", "utc-file-a-day-earlier"],
)
def test_a_files_date_is_its_local_date_in_the_zone(zone, expected):
    akl_file = _file("s_akl", "2024-06-01T13:00:00+00:00", 184)
    block, _ = _borrow(MAX, "2024-06-10T07:00:00+00:00", [akl_file, F2025], _ENTRIES_JUNE, zone=zone)
    assert block == expected


# --- an anchor F016 marks order_conflict (its session_id is None) -----------------------------


def test_order_conflict_anchor_on_its_own_file_keeps_the_empty_block():
    block, anchor = _borrow(MAX, "2024-04-01T07:00:00+00:00", [F2023], anchor_reason=profile.ORDER_CONFLICT)
    assert anchor.session_id is None
    assert block == _anchor_block(anchor, out_of_period=False)
    assert block["value"] is None and block["anchor_unavailable"] == "order_conflict"


def test_order_conflict_anchor_with_no_earlier_source_is_not_out_of_period():
    block, anchor = _borrow(MAX, "2018-06-01T07:00:00+00:00", [F2023], anchor_reason=profile.ORDER_CONFLICT)
    assert block == _anchor_block(anchor, out_of_period=False)
    assert block["anchor_unavailable"] == "order_conflict"


def test_order_conflict_anchor_still_borrows_an_earlier_entry():
    entries = [_entry(3, "2016-01-01", 195)]
    block, _ = _borrow(MAX, "2018-06-01T07:00:00+00:00", [F2023], entries, anchor_reason=profile.ORDER_CONFLICT)
    assert block == _history(195, 3, "2016-01-01")


def test_a_missing_anchor_with_nothing_borrowed_is_the_empty_block():
    block, anchor = _borrow(MAX, "2018-06-01T07:00:00+00:00", [])
    assert anchor.reason == "missing"
    assert block == _anchor_block(anchor, out_of_period=False)
    assert block["anchor_unavailable"] == "missing"


def test_a_field_other_than_the_three_hr_settings_is_refused():
    resolved = _resolved("sex", [])
    with pytest.raises(ValueError, match="sex"):
        hr_history.borrow("sex", "2024-04-01T07:00:00+00:00", UTC, [], [], resolved, _anchor(resolved))
