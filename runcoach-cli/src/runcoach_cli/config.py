"""CLI configuration writing for the runcoach CLI.

Writes the athlete-supplied API URL to a local TOML config file
(``~/.runcoach/cli.toml`` by default). This module owns the write side
only -- reading the config back (T008) is a separate concern.
"""

from pathlib import Path

import tomli_w

CONFIG_PATH = Path.home() / ".runcoach" / "cli.toml"


class ConfigAlreadyExistsError(Exception):
    """Raised when a config file already exists and ``force`` was not set."""


def save_config(api_url: str, force: bool = False) -> None:
    """Write ``api_url`` to ``CONFIG_PATH`` as TOML.

    Args:
        api_url: The base URL of the runcoach API to persist.
        force: When ``False`` (default), refuses to overwrite an existing
            config file. When ``True``, overwrites it.

    Raises:
        ConfigAlreadyExistsError: ``CONFIG_PATH`` already exists and
            ``force`` is ``False``. Checked before any write -- no
            partial or silent overwrite ever happens.
    """
    if CONFIG_PATH.exists() and not force:
        raise ConfigAlreadyExistsError(CONFIG_PATH)

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(tomli_w.dumps({"api_url": api_url}))
