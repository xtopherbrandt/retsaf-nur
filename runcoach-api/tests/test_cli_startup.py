"""Tests for runcoach_api.cli: serve() startup behavior.

Covers T004 (happy-path server start) and T014 (config-error
handling for the three typed failure modes raised by
``load_config``: missing file, corrupt TOML, and invalid field
value). Port-conflict handling (T005) is out of scope here.
"""

from __future__ import annotations

import errno
import socket
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from runcoach_api import cli
from runcoach_api.config import AppConfig, ConfigCorruptError, ConfigNotFoundError
from runcoach_api.config import load_config as real_load_config

# Bound here, at import time, on purpose. ``conftest.isolated_data_dir`` is
# autouse and does ``monkeypatch.setattr(db_module.config_module,
# "load_config", ...)`` -- and ``config_module`` *is* ``runcoach_api.config``,
# so for the duration of every test the attribute on that module is a stub
# returning a ready-made ``AppConfig``. A function-local
# ``from runcoach_api.config import load_config`` therefore resolves to the
# stub, and a test meaning to exercise the real loader silently exercises the
# fake one instead -- observed here as "DID NOT RAISE SystemExit" against an
# api.toml that plainly lacks a required field. This alias is captured before
# any fixture runs and is unaffected.


@dataclass
class _FakeConfig:
    host: str
    port: int


def _make_validation_error() -> ValidationError:
    """Build a real pydantic ValidationError with a 'port' field error.

    Deliberately incomplete in exactly one *other* way -- it never supplies
    ``resting_hrv_profile_names`` -- so the error also carries that field's
    ``missing`` (the shape table below explains why that pairing is the point).
    ``athlete_timezone`` (required since T088) *is* supplied, so the
    incompleteness stays singular and ``errors()[0]`` stays ``port``.
    """
    try:
        AppConfig(host="localhost", port=99999, data_dir=Path("/tmp"), athlete_timezone="UTC")
    except ValidationError as exc:
        return exc
    raise AssertionError("expected AppConfig(...) to raise ValidationError")


def test_serve_starts_uvicorn_with_configured_host_port(monkeypatch, capsys):
    fake_config = _FakeConfig(host="127.0.0.1", port=8123)
    monkeypatch.setattr(cli, "load_config", lambda: fake_config)

    calls = []

    def fake_run(app, host, port):
        calls.append({"app": app, "host": host, "port": port})

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.serve()

    assert len(calls) == 1
    assert calls[0]["app"] is cli.app
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8123

    captured = capsys.readouterr()
    assert "127.0.0.1" in captured.out
    assert "8123" in captured.out


def test_serve_missing_config_exits_nonzero_without_traceback(monkeypatch, capsys):
    def fake_load_config():
        raise ConfigNotFoundError(
            "No config file found at /home/user/.runcoach/api.toml."
        )

    monkeypatch.setattr(cli, "load_config", fake_load_config)

    calls = []
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda *a, **k: calls.append((a, k))
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.serve()

    assert exc_info.value.code == 1
    assert not calls

    captured = capsys.readouterr()
    assert "runcoach-api init" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_serve_invalid_config_exits_nonzero_names_port(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "load_config", lambda: (_ for _ in ()).throw(_make_validation_error())
    )

    calls = []
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda *a, **k: calls.append((a, k))
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.serve()

    assert exc_info.value.code == 1
    assert not calls

    captured = capsys.readouterr()
    assert "port" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_serve_corrupt_config_exits_nonzero_names_file_path(monkeypatch, capsys):
    bad_path = "/home/user/.runcoach/api.toml"

    def fake_load_config():
        raise ConfigCorruptError(
            f"Config file at {bad_path} could not be parsed as TOML: bad syntax"
        )

    monkeypatch.setattr(cli, "load_config", fake_load_config)

    calls = []
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda *a, **k: calls.append((a, k))
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.serve()

    assert exc_info.value.code == 1
    assert not calls

    captured = capsys.readouterr()
    assert bad_path in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_cli_port_in_use_exits_nonzero_without_traceback(monkeypatch, capsys):
    # Bind a real socket on a free port and keep it listening for the
    # duration of the test, so the port is *genuinely* in use. serve()'s
    # pre-flight probe (a plain socket bind attempted before uvicorn.run is
    # ever called) must detect this real conflict and exit cleanly --
    # uvicorn.run must never be reached.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]

        fake_config = _FakeConfig(host="127.0.0.1", port=port)
        monkeypatch.setattr(cli, "load_config", lambda: fake_config)

        calls = []
        monkeypatch.setattr(
            cli.uvicorn, "run", lambda *a, **k: calls.append((a, k))
        )

        with pytest.raises(SystemExit) as exc_info:
            cli.serve()

        assert exc_info.value.code == 1
        assert not calls

        captured = capsys.readouterr()
        assert str(port) in captured.err
        assert "already in use" in captured.err.lower()
        assert "Traceback" not in captured.err
        assert "Traceback" not in captured.out
    finally:
        sock.close()


def test_cli_other_oserror_from_uvicorn_run_propagates(monkeypatch, capsys):
    fake_config = _FakeConfig(host="127.0.0.1", port=8123)
    monkeypatch.setattr(cli, "load_config", lambda: fake_config)

    def fake_run(app, host, port):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    with pytest.raises(OSError) as exc_info:
        cli.serve()

    assert exc_info.value.errno == errno.EACCES


# ---------------------------------------------------------------------------
# T060 -- the missing-field remediation message.
#
# `resting_hrv_profile_names` is a *breaking* config change (F004's 2026-09-06
# amendment): every api.toml written before it fails startup. The validation
# error is the only upgrade path the athlete gets, and pydantic's own `msg` for
# a missing field is the bare "Field required", which names nothing to write.
#
# The error shapes below were enumerated against the vendored pydantic 2.13.5 /
# pydantic-core 2.46.5 under `.venv/` before these assertions were written, per
# `.claude/rules/project-testing.md` -- the discriminator is a real observation,
# not an assumption about what pydantic emits:
#
#   missing field alone      -> [0] type='missing'         loc=('resting_hrv_profile_names',)
#   missing + invalid port   -> [0] type='less_than_equal' loc=('port',)
#                               [1] type='missing'         loc=('resting_hrv_profile_names',)
#   invalid port alone       -> [0] type='less_than_equal' loc=('port',)
#   blank entry in the list  -> [0] type='value_error'     loc=('resting_hrv_profile_names',)
#   a bare string, not list  -> [0] type='list_type'       loc=('resting_hrv_profile_names',)
#   unknown key in api.toml  -> [0] type='extra_forbidden' loc=('nope',)
#   pre-F005 file (no zone)  -> [0] type='missing'         loc=('resting_hrv_profile_names',)
#                               [1] type='missing'         loc=('athlete_timezone',)
#
# The last row was added with T088 (F005), which made `athlete_timezone` required
# and declared it *after* `resting_hrv_profile_names` so a file missing both keeps
# receiving this remediation first (`test_config.py::
# test_port_precedes_the_remediation_bearing_fields` pins that order). Every
# construction in this module now supplies `athlete_timezone="UTC"` so each
# helper is incomplete in exactly the one way its docstring names, and the rows
# above stay the shapes the tests actually see.
#
# The second row is why the remediation is gated on `errors()[0]` rather than on
# "any error mentions the field": `_make_validation_error` above raises *both* a
# port error and the missing-field error, because the amendment made the field
# required and that helper never supplies it. An "any" rule would therefore
# attach upgrade instructions to a failure that has nothing to do with the new
# field -- and since the pre-existing test asserts only `"port" in captured.err`,
# it would have shipped green and silent.
# ---------------------------------------------------------------------------

_REMEDIATION_MARKERS = (
    "resting_hrv_profile_names = []",
    "RUNCOACH_RESTING_HRV_PROFILE_NAMES",
)


def _make_missing_field_error() -> ValidationError:
    """A real ValidationError whose *only* error is the missing new field.

    "Only" is enforced, not described: after F005 made ``athlete_timezone``
    required (T088), leaving it out here would bundle a second ``missing``
    error and the tests below would be reading ``errors()[0]`` of a shape
    they were never written for. Perturbation: drop the zone and the length
    assertion goes red.
    """
    try:
        AppConfig(host="localhost", port=8000, data_dir=Path("/tmp"), athlete_timezone="UTC")
    except ValidationError as exc:
        assert len(exc.errors()) == 1, exc.errors()
        return exc
    raise AssertionError("expected AppConfig(...) to raise ValidationError")


def _serve_expecting_exit(monkeypatch, loader) -> None:
    """Drive `serve()` with `loader` and assert a clean non-zero exit."""
    monkeypatch.setattr(cli, "load_config", loader)
    calls = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append((a, k)))

    with pytest.raises(SystemExit) as exc_info:
        cli.serve()

    assert exc_info.value.code == 1
    assert not calls


def _raising(error: ValidationError):
    return lambda: (_ for _ in ()).throw(error)


def test_serve_missing_resting_hrv_profile_names_states_what_to_write(
    monkeypatch, capsys
):
    _serve_expecting_exit(monkeypatch, _raising(_make_missing_field_error()))

    captured = capsys.readouterr()
    err = captured.err

    # Names the field.
    assert "resting_hrv_profile_names" in err
    # Gives a literal the athlete can paste into api.toml verbatim.
    assert "resting_hrv_profile_names = []" in err
    # Names the environment-variable alternative, and that its value is parsed
    # as JSON -- a bare `RUNCOACH_RESTING_HRV_PROFILE_NAMES=HRV Snapshot` does
    # not parse at all.
    assert "RUNCOACH_RESTING_HRV_PROFILE_NAMES" in err
    assert "JSON" in err
    # `db._load_config_cached` is lru_cache(maxsize=1), so an api.toml edit
    # takes effect only on the next start.
    assert "restart" in err.lower()
    # Says what to write; never suggests the system will assume anything.
    assert "no default" in err.lower()
    assert "Traceback" not in err
    assert "Traceback" not in captured.out


def test_serve_missing_field_message_comes_from_a_real_pre_amendment_toml(
    monkeypatch, capsys, tmp_path
):
    """The same message, driven through the real `load_config` on a real file.

    `_make_missing_field_error` builds the error from a constructor call; this
    pins that a genuine pre-amendment `api.toml` on disk produces the identical
    `errors()[0]` shape, rather than trusting the stand-in. The env var is
    cleared first: it overrides the TOML file, so an ambient value would make
    the config load cleanly and quietly delete this test's premise.

    `real_load_config` is the module-level alias, not a fresh import -- see the
    note beside it for why importing it here would reach `conftest`'s stub.
    """
    monkeypatch.delenv("RUNCOACH_RESTING_HRV_PROFILE_NAMES", raising=False)
    monkeypatch.delenv("RUNCOACH_ATHLETE_TIMEZONE", raising=False)
    api_toml = tmp_path / "api.toml"
    # `athlete_timezone` is written: this file is missing *only* the field
    # whose remediation is under test, so the message is driven by that gap
    # and not by the field-declaration order (T081 removes that dependence).
    api_toml.write_text(
        'host = "127.0.0.1"\n'
        "port = 8000\n"
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        'athlete_timezone = "UTC"\n',
        encoding="utf-8",
    )

    _serve_expecting_exit(monkeypatch, lambda: real_load_config(api_toml))

    captured = capsys.readouterr()
    assert "resting_hrv_profile_names = []" in captured.err
    # The bare pydantic message is replaced, not merely decorated.
    assert "Field required" not in captured.err
    assert "Traceback" not in captured.err


def test_serve_unrelated_validation_error_carries_no_resting_hrv_remediation(
    monkeypatch, capsys
):
    """The gotcha this task exists to close.

    `_make_validation_error()` raises an invalid `port` *and* the missing
    `resting_hrv_profile_names` together. The error actually being reported is
    the port one, so the upgrade instructions must not ride along with it.
    """
    error = _make_validation_error()
    # Guard the premise. Without these, a future change that stops bundling the
    # two errors would leave this test passing while testing nothing.
    assert error.errors()[0]["loc"] == ("port",)
    assert any(
        e["type"] == "missing" and e["loc"] == ("resting_hrv_profile_names",)
        for e in error.errors()
    ), "premise: the helper's error also carries the missing new field"

    _serve_expecting_exit(monkeypatch, _raising(error))

    captured = capsys.readouterr()
    assert "port" in captured.err
    for marker in _REMEDIATION_MARKERS:
        assert marker not in captured.err
    assert "Traceback" not in captured.err


def test_serve_blank_profile_name_renders_the_validators_own_message(
    monkeypatch, capsys
):
    """A blank entry is `type='value_error'`, not `'missing'`.

    T054's field validator already writes an actionable message; the generic
    rendering path must carry it through unchanged rather than replace it with
    the missing-field upgrade text, which would be wrong advice -- the field is
    present, and `[]` is not what this athlete meant to write.
    """
    try:
        AppConfig(
            host="localhost",
            port=8000,
            data_dir=Path("/tmp"),
            resting_hrv_profile_names=["  "],
            athlete_timezone="UTC",
        )
    except ValidationError as exc:
        error = exc
    else:
        raise AssertionError("expected a blank profile name to be rejected")

    assert len(error.errors()) == 1, error.errors()
    assert error.errors()[0]["type"] == "value_error"

    _serve_expecting_exit(monkeypatch, _raising(error))

    captured = capsys.readouterr()
    assert "resting_hrv_profile_names" in captured.err
    assert "is blank" in captured.err
    for marker in _REMEDIATION_MARKERS:
        assert marker not in captured.err
    assert "Traceback" not in captured.err


def test_serve_wrong_type_for_profile_names_renders_generically(monkeypatch, capsys):
    """`resting_hrv_profile_names = "HRV Snapshot"` is `type='list_type'`.

    It names the right field but is not the missing-field case, so it takes the
    generic path too: the discriminator is the error *type* and the reported
    position, never the field name on its own.
    """
    try:
        AppConfig(
            host="localhost",
            port=8000,
            data_dir=Path("/tmp"),
            resting_hrv_profile_names="HRV Snapshot",
            athlete_timezone="UTC",
        )
    except ValidationError as exc:
        error = exc
    else:
        raise AssertionError("expected a bare string to be rejected")

    assert len(error.errors()) == 1, error.errors()
    assert error.errors()[0]["type"] == "list_type"

    _serve_expecting_exit(monkeypatch, _raising(error))

    captured = capsys.readouterr()
    assert "resting_hrv_profile_names" in captured.err
    assert "valid list" in captured.err
    for marker in _REMEDIATION_MARKERS:
        assert marker not in captured.err


def test_cli_startup_init_help_documents_the_resting_hrv_profile_flag():
    """`build_parser()`'s `init` entry lists the subcommand's own flags.

    T059 adds the repeatable `--resting-hrv-profile` flag to `init_cmd`;
    `cli.py` owns the help line that advertises it, and a fresh install that
    hits the missing-field error reaches `init` through exactly this text.
    """
    help_text = cli.build_parser().format_help()
    assert "--resting-hrv-profile" in help_text
