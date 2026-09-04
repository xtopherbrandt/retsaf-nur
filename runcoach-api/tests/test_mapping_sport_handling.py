"""T034 items 2 & 3: ``sport`` mapping edge cases.

- Item 2: a FIT file with neither a ``session`` nor a ``sport``
  message (a watch that died mid-activity) must not reach
  ``db.persist`` with ``sport=None`` and surface as an unhandled 500 --
  ``mapping.to_canonical`` raises a typed ``MissingSportError`` before
  a ``Session`` is even constructed.
- Item 3: a raw FIT ``sport`` enum integer fitdecode's profile has no
  name for (e.g. ``60``, confirmed via a one-off ``fitdecode``
  inspection of ``tests/fixtures/sample_health_snapshot.fit`` --
  both its ``session`` and ``sport`` messages carry the bare int
  ``60``) must not leak into the canonical ``sport`` field, which
  spec/references/F003-canonical-schema.md §2.2.1 defines as the enum
  ``running`` / ``other``. It maps to ``"other"``, with the raw value
  preserved in ``context.provenance`` rather than silently dropped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from runcoach_api.ingestion import fit_parser, mapping
from runcoach_api.ingestion.exceptions import MissingSportError

FIXTURES = Path(__file__).parent / "fixtures"
STRESS_FIXTURE = FIXTURES / "sample_health_snapshot.fit"


class _FakeMsg:
    """Minimal stand-in for a ``fitdecode.FitDataMessage``.

    Only implements what ``to_canonical``/``_build_record`` actually
    read: ``.name``, ``get_value(name, fallback=None)``, and
    ``.fields`` (iterated for developer-field data, empty here).
    """

    def __init__(self, name: str, values: dict) -> None:
        self.name = name
        self._values = values
        self.fields: list = []

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)


_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _record_msg(timestamp=_START, **extra):
    values = {"timestamp": timestamp}
    values.update(extra)
    return _FakeMsg("record", values)


# ---------------------------------------------------------------------------
# Item 2: no session, no sport message -> MissingSportError, not sport=None
# ---------------------------------------------------------------------------


def test_no_session_and_no_sport_message_raises_missing_sport_error() -> None:
    # A watch that died mid-activity: only record messages survive, no
    # session/sport roll-up message at all.
    messages = [_record_msg(heart_rate=140)]

    with pytest.raises(MissingSportError):
        mapping.to_canonical(messages)


# ---------------------------------------------------------------------------
# Item 3: unmapped raw FIT sport int -> "other", raw value in provenance
# ---------------------------------------------------------------------------


def test_unmapped_raw_sport_int_maps_to_other_on_real_fixture() -> None:
    messages = fit_parser.decode(STRESS_FIXTURE.read_bytes())

    session, _records = mapping.to_canonical(messages)

    assert session.sport == "other"


def test_unmapped_raw_sport_int_is_preserved_in_provenance_on_real_fixture() -> None:
    messages = fit_parser.decode(STRESS_FIXTURE.read_bytes())

    session, _records = mapping.to_canonical(messages)

    assert session.context.provenance["raw_sport_value"] == 60


def test_named_sport_string_passes_through_unchanged() -> None:
    messages = [
        _FakeMsg("session", {"sport": "running", "start_time": _START}),
        _record_msg(),
    ]

    session, _records = mapping.to_canonical(messages)

    assert session.sport == "running"
    assert "raw_sport_value" not in session.context.provenance
