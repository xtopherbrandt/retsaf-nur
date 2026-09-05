"""T043: the Tier-1 quality gates -- rows 1-3 of F004's quality-gate outline.

```gherkin
Scenario Outline: A capture failing a quality gate yields no reading, but is still stored
  Given a resting capture whose <condition>
  When it is uploaded to POST /sessions
  Then the upload succeeds and the session is stored with its quality flags
  And hrv_source_tier and rmssd_precomputed are both null
  And the <flag> quality flag is raised rather than failing silently

  Examples:
    | condition                                          | flag                  |
    | total_timer_time is under 120 seconds              | hrv_capture_too_short |
    | rr_valid_fraction is below 0.80                    | hrv_capture_low_quality |
    | beat stream is empty, so rr_valid_fraction is null | hrv_capture_no_beats  |
```

(Rows 4 and 5 -- the device-scalar rows -- are Tier 2's, owned by T039 and asserted in
``test_resting_hrv_tier2.py``.)

**The three thresholds are cited, not invented** (F004 reference document §5):

* **120 s** -- the *lower* bound of §2.4.5's "2-5 minute resting measurement" protocol,
  the same sentence T041 took the 300 s upper bound from.
* **0.80** -- §2.4.3 / spec §3's "reject a capture retaining <80% of beats".
* **no third constant.** Row 3 is not a threshold at all: it is the ``None``/``0.0``
  distinction ``models.Session.rr_valid_fraction`` documents.

**The ``None`` vs ``0.0`` trap is the whole reason row 3 exists.** ``pipeline.py`` assigns
``session.rr_valid_fraction`` only when beats exist, leaving it ``None`` -- not ``0.0`` --
for a session with no RR stream at all. A naive ``if session.rr_valid_fraction < 0.80:``
therefore raises ``TypeError`` on ``None`` and surfaces to the client as a **500 on a
perfectly valid upload**. The two rows are also genuinely different findings: "no beats at
all" and "beats recorded, none survived filtering" are not the same event, and E003 reads
them differently, so they are never collapsed by defaulting ``None`` to ``0.0``.

**No sentinel, ever.** A failed gate leaves ``rmssd_precomputed`` ``None``. It is never
``0`` or ``-1``: E003 takes ``ln(rMSSD)``, and ``rmssd.resting_rmssd`` was built around the
same rule -- ``0.0`` is a genuine measurement of zero variability, absence is ``None``.

**A flag on a file that was never a candidate is noise.** The gates run *after* T041's
Tier-1 discriminator has decided the file is a resting capture, so a 90-second ordinary run
raises no ``hrv_capture_too_short``; that is pinned below and is the reason the gates live
inside the Tier-1 branch rather than beside ``quality_gates.apply``.

Synthetic ``_FakeMsg`` message sets are used for the threshold rows, following
``test_quality_gates_smart_recording.py``: these are threshold checks over already-mapped
summary values, not fitdecode parsing behaviour, so ``.claude/rules/project-testing.md``'s
real-fixture requirement does not bite. The real gate fixture is still asserted end to end
-- it must pass all three gates and keep its reading.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runcoach_api.ingestion import hrv_classification, mapping
from runcoach_api.main import app
from runcoach_api.models import RRInterval

FIXTURES = Path(__file__).parent / "fixtures"

# The T041 gate fixture: 150.797 s, 156 beats, 0.718 m/s, avg HR 60. It clears all
# three gates -- 150.797 >= 120, and its beats survive reconstruction -- so it is the
# positive control for every negative below.
GATE_FIXTURE = "strap_hrv_sample_run.fit"

FLAG_TOO_SHORT = "hrv_capture_too_short"
FLAG_LOW_QUALITY = "hrv_capture_low_quality"
FLAG_NO_BEATS = "hrv_capture_no_beats"
GATE_FLAGS = (FLAG_TOO_SHORT, FLAG_LOW_QUALITY, FLAG_NO_BEATS)

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _FakeMsg:
    """Minimal stand-in for a ``fitdecode.FitDataMessage``.

    The same shape ``test_resting_hrv_tier1.py`` and ``test_resting_hrv_tier2.py`` use:
    only ``.name``, ``get_value(name, fallback=None)`` and ``.fields`` are read by
    ``mapping.to_canonical`` and by ``hrv_classification.classify``.
    """

    def __init__(self, name: str, values: dict) -> None:
        self.name = name
        self._values = values
        self.fields: list = []

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)


def _synthetic(**session_extra):
    """A two-message file routed through the real ``mapping.to_canonical``.

    ``_build_summary`` strips its ``None`` values, so omitting ``total_distance`` leaves
    the summary with **no** ``distance_m`` key at all -- exactly what a real GPS-less
    indoor capture produces.
    """
    values = {"sport": "running", "start_time": _START}
    values.update(session_extra)
    return [
        _FakeMsg("session", values),
        _FakeMsg("record", {"timestamp": _START, "heart_rate": 60}),
    ]


def _resting(**session_extra):
    """A message set that clears T041's discriminator: 150 s, no distance, avg HR 60.

    Every gate test starts from a file the Tier-1 branch *would* route, so a failure
    below can only be the gate under test and never the discriminator.
    """
    values = {"total_timer_time": 150.0, "avg_heart_rate": 60}
    values.update(session_extra)
    return _synthetic(**values)


def _beats(count: int = 8, artefacts: int = 0) -> list[RRInterval]:
    """A short beat stream; the first ``artefacts`` beats are flagged.

    Flagged beats are *retained and flagged*, never excised -- that is
    ``rr_reconstruction``'s contract and what makes ``valid_fraction`` meaningful.
    """
    return [
        RRInterval(
            seq=i,
            rr_ms=1000.0 + (20.0 if i % 2 else 0.0),
            rr_source="chest_strap_ecg",
            is_artefact=i < artefacts,
        )
        for i in range(count)
    ]


def _classified(messages, rr_intervals=None, valid_fraction=None):
    """Map, then classify -- optionally with the ``rr_valid_fraction`` pipeline would set.

    ``pipeline.py`` assigns ``session.rr_valid_fraction`` between mapping and
    ``classify()``, so setting it here reproduces the real call order rather than
    inventing one.
    """
    session, _records = mapping.to_canonical(messages)
    if valid_fraction is not None:
        session.rr_valid_fraction = valid_fraction
    hrv_classification.classify(messages, session, rr_intervals or [])
    return session


def _assert_no_reading(session) -> None:
    """The shared "Then" of all three rows: stored, flagged, but no reading.

    ``rmssd_precomputed`` is asserted ``is None`` rather than falsy on purpose -- ``0``
    and ``-1`` are the sentinels this feature must never use, and both are falsy.
    """
    assert session.hrv_source_tier is None
    assert session.rmssd_precomputed is None
    assert session.rr_source is None
    provenance = session.context.provenance if session.context else {}
    assert "computed_resting_rmssd_ms" not in provenance


def _ingest(client: TestClient, filename: str) -> dict:
    raw = (FIXTURES / filename).read_bytes()
    post = client.post("/sessions", files={"file": (filename, raw)})
    assert post.status_code == 201, post.text
    detail = client.get(f"/sessions/{post.json()['session_id']}")
    assert detail.status_code == 200, detail.text
    return detail.json()


# ---------------------------------------------------------------------------
# Row 1 -- minimum duration, 120 s (§2.4.5's "2-5 minute" lower bound)
# ---------------------------------------------------------------------------


def test_a_capture_under_two_minutes_raises_too_short() -> None:
    """The first failing test of T043: a Tier-1 capture whose ``total_timer_time`` is
    under 120 s is stored with its flag and yields no reading."""
    session = _classified(
        _resting(total_timer_time=90.0), rr_intervals=_beats(), valid_fraction=1.0
    )

    assert FLAG_TOO_SHORT in session.quality_flags
    _assert_no_reading(session)


def test_the_minimum_duration_bound_is_inclusive() -> None:
    """120 s is the protocol's lower bound, so a capture *of* two minutes is inside it.
    Only "shorter than 120 s" fails -- the same inclusive treatment T041 gave 300 s."""
    session = _classified(
        _resting(total_timer_time=120.0), rr_intervals=_beats(), valid_fraction=1.0
    )

    assert FLAG_TOO_SHORT not in session.quality_flags
    assert session.hrv_source_tier == "chest_strap_raw"


def test_one_millisecond_under_the_bound_fails() -> None:
    """119.999 s is the first step outside the protocol. Pinned so the comparison can
    never be relaxed to ``<=`` without a test going red."""
    session = _classified(
        _resting(total_timer_time=119.999), rr_intervals=_beats(), valid_fraction=1.0
    )

    assert FLAG_TOO_SHORT in session.quality_flags
    _assert_no_reading(session)


# ---------------------------------------------------------------------------
# Row 2 -- artefact survival, rr_valid_fraction < 0.80 (§2.4.3 / spec §3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fraction", [0.0, 0.5, 0.799])
def test_a_capture_retaining_under_eighty_percent_raises_low_quality(fraction: float) -> None:
    """§2.4.3's default rejects a sample retaining <80% of beats.

    ``0.0`` is included deliberately: "beats recorded, none survived filtering" is a
    *low quality* finding, not a *no beats* one. Collapsing it into row 3 would lose the
    distinction ``models.Session.rr_valid_fraction`` documents as load-bearing."""
    session = _classified(
        _resting(), rr_intervals=_beats(), valid_fraction=fraction
    )

    assert FLAG_LOW_QUALITY in session.quality_flags
    assert FLAG_NO_BEATS not in session.quality_flags
    _assert_no_reading(session)


@pytest.mark.parametrize("fraction", [0.80, 0.998, 1.0])
def test_a_capture_retaining_at_least_eighty_percent_still_reads(fraction: float) -> None:
    """The bound is inclusive: exactly 80% retained is not "under 80%". 0.998 is
    ``dev_fields_run.fit``'s own measured fraction, kept here as a realistic value."""
    session = _classified(_resting(), rr_intervals=_beats(), valid_fraction=fraction)

    assert FLAG_LOW_QUALITY not in session.quality_flags
    assert session.hrv_source_tier == "chest_strap_raw"


def test_the_surviving_fraction_is_read_from_the_beats_when_the_session_lacks_it() -> None:
    """``classify()`` is public and reachable without ``pipeline.py`` -- T041's own suite
    calls it that way. When the session carries no precomputed fraction but beats are in
    hand, the fraction is derived from them rather than mistaken for "no beats"."""
    session = _classified(_resting(), rr_intervals=_beats(count=10, artefacts=4))

    assert session.rr_valid_fraction is None
    assert FLAG_LOW_QUALITY in session.quality_flags
    assert FLAG_NO_BEATS not in session.quality_flags
    _assert_no_reading(session)


# ---------------------------------------------------------------------------
# Row 3 -- no surviving beats: rr_valid_fraction is None, not 0.0
# ---------------------------------------------------------------------------


def test_a_resting_capture_with_an_empty_beat_stream_raises_no_beats() -> None:
    """``pipeline.py`` leaves ``rr_valid_fraction`` ``None`` for a session with no RR
    stream at all. A resting-shaped capture that recorded no beats is reported as such
    rather than failing silently."""
    session = _classified(_resting(), rr_intervals=[])

    assert session.rr_valid_fraction is None
    assert FLAG_NO_BEATS in session.quality_flags
    _assert_no_reading(session)


def test_no_beats_does_not_raise_type_error_on_a_null_fraction() -> None:
    """The 500-on-a-valid-upload regression: ``is None`` must be checked *before* the
    float comparison. Asserted as a behavioural test rather than a comment, because a
    naive ``< 0.80`` is a ``TypeError`` here and nothing else in the suite catches it."""
    session = _classified(_resting(total_timer_time=90.0), rr_intervals=[])

    # Both findings are independent and both apply; neither masks the other.
    assert FLAG_NO_BEATS in session.quality_flags
    assert FLAG_TOO_SHORT in session.quality_flags
    _assert_no_reading(session)


def test_no_beats_is_not_raised_when_beats_survived() -> None:
    """The negative half of row 3: a healthy capture must not carry the flag."""
    session = _classified(_resting(), rr_intervals=_beats(), valid_fraction=1.0)

    assert FLAG_NO_BEATS not in session.quality_flags
    assert session.hrv_source_tier == "chest_strap_raw"


def test_a_beatless_capture_does_not_reach_a_tier_1_reading() -> None:
    """Belt and braces on the Tier-1 necessary condition: flagging the beatless case
    must not have turned it into a route."""
    session = _classified(_resting(), rr_intervals=[])

    assert session.activity_tag is None
    _assert_no_reading(session)


# ---------------------------------------------------------------------------
# The gates never fire on a file that was never a candidate
# ---------------------------------------------------------------------------


def test_a_ninety_second_ordinary_run_raises_no_gate_flag() -> None:
    """The noise guard, and the reason the gates sit *inside* the Tier-1 branch: a 90 s
    run at 3.2 m/s is under 120 s, but it was never a resting capture, so
    ``hrv_capture_too_short`` would be a meaningless finding on it."""
    session = _classified(
        _synthetic(total_timer_time=90.0, total_distance=289.0, avg_heart_rate=165),
        rr_intervals=_beats(),
        valid_fraction=0.5,
    )

    assert [f for f in session.quality_flags if f in GATE_FLAGS] == []


@pytest.mark.parametrize("filename", ["dev_fields_run.fit", "sample_run.fit", "wrist_ppg_run.fit"])
def test_no_ordinary_fixture_picks_up_a_gate_flag(filename: str) -> None:
    """Real files, through the real API. ``wrist_ppg_run.fit`` carries no RR stream at
    all -- so its ``rr_valid_fraction`` is genuinely ``None`` -- and it must still not
    be flagged: it is a run, not a resting capture."""
    with TestClient(app) as client:
        body = _ingest(client, filename)

    assert [f for f in body["quality_flags"] if f in GATE_FLAGS] == []
    assert body["hrv_source_tier"] is None
    assert body["rmssd_precomputed"] is None


@pytest.mark.parametrize(
    ("filename", "rmssd"),
    [("sample_health_snapshot.fit", 37), ("strap_health_snapshot.fit", 51)],
)
def test_a_health_snapshot_keeps_its_tier_2_reading(filename: str, rmssd: int) -> None:
    """Regression guard on T039. Both snapshots are zero-beat, 120.1 s, low-heart-rate
    files -- exactly the shape rows 1 and 3 describe -- so a gate that ran before the
    Tier-2 branch, or that ignored whether Tier 2 had claimed the file, would strip a
    working reading and flag it as a failed capture."""
    with TestClient(app) as client:
        body = _ingest(client, filename)

    assert body["hrv_source_tier"] == "health_snapshot"
    assert body["rmssd_precomputed"] == rmssd
    assert [f for f in body["quality_flags"] if f in GATE_FLAGS] == []


def test_a_long_resting_shaped_file_is_not_gated() -> None:
    """A capture outside the 300 s upper bound is not a Tier-1 candidate at all, so it
    is silently not routed -- never flagged. The gates report on captures, not on files."""
    session = _classified(
        _resting(total_timer_time=1800.0), rr_intervals=[], valid_fraction=None
    )

    assert [f for f in session.quality_flags if f in GATE_FLAGS] == []


# ---------------------------------------------------------------------------
# Flag mechanics -- bare strings, deduped, surfaced verbatim
# ---------------------------------------------------------------------------


def test_every_applicable_gate_raises_its_own_flag() -> None:
    """"Raise each that applies" -- they are independent findings. A 90 s capture whose
    beats mostly failed filtering is both too short *and* low quality."""
    session = _classified(
        _resting(total_timer_time=60.0), rr_intervals=_beats(), valid_fraction=0.25
    )

    assert FLAG_TOO_SHORT in session.quality_flags
    assert FLAG_LOW_QUALITY in session.quality_flags
    _assert_no_reading(session)


def test_a_gate_flag_is_not_duplicated() -> None:
    """``quality_gates.py``'s ``if flag not in session.quality_flags`` convention: bare
    string literals on a ``list[str]``, no enum, no registry, no flags table."""
    messages = _resting(total_timer_time=90.0)
    session, _records = mapping.to_canonical(messages)
    session.quality_flags.append(FLAG_TOO_SHORT)
    hrv_classification.classify(messages, session, _beats())
    hrv_classification.classify(messages, session, _beats())

    assert session.quality_flags.count(FLAG_TOO_SHORT) == 1


def test_gate_flags_are_plain_strings() -> None:
    """They are persisted through ``db.py``'s ``_json_dump``/``_json_load`` TEXT
    convention and surface verbatim in both responses -- no new plumbing."""
    session = _classified(_resting(total_timer_time=90.0), rr_intervals=[])

    assert all(isinstance(flag, str) for flag in session.quality_flags)
    assert set(session.quality_flags) >= {FLAG_TOO_SHORT, FLAG_NO_BEATS}


# ---------------------------------------------------------------------------
# The gate fixture -- a real capture clears all three and keeps its reading
# ---------------------------------------------------------------------------


def test_the_gate_fixture_passes_all_three_gates() -> None:
    """``strap_hrv_sample_run.fit`` is 150.797 s of real chest-strap beats. If any gate
    were mis-signed -- ``>`` for ``<``, or the 120/300 bounds swapped -- this reading
    would disappear, so it is the positive control the whole file is written around."""
    with TestClient(app) as client:
        body = _ingest(client, GATE_FIXTURE)

    assert [f for f in body["quality_flags"] if f in GATE_FLAGS] == []
    assert body["activity_tag"] == "resting_hrv_check"
    assert body["hrv_source_tier"] == "chest_strap_raw"
    assert body["rr_source"] == "chest_strap_ecg"
    assert body["rmssd_precomputed"] is None


def test_a_failing_capture_uploads_successfully_and_is_stored() -> None:
    """F004 is explicit that the upload succeeds: the *capture* is rejected as an HRV
    input, not the file. Refusing a valid FIT would break F003's flag-don't-refuse
    posture, so no gate may become a 4xx or a 5xx.

    ``wrist_ppg_run.fit`` stands in for the failing upload because no sub-120 s Tier-1
    fixture exists in the corpus; what is asserted here is the HTTP contract -- 201, a
    readable session, no reading -- which is identical on every failing row."""
    with TestClient(app) as client:
        raw = (FIXTURES / "wrist_ppg_run.fit").read_bytes()
        post = client.post("/sessions", files={"file": ("wrist_ppg_run.fit", raw)})

        assert post.status_code == 201, post.text
        assert isinstance(post.json()["quality_flags"], list)
        detail = client.get(f"/sessions/{post.json()['session_id']}")

    assert detail.status_code == 200
    assert detail.json()["rmssd_precomputed"] is None


def test_a_failed_gate_never_writes_a_zero_or_negative_sentinel() -> None:
    """E003 takes ``ln(rMSSD)``. ``0`` and ``-1`` are both falsy *and* both catastrophic
    there -- ``ln(0)`` is undefined and ``ln(-1)`` is a domain error -- which is why
    ``rmssd.resting_rmssd`` returns ``None`` rather than ``0.0`` for "no value" and why
    a failed gate must do the same."""
    for messages, beats, fraction in (
        (_resting(total_timer_time=90.0), _beats(), 1.0),
        (_resting(), _beats(), 0.5),
        (_resting(), [], None),
    ):
        session = _classified(messages, rr_intervals=beats, valid_fraction=fraction)

        assert session.rmssd_precomputed is None
        assert session.rmssd_precomputed is not False
        assert session.hrv_source_tier is None
