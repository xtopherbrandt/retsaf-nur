"""T057: pin the decoded facts of the recorded fixture corpus.

Four fixtures were recorded on the athlete's own FR945 LTE + HRM-Pro-Plus
on 2026-09-06 and are the evidence base for F004's declaration amendment.
The amendment's GO/NO-GO block asserts specific numbers about them --
149 ``hrv`` messages here, zero there, ``'HRV Snapshot'`` on one profile
and ``'Run'`` on another -- and until this module existed **nothing
checked that the committed bytes still produce those numbers.** A claim
nobody verifies is not a claim; this is the fixture-level form of that
rule.

**Provenance of the numbers below.** Every value in ``_CORPUS`` was
obtained by decoding the committed bytes with ``fitdecode`` in this task
and transcribing the output -- *not* by copying the table in the task
spec or the feature file. Where the decode disagreed with the prose, the
decode won and the disagreement is recorded in the notes below. Per
``.claude/rules/learnings/contract-tables-need-an-independent-oracle.md``
this table's oracle is the file bytes themselves, which is the one oracle
that does not share an author with the spec text it is checking.

**Decoded through ``fitdecode`` directly, never through
``mapping.to_canonical``.** The point of a fixture pin is to survive a
mapping change: if the canonical layer starts bucketing sports
differently, or renames a provenance key, these assertions must stay
green, because the *files* did not change. Routing behaviour is asserted
by ``test_resting_hrv_*``; this module asserts nothing about routing.

**What each fixture is for**

``strap_hrv_capture.fit``
    The Tier-1 positive. Recorded on a custom activity profile named
    ``'HRV Snapshot'`` (Start > Add > Other), which is the declaration
    the amendment routes on. A custom "Other" profile reports
    ``sport = 'generic'`` -- corroboration only, never the key.

``strap_health_snapshot_hrv.fit``
    The mutual-exclusivity proof, and the load-bearing one. A Garmin
    Health Snapshot recorded *after* ``Log HRV`` was enabled, and it
    still carries **zero** ``hrv`` messages and zero beats, only a device
    ``rmssd_hrv``. Garmin's built-in declaration and raw beats cannot
    coexist, which is why a dedicated activity profile is the recommended
    workflow rather than a test convenience. The zero below is the pin
    that protects that argument: if a future recording of this fixture
    carried beats, the whole recommendation would need revisiting, and
    this assertion is what would say so.

``strap_cool_down_walk.fit``
    Short-easy-activity **evidence**, deliberately not used as a
    regression pin for any single rule. It is refused three ways over
    (mean speed 1.0356 m/s over the 1.0 veto, 118.874 s under the 120 s
    gate, and undeclared), so an assertion that it does not route would
    pass with any one of those rules deleted -- the exact anti-pattern
    the contract-table learning names, using this exact file. Here it is
    pinned only as *what was recorded*, which is a claim about bytes and
    can genuinely fail.

``strap_run_hrv.fit``
    An ordinary run carrying beats in quantity: 100 minutes, 19.2 km,
    6145 ``hrv`` messages. A stronger version of ``dev_fields_run.fit``'s
    role, and the corroboration that carrying ``hrv`` messages says
    nothing whatsoever about intent.

``strap_hrv_sample_run.fit``
    Included though it predates this task, because the corpus's meaning
    is relational: it is a genuine resting capture recorded on the
    ``'Run'`` profile, and the *only* decoded difference between it and
    ``strap_hrv_capture.fit`` that the declaration rule may key on is
    ``sport_profile_name``. Pinning both sides is what makes that
    contrast checkable. Its misleading name stays -- a rename would
    touch every reference.

**Two decode findings that contradict the prose, stated prominently**

1. ``rmssd_hrv`` is **not absent** from ``strap_hrv_capture.fit``. The
   feature file and task spec both say "no ``rmssd_hrv``", but the
   session definition record *declares* the field and encodes the
   invalid sentinel, so ``msg.has_field('rmssd_hrv')`` is ``True`` on
   every fixture in the corpus and ``get_value`` returns ``None``. A pin
   written from the prose as ``not has_field(...)`` would fail against
   the real bytes. "Absent" here means **value None**, and that is what
   is pinned. The same holds for ``total_distance`` on
   ``strap_health_snapshot_hrv.fit``.
2. ``session.sport`` is not consistently typed. ``fitdecode`` resolves it
   to a **string** where the value is a known enum member
   (``'generic'``, ``'running'``) and leaves it an **int** where it is
   not -- Health Snapshot's ``60`` has no name in the FIT profile, so it
   decodes as ``60``. Both shapes are pinned as decoded. This is why
   ``_classify_tier_2``'s ``raw_sport_value == 60`` is correctly
   ``False`` for ``strap_hrv_capture.fit``: the value there is the
   string ``'generic'``, not an int.

Beat counting is defined here rather than imported: a beat value is a
non-``None`` entry in an ``hrv`` message's ``time`` array. Importing the
production counter would make this table agree with the implementation by
construction, which is the failure mode the learning rule describes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fitdecode
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class DecodedFacts:
    """The facts pinned for one fixture, all obtained by decoding."""

    size_bytes: int
    hrv_message_count: int
    beat_value_count: int
    session_count: int
    sport: object
    sub_sport: str
    sport_profile_name: str | None
    total_timer_time: float
    total_distance: float | None
    total_calories: int
    avg_heart_rate: int
    max_heart_rate: int
    rmssd_hrv: int | None


# Transcribed from a fitdecode run over the committed bytes on
# 2026-09-06 (T057). Not copied from the spec -- see module docstring.
_CORPUS: dict[str, DecodedFacts] = {
    # The Tier-1 positive: declared via the custom 'HRV Snapshot' profile.
    "strap_hrv_capture.fit": DecodedFacts(
        size_bytes=10_225,
        hrv_message_count=149,
        beat_value_count=165,
        session_count=1,
        sport="generic",
        sub_sport="generic",
        sport_profile_name="HRV Snapshot",
        total_timer_time=150.476,
        total_distance=0.0,
        total_calories=4,
        avg_heart_rate=64,
        max_heart_rate=71,
        rmssd_hrv=None,
    ),
    # Wrist PPG on the declared 'HRV Snapshot' profile: the declaration is
    # right and the hardware still cannot answer. Zero hrv messages AND no
    # device rmssd_hrv, so neither tier can claim it -- it is recognised and
    # flagged, never read. Recorded 2026-09-07 without a chest strap.
    "wrist_ppg_hrv_snapshot.fit": DecodedFacts(
        size_bytes=10_677,
        hrv_message_count=0,
        beat_value_count=0,
        session_count=1,
        sport="generic",
        sub_sport="generic",
        sport_profile_name="HRV Snapshot",
        total_timer_time=153.49,
        total_distance=32.38,
        total_calories=3,
        avg_heart_rate=54,
        max_heart_rate=61,
        rmssd_hrv=None,
    ),
    # The mutual-exclusivity proof: 'Log HRV' was on and there are still
    # zero hrv messages.
    "strap_health_snapshot_hrv.fit": DecodedFacts(
        size_bytes=10_091,
        hrv_message_count=0,
        beat_value_count=0,
        session_count=1,
        sport=60,
        sub_sport="generic",
        sport_profile_name="Health Snapshot",
        total_timer_time=120.192,
        total_distance=None,
        total_calories=0,
        avg_heart_rate=66,
        max_heart_rate=75,
        rmssd_hrv=90,
    ),
    # Evidence only -- never a single-rule regression pin.
    "strap_cool_down_walk.fit": DecodedFacts(
        size_bytes=20_085,
        hrv_message_count=120,
        beat_value_count=195,
        session_count=1,
        sport="running",
        sub_sport="generic",
        sport_profile_name="Run",
        total_timer_time=118.874,
        total_distance=123.1,
        total_calories=9,
        avg_heart_rate=99,
        max_heart_rate=127,
        rmssd_hrv=None,
    ),
    # An ordinary run carrying beats in quantity.
    "strap_run_hrv.fit": DecodedFacts(
        size_bytes=536_314,
        hrv_message_count=6_145,
        beat_value_count=13_659,
        session_count=1,
        sport="running",
        sub_sport="generic",
        sport_profile_name="Run",
        total_timer_time=6000.0,
        total_distance=19_236.13,
        total_calories=1_206,
        avg_heart_rate=140,
        max_heart_rate=155,
        rmssd_hrv=None,
    ),
    # The corpus's undeclared negative: a genuine resting capture on the
    # 'Run' profile. Do not rename.
    "strap_hrv_sample_run.fit": DecodedFacts(
        size_bytes=21_172,
        hrv_message_count=149,
        beat_value_count=156,
        session_count=1,
        sport="running",
        sub_sport="generic",
        sport_profile_name="Run",
        total_timer_time=150.797,
        total_distance=108.21,
        total_calories=4,
        avg_heart_rate=60,
        max_heart_rate=73,
        rmssd_hrv=None,
    ),
}


@dataclass
class _Decoded:
    hrv_message_count: int = 0
    beat_value_count: int = 0
    sessions: list[dict[str, object]] = field(default_factory=list)


def _decode(path: Path) -> _Decoded:
    """Decode one FIT file to the facts this module pins.

    Deliberately independent of ``runcoach_api`` -- no ``to_canonical``,
    no production beat counter, no classifier. ``fitdecode`` and the
    bytes, nothing else.
    """
    out = _Decoded()
    with fitdecode.FitReader(str(path)) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "hrv":
                out.hrv_message_count += 1
                value = frame.get_value("time")
                series = value if isinstance(value, (list, tuple)) else [value]
                out.beat_value_count += sum(1 for beat in series if beat is not None)
            elif frame.name == "session":
                out.sessions.append(
                    {fd.name: fd.value for fd in frame.fields}
                )
    return out


@pytest.mark.parametrize("filename", sorted(_CORPUS))
def test_fixture_corpus_decoded_facts(filename: str) -> None:
    """Each committed fixture still decodes to the facts F004 rests on."""
    path = FIXTURES / filename
    expected = _CORPUS[filename]

    assert path.is_file(), (
        f"{filename} is missing from tests/fixtures/. It is committed to the "
        "repository precisely so that it cannot go missing; an untracked "
        "fixture is invisible inside a git worktree."
    )
    assert path.stat().st_size == expected.size_bytes, (
        f"{filename} is no longer the recorded file: expected "
        f"{expected.size_bytes} bytes, found {path.stat().st_size}."
    )

    decoded = _decode(path)

    assert decoded.hrv_message_count == expected.hrv_message_count, (
        f"{filename}: hrv message count changed"
    )
    assert decoded.beat_value_count == expected.beat_value_count, (
        f"{filename}: beat-to-beat value count changed"
    )
    assert len(decoded.sessions) == expected.session_count, (
        f"{filename}: session message count changed -- F004 refuses "
        "multi-session files, so this number is load-bearing"
    )

    session = decoded.sessions[0]
    assert session.get("sport") == expected.sport
    assert type(session.get("sport")) is type(expected.sport), (
        f"{filename}: sport decoded as {type(session.get('sport')).__name__}, "
        f"pinned as {type(expected.sport).__name__}. The Tier-2 identity "
        "compares against the int 60, so the decoded type is part of the fact."
    )
    assert session.get("sub_sport") == expected.sub_sport
    assert session.get("sport_profile_name") == expected.sport_profile_name, (
        f"{filename}: sport_profile_name is the single key the declaration "
        "rule reads; a change here changes what routes."
    )
    assert session.get("total_timer_time") == pytest.approx(
        expected.total_timer_time
    )
    if expected.total_distance is None:
        assert session.get("total_distance") is None
    else:
        assert session.get("total_distance") == pytest.approx(
            expected.total_distance
        )
    assert session.get("total_calories") == expected.total_calories
    assert session.get("avg_heart_rate") == expected.avg_heart_rate
    assert session.get("max_heart_rate") == expected.max_heart_rate

    # "No rmssd_hrv" means the field is declared and carries the invalid
    # sentinel -- see decode finding 1 in the module docstring. Pinning
    # both halves keeps the distinction visible to a later reader.
    assert "rmssd_hrv" in session, (
        f"{filename}: every corpus session declares rmssd_hrv in its "
        "definition record, even where the value is the invalid sentinel. "
        "Absence of the field would be a new shape, not the documented one."
    )
    assert session.get("rmssd_hrv") == expected.rmssd_hrv
