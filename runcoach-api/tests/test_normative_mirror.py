"""The committed mirror of the normative feature documents, and the gate that
keeps it a copy rather than a second source of truth (T124).

Two F005 documents -- the feature file and the construction reference -- lived
only under the gitignored, machine-local ``.shipyard`` breadcrumb, so the
corpus phrasing scan in ``test_hrv_trend_endpoint.py`` and the AST oracle in
``test_hrv_unavailable_causes.py`` read them on the author's laptop and
nowhere else: not in a worktree, not on a CI runner. Those two documents are
the ones whose mutual drift produced G-C6-4 and G-C6-7. F006 then added two
more to the same unreachable place.

Moving them is not available: the Shipyard pipelines read the feature file
from the data dir by a fixed path. So the tree carries **copies**, under
``spec-mirror/``, laid out exactly as ``spec/`` is laid out in the data dir,
and this module is what makes a copy safe:

* ``test_every_mirrored_copy_is_byte_equal_to_its_original`` -- a copy that
  has drifted from its original fails, in either direction.
* ``test_every_document_of_a_mirrored_feature_has_a_committed_copy`` -- a
  document the data dir holds for a mirrored feature and the tree does not
  is a failure, so a document that does not exist yet (T161's
  ``F006-sweep-findings.md``, T162's ``F006-no-regression-report.md``) is
  covered the day it is written. The population is walked, not listed.

**What the gate does when the data dir is unreachable, and why.** Both drift
tests ``pytest.skip`` -- loudly, naming the two ways to make the corpus
reachable -- and that is a different decision from the one the scan makes.
The scan must not skip: its job is to read the documents, and after T124 the
committed copies are where it reads them, on every machine. The drift gate's
job is to compare the copies against originals that exist only where Shipyard
runs, and a comparison against a corpus it cannot see is not a comparison; a
green there would be the vacuous pass this project keeps finding. The skip is
acceptable **because drift can only be introduced where the originals are**:
the pipelines edit the data dir, and every machine that has the data dir has
the gate. A checkout without the data dir cannot desynchronise the pair, so
the gate is absent exactly where it has nothing to catch. What it cannot see
is a copy edited by hand in a checkout with no data dir; the scan's anchors
and the next run on the author machine are what catch that.

The data dir is found through ``SHIPYARD_DATA_DIR`` when set (a worktree or a
runner that wants the gate), else through the ``.shipyard`` breadcrumb.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Where the copies live. Its two subdirectories mirror ``spec/features`` and
#: ``spec/references`` in the data dir by relative path, so a copy's original
#: is ``<data dir>/spec/<path relative to the mirror>`` with no table in
#: between.
MIRROR_ROOT = _REPO_ROOT / "spec-mirror"

#: The subdirectories the mirror may hold, by the data-dir directory each
#: mirrors. Anything else under ``MIRROR_ROOT`` but its README is a file the
#: gate cannot map to an original and is refused.
MIRRORED_DIRS = ("features", "references")

#: The one file in the mirror that is not a copy: it explains the directory
#: to a reader who opened it from the tree.
MIRROR_README = "README.md"

#: The environment variable that names the data dir where the ``.shipyard``
#: breadcrumb is absent -- a worktree, a runner.
DATA_DIR_ENV = "SHIPYARD_DATA_DIR"


def _data_dir() -> Path | None:
    """The Shipyard data dir, or None where neither route reaches one."""
    named = os.environ.get(DATA_DIR_ENV)
    candidates = [Path(named)] if named else []
    candidates.append(_REPO_ROOT / ".shipyard")
    for candidate in candidates:
        if (candidate / "spec" / "features").is_dir():
            return candidate
    return None


def _data_dir_or_skip() -> Path:
    data_dir = _data_dir()
    if data_dir is None:
        pytest.skip(
            f"the Shipyard data dir is unreachable: neither ${DATA_DIR_ENV} nor "
            f"{_REPO_ROOT / '.shipyard'} names a directory with spec/features under it, so "
            f"there is no original to compare the committed copies against (see the module "
            f"docstring for why this is a skip and not a pass)"
        )
    return data_dir


def _mirrored_copies() -> tuple[Path, ...]:
    """Every copy under the mirror, by walking it."""
    return tuple(
        sorted(path for sub in MIRRORED_DIRS for path in (MIRROR_ROOT / sub).rglob("*") if path.is_file())
    )


def _feature_ids() -> tuple[str, ...]:
    """The feature ids whose documents the mirror carries: the prefix of each
    file under ``features/``. Read off the tree, not typed."""
    return tuple(sorted({path.name.split("-", 1)[0] for path in (MIRROR_ROOT / "features").glob("*.md")}))


def test_the_mirror_holds_the_documents_this_task_copied_and_nothing_unmapped() -> None:
    """The positive control, needing no data dir: the mirror is not empty and
    holds the four documents T124 copied, so the reachability the scan now
    relies on is a property of the checkout. Every file under the mirror is
    under one of the two mirrored subdirectories (or is the README), because
    a file anywhere else has no original the gate could compare it to."""
    assert MIRROR_ROOT.is_dir(), f"{MIRROR_ROOT} is not in this checkout: the scan reads the documents nowhere"
    copies = {path.relative_to(MIRROR_ROOT).as_posix() for path in _mirrored_copies()}
    for required in (
        "features/F005-resting-hrv-trend.md",
        "features/F006-per-tier-hrv-datasets.md",
        "references/F005-trend-construction.md",
        "references/F006-dataset-model.md",
    ):
        assert required in copies, f"spec-mirror/{required} is missing: T124 copied it and the scan reads it there"
    stray = sorted(
        path.relative_to(MIRROR_ROOT).as_posix()
        for path in MIRROR_ROOT.rglob("*")
        if path.is_file() and path.name != MIRROR_README and path.relative_to(MIRROR_ROOT).parts[0] not in MIRRORED_DIRS
    )
    assert not stray, f"files in the mirror outside {MIRRORED_DIRS}, which no original maps to: {stray}"
    assert (MIRROR_ROOT / MIRROR_README).is_file(), "the mirror's README is what tells a reader these are copies"


def test_every_mirrored_copy_is_byte_equal_to_its_original() -> None:
    """The drift gate, direction one: every copy under the mirror has an
    original at the same relative path under the data dir's ``spec/``, and
    the two are byte-for-byte equal. Byte equality rather than flattened-text
    equality because the copy is what the scan reads: a difference the
    flattening would hide is a difference the scan would see.

    Every drifted document is collected before the assertion so one run names
    all of them; the compared slice is printed so the witness shows what it
    compared, not only that it agreed."""
    data_dir = _data_dir_or_skip()
    copies = _mirrored_copies()
    assert copies, f"nothing under {MIRROR_ROOT}: the gate compared no file"
    drifted: list[str] = []
    for copy in copies:
        rel = copy.relative_to(MIRROR_ROOT).as_posix()
        original = data_dir / "spec" / rel
        if not original.is_file():
            drifted.append(f"{rel}: no original at {original} (a copy with no original is an orphan)")
            continue
        same = copy.read_bytes() == original.read_bytes()
        print(f"compared spec-mirror/{rel} <-> {original}: {'equal' if same else 'DRIFTED'}")
        if not same:
            drifted.append(f"{rel}: the committed copy differs from {original}")
    assert not drifted, (
        f"{len(drifted)} of {len(copies)} committed copies have drifted from the data dir -- recopy "
        f"them (the data dir is the source of truth; the copy is what the scan reads): " + "; ".join(drifted)
    )


def test_every_document_of_a_mirrored_feature_has_a_committed_copy() -> None:
    """The drift gate, direction two: a document the data dir holds for a
    mirrored feature and the tree does not is a failure, so a new document in
    the same place is covered the day it appears.

    The population is ``<id>-*.md`` under ``spec/features`` and
    ``spec/references`` for every feature id the mirror already carries --
    a walk keyed on the prefix, not a list of names, because a four-name
    allowlist would re-create the reachability hole inside the sprint that
    closes it (T161 and T162 both write to ``spec/references``). What the
    prefix does not reach, stated rather than hidden: a reference filed under
    a task id (``T125-…``, ``T130-…``) is not mirrored by this rule."""
    data_dir = _data_dir_or_skip()
    feature_ids = _feature_ids()
    assert feature_ids, f"no feature file under {MIRROR_ROOT / 'features'}: the rule has no prefix to walk on"
    missing: list[str] = []
    compared = 0
    for feature_id in feature_ids:
        for sub in MIRRORED_DIRS:
            for original in sorted((data_dir / "spec" / sub).glob(f"{feature_id}-*.md")):
                compared += 1
                copy = MIRROR_ROOT / sub / original.name
                print(f"{original} -> spec-mirror/{sub}/{original.name}: {'present' if copy.is_file() else 'MISSING'}")
                if not copy.is_file():
                    missing.append(f"spec/{sub}/{original.name}")
    assert compared >= 4, f"the walk found {compared} documents for {feature_ids}: it has stopped reaching the data dir"
    assert not missing, (
        f"documents in the data dir for mirrored features {feature_ids} with no committed copy, so the "
        f"scan reads them only where the breadcrumb is: " + ", ".join(missing)
    )
