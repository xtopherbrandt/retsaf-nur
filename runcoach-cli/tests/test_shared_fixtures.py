"""Contract tests for the shared fixtures in ``runcoach-cli/tests/conftest.py``.

These pin the behaviour the per-module copies used to provide, so a future
edit to the shared conftest can't silently un-isolate the suite or drop the
pre-written config content that ``test_ingest.py``/``test_status.py`` rely on:

* ``isolated_config_path`` must stay **autouse** and **bare** -- it redirects
  ``config.CONFIG_PATH`` into ``tmp_path`` without creating anything, which is
  what ``test_init.py``'s "creates the parent directory" test depends on.
* ``prewritten_config`` must layer real, loadable config content on top.
* ``install_mock_client`` must swap the module-level ``api_client.client``.
"""

import tomllib

import httpx

from runcoach_cli import api_client, config


def test_isolated_config_path_is_autouse_and_creates_nothing(tmp_path) -> None:
    # No fixture requested other than tmp_path: CONFIG_PATH is already
    # redirected under it, proving the shared fixture is autouse.
    assert config.CONFIG_PATH == tmp_path / ".runcoach" / "cli.toml"
    assert not config.CONFIG_PATH.parent.exists()
    assert not config.CONFIG_PATH.exists()


def test_isolated_config_path_returns_the_patched_path(isolated_config_path) -> None:
    assert isolated_config_path == config.CONFIG_PATH


def test_prewritten_config_writes_loadable_content(prewritten_config) -> None:
    assert prewritten_config == config.CONFIG_PATH
    assert tomllib.loads(prewritten_config.read_text()) == {"api_url": "http://example.test"}
    assert config.load_config().api_url == "http://example.test"


def test_install_mock_client_swaps_the_module_level_client(install_mock_client) -> None:
    def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok", "version": "9.9.9"})

    install_mock_client(ok_handler)

    response = api_client.get_health("http://example.test")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "9.9.9"}
