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


def _validation_error(**overrides) -> ValidationError:
    """The real ``ValidationError`` ``AppConfig`` raises for a valid ``host`` /
    ``port`` / ``data_dir`` plus ``overrides``, so each caller states only the
    one way its config is broken. A config that loads is a failed premise,
    not a passing test, hence the ``AssertionError``.
    """
    fields = {"host": "localhost", "port": 8000, "data_dir": Path("/tmp"), **overrides}
    try:
        AppConfig(**fields)
    except ValidationError as exc:
        return exc
    raise AssertionError("expected AppConfig(...) to raise ValidationError")


def _make_validation_error() -> ValidationError:
    """Build a real pydantic ValidationError with a 'port' field error.

    Deliberately incomplete in exactly one *other* way -- it never supplies
    ``resting_hrv_profile_names`` -- so the error also carries that field's
    ``missing`` (the shape table below explains why that pairing is the point).
    ``athlete_timezone`` (required since T088) *is* supplied, so the
    incompleteness stays singular and ``errors()[0]`` stays ``port``.
    """
    return _validation_error(port=99999, athlete_timezone="UTC")


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
# and declared it *after* `resting_hrv_profile_names`; since T081 a file missing
# both receives *both* remediations, in whichever order pydantic lists them (the
# T081 block at the end of this module pins that). Every construction in this
# module supplies `athlete_timezone="UTC"` so each helper is incomplete in
# exactly the one way its docstring names, and the rows above stay the shapes
# the tests actually see.
#
# The second row is why the remediation *mode* is gated on `errors()[0]` rather
# than on "any error mentions the field": `_make_validation_error` above raises
# *both* a port error and the missing-field error, because the amendment made
# the field required and that helper never supplies it. An "any" rule would
# therefore attach upgrade instructions to a failure that has nothing to do with
# the new field -- and since the pre-existing test asserts only `"port" in
# captured.err`, it would have shipped green and silent. `port` precedes both
# remediation-bearing fields in `AppConfig` for exactly this reason
# (`test_config.py::test_port_precedes_the_remediation_bearing_fields`).
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
    error = _validation_error(athlete_timezone="UTC")
    assert len(error.errors()) == 1, error.errors()
    return error


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


def test_cli_startup_init_help_documents_the_athlete_timezone_flag():
    """`build_parser()`'s `init` entry lists the subcommand's own flags.

    T089 adds the required `--athlete-timezone` flag to `init_cmd`; `cli.py`
    owns the help line that advertises it, and the T090 migration note points
    the athlete at `init --athlete-timezone` through exactly this text.
    """
    help_text = cli.build_parser().format_help()
    assert "--athlete-timezone" in help_text


# ---------------------------------------------------------------------------
# T081 -- order-independent remediation dispatch.
#
# F005 (T088) made `athlete_timezone` a second required field with remediation
# text of its own. With two such fields there is no declaration order that is
# right for every broken `api.toml`: a file missing both must be told about
# both, and a file missing only the zone must get the zone's one-line fix rather
# than the bare "Field required". The tests below drive `serve()` with real
# `ValidationError`s (constructed, or built through `from_exception_data` where
# the order of `errors()` is the thing under test) and pin that the rendered
# message depends on *which* errors are present, never on their position --
# while the terse fallback for an ordinary error (`port`) stays exactly as the
# T060 tests above pin it.
# ---------------------------------------------------------------------------

_TIMEZONE_REMEDIATION_MARKERS = (
    'athlete_timezone = "',
    "RUNCOACH_ATHLETE_TIMEZONE",
)


def _make_missing_both_fields_error() -> ValidationError:
    """A real ValidationError carrying exactly the two `missing` errors.

    The shape is asserted, not assumed: `port` is valid here so nothing
    precedes the two remediation-bearing fields, and the order is the
    declaration order (`resting_hrv_profile_names` first).
    """
    error = _validation_error()
    assert [(e["type"], e["loc"]) for e in error.errors()] == [
        ("missing", ("resting_hrv_profile_names",)),
        ("missing", ("athlete_timezone",)),
    ], error.errors()
    return error


def _assert_both_remediations(err: str) -> None:
    for marker in _REMEDIATION_MARKERS + _TIMEZONE_REMEDIATION_MARKERS:
        assert marker in err, marker
    # Both blocks replace pydantic's message rather than decorating it.
    assert "Field required" not in err
    assert "Traceback" not in err


def test_remediation_dispatch_names_both_missing_fields(monkeypatch, capsys):
    """A pre-F005 `api.toml` -- missing both fields -- is told about both."""
    _serve_expecting_exit(monkeypatch, _raising(_make_missing_both_fields_error()))

    _assert_both_remediations(capsys.readouterr().err)


def test_remediation_dispatch_ignores_declaration_order(monkeypatch, capsys):
    """The same two errors in the opposite order render the same two blocks.

    `ValidationError.from_exception_data` (pydantic-core 2.46.5,
    `_pydantic_core.pyi:666`) builds a real error whose `errors()` order is
    the list order given, which is the only way to present `athlete_timezone`
    first without reordering `AppConfig`'s fields.
    """
    reversed_error = ValidationError.from_exception_data(
        "AppConfig",
        [
            {"type": "missing", "loc": ("athlete_timezone",), "input": {}},
            {"type": "missing", "loc": ("resting_hrv_profile_names",), "input": {}},
        ],
    )
    assert [e["loc"] for e in reversed_error.errors()] == [
        ("athlete_timezone",),
        ("resting_hrv_profile_names",),
    ]

    _serve_expecting_exit(monkeypatch, _raising(reversed_error))

    _assert_both_remediations(capsys.readouterr().err)


def test_remediation_dispatch_missing_timezone_alone_shows_the_one_line_fix(
    monkeypatch, capsys, tmp_path
):
    """F005 @must: "a missing timezone is a startup error ... the message shows
    the one-line fix". Driven through the real `load_config` on a real file
    that lacks *only* `athlete_timezone`, with the env override cleared so an
    exported zone cannot make the file load and delete the premise.
    """
    monkeypatch.delenv("RUNCOACH_RESTING_HRV_PROFILE_NAMES", raising=False)
    monkeypatch.delenv("RUNCOACH_ATHLETE_TIMEZONE", raising=False)
    api_toml = tmp_path / "api.toml"
    api_toml.write_text(
        'host = "127.0.0.1"\n'
        "port = 8000\n"
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        "resting_hrv_profile_names = []\n",
        encoding="utf-8",
    )

    _serve_expecting_exit(monkeypatch, lambda: real_load_config(api_toml))

    err = capsys.readouterr().err
    assert "athlete_timezone" in err
    for marker in _TIMEZONE_REMEDIATION_MARKERS:
        assert marker in err, marker
    # A zone the athlete can paste, in the tz database's own naming.
    assert "Pacific/Auckland" in err
    assert "no default" in err.lower()
    assert "restart" in err.lower()
    assert "Field required" not in err
    # The profile-names field is present, so its remediation must not appear.
    for marker in _REMEDIATION_MARKERS:
        assert marker not in err, marker
    assert "Traceback" not in err


def test_remediation_dispatch_port_error_still_wins_over_both_missing_fields(
    monkeypatch, capsys
):
    """The terse fallback is unchanged: a `port` complaint carries no
    remediation even when both remediation-bearing fields are missing too.

    This is the T060 gotcha (`:347`) with the second field added -- the
    population beside that test's assertion.
    """
    error = _validation_error(port=99999)
    assert [e["loc"] for e in error.errors()] == [
        ("port",),
        ("resting_hrv_profile_names",),
        ("athlete_timezone",),
    ], error.errors()

    _serve_expecting_exit(monkeypatch, _raising(error))

    err = capsys.readouterr().err
    assert "port" in err
    for marker in _REMEDIATION_MARKERS + _TIMEZONE_REMEDIATION_MARKERS:
        assert marker not in err, marker
    assert "Traceback" not in err


def test_remediation_dispatch_bad_zone_renders_the_validators_own_message(
    monkeypatch, capsys
):
    """`athlete_timezone = "Mars/Phobos"` is `type='value_error'`, not `'missing'`.

    The dispatch is keyed on the error type as well as the field: the zone is
    present and wrong, `validate_zone`'s message already says so, and the
    missing-field text would be the wrong advice.
    """
    error = _validation_error(resting_hrv_profile_names=[], athlete_timezone="Mars/Phobos")
    assert len(error.errors()) == 1, error.errors()
    assert error.errors()[0]["type"] == "value_error"

    _serve_expecting_exit(monkeypatch, _raising(error))

    err = capsys.readouterr().err
    assert "athlete_timezone" in err
    assert "not a recognised IANA time zone" in err
    for marker in _TIMEZONE_REMEDIATION_MARKERS:
        assert marker not in err, marker
    assert "Traceback" not in err


def test_remediation_dispatch_profile_names_help_names_the_flag_init_now_requires(
    monkeypatch, capsys
):
    """The profile-names block points at `runcoach-api init`; since T089 that
    command exits 2 without `--athlete-timezone`, so the advice must name it.
    """
    _serve_expecting_exit(monkeypatch, _raising(_make_missing_field_error()))

    err = capsys.readouterr().err
    assert "runcoach-api init" in err
    assert "--athlete-timezone" in err
