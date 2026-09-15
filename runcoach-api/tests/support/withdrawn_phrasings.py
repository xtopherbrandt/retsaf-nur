"""The withdrawn ``reset_reason`` phrasings, and the files scanned for them.

**This module is the one file allowed to contain a withdrawn phrasing**: it is
not listed in ``WITHDRAWN_SCAN_FILES``, so it is definitionally outside the
scan corpus, and it is the only file in the tree that is.

Why it exists (T114, review cycle 7, gaps G-C7-10 and G-C7-11). Until T114 the
declarations below lived in ``test_hrv_trend_endpoint.py`` -- the file that
scans for them -- and every consequence of that self-reference had to be
engineered around:

* the scan would have matched its own declarations, so the declaring literals
  were wrapped in marker comments and excised before scanning. Two generations
  of that fence were defeated by a marker pairing off with another copy of
  itself, the second time *by the commit that fixed the first* (T113 split one
  fenced region into two, after which deleting the first end marker left the
  fence balanced, the suite green and ~12 lines of live commentary silently
  unscanned);
* prose near the declarations could not quote a withdrawn phrasing without
  turning the scan red on itself, so glosses survived on markup asterisks or
  by index alone;
* the anchor proving the scan had read that file was itself one of the
  literals in this table, so a truncation dropped the live copy and passed on
  the configuration copy (measured: truncating the suite after line 1000 left
  it green).

Moving the declarations out removes all three at once. There is no fence in
the tree any more, nothing is excised from any scanned file, and
``test_hrv_trend_endpoint.py`` is scanned in full like every other file.

Nothing here is loaded by name: the workspace runs pytest with
``--import-mode=importlib``, under which nothing in ``tests/`` is importable by
name, so the scan loads this module from its path.
"""

from __future__ import annotations

from pathlib import Path

#: Phrasings withdrawn as false, which no copy may carry again, in the order
#: they are declared below:
#:
#: 1. T103's universal (a reported ``tier_change`` and an unclipped window
#:    can never sit together);
#: 2-3. T105's replacement universal, in the two spellings it was written in
#:    (the no-op stretch presented as something every report ends in, rather
#:    than as the conditional stretch it is);
#: 4-5. T109's two spellings of T105's false equivalence -- the gloss that
#:    equated report-liveness with clause (b), which lived in both contract
#:    copies, and the lifetime sentence that stated the same equivalence the
#:    other way round, which lived in the served copy alone and so was
#:    invisible to a pin that only compared the two copies' shared run.
#:
#: Entries 4 and 5 are false for the same reason: clause (b) is necessary for
#: the report, not sufficient. The one-shot ``! grep -q`` in those tasks' own
#: acceptance probes is the weak form ``sweep-the-claim-not-the-diff`` warns
#: about -- it never runs again. These do.
#:
#: The order is load-bearing: several docstrings in the scanning suite name an
#: entry by its index rather than restating it. ``WITHDRAWN_ORDER`` in
#: ``test_hrv_trend_endpoint.py`` pins index-to-entry by digest, so a reorder
#: or a mid-tuple insert goes red there (T114, G-C7-14).
RESET_REASON_WITHDRAWN = (
    "never sits beside an unclipped",
    "last stretch of every",
    "every report's lifetime",
    "only while the old tier still sustains",
    "is no longer sustained by the old tier",
)

#: The same equivalence in the idioms the *docstrings* use rather than the
#: contract's. The two survivors T111 found are entries 1 and 2 below -- a
#: "(b) fails" form and a "stops ... only when" form of the same sentence,
#: neither of which any contract copy would ever say -- so a tuple written
#: against contract prose could not have caught them even pointed at the
#: right files.
RESET_REASON_WITHDRAWN_IDIOMS = (
    "so that (b) fails",
    "the report stops only when",
    "the reset stops being reported when the previous window",
)

#: Every file that carries a *live* copy of the ``tier_change`` lifetime, in
#: whatever idiom that file uses. T109 withdrew the (b)-alone equivalence and
#: the two-copy oracle enforced it -- but only across the two description
#: strings. Two Stage 0 scanners then found the withdrawn claim still standing
#: in ``hrv_trend.py``'s ``tier_change_reset`` docstring, in the endpoint
#: suite's own ``test_a_reported_tier_change_sits_beside_the_unclipped_
#: window_...`` docstring, and in both spec documents. None of those is a
#: description string, so nothing could see them (T111, gap G-C7-1;
#: ``sweep-the-claim-not-the-diff``, fourth consecutive cycle on this feature).
#:
#: The two spec documents live under the machine-local ``.shipyard``
#: breadcrumb, which is gitignored, so they are scanned when it is present
#: and the row is skipped -- loudly -- when it is not.
#:
#: Paired with each file is its **positive control**: a live phrase that the
#: scanned text must contain. Every other assertion in the scan is negative,
#: so without this the scan is green over a file it never read -- an empty
#: read, or a path that stopped carrying the prose it is here for (T113, gap
#: G-C7-6). Each anchor is taken from the **last live paragraph** of its file,
#: and T114 measured where each one lands in that file's flattened text:
#: 99.1%, 99.4%, 99.8%, 99.6%, 99.6%, 99.9%, 99.7%, in the order below. That
#: measurement is the guarantee, and it is the whole of it: a truncation that
#: drops the tail past the anchor's offset takes the anchor with it and goes
#: red, while an edit confined to the last fraction of a percent after the
#: anchor is not something these controls can see.
_REPO_ROOT = Path(__file__).resolve().parents[3]
WITHDRAWN_SCAN_FILES = (
    (
        _REPO_ROOT / "runcoach-api" / "src" / "runcoach_api" / "metrics" / "hrv_trend.py",
        "the gap's clip is the later one and this one removes nothing",
    ),
    (
        _REPO_ROOT / "runcoach-api" / "src" / "runcoach_api" / "schemas.py",
        "that fed neither the baseline nor the window",
    ),
    (
        _REPO_ROOT / "contracts" / "openapi.yaml",
        "decision-record ids this reply is grounded in",
    ),
    (
        _REPO_ROOT / "runcoach-api" / "tests" / "test_hrv_trend_endpoint.py",
        "which is the parameter's problem and is named as such",
    ),
    (
        _REPO_ROOT / "runcoach-api" / "tests" / "test_hrv_trend_reset.py",
        "nothing else separates the two runs",
    ),
    (
        _REPO_ROOT / ".shipyard" / "spec" / "features" / "F005-resting-hrv-trend.md",
        "before a worktree-isolated builder can run the drift check",
    ),
    (
        _REPO_ROOT / ".shipyard" / "spec" / "references" / "F005-trend-construction.md",
        "not the absence of one string",
    ),
)
