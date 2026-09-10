import tomllib

import pytest
from pydantic import ValidationError

from runcoach_api import init_cmd
from runcoach_api.config import load_config


@pytest.fixture(autouse=True)
def isolated_config_path(tmp_path, monkeypatch):
    """Point CONFIG_PATH at a throwaway location for every test in this module."""
    config_path = tmp_path / ".runcoach" / "api.toml"
    monkeypatch.setattr(init_cmd, "CONFIG_PATH", config_path)
    return config_path


def test_api_init_writes_config_and_refuses_overwrite_without_force(isolated_config_path):
    config_path = isolated_config_path

    # No existing config: init writes the file with the given values.
    init_cmd.main(
        ["--host", "127.0.0.1", "--port", "8123", "--data-dir", "/tmp/x", "--athlete-timezone", "UTC"]
    )

    assert config_path.exists()
    written = tomllib.loads(config_path.read_text())
    assert written == {
        "host": "127.0.0.1",
        "port": 8123,
        "data_dir": "/tmp/x",
        "resting_hrv_profile_names": [],
        "athlete_timezone": "UTC",
    }

    # Re-running without --force refuses to overwrite and leaves the file untouched.
    with pytest.raises(SystemExit) as exc_info:
        init_cmd.main(
            ["--host", "0.0.0.0", "--port", "9999", "--data-dir", "/tmp/y", "--athlete-timezone", "UTC"]
        )

    assert exc_info.value.code != 0
    unchanged = tomllib.loads(config_path.read_text())
    assert unchanged == {
        "host": "127.0.0.1",
        "port": 8123,
        "data_dir": "/tmp/x",
        "resting_hrv_profile_names": [],
        "athlete_timezone": "UTC",
    }


def test_api_init_overwrites_with_force(isolated_config_path):
    config_path = isolated_config_path

    init_cmd.main(
        ["--host", "127.0.0.1", "--port", "8123", "--data-dir", "/tmp/x", "--athlete-timezone", "UTC"]
    )
    init_cmd.main(
        [
            "--host",
            "0.0.0.0",
            "--port",
            "9999",
            "--data-dir",
            "/tmp/y",
            "--athlete-timezone",
            "UTC",
            "--force",
        ]
    )

    written = tomllib.loads(config_path.read_text())
    assert written == {
        "host": "0.0.0.0",
        "port": 9999,
        "data_dir": "/tmp/y",
        "resting_hrv_profile_names": [],
        "athlete_timezone": "UTC",
    }


def test_api_init_creates_parent_directory(isolated_config_path):
    config_path = isolated_config_path
    assert not config_path.parent.exists()

    init_cmd.main(
        ["--host", "127.0.0.1", "--port", "8123", "--data-dir", "/tmp/x", "--athlete-timezone", "UTC"]
    )

    assert config_path.parent.exists()
    assert config_path.exists()


# ── T059: the resting-HRV declaration reaches the written config ────────────
#
# `resting_hrv_profile_names` is a required `AppConfig` field with no default
# (T054), so an `init` that does not write it produces a config `serve` refuses
# -- and the refusal tells the athlete to run `init`. These tests pin the two
# halves of that loop: the key is always written, and what `init` writes loads.


def test_api_init_with_no_profile_flag_writes_an_empty_list(isolated_config_path):
    """Zero flags is the explicit "Tier 2 only" declaration, not an absent key.

    `argparse`'s `action="append"` yields `None` when the flag never appears,
    and `tomli_w.dumps` writes nothing at all for a `None` value -- which is
    exactly the fresh-install loop this task closes. Asserting on the parsed
    dict by exact equality is what catches a missing key rather than a wrong
    one.
    """
    init_cmd.main(
        ["--host", "127.0.0.1", "--port", "8123", "--data-dir", "/tmp/x", "--athlete-timezone", "UTC"]
    )

    written = tomllib.loads(isolated_config_path.read_text())
    assert written["resting_hrv_profile_names"] == []
    assert written == {
        "host": "127.0.0.1",
        "port": 8123,
        "data_dir": "/tmp/x",
        "resting_hrv_profile_names": [],
        "athlete_timezone": "UTC",
    }


def test_api_init_with_one_profile_flag_writes_one_entry(isolated_config_path):
    init_cmd.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--data-dir",
            "/tmp/x",
            "--athlete-timezone",
            "UTC",
            "--resting-hrv-profile",
            "HRV Snapshot",
        ]
    )

    written = tomllib.loads(isolated_config_path.read_text())
    assert written["resting_hrv_profile_names"] == ["HRV Snapshot"]


def test_api_init_repeated_profile_flags_are_written_in_order(isolated_config_path):
    """Two flags write two entries, and the order given is the order written."""
    init_cmd.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--data-dir",
            "/tmp/x",
            "--athlete-timezone",
            "UTC",
            "--resting-hrv-profile",
            "HRV Snapshot",
            "--resting-hrv-profile",
            "Morning HRV",
        ]
    )

    written = tomllib.loads(isolated_config_path.read_text())
    assert written["resting_hrv_profile_names"] == ["HRV Snapshot", "Morning HRV"]


def test_api_init_config_round_trips_through_app_config(isolated_config_path, tmp_path, monkeypatch):
    """The loop-closure check at library level: what `init` writes, `serve` loads.

    `serve()` calls `load_config()`, which is the only thing between a fresh
    `api.toml` and a started server that can reject it. A `ValidationError`
    here is the fresh-install loop; its absence is the loop closed.
    """
    monkeypatch.delenv("RUNCOACH_ATHLETE_TIMEZONE", raising=False)
    init_cmd.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--data-dir",
            str(tmp_path / "data"),
            "--athlete-timezone",
            "UTC",
        ]
    )

    config = load_config(isolated_config_path)

    assert config.resting_hrv_profile_names == []
    assert config.host == "127.0.0.1"
    assert config.port == 8123
    assert config.athlete_timezone == "UTC"


def test_api_init_config_with_profiles_round_trips_through_app_config(
    isolated_config_path, tmp_path, monkeypatch
):
    """A populated list survives the TOML round trip into `AppConfig` too.

    `RUNCOACH_RESTING_HRV_PROFILE_NAMES` is deleted first: `AppConfig`'s env
    source outranks the TOML file, so a value in the ambient environment would
    make this assertion about the environment rather than about the file
    `init` just wrote.
    """
    monkeypatch.delenv("RUNCOACH_RESTING_HRV_PROFILE_NAMES", raising=False)
    monkeypatch.delenv("RUNCOACH_ATHLETE_TIMEZONE", raising=False)
    init_cmd.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--data-dir",
            str(tmp_path / "data"),
            "--athlete-timezone",
            "UTC",
            "--resting-hrv-profile",
            "HRV Snapshot",
            "--resting-hrv-profile",
            "Morning HRV",
        ]
    )

    config = load_config(isolated_config_path)

    assert config.resting_hrv_profile_names == ["HRV Snapshot", "Morning HRV"]


def test_api_init_writes_a_blank_profile_name_that_serve_then_rejects(
    isolated_config_path, monkeypatch
):
    """`init` does not validate entry content; `load_config` is the single gate.

    A blank `--resting-hrv-profile ""` is written verbatim, and T054's
    `_reject_blank_profile_names` refuses it on load. Pinned deliberately: one
    validator, in one place, is what stops `init` and `serve` drifting into two
    disagreeing notions of a valid name -- and the rejection names the field
    and says what to write, so it is diagnosable rather than a silent match
    against every file with no profile name.
    """
    monkeypatch.delenv("RUNCOACH_RESTING_HRV_PROFILE_NAMES", raising=False)
    monkeypatch.delenv("RUNCOACH_ATHLETE_TIMEZONE", raising=False)
    init_cmd.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--data-dir",
            "/tmp/x",
            "--athlete-timezone",
            "UTC",
            "--resting-hrv-profile",
            "",
        ]
    )

    written = tomllib.loads(isolated_config_path.read_text())
    assert written["resting_hrv_profile_names"] == [""]

    with pytest.raises(ValidationError) as exc_info:
        load_config(isolated_config_path)

    assert "resting_hrv_profile_names" in str(exc_info.value)


# ── T089 (F005): the athlete's timezone reaches the written config ──────────
#
# `athlete_timezone` is a required `AppConfig` field with no default (T088),
# for the same reason `resting_hrv_profile_names` is: a silent "UTC" would
# bucket a 06:00 capture at UTC+13 onto the previous day with no error. `init`
# mirrors the config: it asks for every field it writes, and it refuses a zone
# `serve` would refuse, *before* writing, so it can never produce a config that
# fails at startup. The zone check is T078's `validate_zone`, shared with
# `AppConfig` so `init` and `serve` cannot drift into two notions of "valid".
#
# `RUNCOACH_ATHLETE_TIMEZONE` is deleted wherever `load_config` is called:
# env outranks the file, so a developer with it exported would otherwise pass
# the round trip vacuously (the same shape as `test_cli_startup.py:299`).


def test_init_writes_athlete_timezone_and_serve_accepts_it(
    isolated_config_path, tmp_path, monkeypatch
):
    """What `init --athlete-timezone` writes, `load_config` accepts unchanged.

    The zone is deliberately not UTC: a round trip that asserts the value UTC
    cannot tell "the flag was written" from "something defaulted to UTC", and
    the no-defaults rule is the thing under test.
    """
    monkeypatch.delenv("RUNCOACH_ATHLETE_TIMEZONE", raising=False)
    monkeypatch.delenv("RUNCOACH_RESTING_HRV_PROFILE_NAMES", raising=False)
    init_cmd.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--data-dir",
            str(tmp_path / "data"),
            "--athlete-timezone",
            "Pacific/Auckland",
        ]
    )

    written = tomllib.loads(isolated_config_path.read_text())
    assert written["athlete_timezone"] == "Pacific/Auckland"

    config = load_config(isolated_config_path)

    assert config.athlete_timezone == "Pacific/Auckland"


def test_init_rejects_an_unknown_zone_before_writing(isolated_config_path, capsys):
    """An unresolvable zone exits non-zero, names the flag, and writes nothing.

    This is the "never write a config `serve` rejects" invariant: the
    rejection has to happen before the file exists, because a written-then-
    refused config is exactly the fresh-install loop `init` exists to close.
    """
    with pytest.raises(SystemExit) as exc_info:
        init_cmd.main(
            [
                "--host",
                "127.0.0.1",
                "--port",
                "8123",
                "--data-dir",
                "/tmp/x",
                "--athlete-timezone",
                "Mars/Phobos",
            ]
        )

    assert exc_info.value.code != 0
    assert not isolated_config_path.exists()
    assert not isolated_config_path.parent.exists(), "nothing is created for a refused config"
    err = capsys.readouterr().err
    assert "--athlete-timezone" in err
    assert "Mars/Phobos" in err
    # `validate_zone`'s own wording, not argparse's: this is what separates a
    # zone the shared validator refused from a flag argparse never knew about
    # (which also exits 2, names the flag, and writes nothing).
    assert "IANA" in err


def test_init_rejects_a_padded_zone_before_writing(isolated_config_path, capsys):
    """`"Pacific/Auckland "` resolves on NTFS (the trailing space is stripped
    by the filesystem) and not on Linux, so an `api.toml` written here would
    fail to load there (sprint-005 review, M3). `init` refuses it with the
    validator's wording and writes nothing."""
    with pytest.raises(SystemExit) as exc_info:
        init_cmd.main(
            [
                "--host",
                "127.0.0.1",
                "--port",
                "8123",
                "--data-dir",
                "/tmp/x",
                "--athlete-timezone",
                "Pacific/Auckland ",
            ]
        )

    assert exc_info.value.code != 0
    assert not isolated_config_path.exists()
    err = capsys.readouterr().err
    assert "--athlete-timezone" in err
    assert "not a recognised IANA time zone" in err


def test_init_with_force_and_an_unknown_zone_leaves_the_existing_config_untouched(
    isolated_config_path, capsys
):
    """`--force` authorises an overwrite, not an overwrite with a config `serve` rejects.

    The check on the zone precedes the overwrite decision, so a good config on
    disk is never replaced by a bad one -- the case where "writes nothing" is
    load-bearing rather than merely tidy.
    """
    init_cmd.main(
        ["--host", "127.0.0.1", "--port", "8123", "--data-dir", "/tmp/x", "--athlete-timezone", "UTC"]
    )
    before = isolated_config_path.read_text()

    with pytest.raises(SystemExit) as exc_info:
        init_cmd.main(
            [
                "--host",
                "0.0.0.0",
                "--port",
                "9999",
                "--data-dir",
                "/tmp/y",
                "--athlete-timezone",
                "Mars/Phobos",
                "--force",
            ]
        )

    assert exc_info.value.code != 0
    assert isolated_config_path.read_text() == before
    assert "Mars/Phobos" in capsys.readouterr().err


def test_init_without_the_flag_exits_2_and_names_it(isolated_config_path, capsys):
    """Omitting `--athlete-timezone` is an argparse error (exit 2) that names the flag.

    Required with no default, like every other field `init` writes: a default
    here would be the silently-absorbed "UTC" one layer earlier than the config
    rule forbids it.
    """
    with pytest.raises(SystemExit) as exc_info:
        init_cmd.main(["--host", "127.0.0.1", "--port", "8123", "--data-dir", "/tmp/x"])

    assert exc_info.value.code == 2
    assert "--athlete-timezone" in capsys.readouterr().err
    assert not isolated_config_path.exists()
