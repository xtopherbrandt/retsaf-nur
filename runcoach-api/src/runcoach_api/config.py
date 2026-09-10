"""Application configuration loading for the Run Coaching API.

Reads ``host``, ``port``, ``data_dir``, ``resting_hrv_profile_names`` and
``athlete_timezone`` from a local TOML config
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
import zoneinfo
from pathlib import Path
from zoneinfo import ZoneInfo

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


def validate_zone(name: str) -> ZoneInfo:
    """Resolve an IANA zone name to a ``ZoneInfo`` or raise ``ValueError``.

    The shared validator for ``athlete_timezone`` (F005): the config field
    (T088) and ``init_cmd``'s flag (T089) both call this so the zone is
    checked once, at load, and never at request time. It takes a string and
    returns a ``ZoneInfo``; it does not read settings (the pure-module
    precedent is ``pipeline.py``'s handover of ``profile_names``).

    Two failures look the same to the stdlib and must not to the operator.
    ``zoneinfo.ZoneInfoNotFoundError`` -- a ``KeyError`` subclass -- is raised
    identically for "not a real zone" and "no time-zone database installed"
    (``zoneinfo/_common.py::load_tzdata``). Python's stdlib bundles no tzdb and
    Windows ships none, so a validator that only caught that exception would
    call ``Pacific/Auckland`` invalid on a tzdb-less host, and F005's
    "an invalid timezone fails at load" would be satisfied by a check that is
    wrong in exactly the case it exists for. ``available_timezones()`` is empty
    only when neither the ``tzdata`` package nor a system tzdb exists
    (``zoneinfo/_tzpath.py``), so that is the discriminator: an installation
    fault, reported in different words from a bad zone.

    Both paths raise ``ValueError`` rather than letting the ``KeyError``
    escape: inside a pydantic ``@field_validator`` a ``ValueError`` surfaces as
    ``type='value_error'`` naming the field, whereas a ``KeyError`` becomes an
    internal error with no field attached.

    **A resolved zone is not yet a valid one.** ``ZoneInfo(name)`` is a file
    lookup under the tzdb root, so what it accepts is what the host's
    filesystem accepts: on NTFS ``'Pacific/Auckland '`` (trailing space) and
    ``'utc'`` both resolve -- and both fail on Linux -- so an ``api.toml``
    validated on one host would refuse to load on another, and the response's
    ``timezone`` field would echo the padded key. ``''`` and a path-like key
    (``'../Etc/UTC'``) raise ``zoneinfo``'s own ``ValueError`` instead, in
    words that name neither the field nor the fix. The canonical key set is
    ``available_timezones()`` -- the tz database's own names, legacy keys such
    as ``EST`` and ``Etc/GMT+5`` included -- and a ``name`` outside it is
    refused with the "not a recognised" wording whatever the filesystem said
    (sprint-005 review, M3; the probe table is in T078's task file). Nothing
    is stripped or case-folded on the athlete's behalf: the value carries
    intent and is not guessed at.
    """
    try:
        zone = zoneinfo.ZoneInfo(name)
    except zoneinfo.ZoneInfoNotFoundError as exc:
        if not zoneinfo.available_timezones():
            raise ValueError(
                f"cannot resolve time zone {name!r}: no time-zone database is "
                "installed in this environment, so no zone can be checked. "
                "The `tzdata` package is a declared dependency of runcoach-api; "
                "run `uv sync --all-packages` to install it."
            ) from exc
        raise _not_a_recognised_zone(name) from exc
    except ValueError as exc:
        # ``''`` and path-like keys: ``zoneinfo`` refuses them before any
        # lookup, in its own words. Re-raised in the validator's, so the
        # operator message names the field and the fix.
        raise _not_a_recognised_zone(name) from exc
    if name not in zoneinfo.available_timezones():
        raise _not_a_recognised_zone(name)
    return zone


def _not_a_recognised_zone(name: str) -> ValueError:
    """The one wording for "this is not a zone", whichever way it failed."""
    return ValueError(
        f"{name!r} is not a recognised IANA time zone; use the zone's IANA "
        'name as in the tz database (e.g. "Pacific/Auckland").'
    )


class AppConfig(BaseSettings):
    """Typed application configuration.

    No field has a default: an incomplete config is a validation
    error rather than a silently-absorbed default (project decision
    against implicit configuration -- see F001 decision log).
    """

    host: str
    port: int = Field(ge=1, le=65535)
    data_dir: Path
    # The two remediation-bearing fields are declared AFTER `port`,
    # deliberately. Pydantic v2 orders `ValidationError.errors()` by
    # field-definition order, and `cli._render_validation_error` picks its
    # mode from `errors()[0]`: a reported error *with* remediation text
    # renders the remediation for every error that has it (so the order of
    # the two fields below no longer matters -- T081), but a reported error
    # *without* it is rendered alone, in the terse form, so a port complaint
    # never carries upgrade instructions. `test_cli_startup` builds a real
    # error from an out-of-range `port` and asserts "port" reaches stderr;
    # moving either field above `port` would silently turn that terse message
    # into a remediation block (`test_config.py::
    # test_port_precedes_the_remediation_bearing_fields` pins the order).
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

    # The IANA zone the resting-HRV trend (F005) buckets local days in: the
    # 7-day window, the 21-day coverage gap and same-morning grouping are all
    # measured in the athlete's local calendar, and rows are stored `+00:00`.
    #
    # NO DEFAULT. A silent "UTC" would bucket a 06:00 capture at UTC+13 onto
    # the previous day and shift which readings fall in the window, with no
    # error -- exactly the failure the no-defaults rule exists to prevent. The
    # zone is validated here, at load, so an unresolvable zone is a startup
    # failure rather than a per-request 500. Stored as the name, not the
    # `ZoneInfo`: the route (`main.get_hrv_trend`) constructs `ZoneInfo(name)`
    # from the already-validated name -- no second validation; `ZoneInfo`
    # caches by key, so the construction is cheap -- and hands the object
    # down, so the metrics module stays pure and the config stays a plain,
    # serialisable record.
    athlete_timezone: str

    model_config = SettingsConfigDict(env_prefix="RUNCOACH_", extra="forbid")

    @field_validator("athlete_timezone")
    @classmethod
    def _reject_unresolvable_zone(cls, name: str) -> str:
        """Delegate to ``validate_zone`` so the check lives in one place.

        A bad zone surfaces as ``type='value_error'`` on this field, never as
        the bare ``KeyError`` ``zoneinfo`` raises; see ``validate_zone`` for
        why the tzdb-less host is reported in different words.
        """
        validate_zone(name)
        return name

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
