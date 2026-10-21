"""The overnight mapper stores ``last_night_average`` and nothing Garmin derived from it.

The same shape as ``test_hrv_quarantine_boundary.py``, over ``ingestion/overnight.py``:
Garmin's HRV Status classification, its weekly average and its three baseline
bounds ride on the same ``hrv_status_summary`` message as the night's rMSSD
average, and the quarantine registry holds them (the label is a black box, the
number is not). The boundary is asserted three ways:

1. **Structural.** ``OVERNIGHT_INPUT_FIELDS`` is exactly the one input and is
   disjoint from ``quarantine._VENDOR_DERIVED_FIELDS["hrv_status_summary"]``,
   which registers exactly the five names.
2. **Honesty of the declared set.** The names the module reads, recovered by
   walking its source with ``support/field_reads.py``, split exactly into the
   declared input and the declared non-inputs, and none of them is quarantined.
   The sidecar comes through ``quarantine.extract`` with the message as a
   whole, so no quarantined name is spelled in the mapper at all.
3. **Perturbation.** A copy of the source with the value read swapped to
   ``status`` or to ``weekly_average`` is reported as a violation, so the
   guard is known to bite, not to pass because a set is empty.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from runcoach_api.ingestion import overnight, quarantine

SUPPORT = Path(__file__).parent / "support"
NIGHT = Path(__file__).parent / "fixtures" / "nights" / "night_recent.fit"

# The five vendor-derived fields of ``hrv_status_summary``, spelled out here
# independently of the registry, so a registry quietly narrowed to fewer names
# cannot make the disjointness assertion vacuous.
KNOWN_QUARANTINED_SUMMARY_FIELDS = frozenset(
    {
        "status",
        "weekly_average",
        "baseline_low_upper",
        "baseline_balanced_lower",
        "baseline_balanced_upper",
    }
)
THE_VALUE = "last_night_average"


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


field_reads = _load_support("field_reads")


def _quarantined_summary_fields() -> frozenset[str]:
    return frozenset(quarantine._VENDOR_DERIVED_FIELDS["hrv_status_summary"])


def _mapper_source() -> str:
    return Path(overnight.__file__).read_text(encoding="utf-8")


def _names_read(source: str) -> frozenset[str]:
    collector = field_reads.collect_reads(source)
    field_reads.assert_every_lookup_key_is_resolvable(collector)
    return frozenset(collector.names)


def _violations(source: str) -> frozenset[str]:
    """The quarantined names a mapper written as ``source`` reads."""
    return _names_read(source) & _quarantined_summary_fields()


# ---------------------------------------------------------------------------
# 1. structural
# ---------------------------------------------------------------------------


def test_the_registry_quarantines_exactly_the_five_summary_fields() -> None:
    assert _quarantined_summary_fields() == KNOWN_QUARANTINED_SUMMARY_FIELDS


def test_the_declared_input_is_the_one_average_and_is_not_quarantined() -> None:
    assert overnight.OVERNIGHT_INPUT_FIELDS == (THE_VALUE,)
    assert not frozenset(overnight.OVERNIGHT_INPUT_FIELDS) & _quarantined_summary_fields()
    assert not frozenset(overnight.OVERNIGHT_INPUT_FIELDS) & frozenset(overnight.OVERNIGHT_NON_INPUT_READS)
    assert not frozenset(overnight.OVERNIGHT_NON_INPUT_READS) & _quarantined_summary_fields()


# ---------------------------------------------------------------------------
# 2. the declared sets are honest: the reads split into input, non-input and
#    (by absence) the quarantined five
# ---------------------------------------------------------------------------


def test_the_mapper_reads_only_last_night_average_as_the_value() -> None:
    discovered = _names_read(_mapper_source())
    declared_input = frozenset(overnight.OVERNIGHT_INPUT_FIELDS)
    declared_non_input = frozenset(overnight.OVERNIGHT_NON_INPUT_READS)

    unclassified = discovered - declared_input - declared_non_input
    assert not unclassified, f"overnight reads names declared in neither bucket: {sorted(unclassified)}"
    stale = (declared_input | declared_non_input) - discovered
    assert not stale, f"declared but not read by overnight: {sorted(stale)}"
    assert discovered & declared_input == {THE_VALUE}
    assert not _violations(_mapper_source())


def test_no_lookup_key_in_the_mapper_is_unresolvable() -> None:
    collector = field_reads.collect_reads(_mapper_source())

    assert not collector.unresolved, sorted(collector.unresolved)


def test_the_real_night_stores_the_average_and_sidecars_the_five() -> None:
    night = overnight.map_night(NIGHT.read_bytes())

    assert isinstance(night, overnight.Night)
    assert frozenset(night.sidecar) == KNOWN_QUARANTINED_SUMMARY_FIELDS
    # 43.0 is the file's last_night_average; its weekly average and baselines
    # are 40.0, 34.0, 36.0 and 46.0, so the stored number is none of them.
    assert night.session.resting_rmssd_ms == 43.0
    numeric_sidecar = {float(v) for k, v in night.sidecar.items() if k != "status"}
    assert numeric_sidecar == {40.0, 34.0, 36.0, 46.0}
    assert night.session.resting_rmssd_ms not in numeric_sidecar


# ---------------------------------------------------------------------------
# 3. perturbation: the guard bites
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("swapped_to", ["status", "weekly_average"])
def test_storing_a_quarantined_field_as_the_value_is_caught(swapped_to: str) -> None:
    source = _mapper_source()
    assert f'"{THE_VALUE}"' in source, "the value read is spelled as a literal, so the swap below is real"
    doctored = source.replace(f'"{THE_VALUE}"', f'"{swapped_to}"')
    assert doctored != source

    assert _violations(doctored) == {swapped_to}
