"""T034 items 2 & 3: ``sport`` mapping edge cases.

- Item 2: a FIT file with neither a ``session`` nor a ``sport``
  message (a watch that died mid-activity) must not reach
  ``db.persist`` with ``sport=None`` and surface as an unhandled 500 --
  ``mapping.to_canonical`` raises a typed
  ``MissingCanonicalFieldError`` before a ``Session`` is even
  constructed.
- Item 3: a raw FIT ``sport`` enum integer fitdecode's profile has no
  name for (e.g. ``60``, confirmed via a one-off ``fitdecode``
  inspection of ``tests/fixtures/sample_health_snapshot.fit`` --
  both its ``session`` and ``sport`` messages carry the bare int
  ``60``) must not leak into the canonical ``sport`` field, which
  spec/references/F003-canonical-schema.md §2.2.1 defines as the enum
  ``running`` / ``other``. It maps to ``"other"``, with the raw value
  preserved in ``context.provenance`` rather than silently dropped.

T056 extends the module to the other sport-shaped session field:
``sport_profile_name``, the athlete-typed activity-profile name, is
lifted into ``context.provenance`` beside ``raw_sport_value``. That lift
changes **no routing** -- nothing reads the key until T063 -- so every
assertion here is about the value arriving verbatim, or about the key
being absent when the file carried nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import fitdecode
import pytest

from runcoach_api.ingestion import fit_parser, mapping
from runcoach_api.ingestion.exceptions import MissingCanonicalFieldError

FIXTURES = Path(__file__).parent / "fixtures"
STRESS_FIXTURE = FIXTURES / "sample_health_snapshot.fit"


class _FakeMsg:
    """Minimal stand-in for a ``fitdecode.FitDataMessage``.

    Only implements what ``to_canonical``/``_build_record`` actually
    read: ``.name``, ``get_value(name, fallback=None)``, and
    ``.fields`` (iterated for developer-field data, empty here).
    """

    def __init__(self, name: str, values: dict) -> None:
        self.name = name
        self._values = values
        self.fields: list = []

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)


_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _record_msg(timestamp=_START, **extra):
    values = {"timestamp": timestamp}
    values.update(extra)
    return _FakeMsg("record", values)


# ---------------------------------------------------------------------------
# Item 2: no session, no sport message -> MissingCanonicalFieldError,
# not sport=None
# ---------------------------------------------------------------------------


def test_no_session_and_no_sport_message_raises_missing_sport_error() -> None:
    # A watch that died mid-activity: only record messages survive, no
    # session/sport roll-up message at all.
    messages = [_record_msg(heart_rate=140)]

    with pytest.raises(MissingCanonicalFieldError) as exc_info:
        mapping.to_canonical(messages)

    assert exc_info.value.field == "sport"


# ---------------------------------------------------------------------------
# Item 3: unmapped raw FIT sport int -> "other", raw value in provenance
# ---------------------------------------------------------------------------


def test_unmapped_raw_sport_int_maps_to_other_on_real_fixture() -> None:
    messages = fit_parser.decode(STRESS_FIXTURE.read_bytes())

    session, _records = mapping.to_canonical(messages)

    assert session.sport == "other"


def test_unmapped_raw_sport_int_is_preserved_in_provenance_on_real_fixture() -> None:
    messages = fit_parser.decode(STRESS_FIXTURE.read_bytes())

    session, _records = mapping.to_canonical(messages)

    assert session.context.provenance["raw_sport_value"] == 60


def test_named_sport_string_passes_through_unchanged() -> None:
    messages = [
        _FakeMsg("session", {"sport": "running", "start_time": _START}),
        _record_msg(),
    ]

    session, _records = mapping.to_canonical(messages)

    assert session.sport == "running"
    assert "raw_sport_value" not in session.context.provenance


def test_named_non_running_sport_string_maps_to_other() -> None:
    # Sprint-002 re-review, Stage 0 code-review finding: only an
    # *unmapped raw int* sport value was coerced to "other" (item 3
    # above) -- a fitdecode-resolved name fitdecode's own profile does
    # have, like "cycling", is not an int and passed through this
    # module unchanged, storing a value the canonical schema's sport
    # enum (spec/references/F003-canonical-schema.md §2.2.1:
    # running/other) has no slot for.
    messages = [
        _FakeMsg("session", {"sport": "cycling", "start_time": _START}),
        _record_msg(),
    ]

    session, _records = mapping.to_canonical(messages)

    assert session.sport == "other"
    assert session.context.provenance["raw_sport_value"] == "cycling"


# ---------------------------------------------------------------------------
# T056: lift session.sport_profile_name into context.provenance
#
# The value is *lifted*, not routed on. Nothing reads it until T063; this
# module asserts only that what the file said arrives in provenance
# verbatim, and that a file that said nothing produces no key.
#
# Oracle note (.claude/rules/learnings/contract-tables-need-an-independent-
# oracle.md): the corpus-wide assertion below compares the lifted value
# against a direct ``fitdecode`` read of the same bytes -- deliberately not
# against a table hand-copied from the spec, and not through ``mapping``
# itself. The three literal pins beside it are transcribed from that same
# decode run, and they are the rows that would still fail a lift returning
# a constant, which the corpus-wide comparison alone would not.
# ---------------------------------------------------------------------------

TIER1_FIXTURE = FIXTURES / "strap_hrv_capture.fit"


def _decode_session_sport_profile_name(path: Path) -> str | None:
    """Read ``session.sport_profile_name`` straight from the bytes.

    Independent of ``runcoach_api.ingestion.mapping`` on purpose -- this is
    the oracle the lift is checked against, so it must not share an
    implementation with the thing under test.
    """
    with fitdecode.FitReader(str(path)) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "session":
                return frame.get_value("sport_profile_name", fallback=None)
    return None


def test_sport_profile_name_is_lifted_into_provenance() -> None:
    """T056's first failing test: the declared Tier-1 capture's profile
    name reaches ``context.provenance``."""
    messages = fit_parser.decode(TIER1_FIXTURE.read_bytes())

    session, _records = mapping.to_canonical(messages)

    assert session.context.provenance["sport_profile_name"] == "HRV Snapshot"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # The declared capture and the undeclared one recorded on the stock
        # 'Run' profile: sport_profile_name is the *only* decoded difference
        # between them (see test_fixture_corpus.py's docstring), so pinning
        # both sides is what makes the contrast checkable.
        ("strap_hrv_capture.fit", "HRV Snapshot"),
        ("strap_hrv_sample_run.fit", "Run"),
        ("sample_health_snapshot.fit", "Health Snapshot"),
    ],
)
def test_sport_profile_name_lift_is_pinned_on_the_named_fixtures(
    filename: str, expected: str
) -> None:
    messages = fit_parser.decode((FIXTURES / filename).read_bytes())

    session, _records = mapping.to_canonical(messages)

    assert session.context.provenance.get("sport_profile_name") == expected


@pytest.mark.parametrize("filename", sorted(p.name for p in FIXTURES.glob("*.fit")))
def test_sport_profile_name_lift_matches_the_decoded_field_corpus_wide(
    filename: str,
) -> None:
    """Across the whole fixture corpus the lifted value is exactly the
    decoded session field -- no normalisation, no fallback, no default."""
    path = FIXTURES / filename
    decoded = _decode_session_sport_profile_name(path)

    session, _records = mapping.to_canonical(fit_parser.decode(path.read_bytes()))
    provenance = session.context.provenance

    if decoded is None:
        assert "sport_profile_name" not in provenance, (
            f"{filename}: the file declared no profile name, so provenance "
            "must carry no key at all -- not a None value."
        )
    else:
        assert provenance["sport_profile_name"] == decoded, (
            f"{filename}: the lift must reproduce the decoded session field verbatim."
        )


def test_absent_sport_profile_name_produces_no_key_at_all() -> None:
    """Absence is absence. A ``None`` value would assert that a profile name
    was observed and was empty, which is a different claim -- and it is
    exactly the claim T064's undeclared-candidate note deliberately makes
    with ``{"sport_profile_name": null}``. The lift must not make it."""
    messages = [
        _FakeMsg("session", {"sport": "running", "start_time": _START}),
        _record_msg(),
    ]

    session, _records = mapping.to_canonical(messages)

    assert "sport_profile_name" not in session.context.provenance


@pytest.mark.parametrize(
    "value",
    [
        "",  # present-but-empty: recorded, and distinguishable from absent
        "   ",  # whitespace only -- preserved, never stripped
        "HRV Snapshot ",  # a trailing space is a different profile name
        "hrv snapshot",  # case is preserved; matching is T063's problem
        "HRV Snapshot — été ❤",  # non-ASCII free text
        "P" * 4096,  # absurdly long free text
    ],
)
def test_degenerate_sport_profile_names_are_lifted_verbatim(value: str) -> None:
    """Adversarial-probe deliverable (.claude/rules/learnings/adversarial-
    input-probes-are-a-task-deliverable.md). ``sport_profile_name`` is
    device-side free text the athlete types when naming an activity
    profile, so every one of these is a reachable input. The lift
    normalises none of them: it records what the file said, and any
    trimming or case-folding here would silently move the boundary of the
    declaration rule T063 builds on top of it.

    The empty string is the load-bearing row: a truthiness test in
    ``_build_context`` would drop it and make "observed and empty"
    indistinguishable from "never observed"."""
    messages = [
        _FakeMsg(
            "session",
            {
                "sport": "running",
                "start_time": _START,
                "sport_profile_name": value,
            },
        ),
        _record_msg(),
    ]

    session, _records = mapping.to_canonical(messages)

    assert session.context.provenance["sport_profile_name"] == value


def test_sport_profile_name_is_not_taken_from_the_sport_message() -> None:
    """The session field is the single key. All ten fixtures currently agree
    with the ``sport`` message's own ``name``, but consulting it as a
    fallback would introduce a second route that a future disagreement
    turns into a routing bug."""
    messages = [
        _FakeMsg("session", {"sport": "running", "start_time": _START}),
        _FakeMsg("sport", {"sport": "running", "name": "HRV Snapshot"}),
        _record_msg(),
    ]

    session, _records = mapping.to_canonical(messages)

    assert "sport_profile_name" not in session.context.provenance
