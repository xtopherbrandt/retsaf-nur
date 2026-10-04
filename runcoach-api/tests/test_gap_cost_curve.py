"""Minetti's cost curve and the grade domain guard (F013 AC2, the pure half).

The oracle is written out here from the published coefficients (Minetti et
al. 2002, spec/03 section 3.3.2) and **never imported** from the module under
test. Importing ``gap.C`` would make these tests agree with whatever the
module currently says: a coefficient typo would pass. The spot values and the
tolerance follow the F013 reference, section 4.

What is pinned:

- ``g(i) == C(i) / 3.6`` at the six spot values, to 1e-12 absolute.
- ``g`` refuses extrapolation: ``g(+-0.46)``, ``g(nan)`` and ``g(+-inf)`` raise
  ``ValueError``. The nan case also pins the *ordering* of the two checks in
  ``g`` -- ``abs(nan) > 0.45`` is ``False``, so a domain check alone lets nan
  through; the finiteness check has to come first.
- ``clamp_grade`` is the explicit clamp seam. The boundary is inclusive:
  exactly +-0.45 is in range and not clamped. A non-finite grade has no
  clamped value, so it raises.
- Over a dense grid on the measured domain, ``g`` is finite and strictly
  positive (the polynomial goes negative below about -0.9, outside the
  domain).
"""

from __future__ import annotations

import math

import pytest

from runcoach_api.metrics import gap

GRADE_DOMAIN = 0.45  # restated, not imported (independent-oracle rule)
TOLERANCE = 1e-12


def _published_cost(i: float) -> float:
    """Minetti's cost polynomial, J/(kg*m), from the published coefficients."""
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6


SPOT_VALUES = [0.0, 0.10, -0.10, -0.18, 0.45, -0.45]


@pytest.mark.parametrize("i", SPOT_VALUES, ids=[repr(v) for v in SPOT_VALUES])
def test_g_matches_the_published_polynomial_at_the_spot_values(i: float) -> None:
    expected = _published_cost(i) / 3.6
    assert abs(gap.g(i) - expected) < TOLERANCE


def test_g_is_one_on_the_flat() -> None:
    assert gap.g(0.0) == 1.0


def test_g_reference_values_from_the_feature_reference() -> None:
    # The reference's quoted figures, to the printed precision.
    assert abs(gap.g(0.10) - 1.6578) < 1e-4
    assert abs(gap.g(-0.10) - 0.5977) < 1e-4
    assert abs(gap.g(-0.18) - 0.4948) < 1e-4
    assert abs(gap.g(0.45) - 5.3961) < 1e-4
    assert abs(gap.g(-0.45) - 1.1201) < 1e-4


@pytest.mark.parametrize(
    "i",
    [0.46, -0.46, math.nan, math.inf, -math.inf],
    ids=["0.46", "-0.46", "nan", "inf", "-inf"],
)
def test_g_raises_outside_the_measured_domain(i: float) -> None:
    with pytest.raises(ValueError):
        gap.g(i)


def test_g_raises_on_nan_so_the_finiteness_check_comes_first() -> None:
    # abs(nan) > 0.45 is False: a domain check alone would let nan through
    # and return nan. This is the pin for the ordering in g.
    with pytest.raises(ValueError):
        gap.g(float("nan"))


def test_g_accepts_the_domain_boundary_inclusively() -> None:
    assert math.isfinite(gap.g(GRADE_DOMAIN))
    assert math.isfinite(gap.g(-GRADE_DOMAIN))


@pytest.mark.parametrize(
    ("i", "expected"),
    [
        (0.6, (0.45, True)),
        (-0.6, (-0.45, True)),
        (0.1, (0.1, False)),
        (0.45, (0.45, False)),
        (-0.45, (-0.45, False)),
        (0.0, (0.0, False)),
    ],
    ids=["above", "below", "inside", "upper-edge", "lower-edge", "flat"],
)
def test_clamp_grade_returns_the_clamped_value_and_whether_it_clamped(
    i: float, expected: tuple[float, bool]
) -> None:
    assert gap.clamp_grade(i) == expected


def test_clamp_grade_clamps_just_past_the_boundary() -> None:
    assert gap.clamp_grade(0.4500001) == (GRADE_DOMAIN, True)
    assert gap.clamp_grade(-0.4500001) == (-GRADE_DOMAIN, True)


@pytest.mark.parametrize("i", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "-inf"])
def test_clamp_grade_raises_on_a_non_finite_grade(i: float) -> None:
    with pytest.raises(ValueError):
        gap.clamp_grade(i)


def test_clamped_grades_are_accepted_by_g() -> None:
    for raw in (0.6, -0.6, 4.13, -4.13):
        clamped, was_clamped = gap.clamp_grade(raw)
        assert was_clamped
        assert math.isfinite(gap.g(clamped))


def test_g_is_finite_and_positive_over_a_dense_grid_on_the_domain() -> None:
    steps = 900  # 901 points, 0.001 apart
    for k in range(steps + 1):
        i = -GRADE_DOMAIN + 2 * GRADE_DOMAIN * k / steps
        value = gap.g(i)
        assert math.isfinite(value), i
        assert value > 0, i


def test_g_minimum_on_the_domain_sits_near_minus_point_eighteen() -> None:
    # The reference: C's minimum is about 1.78 near i = -0.18, so g's is
    # about 0.49. Checks the curve's shape, not just its sign.
    grid = [-GRADE_DOMAIN + 2 * GRADE_DOMAIN * k / 900 for k in range(901)]
    argmin = min(grid, key=gap.g)
    assert abs(argmin + 0.18) < 0.01
    assert abs(gap.g(argmin) * 3.6 - 1.78) < 0.01


def test_grade_domain_constant_is_minettis_measured_range() -> None:
    assert gap.GRADE_DOMAIN == GRADE_DOMAIN


def test_the_module_is_pure() -> None:
    # The module must import neither config nor db nor fastapi. Read the real
    # import statements from the AST, not substrings: the docstring itself
    # names what it does not import.
    import ast

    with open(gap.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    for forbidden in ("fastapi", "runcoach_api.config", "runcoach_api.db", "config", "db"):
        assert not any(name == forbidden or name.startswith(forbidden + ".") for name in imported), imported
    assert imported == {"__future__.annotations", "__future__", "math"}
