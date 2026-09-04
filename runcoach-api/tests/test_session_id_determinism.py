"""T032: ``session_id`` is a deterministic function of ``(source_device, start_time)``.

Spec §2.2.1 requires ``session_id`` be "opaque string, stable" and
"unique per activity" -- it is the key the decision log (spec/09) and
the state model reference. F003's Configuration section fixes the
derivation as ``(source_device, start_time)``, the same tuple the
``UNIQUE (source_device, start_time)`` constraint on ``sessions``
already dedups on.

The fixtures used here (``tests/fixtures/*.fit``) are real,
user-supplied FIT data -- per ``.claude/rules/project-testing.md``
these decode for real and never mock ``fitdecode``. The stability
assertion is deliberately made across *two fresh databases*, because
the failure this task closes is the local-first rebuild path: the
database is expected to be rebuilt from the FIT corpus, and every
``session_id`` used to change on rebuild.

The duplicate-upload behaviour (409, referencing the existing session,
no re-parse) is covered by ``tests/test_duplicate_upload.py``, which
stays unchanged as this change's regression guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runcoach_api import db
from runcoach_api.config import AppConfig
from runcoach_api.ingestion import fit_parser, mapping
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_RUN = FIXTURES / "sample_run.fit"
CHEST_STRAP_RUN = FIXTURES / "chest_strap_run.fit"


def _ingest_into_fresh_db(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, fixture: Path
) -> dict:
    """Point the DB layer at ``data_dir`` (which must not yet exist as a
    database) and upload ``fixture`` through the real HTTP route."""
    fake_config = AppConfig(host="127.0.0.1", port=8000, data_dir=data_dir)
    monkeypatch.setattr(db.config_module, "load_config", lambda *a, **k: fake_config)
    db._load_config_cached.cache_clear()

    with TestClient(app) as client:
        response = client.post(
            "/sessions", files={"file": (fixture.name, fixture.read_bytes())}
        )
    assert response.status_code == 201, response.text
    return response.json()


def test_same_fixture_into_two_fresh_databases_yields_same_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _ingest_into_fresh_db(tmp_path / "db-a", monkeypatch, SAMPLE_RUN)
    second = _ingest_into_fresh_db(tmp_path / "db-b", monkeypatch, SAMPLE_RUN)

    assert first["session_id"] == second["session_id"]


def test_two_different_real_fixtures_get_different_session_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Determinism must not come at the cost of uniqueness per activity."""
    first = _ingest_into_fresh_db(tmp_path / "db-a", monkeypatch, SAMPLE_RUN)
    second = _ingest_into_fresh_db(tmp_path / "db-b", monkeypatch, CHEST_STRAP_RUN)

    assert first["session_id"] != second["session_id"]


def test_to_canonical_session_id_is_derived_from_source_device_and_start_time() -> None:
    """The id the mapper emits is exactly the derivation applied to the
    session's own ``(source_device, start_time)`` -- i.e. anyone holding
    that tuple can recompute the id without the file."""
    messages = fit_parser.decode(SAMPLE_RUN.read_bytes())
    session, _records = mapping.to_canonical(messages)

    assert session.session_id == mapping.derive_session_id(
        session.source_device, session.start_time
    )


def test_repeated_decode_of_same_fixture_yields_same_session_id() -> None:
    """No wall-clock, randomness, or decode-order input leaks into the id."""
    first, _ = mapping.to_canonical(fit_parser.decode(SAMPLE_RUN.read_bytes()))
    second, _ = mapping.to_canonical(fit_parser.decode(SAMPLE_RUN.read_bytes()))

    assert first.session_id == second.session_id


def test_unknown_source_device_session_id_is_stable() -> None:
    sentinel = mapping._UNKNOWN_SOURCE_DEVICE
    first = mapping.derive_session_id(sentinel, "2026-01-01T00:00:00+00:00")
    second = mapping.derive_session_id(sentinel, "2026-01-01T00:00:00+00:00")

    assert isinstance(first, str)
    assert first
    assert first == second


def test_unknown_source_device_different_start_times_do_not_collide() -> None:
    sentinel = mapping._UNKNOWN_SOURCE_DEVICE
    first = mapping.derive_session_id(sentinel, "2026-01-01T00:00:00+00:00")
    second = mapping.derive_session_id(sentinel, "2026-01-01T00:00:01+00:00")

    assert first != second


def test_derivation_does_not_conflate_device_and_time_boundary() -> None:
    """A naive ``device + start_time`` concatenation would make
    ``("ab", "c")`` and ``("a", "bc")`` the same id."""
    assert mapping.derive_session_id("ab", "c") != mapping.derive_session_id("a", "bc")
