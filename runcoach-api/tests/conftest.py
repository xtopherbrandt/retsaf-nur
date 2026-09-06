"""Shared pytest fixtures for runcoach-api tests.

``isolated_data_dir`` is an autouse fixture that redirects
``db.get_connection()``'s config lookup to a per-test ``tmp_path``, so
no test -- here or in any later F003 task that touches the DB -- ever
opens or writes the real ``~/.runcoach/runcoach.db``. It works by
monkeypatching ``runcoach_api.db.config_module.load_config`` to return
a fake ``AppConfig`` pointed at a tmp_path-derived data dir; it's
autouse so every test gets isolation for free without needing to
request it explicitly.

``db.get_connection()`` wraps ``config_module.load_config`` in an
``lru_cache`` (maxsize=1) so the config is only read from disk once per
process. That cache is cleared here, before each test's monkeypatch
takes effect, so a stale cached config from a previous test never
leaks into this one.

Everything below ``isolated_data_dir`` is the resting-HRV helper set --
one ``_FakeMsg``, one ``_synthetic``, one ``_classified``, one
``_ingest`` -- shared by the five ``test_resting_hrv_*.py`` modules that
previously each carried their own drifting copy. See the section comment
there for why they are fixtures rather than importable helpers, and why
none of them is autouse.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from runcoach_api import db as db_module
from runcoach_api.config import AppConfig
from runcoach_api.ingestion import hrv_classification, mapping


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    fake_config = AppConfig(host="127.0.0.1", port=8000, data_dir=data_dir)
    monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: fake_config)
    db_module._load_config_cached.cache_clear()
    return data_dir


# ---------------------------------------------------------------------------
# The resting-HRV test helpers
#
# ``_FakeMsg``, ``_synthetic``, ``_classified`` and ``_ingest`` were each
# re-implemented in five ``test_resting_hrv_*.py`` modules, and the copies
# had already drifted: ``quality_gates``'s ``_classified`` grew a third
# ``valid_fraction`` parameter the others lacked, and ``_synthetic``
# defaulted ``sport`` to ``"running"`` in two files, to ``60`` in a third,
# and made it required in a fourth. They were no longer interchangeable,
# so a fix to the stand-in had to land in five places. This is the same
# consolidation T052 did for the CLI package.
#
# They are exposed as **fixtures returning the callable** rather than as
# importable module-level helpers, exactly as ``install_mock_client`` is in
# ``runcoach-cli/tests/conftest.py``, and for a harder reason here: this
# workspace runs pytest with ``--import-mode=importlib`` (see the root
# ``pyproject.toml``), under which the ``tests/`` directories are never
# placed on ``sys.path``, so ``from conftest import _FakeMsg`` raises
# ``ModuleNotFoundError`` outright. A fixture is the only supported channel.
#
# None of these is autouse -- deliberately. ``isolated_data_dir`` above is,
# because isolation must not be opt-in; these are plain helpers, and making
# them autouse would build messages and open uploads for every test in the
# package that neither needs nor names them.
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"

# The start timestamp every synthetic capture is pinned to. The value
# carries no meaning beyond being fixed: session-id determinism is tested
# elsewhere, and holding it constant keeps synthetic captures in one module
# distinguishable only by what the test actually varies.
SYNTHETIC_START = datetime(2026, 1, 1, tzinfo=timezone.utc)

# The empirically observed Garmin Health Snapshot ``sport`` value -- the
# Tier-2 identity signal. Modules that need it pass it explicitly.
SNAPSHOT_SPORT = 60


class _FakeMsg:
    """Minimal stand-in for a ``fitdecode.FitDataMessage``.

    The shape ``test_mapping_sport_handling.py`` established: only
    ``.name``, ``get_value(name, fallback=None)`` and ``.fields`` are read
    by ``mapping.to_canonical`` and by ``hrv_classification.classify``.
    """

    def __init__(self, name: str, values: dict) -> None:
        self.name = name
        self._values = values
        self.fields: list = []

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)


def _synthetic(sport="running", **session_extra):
    """A two-message file: one ``session`` roll-up plus one ``record``.

    Routed through the real ``mapping.to_canonical``, so ``_build_summary``'s
    ``None``-stripping applies exactly as it does to a real file: omit
    ``total_distance`` and the summary has **no** ``distance_m`` key at all --
    not a key holding ``None``. Both Health Snapshot fixtures are in that
    state, so subscripting rather than ``.get()`` would turn every indoor
    capture into a 500. ``sport`` likewise goes through the real mapping
    code, so ``context.provenance["raw_sport_value"]`` -- the corroborating
    identity signal Tier 2 reads -- is written by ``mapping.py`` itself
    rather than hand-injected by the test.

    The ``"running"`` default is the Tier-1 modules' one, where ``sport``
    carries no signal and is deliberately not gated on. Tier-2 and
    tier-precedence captures need the Health Snapshot value and pass
    ``SNAPSHOT_SPORT`` explicitly -- one helper, no second default.
    """
    values = {"sport": sport, "start_time": SYNTHETIC_START}
    values.update(session_extra)
    return [
        _FakeMsg("session", values),
        _FakeMsg("record", {"timestamp": SYNTHETIC_START, "heart_rate": 60}),
    ]


def _classified(messages, rr_intervals=None, valid_fraction=None):
    """Map, then classify -- optionally with the ``rr_valid_fraction`` the
    pipeline would have set.

    ``pipeline.py`` assigns ``session.rr_valid_fraction`` between mapping
    and ``classify()``, so setting it here reproduces the real call order
    rather than inventing one. Passing no ``valid_fraction`` leaves the
    attribute exactly as ``mapping.to_canonical`` left it, which is the
    state the four modules that never set it were already asserting
    against.
    """
    session, _records = mapping.to_canonical(messages)
    if valid_fraction is not None:
        session.rr_valid_fraction = valid_fraction
    hrv_classification.classify(messages, session, rr_intervals or [])
    return session


def _post_fit(client, filename: str):
    """POST one fixture to ``/sessions`` and return the raw response.

    For tests asserting on the *upload* outcome itself -- a 409 duplicate,
    a rejected capture -- where a helper that asserted 201 would swallow
    the very thing under test.
    """
    raw = (FIXTURES / filename).read_bytes()
    return client.post("/sessions", files={"file": (filename, raw)})


def _ingest(client, filename: str) -> dict:
    """Upload one fixture and return its canonical ``GET /sessions/{id}`` body.

    The round trip is the point: the tags and tiers these modules assert on
    are what E003 reads back out of the store, not what ``classify()`` left
    in memory.
    """
    post = _post_fit(client, filename)
    assert post.status_code == 201, post.text
    detail = client.get(f"/sessions/{post.json()['session_id']}")
    assert detail.status_code == 200, detail.text
    return detail.json()


@pytest.fixture
def fake_msg() -> type[_FakeMsg]:
    """The ``fitdecode.FitDataMessage`` stand-in class itself."""
    return _FakeMsg


@pytest.fixture
def synthetic():
    """``synthetic(sport="running", **session_extra)`` -> message list."""
    return _synthetic


@pytest.fixture
def classified():
    """``classified(messages, rr_intervals=None, valid_fraction=None)``."""
    return _classified


@pytest.fixture
def ingest():
    """``ingest(client, filename)`` -> canonical GET body."""
    return _ingest


@pytest.fixture
def post_fit():
    """``post_fit(client, filename)`` -> raw POST response."""
    return _post_fit
