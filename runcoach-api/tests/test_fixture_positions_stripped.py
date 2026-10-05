"""The two hilly fixtures carry no position value.

``hilly_run_8k_fr945.fit`` and ``hilly_long_run_17k_fr945.fit`` are the
athlete's own runs with every position value overwritten by
``tests/support/strip_fit_positions.py``: the GPS tracks stay out of the
repository. A position value is any field whose profile name ends in
``_lat`` or ``_long``, in any message, plus lap fields 27-30, which the
FR945 writes unnamed and which held semicircle values on the original
track.

Every message is decoded with the file CRC checked, and each position
field must decode as absent (the sint32 invalid value ``0x7FFFFFFF``
decodes to ``None``). The fields are found by number, not by name, so an
unnamed lap field and a named one are checked the same way. Each file must
also still upload with 201.

The originals live outside the repository, so the byte-diff and
bounding-box checks made when the files were written are build-time
evidence, recorded in the commit that added them.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import fitdecode
import pytest
from fastapi.testclient import TestClient
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
HILLY_FIXTURES = ("hilly_run_8k_fr945.fit", "hilly_long_run_17k_fr945.fit")

_SPEC = importlib.util.spec_from_file_location(
    "strip_fit_positions", Path(__file__).parent / "support" / "strip_fit_positions.py"
)
assert _SPEC is not None and _SPEC.loader is not None
strip_fit_positions = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(strip_fit_positions)

POSITION_FIELDS: dict[int, frozenset[int]] = strip_fit_positions.position_fields()


def test_the_position_field_set_holds_the_named_and_the_unnamed_lap_fields() -> None:
    """The set the test checks is the set the task names: record 0/1, lap 3-6
    and 27-30, session 3, 4, 29-32, 38, 39, split 21-24."""
    assert {0, 1} <= POSITION_FIELDS[20]
    assert {3, 4, 5, 6, 27, 28, 29, 30} <= POSITION_FIELDS[19]
    assert {3, 4, 29, 30, 31, 32, 38, 39} <= POSITION_FIELDS[18]
    assert {21, 22, 23, 24} <= POSITION_FIELDS[312]


@pytest.mark.parametrize("fixture_name", HILLY_FIXTURES)
def test_no_position_value_remains(fixture_name: str) -> None:
    path = FIXTURES / fixture_name
    checked = 0
    present: list[str] = []
    with fitdecode.FitReader(path, check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            wanted = POSITION_FIELDS.get(frame.global_mesg_num, frozenset())
            for field in frame.fields:
                if field.def_num in wanted:
                    checked += 1
                    if field.value is not None:
                        present.append(f"{frame.name}/{field.def_num}")
    # Counts and field identities only: never a value.
    assert checked > 0, f"{fixture_name}: no position field was decoded at all"
    assert not present, f"{fixture_name}: {len(present)} position values remain, first {present[:5]}"


@pytest.mark.parametrize("fixture_name", HILLY_FIXTURES)
def test_the_stripped_fixture_uploads(fixture_name: str, post_fit) -> None:
    with TestClient(app) as client:
        response = post_fit(client, fixture_name)
    assert response.status_code == 201, response.text
