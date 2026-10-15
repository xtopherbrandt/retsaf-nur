"""Which races qualify for threshold pace, end to end through the store (F024 AC1, AC5's un-tag).

Every row of the F024 reference section 2 table and every recording-mode population of its
section 7 is driven through the real app: the session is stored (``db.persist`` for a synthetic
session, ``POST /sessions`` for a real file), tagged, and read back from ``GET /state``. Each row
asserts where the race lands (``efforts``, ``excluded`` with its reason, or neither list) and, for
an excluded race, that it **changes nothing**: the determinant served beside a control race is the
one served with the control alone. The clock is frozen through ``main._utcnow`` and every request
runs under the autouse isolated store.

Synthetic races carry 1 Hz flat records, ``distance = t * 1000 / pace``, so F013's GAP pace is the
given pace exactly. Real files, with their rows of the provenance table in ``fixtures/README.md``:

| file | kind | notes | mode | devices | positions | run proof |
|---|---|---|---|---|---|---|
| sample_run | real run | | 1 Hz | FR945 LTE; HRM-Pro Plus | yes | yes |
| hilly_run_8k_fr945 | real run | was `Hilly_Smart_Recorded_run.fit`; Smart selected, watch wrote 1 Hz; positions stripped | 1 Hz, 82 s pause | FR945 LTE; HRM-Pro Plus | no | yes |

``hilly_run_8k_fr945`` is the paused population (timer 2,872.1 s, elapsed 2,954.5 s: both in the
band, so it is evidence that a paused real race qualifies, not a pin that the timer excludes
pauses; ``test_timer_in_band_elapsed_out_qualifies`` pins that with a synthetic session).
``sample_run`` with moving dropouts of 11 s and 30 s removed from its FIT bytes by
``support/fit_patch.py`` (as ``test_session_features_decimated.py`` injects them) is the
moving-dropout population: it stays predominantly 1 Hz, gains ``moving_gap`` and qualifies.
``sample_run`` uniformly decimated to every 7th record is not that population: the ingestion gate
classifies its spacing ``smart`` and stores ``smart_recording``, so it is excluded with that
reason. Decimated copies are constructed, not smart-recording proof; the smart-recording row of
section 7 is pinned by the stored flag (``test_smart_recording_excludes``).

Real races are tagged through F023's ``PUT /race-tags`` with an item whose distance and duration
are the stored summary's within 1 %, so the qualifying path is the athlete's: Connect's mark,
the store, the estimate.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api import main as main_module
from runcoach_api.ingestion.mapping import derive_session_id
from runcoach_api.main import app
from runcoach_api.models import Context, Record, Session

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
CONTROL_START = datetime(2026, 8, 2, 7, 0, tzinfo=UTC)
RACE_START = datetime(2026, 8, 30, 7, 0, tzinfo=UTC)
CONTROL_PACE = 270.0
RACE_PACE = 255.0

DECIMATE_EVERY = 7
DROPOUTS_S = ((600, 611), (1200, 1230))  # [start, end) s after the first record: 11 s and 30 s removed
FIT_ALTITUDE_FIELDS = (2, 78)  # record.altitude, record.enhanced_altitude


def _load_support(name: str):
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fit_patch = _load_support("fit_patch")


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "_utcnow", lambda: NOW)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _records(duration_s: float, pace_s_per_km: float, *, altitude: float | None = 100.0) -> list[Record]:
    speed = 1000.0 / pace_s_per_km
    return [
        Record(t=float(t), distance=t * speed, speed=speed, altitude=altitude, heart_rate=150.0)
        for t in range(int(duration_s) + 1)
    ]


def _store(
    start: datetime,
    *,
    pace: float = RACE_PACE,
    duration_s: float = 1800.0,
    sport: str = "running",
    flags: list[str] | None = None,
    timer_time_s: float | None | str = "same",
    records: list[Record] | None = None,
) -> str:
    start_time = start.isoformat()
    records = _records(duration_s, pace) if records is None else records
    distance = records[-1].distance if records and records[-1].distance is not None else None
    session = Session(
        session_id=derive_session_id("fr945", start_time),
        sport=sport,
        source_vendor="garmin",
        start_time=start_time,
        source_device="fr945",
        activity_tag="race",
        timer_time_s=duration_s if timer_time_s == "same" else timer_time_s,
        quality_flags=list(flags or []),
        summary={"duration_s": duration_s, "distance_m": distance},
        context=Context(),
    )
    conn = db_module.get_connection()
    try:
        db_module.init_schema(conn)
        db_module.persist(conn, session, records, [], {})
    finally:
        conn.close()
    return session.session_id


def _sql(statement: str, *params) -> int:
    conn = db_module.get_connection()
    try:
        changed = conn.execute(statement, params).rowcount
        conn.commit()
    finally:
        conn.close()
    return changed


def _ftp(client: TestClient) -> dict:
    response = client.get("/state")
    assert response.status_code == 200, response.text
    determinants = response.json()["determinants"]
    assert [d["name"] for d in determinants] == ["functional_threshold_pace"]
    return determinants[0]


def _features(client: TestClient, session_id: str) -> dict:
    response = client.get(f"/sessions/{session_id}/features")
    assert response.status_code == 200, response.text
    return response.json()


def _efforts(determinant: dict) -> list[str]:
    return [e["session_id"] for e in determinant["efforts"]]


def _excluded(determinant: dict) -> list[tuple[str, str]]:
    return [(e["session_id"], e["reason"]) for e in determinant["excluded"]]


def _comparable(determinant: dict) -> dict:
    """Everything an excluded race must leave as it was: the estimate, its error, the races used."""
    return {key: determinant[key] for key in ("estimate", "unavailable", "error", "short_races_only", "efforts")}


def _assert_excluded_changes_nothing(store_race, reason: str) -> str:
    """Store the control race, read ``GET /state``, store the race under test, read again: the race
    lands in ``excluded`` with ``reason`` and every other part of the determinant is unchanged."""
    control = _store(CONTROL_START, pace=CONTROL_PACE)
    with TestClient(app) as client:
        before = _ftp(client)
        assert _efforts(before) == [control] and before["excluded"] == []
        race = store_race()
        after = _ftp(client)
    print(f"  excluded {race}: {_excluded(after)}; estimate {after['estimate']}")
    assert _excluded(after) == [(race, reason)]
    assert _comparable(after) == _comparable(before)
    return race


def _assert_qualifies(determinant: dict, race: str, *, duration_s: float, pace: float) -> dict:
    """The race is the one effort used, with its section 2 duration and GAP pace, and nothing is excluded."""
    print(f"  efforts {determinant['efforts']}; excluded {determinant['excluded']}")
    assert determinant["excluded"] == []
    assert _efforts(determinant) == [race]
    effort = determinant["efforts"][0]
    assert effort["duration_s"] == pytest.approx(duration_s, abs=1e-9)
    assert effort["gap_pace_s_per_km"] == pytest.approx(pace, rel=1e-9)
    assert effort["gain"] == 1
    assert determinant["unavailable"] is None
    assert determinant["estimate"]["value"] == pytest.approx(pace, rel=1e-9)
    return effort


def _upload(client: TestClient, name: str, raw: bytes) -> str:
    response = client.post("/sessions", files={"file": (name, raw)})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _stored_summary(session_id: str) -> tuple[str, dict]:
    conn = db_module.get_connection()
    try:
        row = conn.execute("SELECT start_time, summary FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None, session_id
    return row["start_time"], json.loads(row["summary"])


def _race_tag_item(session_id: str, *, race: bool, activity_id: int = 1001) -> dict:
    """Connect's mark for a stored run: its start second in UTC, its distance 0.5 % long and its
    duration 0.5 % short of the stored summary (inside F023's 1 %)."""
    start_time, summary = _stored_summary(session_id)
    start = datetime.fromisoformat(start_time).astimezone(UTC)
    return {
        "connect_activity_id": activity_id,
        "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "distance_m": summary["distance_m"] * 1.005,
        "duration_s": summary["duration_s"] * 0.995,
        "race": race,
    }


def _put_race_tag(client: TestClient, session_id: str, *, race: bool) -> dict:
    response = client.put("/race-tags", json={"items": [_race_tag_item(session_id, race=race)]})
    assert response.status_code == 200, response.text
    return response.json()


def _tag_real_race(client: TestClient, session_id: str) -> None:
    body = _put_race_tag(client, session_id, race=True)
    assert [(i["outcome"], i["session_id"]) for i in body["items"]] == [("tagged", session_id)], body


def _timer(session_id: str) -> float:
    conn = db_module.get_connection()
    try:
        return conn.execute("SELECT timer_time_s FROM sessions WHERE session_id = ?", (session_id,)).fetchone()[0]
    finally:
        conn.close()


def _assert_real_race_qualifies(client: TestClient, race: str) -> tuple[dict, dict]:
    """A real race tagged through ``PUT /race-tags`` is the one effort used, with the stored timer as
    its duration and F013's GAP pace, coverage and degraded fraction as ``GET /sessions/{id}/features``
    serves them."""
    features = _features(client, race)
    determinant = _ftp(client)
    print(f"  efforts {determinant['efforts']}; excluded {determinant['excluded']}; flags {features['flags']}")
    assert determinant["excluded"] == []
    assert _efforts(determinant) == [race]
    effort = determinant["efforts"][0]
    assert effort["duration_s"] == _timer(race)
    assert effort["gap_pace_s_per_km"] == features["features"]["gap_avg_pace_s_per_km"]["value"]
    assert effort["gap_coverage"] == features["gap_coverage"]
    assert effort["gps_degraded_fraction"] == features["gps_degraded_fraction"]
    assert effort["gain"] == 1
    assert determinant["estimate"]["value"] == pytest.approx(effort["gap_pace_s_per_km"], rel=1e-12)
    return determinant, features


# ---------------------------------------------------------------------------
# Section 2 row 2: the duration band, inclusive at 1200 and 3600 s
# ---------------------------------------------------------------------------


def test_duration_1199_is_outside_threshold_band() -> None:
    _assert_excluded_changes_nothing(lambda: _store(RACE_START, duration_s=1199.0), "outside_threshold_band")


def test_duration_1200_qualifies() -> None:
    race = _store(RACE_START, duration_s=1200.0)
    with TestClient(app) as client:
        _assert_qualifies(_ftp(client), race, duration_s=1200.0, pace=RACE_PACE)


def test_duration_3600_qualifies() -> None:
    race = _store(RACE_START, duration_s=3600.0)
    with TestClient(app) as client:
        _assert_qualifies(_ftp(client), race, duration_s=3600.0, pace=RACE_PACE)


def test_duration_3601_is_outside_threshold_band() -> None:
    _assert_excluded_changes_nothing(lambda: _store(RACE_START, duration_s=3601.0), "outside_threshold_band")


def test_null_timer_falls_back_to_summary_duration() -> None:
    """With ``timer_time_s`` null the summary's ``duration_s`` is the duration (2000 s: in the band);
    a stored timer comes first, so the same summary beside a 3700 s timer is out of the band."""
    fallback = _store(RACE_START, duration_s=2000.0, timer_time_s=None)
    assert _timer(fallback) is None
    with TestClient(app) as client:
        _assert_qualifies(_ftp(client), fallback, duration_s=2000.0, pace=RACE_PACE)
        timed = _store(RACE_START + timedelta(days=7), duration_s=2000.0, timer_time_s=3700.0)
        determinant = _ftp(client)
    assert _efforts(determinant) == [fallback]
    assert _excluded(determinant) == [(timed, "outside_threshold_band")]


def test_bool_string_nonfinite_fallback_is_refused() -> None:
    """With the timer null, a summary ``duration_s`` that is a bool, a string or non-finite is never
    a duration: each such race is excluded ``outside_threshold_band``, nothing raises, and the
    control's estimate is unchanged. Only the strings discriminate the guard (``"1800"`` would parse
    into the band); ``true`` reads as 1 s and a non-finite value fails both bounds, so those rows pin
    the outcome, not the guard."""
    control = _store(CONTROL_START, pace=CONTROL_PACE)
    cases = ("true", '"1800"', '"1800.0"', "NaN", "Infinity", "-Infinity")
    with TestClient(app) as client:
        before = _ftp(client)
        races = []
        for day, raw in enumerate(cases):
            race = _store(RACE_START + timedelta(days=day), duration_s=1800.0, timer_time_s=None)
            changed = _sql(
                "UPDATE sessions SET timer_time_s = NULL, summary = ? WHERE session_id = ?",
                f'{{"duration_s": {raw}, "distance_m": 7000.0}}',
                race,
            )
            assert changed == 1
            races.append(race)
        after = _ftp(client)
    print(f"  cases {cases}: excluded {_excluded(after)}")
    assert _efforts(after) == [control]
    assert _excluded(after) == [(race, "outside_threshold_band") for race in races]
    assert _comparable(after) == _comparable(before)


# ---------------------------------------------------------------------------
# Section 2 row 3: the two F013 flags, in a fixed order
# ---------------------------------------------------------------------------


def _store_regressing(start: datetime, *, flags: list[str]) -> str:
    """An in-band race whose records hold one 1 s step where distance goes down, so F013 computes
    ``distance_regressed`` from the records whatever ``quality_flags`` stores."""
    race = _store(start, flags=flags)
    assert _sql("UPDATE records SET distance = distance - 10.0 WHERE session_id = ? AND t = 900.0", race) == 1
    return race


def test_distance_regressed_excludes() -> None:
    _assert_excluded_changes_nothing(lambda: _store_regressing(RACE_START, flags=[]), "distance_regressed")


def test_smart_recording_excludes() -> None:
    _assert_excluded_changes_nothing(lambda: _store(RACE_START, flags=["smart_recording"]), "smart_recording")


def test_both_flags_name_distance_regressed() -> None:
    """``smart_recording`` stored first, and F013 moves it to the front of its flags; the reason is
    still ``distance_regressed``, checked first in the fixed order, never F013's first flag."""

    def store() -> str:
        race = _store_regressing(RACE_START, flags=["smart_recording"])
        with TestClient(app) as client:
            flags = _features(client, race)["flags"]
        assert flags.index("smart_recording") < flags.index("distance_regressed"), flags
        return race

    _assert_excluded_changes_nothing(store, "distance_regressed")


# ---------------------------------------------------------------------------
# What does not exclude: degraded GPS, no altitude, GAP coverage 0
# ---------------------------------------------------------------------------


def test_degraded_gps_race_qualifies_with_fraction() -> None:
    """Degraded GPS is per record (``sample_quality`` ``gps_degraded``), never a session flag: a race
    with its first quarter degraded qualifies and serves F013's fraction, about 0.25."""
    records = _records(1800.0, RACE_PACE)
    for record in records[:450]:
        record.gps_degraded = True
        record.sample_quality = ["gps_degraded"]
    race = _store(RACE_START, records=records)
    with TestClient(app) as client:
        effort = _assert_qualifies(_ftp(client), race, duration_s=1800.0, pace=RACE_PACE)
        served = _features(client, race)["gps_degraded_fraction"]
    assert effort["gps_degraded_fraction"] == served
    assert effort["gps_degraded_fraction"] == pytest.approx(0.25, abs=0.001)


def test_no_altitude_race_qualifies() -> None:
    """A track race with no altitude counts at its flat pace: GAP pace is the raw pace, coverage 0."""
    race = _store(RACE_START, records=_records(1800.0, RACE_PACE, altitude=None))
    with TestClient(app) as client:
        effort = _assert_qualifies(_ftp(client), race, duration_s=1800.0, pace=RACE_PACE)
        features = _features(client, race)
    assert effort["gap_pace_s_per_km"] == features["features"]["avg_pace_s_per_km"]["value"]
    assert effort["gap_coverage"] == 0


def _without_altitude(raw: bytes) -> bytes:
    """Every record's altitude fields set to the FIT invalid value (those the file defines)."""
    blanked = 0
    for field in FIT_ALTITUDE_FIELDS:
        try:
            raw = fit_patch.blank_record_field(raw, field, 0, 10**7)
        except fit_patch.FitPatchError:
            continue
        blanked += 1
    assert blanked, "the fixture defines no record altitude field"
    return raw


def test_gap_coverage_zero_qualifies() -> None:
    """``sample_run`` with its altitude blanked: F013 serves coverage 0 and its ``gap_unavailable``
    flag, which is not the exclusion ``gap_pace_unavailable``; the race qualifies at its raw pace."""
    with TestClient(app) as client:
        race = _upload(client, "sample_run.fit", _without_altitude((FIXTURES / "sample_run.fit").read_bytes()))
        _tag_real_race(client, race)
        determinant, features = _assert_real_race_qualifies(client, race)
    assert features["gap_coverage"] == 0
    assert "gap_unavailable" in features["flags"]
    effort = determinant["efforts"][0]
    assert effort["gap_coverage"] == 0
    assert effort["gap_pace_s_per_km"] == features["features"]["avg_pace_s_per_km"]["value"]


# ---------------------------------------------------------------------------
# Section 2 row 1: a tagged race of another sport is in neither list
# ---------------------------------------------------------------------------


def test_tagged_other_sport_is_in_neither_list() -> None:
    control = _store(CONTROL_START, pace=CONTROL_PACE)
    with TestClient(app) as client:
        before = _ftp(client)
        other = _store(RACE_START, sport="other")
        after = _ftp(client)
    print(f"  sport other {other}: efforts {_efforts(after)}, excluded {after['excluded']}")
    assert _efforts(after) == [control]
    assert after["excluded"] == []
    assert after == before


# ---------------------------------------------------------------------------
# Section 7: the recording-mode populations
# ---------------------------------------------------------------------------


def test_paused_real_run_qualifies() -> None:
    """``hilly_run_8k_fr945`` (82 s pause): its duration is the stored timer, 2,872.1 s, not the
    elapsed 2,954.5 s, and its GAP pace is F013's."""
    with TestClient(app) as client:
        race = _upload(client, "hilly_run_8k_fr945.fit", (FIXTURES / "hilly_run_8k_fr945.fit").read_bytes())
        _tag_real_race(client, race)
        determinant, _ = _assert_real_race_qualifies(client, race)
    assert determinant["efforts"][0]["duration_s"] == pytest.approx(2872.078, abs=0.001)


def test_timer_in_band_elapsed_out_qualifies() -> None:
    """Timer 3000 s, in the band; records from 0 to 4000 s with a 1000 s pause, so the elapsed time
    (4000 s) is out of it. The race qualifies with duration 3000 s: the timer excludes pauses."""
    speed = 1000.0 / RACE_PACE
    moving = [*range(0, 1501), *range(2500, 4001)]
    records = []
    for t in moving:
        covered = t if t <= 1500 else t - 1000
        records.append(Record(t=float(t), distance=covered * speed, speed=speed, altitude=100.0, heart_rate=150.0))
    race = _store(RACE_START, duration_s=3000.0, records=records)
    assert records[-1].t - records[0].t > 3600
    with TestClient(app) as client:
        _assert_qualifies(_ftp(client), race, duration_s=3000.0, pace=RACE_PACE)


def _recording_interval(session_id: str) -> str:
    conn = db_module.get_connection()
    try:
        return conn.execute("SELECT recording_interval FROM sessions WHERE session_id = ?", (session_id,)).fetchone()[0]
    finally:
        conn.close()


def test_decimated_real_run_qualifies() -> None:
    """The moving-dropout population: ``sample_run`` with an 11 s and a 30 s run of records removed
    in its FIT bytes while the runner moved (``fit_patch.drop_records``, as
    ``test_session_features_decimated.py`` injects its dropouts). The file stays predominantly 1 Hz,
    so no ``smart_recording``; F013 flags ``moving_gap`` and counts both gaps; the race qualifies."""
    raw = (FIXTURES / "sample_run.fit").read_bytes()
    for start_s, end_s in DROPOUTS_S:
        raw = fit_patch.drop_records(raw, start_s, end_s)
    with TestClient(app) as client:
        race = _upload(client, "sample_run.fit", raw)
        _tag_real_race(client, race)
        _, features = _assert_real_race_qualifies(client, race)
    assert _recording_interval(race) == "1hz"
    assert "smart_recording" not in features["flags"]
    assert "moving_gap" in features["flags"]
    assert math.isfinite(features["features"]["gap_avg_pace_s_per_km"]["value"])


def test_uniformly_decimated_real_run_reads_smart_recording_and_is_excluded() -> None:
    """``sample_run`` decimated to every 7th record is the smart-recording population, not the
    moving-dropout one: the ingestion gate classifies its spacing ``smart`` and stores the flag, so
    the race is excluded ``smart_recording`` (reference section 2 row 3), though F013 also counts
    its gaps as ``moving_gap``. Constructed, not smart-recording proof (see the module docstring)."""
    sparse = fit_patch.decimate_records((FIXTURES / "sample_run.fit").read_bytes(), DECIMATE_EVERY)
    with TestClient(app) as client:
        race = _upload(client, "sample_run.fit", sparse)
        _tag_real_race(client, race)
        flags = _features(client, race)["flags"]
        determinant = _ftp(client)
    print(f"  every {DECIMATE_EVERY}th record: flags {flags}; excluded {_excluded(determinant)}")
    assert _recording_interval(race) != "1hz"
    assert "moving_gap" in flags
    assert determinant["efforts"] == []
    assert _excluded(determinant) == [(race, "smart_recording")]


# ---------------------------------------------------------------------------
# AC5: un-tagging through F023's PUT /race-tags
# ---------------------------------------------------------------------------


def test_untag_through_race_tags_removes_the_effort() -> None:
    """The paused real race tagged and used; Connect's mark cleared (``race: false``, distance and
    duration within 1 % of the stored summary): the next ``GET /state`` has no effort and does
    not list the run as excluded, since an untagged run is in neither list."""
    with TestClient(app) as client:
        race = _upload(client, "hilly_run_8k_fr945.fit", (FIXTURES / "hilly_run_8k_fr945.fit").read_bytes())
        _tag_real_race(client, race)
        assert _efforts(_ftp(client)) == [race]
        body = _put_race_tag(client, race, race=False)
        determinant = _ftp(client)
    print(f"  untag: {body['items']}; efforts {determinant['efforts']}, excluded {determinant['excluded']}")
    assert [(i["outcome"], i["session_id"]) for i in body["items"]] == [("cleared", race)]
    assert determinant["efforts"] == []
    assert determinant["excluded"] == []
    assert determinant["estimate"] is None
    assert determinant["unavailable"] == "no_qualifying_effort"
