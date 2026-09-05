"""Shared pytest fixtures for runcoach-cli tests.

Every fixture here was previously copy-pasted into the individual test
modules -- ``_install_mock_client`` three times and ``isolated_config_path``
four times -- and the duplication was actively growing as new command test
files were added. They live here now so the next test file gets them for
free instead of inheriting another copy.

``isolated_config_path`` is autouse and deliberately **bare**: it points
``config.CONFIG_PATH`` at a per-test ``tmp_path`` location and creates
nothing, so no test -- here or in any later CLI task -- ever reads or writes
the real ``~/.runcoach/cli.toml``. Being autouse matters: tests that never
name it (``test_api_client.py``, ``test_main.py``) still get the isolation,
and making it opt-in would silently un-isolate anything that forgot to ask.
Creating nothing also matters: ``test_config.py`` asserts the missing-config
guard fires, and ``test_init.py`` asserts ``runcoach init`` creates the
parent directory itself -- both would be defeated by a fixture that
pre-created the file or its directory.

``prewritten_config`` layers on top for the command tests that need config
*content* to exist rather than just a redirected path (``test_ingest.py``,
``test_status.py``, which invoke commands that call ``load_config()``
first). It is opt-in per module via
``pytestmark = pytest.mark.usefixtures("prewritten_config")`` rather than
autouse, precisely because making it global would break the two modules
above.

``install_mock_client`` is exposed as a fixture yielding the installer
callable rather than as an importable helper, so test modules don't have to
import from ``conftest`` (fragile: two workspace members each have a
``conftest`` module). It swaps the module-level ``api_client.client`` for
one backed by an ``httpx.MockTransport``, which is how the suite simulates
connect errors and timeouts without ever touching the network.

``runcoach-api/tests/test_init_cmd.py`` has a similar-looking
``isolated_config_path`` and is intentionally **not** covered here: it
patches a different module attribute (``init_cmd.CONFIG_PATH``) against a
different filename (``api.toml``) in a different workspace package.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import tomli_w

from runcoach_cli import api_client, config


@pytest.fixture(autouse=True)
def isolated_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``config.CONFIG_PATH`` at a throwaway location for every test.

    Nothing is created on disk -- the path simply doesn't exist yet. Never
    touches the real ``~/.runcoach/`` directory.
    """
    config_path = tmp_path / ".runcoach" / "cli.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    return config_path


@pytest.fixture
def prewritten_config(isolated_config_path: Path) -> Path:
    """``isolated_config_path`` plus a real, loadable config file on disk.

    For command tests whose commands call ``load_config()`` before doing
    anything else, and so need content to exist -- not merely a redirected
    path.
    """
    isolated_config_path.parent.mkdir(parents=True, exist_ok=True)
    isolated_config_path.write_bytes(tomli_w.dumps({"api_url": "http://example.test"}).encode())
    return isolated_config_path


@pytest.fixture
def install_mock_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Callable[[httpx.Request], httpx.Response]], None]:
    """Return an installer that swaps the module-level client for a mock one.

    Usage: ``install_mock_client(handler)``, where ``handler`` is an
    ``httpx.MockTransport`` handler -- it may return a response or raise an
    ``httpx`` transport error to simulate an unreachable API.
    """

    def _install_mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        mock_client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
        monkeypatch.setattr(api_client, "client", mock_client)

    return _install_mock_client
