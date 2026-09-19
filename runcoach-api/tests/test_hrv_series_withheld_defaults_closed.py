"""T134 -- ``withheld`` fails closed, or is documented at the field for why not.

**Re-pointed by T151 (F006):** the field moved from ``HrvSeries`` to ``HrvDataset`` when the
series became N per-tier datasets, and the pin moved with it. ``hrv_trend.py``'s
``build_series`` is ``HrvDataset``'s only production construction site (``HrvDataset(``
appears there once), and it always passes the computed ``withheld`` value -- so the dataclass
field's own default was, before T134, unreached in practice but *permissive* in direction: a
future construction path that omitted the argument would silently manufacture a dataset
eligible for ``hrv_normal``, the one direction ``research/00`` Section 1.7 forbids on weak
evidence. ``band`` (``None``) and ``established`` (``False``) both default toward
``hrv_unavailable`` in this same module; ``withheld`` was the odd one out.

Kept out of the five HRV suites (test_hrv_trend_band.py/_endpoint.py/_points.py/_reset.py/
_series.py) so nobody's SCOPED_SUITE_COLLECTED pin has to move for a test about a field
default nobody's construction path currently omits.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from runcoach_api.metrics.hrv_trend import (
    VERDICT_NORMAL,
    VERDICT_UNAVAILABLE,
    HrvDataset,
    build_series,
    judge,
    selected_view,
)

AUCKLAND = ZoneInfo("Pacific/Auckland")
D = date(2026, 9, 8)
STRAP = "chest_strap_raw"
VALUE = 40.0


def _local(day: date, hh: int = 6) -> str:
    return datetime(day.year, day.month, day.day, hh, 0, tzinfo=AUCKLAND).astimezone(UTC).isoformat()


def _row(day: date, value: float = VALUE) -> dict:
    return {
        "session_id": f"s-{day.isoformat()}",
        "start_time": _local(day),
        "resting_rmssd_ms": value,
        "hrv_source_tier": STRAP,
    }


def _settled_series() -> HrvDataset:
    """An established, in-band dataset -- ``judge`` reads it ``hrv_normal``
    today, and must go on doing so: it is the regression half of this
    module's proof that the field's new default moves no verdict for the
    one construction path that exists. The one dataset of a one-tier
    series, taken through F006's selection (T155) so it is the very object
    the route hands ``judge``."""
    days = [D - timedelta(days=7) - timedelta(days=i) for i in range(20)][::-1]
    days += [D - timedelta(days=i) for i in range(7)][::-1]
    rows = [_row(day) for day in days]
    selected = selected_view(build_series(rows, AUCKLAND, D)).selected
    assert selected is not None and selected.tier == STRAP
    return selected


def test_build_series_still_reads_normal_on_a_settled_athlete() -> None:
    """Sanity fixture check, and half the regression proof for deliverable 3:
    ``build_series`` explicitly computes ``withheld=False`` here, so this
    must read ``hrv_normal`` before and after the field's default changes --
    the acceptance probe's third clause re-runs this claim at the suite
    level (test_hrv_trend_band.py, test_hrv_trend_reset.py); this is the
    same claim, local to this module's own fixture."""
    series = _settled_series()
    assert series.withheld is False
    assert judge(series).verdict == VERDICT_NORMAL


def test_a_series_constructed_without_withheld_reads_unavailable() -> None:
    """The deliverable itself, and its own mutation probe in one: build a
    real, established, in-band dataset through ``build_series`` (so every
    other field is exactly what production would compute), then construct a
    **second** ``HrvDataset`` from its fields with ``withheld`` *omitted*
    entirely -- not copied, not defaulted from the first -- so the
    dataclass's own default is what decides the verdict.

    Before T134 (``withheld: bool = False``) this read ``hrv_normal``,
    identical to the fully-specified series, because the permissive default
    happened to match the computed value on this fixture -- the silent-
    failure shape the task exists to close. After T134
    (``withheld: bool = True``) it reads ``hrv_unavailable``: the one
    dataclass field whose default used to point the wrong way now points
    toward the safe verdict when nobody says otherwise.
    """
    settled = _settled_series()
    assert judge(settled).verdict == VERDICT_NORMAL  # the fully-specified control

    fields_but_withheld = {
        field.name: getattr(settled, field.name)
        for field in dataclasses.fields(HrvDataset)
        if field.name != "withheld"
    }
    unset = HrvDataset(**fields_but_withheld)

    assert judge(unset).verdict == VERDICT_UNAVAILABLE, (
        "HrvDataset.withheld must default closed: a construction site that omits it "
        "should read hrv_unavailable, not silently inherit hrv_normal eligibility"
    )


def test_explicitly_unwithheld_is_unaffected_by_the_new_default() -> None:
    """The default only bites when the argument is omitted. A construction
    site that explicitly passes ``withheld=False`` -- exactly what
    ``build_series`` always does -- must be untouched by this change,
    which is the other half of "moves no verdict"."""
    settled = _settled_series()
    explicit = dataclasses.replace(settled, withheld=False)
    assert judge(explicit).verdict == VERDICT_NORMAL
    assert judge(explicit).verdict == judge(settled).verdict
