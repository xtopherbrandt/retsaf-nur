"""The pure half of F017: ``metrics.session_load.compute_session_load`` against an oracle outside the code.

F017 AC1 (the numbers), AC4 (both coefficient pairs), AC6's gates 1-3 and the
``driver``-null rule, AC2's no-clamp rule on ``hr_time_fraction``.

**The oracle** (``tests/support/trimp_oracle.py``) decodes the fixture with
``fitdecode`` and applies the F017 reference's time basis and formulas; it
imports nothing from ``runcoach_api``, and
``test_the_oracle_does_not_import_runcoach_api`` pins that by reading
``sys.modules`` after a fresh import. The module is driven on inputs built
here from the same file through the ingestion mapping and
``session_features`` (``segment_rows`` and ``compute_session_features``), the
way the upload path builds them, with every HR setting from the session's own
file. The two paths share the file bytes and nothing else. The pinned
expected values (82.86 / 174.97 / 47.36 and the rest) are the reference's
worked values, computed at discuss before the module existed; the test
asserts the module against the oracle and both against the pin, and prints
the three side by side so the probe's ``-rA`` output shows the figures.

Only fixtures with run proof ``yes`` in ``fixtures/README.md`` are cited as
runs (``.claude/rules/learnings/cite-only-real-activity-fixtures-as-proof.md``).

**Synthetic rows** (gates, equality rows, the fraction above 1) are hand-built
records at 1 Hz with a constant HR, so each row isolates one rule: a cycling
session, a ``health_snapshot``-tagged running session, a run whose records
carry no HR, a run with no resting value, and runs whose average HR sits
exactly on a bound (``r = 0`` and ``r = 1`` are computed, never refused).
"""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
import math
import sys
from pathlib import Path

import pytest
from runcoach_api.ingestion import fit_parser, mapping, profile_values, quality_gates
from runcoach_api.metrics import session_load
from runcoach_api.metrics.session_load import compute_session_load

FIXTURES = Path(__file__).parent / "fixtures"
ORACLE_PATH = Path(__file__).parent / "support" / "trimp_oracle.py"
TOLERANCE = 0.01

# ``tests/`` is not a package (importlib mode), so the oracle is loaded from its path, as fit_patch is.
# It is registered in ``sys.modules`` first: its dataclasses resolve their string annotations there.
_SPEC = importlib.util.spec_from_file_location("trimp_oracle", ORACLE_PATH)
trimp_oracle = importlib.util.module_from_spec(_SPEC)
sys.modules["trimp_oracle"] = trimp_oracle
_SPEC.loader.exec_module(trimp_oracle)  # type: ignore[union-attr]

_INPUTS_SPEC = importlib.util.spec_from_file_location("session_load_inputs", ORACLE_PATH.parent / "session_load_inputs.py")
session_load_inputs = importlib.util.module_from_spec(_INPUTS_SPEC)
sys.modules["session_load_inputs"] = session_load_inputs
_INPUTS_SPEC.loader.exec_module(session_load_inputs)  # type: ignore[union-attr]
_synthetic = session_load_inputs.synthetic
_from_file = session_load_inputs.from_file

# The F017 reference's worked values ("Worked values (oracle for AC1)"): run proof `yes` on both rows.
REFERENCE_VALUES = {
    "sample_run.fit": (82.86, 174.97, 47.36),
    "hilly_run_8k_fr945.fit": (77.35, 174.97, 44.21),
}
FEMALE_SAMPLE_RUN = (93.29, 189.38, 49.26)

HR_FIELDS = session_load_inputs.HR_FIELDS


def _fixture_inputs(name: str, **overrides) -> dict:
    """Inputs for a fixture through the ingestion mapping, every HR setting from its own file."""
    messages = fit_parser.decode((FIXTURES / name).read_bytes())
    canonical, records = mapping.to_canonical(messages)
    for field_name, value in profile_values.extract(messages).items():
        setattr(canonical, field_name, value)
    quality_gates.apply(canonical, records)
    session = {
        "session_id": canonical.session_id,
        "sport": canonical.sport,
        "activity_tag": canonical.activity_tag,
        "hr_source": canonical.hr_source,
        "quality_flags": list(canonical.quality_flags),
        "context": dataclasses.asdict(canonical.context) if canonical.context else {},
        "timer_time_s": (canonical.summary or {}).get("duration_s"),
        **{field_name: getattr(canonical, field_name) for field_name in HR_FIELDS},
    }
    rows = [dataclasses.asdict(r) for r in records]
    return session_load_inputs.inputs_for(session, rows, **overrides)


def _expected_trimp(duration_min: float, r: float, sex: str) -> float:
    k, c = {"male": (0.64, 1.92), "female": (0.86, 1.67)}[sex]
    return duration_min * r * k * math.exp(c * r)


# --- AC1: the oracle and the module on the real runs ------------------------------------------


@pytest.mark.parametrize("name", sorted(REFERENCE_VALUES))
def test_sample_run_oracle_values_match_the_module(name: str) -> None:
    oracle = trimp_oracle.trimp_for_file(FIXTURES / name)
    body = compute_session_load(_fixture_inputs(name))
    served = (
        body["metrics"]["hr_trimp"]["value"],
        body["metrics"]["hr_trimp"]["threshold_hour_reference"],
        body["session_load"]["value"],
    )
    pinned = REFERENCE_VALUES[name]
    print(
        f"{name}: trimp oracle {oracle.trimp:.4f} module {served[0]:.4f} pin {pinned[0]} | "
        f"reference oracle {oracle.threshold_hour_reference:.4f} module {served[1]:.4f} pin {pinned[1]} | "
        f"load oracle {oracle.session_load:.4f} module {served[2]:.4f} pin {pinned[2]} | "
        f"hr_time_s oracle {oracle.hr_time_s} module {body['inputs']['hr_time_s']} | "
        f"avg_hr oracle {oracle.avg_hr_bpm:.4f} module {body['inputs']['avg_hr_bpm']:.4f}"
    )
    for got, want_oracle, want_pin in zip(served, (oracle.trimp, oracle.threshold_hour_reference, oracle.session_load), pinned):
        assert got == pytest.approx(want_oracle, abs=TOLERANCE)
        assert got == pytest.approx(want_pin, abs=TOLERANCE)
    assert body["inputs"]["hr_time_s"] == pytest.approx(oracle.hr_time_s, abs=TOLERANCE)
    assert body["inputs"]["avg_hr_bpm"] == pytest.approx(oracle.avg_hr_bpm, abs=TOLERANCE)
    assert body["metrics"]["hr_trimp"]["coefficients"] == "male"
    assert body["session_load"]["driver"] == "hr_trimp"
    assert body["session_load"]["unavailable"] is None
    assert body["metrics"]["rtss"] == {"value": None, "unavailable": "no_threshold_pace"}
    assert body["metrics"]["srpe"] == {"value": None, "unavailable": "no_rpe"}
    for field_name in HR_FIELDS:
        assert body["inputs"][field_name]["source"] == "session_file"
        assert body["inputs"][field_name]["session_id"] == body["session_id"]
    assert body["inputs"]["timer_time_s"] == pytest.approx(oracle.timer_time_s)


def test_the_time_basis_is_f013s_segments_and_served_values() -> None:
    """``hr_time_s`` sums counted usable segments; ``avg_hr_bpm`` and ``recorded_time_s`` are F013's own."""
    inputs = _fixture_inputs("sample_run.fit")
    body = compute_session_load(inputs)
    usable = [row["dt"] for row in inputs["segment_rows"] if not row["hr_excluded"]]
    assert body["inputs"]["hr_time_s"] == pytest.approx(sum(usable))
    assert body["inputs"]["avg_hr_bpm"] == inputs["features"]["features"]["avg_hr_bpm"]["value"]
    assert body["inputs"]["recorded_time_s"] == inputs["features"]["features"]["duration_s"]["value"]
    assert body["inputs"]["hr_time_fraction"] == pytest.approx(
        body["inputs"]["hr_time_s"] / body["inputs"]["timer_time_s"]
    )
    assert body["inputs"]["hr_source"] == "chest_strap"
    assert body["input_flags"] == inputs["features"]["flags"]


# --- the oracle's independence --------------------------------------------------------------------


def test_the_oracle_does_not_import_runcoach_api() -> None:
    """A fresh import of the oracle, with no ``runcoach_api`` module loaded, loads none."""
    tree = ast.parse(ORACLE_PATH.read_text(encoding="utf-8"))
    imported = [
        name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for name in ([alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""])
    ]
    assert not [name for name in imported if name.split(".")[0] == "runcoach_api"], imported

    hidden = {name: sys.modules.pop(name) for name in list(sys.modules) if name.split(".")[0] == "runcoach_api"}
    try:
        spec = importlib.util.spec_from_file_location("trimp_oracle_fresh_import", ORACLE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules["trimp_oracle_fresh_import"] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        loaded = sorted(name for name in sys.modules if name.split(".")[0] == "runcoach_api")
        assert loaded == [], loaded
        assert module.trimp_for_file(FIXTURES / "sample_run.fit").trimp == pytest.approx(82.86, abs=TOLERANCE)
    finally:
        sys.modules.pop("trimp_oracle_fresh_import", None)
        sys.modules.update(hidden)


# --- AC4: both coefficient pairs -------------------------------------------------------------------


def test_the_female_pair_scales_trimp_and_the_reference_together() -> None:
    inputs = _fixture_inputs("sample_run.fit", sex=_from_file("female", "forced"))
    body = compute_session_load(inputs)
    oracle = trimp_oracle.trimp_for_file(FIXTURES / "sample_run.fit", sex="female")
    served = (
        body["metrics"]["hr_trimp"]["value"],
        body["metrics"]["hr_trimp"]["threshold_hour_reference"],
        body["session_load"]["value"],
    )
    print(f"female sample_run: module {served} oracle {(oracle.trimp, oracle.threshold_hour_reference, oracle.session_load)}")
    for got, want_oracle, want_pin in zip(served, (oracle.trimp, oracle.threshold_hour_reference, oracle.session_load), FEMALE_SAMPLE_RUN):
        assert got == pytest.approx(want_oracle, abs=TOLERANCE)
        assert got == pytest.approx(want_pin, abs=TOLERANCE)
    assert body["metrics"]["hr_trimp"]["coefficients"] == "female"
    assert body["inputs"]["sex"]["value"] == "female"
    assert "sex_defaulted" not in body["flags"]


def test_no_sex_value_takes_the_male_pair_and_flags_sex_defaulted() -> None:
    body = compute_session_load(_synthetic(sex=None))
    assert body["metrics"]["hr_trimp"]["coefficients"] == "male"
    assert body["flags"] == ["sex_defaulted"]
    assert body["metrics"]["hr_trimp"]["value"] == pytest.approx(_expected_trimp(10.0, 100 / 140, "male"))


# --- the equality rows: r = 0 and r = 1 are computed, never refused ------------------------------


def test_average_hr_equal_to_resting_gives_r_zero_and_a_zero_load() -> None:
    body = compute_session_load(_synthetic(heart_rate=50.0, resting=50, max_hr=190))
    assert body["metrics"]["hr_trimp"]["value"] == 0.0
    assert body["metrics"]["hr_trimp"]["unavailable"] is None
    assert body["session_load"] == {"value": 0.0, "unavailable": None, "driver": "hr_trimp"}


def test_average_hr_equal_to_max_gives_r_one_and_the_full_exponent() -> None:
    body = compute_session_load(_synthetic(heart_rate=190.0, resting=50, max_hr=190, seconds=600))
    assert body["metrics"]["hr_trimp"]["value"] == pytest.approx(_expected_trimp(10.0, 1.0, "male"))
    assert body["metrics"]["hr_trimp"]["threshold_hour_reference"] == pytest.approx(_expected_trimp(60.0, 120 / 140, "male"))
    assert body["session_load"]["value"] == pytest.approx(
        _expected_trimp(10.0, 1.0, "male") / _expected_trimp(60.0, 120 / 140, "male") * 100.0
    )
    assert body["session_load"]["driver"] == "hr_trimp"


# --- AC6: gates 1-3 in order, and the driver-null rule -------------------------------------------


def _assert_all_three_unavailable(body: dict, reason: str) -> None:
    assert body["metrics"]["hr_trimp"]["value"] is None
    assert body["metrics"]["hr_trimp"]["unavailable"] == reason
    assert body["metrics"]["hr_trimp"]["threshold_hour_reference"] is None
    assert body["metrics"]["hr_trimp"]["coefficients"] is None
    assert body["metrics"]["rtss"] == {"value": None, "unavailable": reason}
    assert body["metrics"]["srpe"] == {"value": None, "unavailable": reason}
    assert body["session_load"] == {"value": None, "unavailable": reason, "driver": None}


def test_gate_1_a_non_running_sport_is_sport_not_running_for_all_three_metrics() -> None:
    body = compute_session_load(_synthetic(sport="cycling"))
    _assert_all_three_unavailable(body, "sport_not_running")
    assert body["sport"] == "cycling"
    # Under gates 1-2 the inputs are still reported.
    assert body["inputs"]["resting_hr_bpm"]["value"] == 50
    assert body["inputs"]["timer_time_s"] == 600.0


def test_gate_2_a_health_snapshot_tagged_running_session_is_declared_capture() -> None:
    body = compute_session_load(_synthetic(activity_tag="health_snapshot"))
    _assert_all_three_unavailable(body, "declared_capture")
    assert body["inputs"]["hr_time_s"] == 600.0


def test_gate_2_resting_hrv_check_is_declared_capture_too() -> None:
    _assert_all_three_unavailable(compute_session_load(_synthetic(activity_tag="resting_hrv_check")), "declared_capture")


def test_gate_1_wins_over_gate_2_on_a_declared_non_running_capture() -> None:
    body = compute_session_load(_synthetic(sport="other", activity_tag="health_snapshot"))
    _assert_all_three_unavailable(body, "sport_not_running")


def test_gate_3_no_usable_hr_is_no_hr_with_rtss_and_srpe_at_their_own_reasons() -> None:
    body = compute_session_load(_synthetic(heart_rate=None))
    assert body["metrics"]["hr_trimp"]["value"] is None
    assert body["metrics"]["hr_trimp"]["unavailable"] == "no_hr"
    assert body["session_load"] == {"value": None, "unavailable": "no_hr", "driver": None}
    assert body["metrics"]["rtss"] == {"value": None, "unavailable": "no_threshold_pace"}
    assert body["metrics"]["srpe"] == {"value": None, "unavailable": "no_rpe"}
    assert body["inputs"]["hr_time_s"] == 0.0
    assert body["inputs"]["avg_hr_bpm"] is None
    assert body["inputs"]["hr_time_fraction"] == 0.0


def test_gate_2_wins_over_gate_3_on_a_declared_capture_without_hr() -> None:
    _assert_all_three_unavailable(
        compute_session_load(_synthetic(activity_tag="health_snapshot", heart_rate=None)), "declared_capture"
    )


def test_a_missing_file_value_is_missing_anchor_naming_the_field() -> None:
    body = compute_session_load(_synthetic(resting=None))
    assert body["metrics"]["hr_trimp"]["unavailable"] == "missing_anchor"
    assert body["metrics"]["hr_trimp"]["unavailable_fields"] == ["resting_hr_bpm"]
    assert body["session_load"] == {"value": None, "unavailable": "missing_anchor", "driver": None}
    assert body["inputs"]["resting_hr_bpm"] == {
        "value": None,
        "source": None,
        "session_id": None,
        "anchor_version": None,
        "anchor_unavailable": None,
    }
    both = compute_session_load(_synthetic(resting=None, max_hr=None))
    assert both["metrics"]["hr_trimp"]["unavailable_fields"] == ["resting_hr_bpm", "max_hr_bpm"]


def test_a_missing_threshold_serves_trimp_and_withholds_the_load() -> None:
    body = compute_session_load(_synthetic(threshold=None))
    assert body["metrics"]["hr_trimp"]["value"] == pytest.approx(_expected_trimp(10.0, 100 / 140, "male"))
    assert body["metrics"]["hr_trimp"]["threshold_hour_reference"] is None
    assert body["session_load"] == {"value": None, "unavailable": "no_threshold_hr", "driver": None}


# --- AC2: hr_time_fraction is served unclamped ----------------------------------------------------


def test_hr_time_fraction_above_one_is_served_unclamped() -> None:
    body = compute_session_load(_synthetic(seconds=2819, timer_time_s=2816.667))
    assert body["inputs"]["hr_time_s"] == 2819.0
    assert body["inputs"]["timer_time_s"] == 2816.667
    assert body["inputs"]["hr_time_fraction"] == pytest.approx(2819.0 / 2816.667)
    assert body["inputs"]["hr_time_fraction"] > 1.0


def test_hr_time_fraction_is_null_without_a_timer_time() -> None:
    body = compute_session_load(_synthetic(timer_time_s=None, seconds=100))
    assert body["inputs"]["timer_time_s"] is None
    assert body["inputs"]["hr_time_fraction"] is None
    assert body["metrics"]["hr_trimp"]["value"] is not None


def test_hr_time_fraction_is_null_when_the_timer_time_is_zero() -> None:
    """A zero timer time is no denominator: the fraction is null, never a ``ZeroDivisionError``,
    and TRIMP, which reads ``hr_time_s`` and not the timer, is still computed."""
    body = compute_session_load(_synthetic(timer_time_s=0.0, seconds=100))
    print(f"  timer_time_s 0.0 -> inputs {body['inputs']['hr_time_s'], body['inputs']['hr_time_fraction']}")
    assert body["inputs"]["timer_time_s"] == 0.0
    assert body["inputs"]["hr_time_s"] == 100.0
    assert body["inputs"]["hr_time_fraction"] is None
    assert body["metrics"]["hr_trimp"]["value"] == pytest.approx(_expected_trimp(100 / 60, 100 / 140, "male"))


def test_the_empty_value_block_is_five_null_keys_and_read_only() -> None:
    """``EMPTY_VALUE`` is the block ``db`` and these suites build every empty field from, so its
    literal is pinned once here."""
    assert dict(session_load.EMPTY_VALUE) == {
        "value": None,
        "source": None,
        "session_id": None,
        "anchor_version": None,
        "anchor_unavailable": None,
    }
    assert list(session_load.EMPTY_VALUE) == ["value", "source", "session_id", "anchor_version", "anchor_unavailable"]
    with pytest.raises(TypeError):
        session_load.EMPTY_VALUE["value"] = 1  # type: ignore[index]
