"""Application configuration loading for the Run Coaching API.

Reads ``host``, ``port``, ``data_dir`` and ``resting_hrv_profile_names``
from a local TOML config
file (``~/.runcoach/api.toml`` by default), with ``RUNCOACH_``-prefixed
environment variables taking priority over the file's values.

This module stays pure: given a path, it returns a validated
``AppConfig`` or raises one of the typed exceptions below. It never
calls ``sys.exit``/``print`` -- that seam belongs to ``cli.py`` (T004),
which is responsible for turning a raised exception into an operator
-facing message and a non-zero exit code.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

CONFIG_PATH = Path.home() / ".runcoach" / "api.toml"


class ConfigNotFoundError(Exception):
    """Raised when the config file does not exist at the expected path."""


class ConfigCorruptError(Exception):
    """Raised when the config file exists but is not valid TOML.

    Distinct from a validation failure on a well-formed file (see
    ``pydantic.ValidationError``, raised directly by ``load_config``
    for e.g. an invalid port value).
    """


class AppConfig(BaseSettings):
    """Typed application configuration.

    No field has a default: an incomplete config is a validation
    error rather than a silently-absorbed default (project decision
    against implicit configuration -- see F001 decision log).
    """

    host: str
    port: int = Field(ge=1, le=65535)
    data_dir: Path
    # Declared LAST deliberately. Pydantic v2 orders `ValidationError.errors()`
    # by field-definition order, and `cli.serve()` renders only `errors()[0]`;
    # `test_cli_startup` builds a real error from an out-of-range `port` and
    # asserts "port" reaches stderr. Moving this field above `port` would
    # silently reorder that message.
    #
    # The athlete's Tier-1 resting-HRV declaration: a FIT file routes Tier 1
    # only when its `sport_profile_name` appears here (exact, case-sensitive)
    # or the upload carried an explicit override. See F004's 2026-09-06
    # amendment.
    #
    # NO DEFAULT -- not even `[]`. An athlete who uses only Health Snapshot
    # writes `resting_hrv_profile_names = []` explicitly; a `default_factory`
    # would reinstate the behavioural default "Tier 1 derives nothing" under
    # the guise of a value, which is precisely what the amendment exists to
    # prevent, and what `AppConfig`'s no-defaults rule already forbids.
    resting_hrv_profile_names: list[str]

    model_config = SettingsConfigDict(env_prefix="RUNCOACH_", extra="forbid")

    @field_validator("resting_hrv_profile_names")
    @classmethod
    def _reject_blank_profile_names(cls, names: list[str]) -> list[str]:
        """Reject empty and whitespace-only entries.

        Matching against `sport_profile_name` is exact and case-sensitive, so
        a `""` entry would silently match every file whose profile name is
        empty or absent -- turning a config typo into a blanket Tier-1 route.
        `str.strip()` covers every Unicode space, so a non-breaking space or a
        tab is caught alongside a plain blank.
        """
        for index, name in enumerate(names):
            if not name.strip():
                raise ValueError(
                    f"entry {index} is blank; a resting-HRV profile name must be "
                    "the exact activity-profile name as it appears on the watch "
                    '(e.g. "HRV Snapshot"). Write an empty list to declare that '
                    "no profile means a resting capture."
                )
        return names

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Order = priority; first-listed wins, so env vars override the
        # TOML file. `toml_file` is intentionally omitted here so this
        # source reads it from `settings_cls.model_config['toml_file']`,
        # letting `load_config` target an arbitrary path per call (see
        # below) without a hardcoded module-level path baked into this
        # method.
        return (init_settings, env_settings, TomlConfigSettingsSource(settings_cls))


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    """Load and validate the application config from ``path``.

    Args:
        path: Location of the TOML config file. Defaults to
            ``CONFIG_PATH`` (``~/.runcoach/api.toml``); tests inject a
            ``tmp_path``-derived path instead of touching the real file.

    Returns:
        A populated ``AppConfig``.

    Raises:
        ConfigNotFoundError: ``path`` does not exist. Checked explicitly
            up front -- pydantic-settings silently treats a missing TOML
            file as "no data from this source", which would surface as a
            confusing "field required" error instead of a clear
            missing-config message.
        ConfigCorruptError: ``path`` exists but is not valid TOML syntax.
        pydantic.ValidationError: the file parses but a field value is
            invalid (e.g. a non-numeric or out-of-range port). Raised
            as-is; the CLI entry point (T004) formats it into a clean,
            field-naming operator message via ``.errors()``.
    """
    if not path.exists():
        raise ConfigNotFoundError(
            f"No config file found at {path}. Run `runcoach-api init` to create one."
        )

    # Build a throwaway subclass pinning `toml_file` to the requested
    # path. `settings_customise_sources` receives this subclass as
    # `settings_cls`, so `TomlConfigSettingsSource(settings_cls)` reads
    # the path from *its* model_config rather than a fixed module-level
    # constant -- this is what makes the path test-injectable.
    config_cls = type(
        "AppConfig",
        (AppConfig,),
        {
            "model_config": SettingsConfigDict(
                env_prefix="RUNCOACH_", extra="forbid", toml_file=path
            )
        },
    )

    try:
        return config_cls()
    except tomllib.TOMLDecodeError as exc:
        raise ConfigCorruptError(
            f"Config file at {path} could not be parsed as TOML: {exc}"
        ) from exc
