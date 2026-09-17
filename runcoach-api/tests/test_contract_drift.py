"""Scenario 33 (``@must``), pinned: the as-built API matches the contract.

T136 (F005 review cycle 9, gap G-C9-6). ``contracts/check_drift.py`` compares
``contracts/openapi.yaml``'s implemented operations against ``app.openapi()``
and reports no drift **at 2f3ad06, confirmed by running it** -- but nothing
pytest runs calls it. ``test_ci_workflows.py`` pins that the CI workflow file
*invokes* the script; it says nothing about the script's own verdict. The
feature's demo probe runs it too, by hand. Both are outside a local ``pytest``
run, so a reviewer -- or an agent -- who takes a green ``pytest`` as evidence
that the contract hasn't drifted is trusting something nothing just checked.

This module closes that by calling the checker's own functions directly, in
the same process pytest is already running in.

**Why import-by-path, not ``import contracts.check_drift``.** ``contracts/``
carries no ``__init__.py`` and is not on ``sys.path`` by declaration -- only
by whatever pytest's import mode happens to seed for this invocation, which
is a property of *how the suite was launched*, not of the file. Loading it
by absolute path (``importlib.util.spec_from_file_location``) is independent
of both the working directory and the import-mode flag in the root
``pyproject.toml``.

**Why not a subprocess.** A subprocess that fails to start -- a missing
interpreter, a bad ``--package`` flag, a ``PYTHONPATH`` that doesn't reach
``runcoach_api`` -- exits nonzero for a reason that has nothing to do with
contract drift, and a careless ``assert result.returncode == 0`` cannot tell
the two apart. Calling ``check()`` directly and reading its actual return
value can only be silent if there truly is nothing to report.

**What a green result here proves, and what it does not ([[IDEA-061]]).**
``check()`` compares, for every operation the contract marks ``x-readiness:
implemented``: the path exists in the as-built API, the method exists, every
2xx status the contract promises is still served, and every multipart
property and required query parameter the contract names is still accepted.
It does **not** compare descriptions, enums, response types, or full response
schema shape -- ``check_drift.py``'s own docstring says the contract is
deliberately allowed to run ahead of the backend's response typing there, and
those gaps surface only as advisory *notes*, never as failures. This module
asserts exactly what ``check()`` asserts, and its docstrings say so at every
level so a reader skimming a green summary here does not read into it a
schema guarantee the underlying check never made.
"""

from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECK_DRIFT_PATH = _REPO_ROOT / "contracts" / "check_drift.py"
_TARGET_PATH = _REPO_ROOT / "contracts" / "openapi.yaml"


def _load_check_drift() -> types.ModuleType:
    """Import ``contracts/check_drift.py`` in-process, by absolute file path."""
    assert _CHECK_DRIFT_PATH.exists(), (
        f"{_CHECK_DRIFT_PATH} is missing: contracts/check_drift.py is committed and this "
        f"oracle has nothing to import"
    )
    spec = importlib.util.spec_from_file_location("_check_drift_under_test", _CHECK_DRIFT_PATH)
    assert spec is not None and spec.loader is not None, (
        f"could not build an import spec for {_CHECK_DRIFT_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def check_drift() -> types.ModuleType:
    return _load_check_drift()


def test_check_drift_module_imports_cleanly_in_process(check_drift: types.ModuleType) -> None:
    """The deliverable's own claim -- "already importable" -- checked on its
    own, separately from the drift assertion below, so an import failure and
    an actual drift finding never read as the same red to someone skimming
    the summary."""
    for name in ("load_target", "load_as_built", "check", "implemented_operations"):
        assert hasattr(check_drift, name), (
            f"contracts/check_drift.py no longer exposes {name}(): the call below would be "
            f"calling nothing, and any drift it happened to miss would look identical to a "
            f"clean run"
        )


def test_check_drift_actually_detects_a_broken_contract(check_drift: types.ModuleType) -> None:
    """A check authored by the thing it checks is not a check
    (``contract-tables-need-an-independent-oracle``): before trusting
    ``check()``'s "no drift" on the real contract below, prove it can say
    "drift" at all, on a synthetic pair this test builds itself rather than
    on any file ``check_drift.py`` or this suite already reads.

    Three independent breaks, each on its own minimal target/built pair, so
    one broken code path in ``check()`` cannot hide behind another still
    working: a path/method missing from the as-built API, a promised success
    status no longer served, and a required query parameter the as-built
    route no longer accepts."""
    base_target = {
        "paths": {
            "/widgets": {
                "get": {
                    "x-readiness": "implemented",
                    "responses": {"200": {}},
                    "parameters": [{"name": "since", "in": "query", "required": True}],
                }
            }
        }
    }
    matching_built = {
        "paths": {
            "/widgets": {
                "get": {
                    "responses": {"200": {}},
                    "parameters": [{"name": "since", "in": "query", "required": True}],
                }
            }
        }
    }
    failures, _notes = check_drift.check(base_target, matching_built, verbose=False)
    assert failures == [], (
        f"the synthetic matching pair itself reads as drifted: {failures} -- this test's "
        f"positive control is broken, not the checker"
    )

    missing_path_built: dict = {"paths": {}}
    failures, _notes = check_drift.check(base_target, missing_path_built, verbose=False)
    assert failures, "check() reported no drift when the as-built API is missing the path entirely"

    missing_status_built = {
        "paths": {
            "/widgets": {
                "get": {
                    "responses": {"404": {}},
                    "parameters": [{"name": "since", "in": "query", "required": True}],
                }
            }
        }
    }
    failures, _notes = check_drift.check(base_target, missing_status_built, verbose=False)
    assert failures, "check() reported no drift when the as-built API dropped the promised 200"

    missing_query_built = {
        "paths": {
            "/widgets": {
                "get": {
                    "responses": {"200": {}},
                    "parameters": [],
                }
            }
        }
    }
    failures, _notes = check_drift.check(base_target, missing_query_built, verbose=False)
    assert failures, "check() reported no drift when the as-built API dropped a required query param"


def test_the_as_built_endpoint_drift_is_pinned_by_pytest(check_drift: types.ModuleType) -> None:
    """Scenario 33, enforced: the real ``contracts/openapi.yaml`` against
    ``runcoach_api.main:app``'s own ``app.openapi()``, generated in-process --
    exactly what ``uv run python contracts/check_drift.py`` does at the
    command line, called directly here instead.

    ``implemented`` is asserted non-empty before ``check()`` runs so a green
    result over zero checked operations -- ``check()``'s own first failure
    case, restated here as an independent read of the same list -- cannot be
    the reason this passes."""
    target = check_drift.load_target(_TARGET_PATH)
    built = check_drift.load_as_built(None)  # None => import runcoach_api.main:app in-process

    implemented = check_drift.implemented_operations(target)
    assert implemented, (
        f"{_TARGET_PATH} marks no operation x-readiness: implemented -- a green drift result "
        f"over zero checked operations is a report over nothing"
    )

    failures, _notes = check_drift.check(target, built, verbose=False)
    assert failures == [], (
        f"contracts/check_drift.py reports drift between {_TARGET_PATH.name} and the as-built "
        f"API: {failures}"
    )
