"""M3 (sprint-002 review): ``session_id`` must never silently derive
from wall-clock time.

``to_canonical()`` requires either a ``session`` or ``sport`` message to
determine ``sport`` (else ``MissingSportError``, T034 item 2) -- but
does NOT require a ``session`` message to determine ``start_time``,
since ``sport`` can come from a standalone ``sport`` message while
``session_msg`` stays ``None``. Before this fix, that path (or any file
with a ``session`` message present but no ``start_time`` field, and
zero/timestamp-less ``record`` messages) fell through to
``datetime.now(timezone.utc)``.

Since ``derive_session_id()`` hashes ``(source_device, start_time)``,
and the entire point of commit 8f7e488 ("derive session_id
deterministically from (source_device, start_time)") is that
``session_id`` must be a stable, rebuildable function of the FIT
file's own content, that fallback silently reintroduced the
non-determinism T032 closed: re-ingesting identical bytes at a
different wall-clock second would produce a different ``session_id``
and defeat the ``UNIQUE(source_device, start_time)`` dedup constraint.

``mapping.to_canonical`` now raises ``MissingStartTimeError`` instead
of substituting a plausible-looking wall-clock value, mirroring
``MissingSportError``'s treatment of the analogous missing-sport gap.
"""

from __future__ import annotations

import pytest

from runcoach_api.ingestion import mapping
from runcoach_api.ingestion.exceptions import MissingStartTimeError


class _FakeMsg:
    """Minimal stand-in for a ``fitdecode.FitDataMessage`` -- same shape
    as ``test_mapping_sport_handling.py``'s helper: only implements
    ``.name``, ``get_value(name, fallback=None)``, and ``.fields``."""

    def __init__(self, name: str, values: dict) -> None:
        self.name = name
        self._values = values
        self.fields: list = []

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)


def test_sport_message_with_no_session_and_no_record_timestamps_raises() -> None:
    """Sport is resolvable from a standalone ``sport`` message, so
    MissingSportError does not fire -- but there is no session message
    and no record message at all, so start_time has nothing to derive
    from."""
    messages = [_FakeMsg("sport", {"sport": "running"})]

    with pytest.raises(MissingStartTimeError):
        mapping.to_canonical(messages)


def test_session_message_with_no_start_time_and_no_record_timestamps_raises() -> None:
    """A session message is present (sport resolves fine) but carries
    no start_time field, and no record message supplies a fallback
    timestamp either."""
    messages = [_FakeMsg("session", {"sport": "running"})]

    with pytest.raises(MissingStartTimeError):
        mapping.to_canonical(messages)


def test_session_message_with_no_start_time_but_record_timestamps_present_does_not_raise() -> None:
    """The existing fallback -- deriving start_time from the earliest
    record timestamp -- is still a legitimate native-field derivation,
    not a fabrication, and must keep working."""
    from datetime import datetime, timezone

    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    messages = [
        _FakeMsg("session", {"sport": "running"}),
        _FakeMsg("record", {"timestamp": ts}),
    ]

    session, _records = mapping.to_canonical(messages)

    assert session.start_time == ts.isoformat()
