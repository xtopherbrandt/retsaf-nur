"""Regression (code-review Fix 4): ``mapping._build_summary``'s speed
fallback drops legitimate ``0.0`` values.

``val("enhanced_avg_speed") or val("avg_speed")`` treats a genuinely-
zero speed (e.g. a very short or stationary-trainer session) as falsy,
silently replacing it with the fallback field's value instead of
keeping the true ``0.0`` -- every other fallback in ``mapping.py``
(e.g. altitude in ``_build_record``) correctly uses an explicit
``if x is None`` check for exactly this reason.

Exercised directly against ``_build_summary`` with a lightweight fake
FIT ``session`` message object exposing the same ``get_value()``
surface it actually reads (no real fixture is known to have a
genuinely-zero ``enhanced_avg_speed``/``enhanced_max_speed``).
"""

from __future__ import annotations

from runcoach_api.ingestion import mapping


class _FakeSessionMsg:
    def __init__(self, values: dict) -> None:
        self._values = values

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)


def test_zero_enhanced_avg_speed_is_not_replaced_by_fallback_field() -> None:
    msg = _FakeSessionMsg({"enhanced_avg_speed": 0.0, "avg_speed": 3.1})

    summary = mapping._build_summary(msg)

    assert summary["avg_speed"] == 0.0


def test_zero_enhanced_max_speed_is_not_replaced_by_fallback_field() -> None:
    msg = _FakeSessionMsg({"enhanced_max_speed": 0.0, "max_speed": 4.4})

    summary = mapping._build_summary(msg)

    assert summary["max_speed"] == 0.0


def test_absent_enhanced_avg_speed_still_falls_back_to_avg_speed() -> None:
    msg = _FakeSessionMsg({"avg_speed": 3.1})

    summary = mapping._build_summary(msg)

    assert summary["avg_speed"] == 3.1


def test_nonzero_enhanced_avg_speed_wins_over_fallback_field() -> None:
    msg = _FakeSessionMsg({"enhanced_avg_speed": 2.5, "avg_speed": 3.1})

    summary = mapping._build_summary(msg)

    assert summary["avg_speed"] == 2.5
