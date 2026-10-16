"""The one finite-number screen (``metrics/finite.py``) and the threshold band rule made public.

``finite`` is the screen the session time base (``metrics.segments``), the race-tag matching rule
(``race_tags``) and the threshold-pace qualifier (``metrics.threshold_pace``) all read numbers
through: a real, finite number becomes a float; a bool, a string, ``None``, a non-finite number or
an int beyond float range is absent. ``hrv_classification._numeric`` is deliberately not one of its
callers (``segments``' docstring says why).
"""

from __future__ import annotations

import ast
import importlib.util
import math
import sys
from pathlib import Path

import pytest

from runcoach_api import db, race_tags
from runcoach_api.metrics import finite, segments, threshold_pace


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0.0),
        (-3, -3.0),
        (2.5, 2.5),
        (10**300, 1e300),
        (True, None),
        (False, None),
        ("1.0", None),
        (None, None),
        (math.nan, None),
        (math.inf, None),
        (-math.inf, None),
        (10**400, None),
        (-(10**400), None),
    ],
)
def test_finite_reads_real_finite_numbers_only(value: object, expected: float | None) -> None:
    result = finite.finite(value)
    assert result == expected
    if expected is not None:
        assert type(result) is float


def test_screen_reads_an_int_beyond_float_range_as_absent() -> None:
    """``float(10**400)`` raises ``OverflowError``; the screen reads the value as absent instead."""
    (record,) = segments.screen([{"t": 0, "distance": 10**400, "speed": 10**400, "power": 250}])
    assert record.distance is None
    assert record.speed is None
    assert record.power == 250.0
    assert segments.screen([{"t": 10**400, "distance": 5.0}]) == []
    (hr,) = segments.screen([{"t": 1, "heart_rate": 10**400}])
    assert hr.heart_rate is None


def test_the_three_callers_share_one_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Perturb ``finite.finite`` and every caller follows: none keeps a screen of its own."""
    item = {"distance_m": "a", "duration_s": "b"}
    summary = {"distance_m": "c", "duration_s": "d"}
    row = {"t": "t", "distance": "x"}
    timed = {"timer_time_s": "nope", "duration_s": None}

    # Before: strings are absent everywhere.
    assert segments.screen([row]) == []
    assert race_tags.agrees(item, summary) is False
    assert threshold_pace.duration_of(timed) is None
    assert threshold_pace._log_speed("300") is None

    calls: list[object] = []

    def perturbed(value: object) -> float:
        calls.append(value)
        return 1500.0

    monkeypatch.setattr(finite, "finite", perturbed)

    (record,) = segments.screen([row])
    assert (record.t, record.distance) == (1500.0, 1500.0)
    assert race_tags.agrees(item, summary) is True
    assert threshold_pace.duration_of(timed) == 1500.0
    assert threshold_pace._log_speed("300") == pytest.approx(math.log(1000 / 1500.0))
    assert {"t", "x", "a", "b", "c", "d", "nope", "300"} <= set(calls)


def test_the_finite_module_imports_no_db_config_or_fastapi() -> None:
    tree = ast.parse(Path(finite.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    assert not {n for n in names if n.split(".")[0] in {"fastapi", "sqlite3", "runcoach_api"}}, names


def _load_threshold_pace(source: str, name: str) -> None:
    spec = importlib.util.spec_from_loader(name, loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        exec(compile(source, threshold_pace.__file__, "exec"), module.__dict__)
    finally:
        del sys.modules[name]


def test_z_matches_coverage_at_import() -> None:
    """``Z`` stays the normative literal; the module refuses to load when it drifts from COVERAGE."""
    source = Path(threshold_pace.__file__).read_text(encoding="utf-8")
    literal = "Z = 1.2815515655446004\n"
    assert source.count(literal) == 1
    assert threshold_pace.Z == 1.2815515655446004

    _load_threshold_pace(source, "_threshold_pace_unchanged")  # the literal as shipped loads

    with pytest.raises(AssertionError):
        _load_threshold_pace(source.replace(literal, "Z = 1.2816\n"), "_threshold_pace_mutated_z")
    with pytest.raises(AssertionError):
        _load_threshold_pace(
            source.replace("COVERAGE = 0.80\n", "COVERAGE = 0.90\n"), "_threshold_pace_mutated_coverage"
        )


def test_duration_of_and_in_band_are_public() -> None:
    assert threshold_pace.duration_of({"timer_time_s": 1500, "duration_s": 9999}) == 1500.0
    assert threshold_pace.duration_of({"timer_time_s": None, "duration_s": 2000}) == 2000.0
    assert threshold_pace.duration_of({"duration_s": 2000}) == 2000.0
    assert threshold_pace.duration_of({"timer_time_s": True, "duration_s": 2000}) is None
    assert threshold_pace.duration_of({"timer_time_s": 10**400}) is None

    assert threshold_pace.in_band(1200) is True
    assert threshold_pace.in_band(1200.0) is True
    assert threshold_pace.in_band(3600.0) is True
    assert threshold_pace.in_band(1199.999) is False
    assert threshold_pace.in_band(3600.001) is False
    assert threshold_pace.in_band(None) is False

    # The store's reader applies the same rule, not a copy of it.
    db_source = Path(db.__file__).read_text(encoding="utf-8")
    assert "_threshold_pace.duration_of(row)" in db_source
    assert "_threshold_pace.in_band(duration)" in db_source
    assert "_threshold_pace._duration" not in db_source
    assert not hasattr(threshold_pace, "_duration")
