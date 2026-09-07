"""T038: the F004 walking skeleton is actually wired into the running app.

This file is the anti-regression for one specific trap. In
``pipeline.ingest_fit_bytes`` the ``session.rr_valid_fraction``
assignment sits *inside* ``if rr_intervals:``. Placing the
``hrv_classification.classify(...)`` call inside that same conditional
would mean it never fires for a file with zero beats -- which is
*every* Garmin Health Snapshot, i.e. the whole Tier-2 path. The bug
would not surface until T039, whose scope does not include
``pipeline.py``. So the call site is asserted here, against a real
zero-beat fixture, rather than left to a later task to discover.

It also answers F003's own code-review finding M1 ("tests only
unit-test the class directly; nothing proves it's actually mounted on
the running app") for the two new modules: both are exercised through
``POST /sessions`` and ``GET /sessions/{id}``, not in isolation.

No behaviour is asserted of ``classify()`` or ``resting_rmssd()``
themselves -- T038 deliberately ships them empty; T039/T041/T042/T043
fill in the tiers. What is asserted is that they exist, are callable,
are reached, and that the three session-level columns they will write
survive a full round trip to SQLite and back out of the API.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from fastapi.testclient import TestClient

from runcoach_api.ingestion import fit_parser, hrv_classification, mapping, pipeline, rmssd
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
# A real Garmin Health Snapshot: 120.198 s, 121 records, zero ``hrv``
# messages -- so ``rr_reconstruction.reconstruct()`` returns [] and the
# ``if rr_intervals:`` branch above the call site is never taken.
ZERO_BEAT_FIXTURE = FIXTURES / "sample_health_snapshot.fit"
# An ordinary run, 3127 ``hrv`` messages / 7220 beats -- the branch IS
# taken here, so the call must fire on both sides of the conditional.
BEAT_BEARING_FIXTURE = FIXTURES / "dev_fields_run.fit"

# ``resting_rmssd_ms`` joined the set with the 2026-09-06 amendment (T055).
# It is the **resolved** field E003 reads on either tier; ``rmssd_precomputed``
# stays the device-only audit record. T055 shipped the column with no writer;
# **T065 is the writer** -- it is populated for every successful reading of
# either tier and null for every other outcome, which is what closes IDEA-007's
# null-shaped trap structurally rather than by convention.
_NEW_SESSION_FIELDS = (
    "rmssd_precomputed",
    "hrv_source_tier",
    "rr_source",
    "resting_rmssd_ms",
)


# ---------------------------------------------------------------------------
# the call site
# ---------------------------------------------------------------------------


def test_classify_is_invoked_once_on_a_zero_beat_ingest(monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        hrv_classification, "classify", lambda *args, **kwargs: calls.append(args)
    )

    pipeline.ingest_fit_bytes(ZERO_BEAT_FIXTURE.read_bytes())

    assert len(calls) == 1, (
        f"classify() ran {len(calls)}x on a zero-beat file; expected exactly 1. "
        "A count of 0 means the call is inside `if rr_intervals:` -- which "
        "kills the entire Tier-2 path."
    )


def test_classify_is_invoked_once_on_a_beat_bearing_ingest(monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        hrv_classification, "classify", lambda *args, **kwargs: calls.append(args)
    )

    pipeline.ingest_fit_bytes(BEAT_BEARING_FIXTURE.read_bytes())

    assert len(calls) == 1


def test_classify_receives_messages_session_and_the_reconstructed_beats(monkeypatch) -> None:
    """The signature T039/T041 will build against: ``(messages, session, rr_intervals)``."""
    captured: list[tuple] = []
    monkeypatch.setattr(
        hrv_classification, "classify", lambda *args, **kwargs: captured.append(args)
    )

    pipeline.ingest_fit_bytes(ZERO_BEAT_FIXTURE.read_bytes())

    (args,) = captured
    messages, session, rr_intervals = args
    assert {m.name for m in messages} >= {"session", "record"}
    assert session.source_vendor == "garmin"
    assert rr_intervals == []


def test_classify_runs_before_quality_gates_apply(monkeypatch) -> None:
    """Ordering contract: ``classify()`` may raise quality flags that
    ``quality_gates.apply()`` must already see, so it goes first --
    immediately before ``apply()``, mirroring how ``reconstruct()``
    precedes it for ``hr_source`` (T019 code-review Fix 5)."""
    order: list[str] = []
    monkeypatch.setattr(
        hrv_classification, "classify", lambda *a, **k: order.append("classify")
    )
    real_apply = pipeline.quality_gates.apply

    def spy_apply(session, records):
        order.append("quality_gates.apply")
        return real_apply(session, records)

    monkeypatch.setattr(pipeline.quality_gates, "apply", spy_apply)

    pipeline.ingest_fit_bytes(ZERO_BEAT_FIXTURE.read_bytes())

    assert order == ["classify", "quality_gates.apply"]


# ---------------------------------------------------------------------------
# the two modules exist and are callable with their declared contracts
# ---------------------------------------------------------------------------


def test_classify_mutates_by_reference_and_returns_none() -> None:
    """Same contract as ``quality_gates.apply(session, records)``: mutate
    ``session`` in place, return ``None``."""
    messages = fit_parser.decode(ZERO_BEAT_FIXTURE.read_bytes())
    session, _records = mapping.to_canonical(messages)

    assert hrv_classification.classify(messages, session, []) is None


def test_resting_rmssd_is_exported_and_callable() -> None:
    assert callable(rmssd.resting_rmssd)
    # T038 ships no arithmetic -- T042 supplies it. Only the seam is fixed here.
    assert rmssd.resting_rmssd([]) is None


def test_rmssd_module_does_not_depend_on_the_classification_module() -> None:
    """The two modules are a deliberate seam: ``rmssd.py`` is the pure
    statistic, ``hrv_classification.py`` the policy that consumes it --
    mirroring ``rr_reconstruction.py`` vs ``quality_gates.py``. Keeping
    the dependency one-way is what lets T042 build the computation
    without queueing behind the Tier-1 discriminator.

    Asserted over the import graph rather than the raw source text: the
    module docstring names its counterpart on purpose, to explain the
    seam, and a substring grep would forbid documenting it."""
    tree = ast.parse(Path(rmssd.__file__).read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.add(module)
            imported.update(f"{module}.{alias.name}" for alias in node.names)

    assert not any("hrv_classification" in name for name in imported), imported
    # ...and the reverse direction is the one that is allowed to exist.
    assert "hrv_classification" not in dir(rmssd)


# ---------------------------------------------------------------------------
# the three new session-level fields, end to end
# ---------------------------------------------------------------------------


def test_new_fields_survive_a_full_ingest_to_get_round_trip() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            files={"file": ("sample_health_snapshot.fit", ZERO_BEAT_FIXTURE.read_bytes())},
        )
        assert response.status_code == 201
        session_id = response.json()["session_id"]

        detail = client.get(f"/sessions/{session_id}")

    assert detail.status_code == 200
    body = detail.json()
    for name in _NEW_SESSION_FIELDS:
        assert name in body, f"GET /sessions/{{id}} is missing {name}"
    # T038 wrote nothing here and this asserted all three stayed null;
    # T039's Tier-2 branch now routes this very fixture, so the round
    # trip is asserted against the values it writes instead. What the
    # test is for is unchanged: the three columns survive ingest ->
    # SQLite -> GET. The routing rules themselves are owned by
    # tests/test_resting_hrv_tier2.py.
    assert body["hrv_source_tier"] == "health_snapshot"
    assert body["rmssd_precomputed"] == 37
    assert body["rr_source"] == "health_snapshot_ppg"
    # T055 added the column and deliberately no writer; **T065 is the writer.**
    # This fixture is the Tier-2 positive, so the resolved column now carries the
    # device value that ``rmssd_precomputed`` audits -- the two agree on Tier 2 by
    # construction, because both are written from one read of the message.
    assert body["resting_rmssd_ms"] == 37


def test_post_201_response_does_not_leak_the_new_fields() -> None:
    """Every F004 ``@must`` scenario asserts against ``GET /sessions/{id}``.
    The POST 201 body deliberately stays as it is (``IngestResponse``)."""
    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            files={"file": ("sample_health_snapshot.fit", ZERO_BEAT_FIXTURE.read_bytes())},
        )

    assert response.status_code == 201
    body = response.json()
    for name in _NEW_SESSION_FIELDS:
        assert name not in body


def test_session_rr_source_is_distinct_from_the_per_beat_rr_source() -> None:
    """§2.2.3 defines ``rr_source`` at RR-stream level, but F003 modelled it
    per beat (``rr_intervals.rr_source``). A Tier-2 reading has no beats, so
    it needs a session-level home too -- two different columns in two
    different tables, both surfaced by ``GET /sessions/{id}``."""
    from runcoach_api import db

    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            files={"file": ("dev_fields_run.fit", BEAT_BEARING_FIXTURE.read_bytes())},
        )
        assert response.status_code == 201
        body = client.get(f"/sessions/{response.json()['session_id']}").json()

    assert "rr_source" in body  # session level
    assert len(body["rr_intervals"]) > 0
    assert "rr_source" in body["rr_intervals"][0]  # per beat, unchanged

    conn = db.get_connection()
    try:
        session_columns = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
        beat_columns = {r["name"] for r in conn.execute("PRAGMA table_info(rr_intervals)")}
    finally:
        conn.close()

    assert set(_NEW_SESSION_FIELDS) <= session_columns
    assert "rr_source" in beat_columns


def test_ingest_works_against_a_data_dir_the_server_never_booted() -> None:
    """``main.py``'s lifespan creates the schema on *server startup*, so
    every caller that drives ingestion without booting the ASGI app --
    the CLI ingest path, a batch import, F004's acceptance probe -- used
    to hit a bare ``no such table: sessions``. ``ingest_fit_bytes`` now
    reconciles the schema on the connection it opens, which is also what
    lands F004's three new columns on a pre-existing database at the
    first upload after the upgrade rather than at the next restart."""
    from runcoach_api import db

    # Deliberately no db.init_schema() and no TestClient(app) first:
    # the autouse isolated_data_dir fixture gives a data dir that has
    # never been touched.
    result = pipeline.ingest_fit_bytes(ZERO_BEAT_FIXTURE.read_bytes())

    assert result.session_id

    conn = db.get_connection()
    try:
        detail = db.get_session_detail(conn, result.session_id)
    finally:
        conn.close()

    assert detail is not None
    for name in _NEW_SESSION_FIELDS:
        assert name in detail.keys()
    # Same T038 -> T039 update as above: the fixture is a Health
    # Snapshot, so the Tier-2 branch now populates these. The point of
    # the test is the data dir, not the values.
    assert detail["hrv_source_tier"] == "health_snapshot"


def test_new_columns_are_added_to_a_preexisting_database() -> None:
    """``_reconcile_columns`` handles additive nullable columns, so a
    database created before F004 must gain all of ``_NEW_SESSION_FIELDS``
    without a migration -- including ``resting_rmssd_ms``, which the
    2026-09-06 amendment adds to an already-upgraded database a second
    time."""
    from runcoach_api import db
    from runcoach_api.models import Session

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        conn.execute("DROP TABLE sessions")
        conn.execute(
            """
            CREATE TABLE sessions (
              session_id TEXT PRIMARY KEY, athlete_id TEXT, start_time TEXT NOT NULL,
              sport TEXT NOT NULL, activity_tag TEXT, source_vendor TEXT NOT NULL,
              source_device TEXT, recording_interval TEXT, hr_source TEXT,
              rr_valid_fraction REAL, quality_flags TEXT, summary TEXT, context TEXT,
              UNIQUE (source_device, start_time)
            )
            """
        )
        conn.commit()

        db.init_schema(conn)

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        assert set(_NEW_SESSION_FIELDS) <= columns

        # and the INSERT naming them still works against the reconciled table
        db.persist(
            conn,
            Session(
                session_id="s-reconciled-1",
                sport="running",
                source_vendor="garmin",
                start_time="2026-01-01T00:00:00+00:00",
                source_device="reconcile-device",
            ),
            [],
            [],
            {},
        )
        detail = db.get_session_detail(conn, "s-reconciled-1")
    finally:
        conn.close()

    assert detail is not None
    assert detail["rmssd_precomputed"] is None
    assert detail["hrv_source_tier"] is None
    assert detail["rr_source"] is None
    assert detail["resting_rmssd_ms"] is None


# ---------------------------------------------------------------------------
# T055: the resolved column, added with no writer
# ---------------------------------------------------------------------------


def test_resting_rmssd_ms_is_null_on_an_ordinary_run() -> None:
    """F004 @must "An ordinary run is never treated as a resting-HRV reading"
    now names ``resting_rmssd_ms`` among the fields that must all be null.

    ``dev_fields_run.fit`` is the beat-bearing negative -- 3127 ``hrv``
    messages, so the column is reached on the branch that *does* have beats.
    """
    from runcoach_api import db

    result = pipeline.ingest_fit_bytes(BEAT_BEARING_FIXTURE.read_bytes())

    conn = db.get_connection()
    try:
        detail = db.get_session_detail(conn, result.session_id)
    finally:
        conn.close()

    assert detail is not None
    assert "resting_rmssd_ms" in detail.keys()
    assert detail["activity_tag"] is None
    assert detail["hrv_source_tier"] is None
    assert detail["rmssd_precomputed"] is None
    assert detail["resting_rmssd_ms"] is None


def test_the_pipeline_writes_resting_rmssd_ms_for_a_reading_and_only_for_one() -> None:
    """**Inverted by T065**, which is the writer T055 named when it shipped the
    column with no behaviour and asserted null on every row.

    The two fixtures are the two sides of the column's contract driven through the
    real ``pipeline.ingest_fit_bytes``, not through ``classify()`` directly:

    * ``sample_health_snapshot.fit`` is a successful Tier-2 reading, so the column
      is populated -- and populated *positively*, which is the invariant E003
      relies on to take ``ln(resting_rmssd_ms)`` unguarded.
    * ``dev_fields_run.fit`` is an ordinary 52-minute run carrying 7220 beats. It
      routes nowhere, so it must stay null. It is the guard against the failure
      mode that would make the column worthless: a writer that fires on ingest
      rather than on a reading would fill this row too, and E003's readiness trend
      would then be built partly from runs.
    """
    from runcoach_api import db

    for fixture in (ZERO_BEAT_FIXTURE, BEAT_BEARING_FIXTURE):
        pipeline.ingest_fit_bytes(fixture.read_bytes())

    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT session_id, hrv_source_tier, resting_rmssd_ms FROM sessions"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 2
    by_tier = {row["hrv_source_tier"]: row["resting_rmssd_ms"] for row in rows}
    assert by_tier["health_snapshot"] == 37
    assert by_tier["health_snapshot"] > 0
    assert by_tier[None] is None


def test_a_real_resting_rmssd_ms_round_trips_as_a_json_number_equal_to_37() -> None:
    """Confirms the gotcha rather than assuming it.

    F004's demo probe asserts ``.resting_rmssd_ms == 37`` with ``jq -e`` on a
    ``REAL`` column, so the stored float must reach the HTTP body as a JSON
    number that compares equal to the integer literal. No writer exists yet,
    so the value is persisted directly through ``db.persist`` -- the same
    ``_insert_session`` path T065 will drive -- and read back over the real
    ``GET /sessions/{id}`` route.
    """
    import json

    from runcoach_api import db
    from runcoach_api.models import Session

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.persist(
            conn,
            Session(
                session_id="s-real-affinity-1",
                sport="running",
                source_vendor="garmin",
                start_time="2026-02-02T06:00:00+00:00",
                source_device="affinity-device",
                activity_tag="resting_hrv_check",
                hrv_source_tier="chest_strap_raw",
                resting_rmssd_ms=37.0,
            ),
            [],
            [],
            {},
        )
        stored = conn.execute(
            "SELECT typeof(resting_rmssd_ms) AS t, resting_rmssd_ms AS v"
            " FROM sessions WHERE session_id = 's-real-affinity-1'"
        ).fetchone()
    finally:
        conn.close()

    assert stored["t"] == "real"
    assert stored["v"] == 37

    with TestClient(app) as client:
        detail = client.get("/sessions/s-real-affinity-1")

    assert detail.status_code == 200
    assert detail.json()["resting_rmssd_ms"] == 37
    # ...and as jq sees it: a bare JSON number, not a string.
    assert isinstance(json.loads(detail.text)["resting_rmssd_ms"], float)


# ---------------------------------------------------------------------------
# firmware must keep riding along in source_device
# ---------------------------------------------------------------------------


def test_source_device_still_carries_its_firmware_segment() -> None:
    """F004's Data Model requires ``source_device`` to keep carrying
    firmware: §2.4.5's anti-mixing rule needs it so E003 can re-establish
    the HRV baseline when a device or firmware change shifts the pipeline.
    ``mapping._build_source_device`` returns ``f"{product} fw{firmware}"``
    today, but nothing asserted it -- so a future simplification could drop
    it silently and only be noticed as a wrong baseline months later."""
    messages = fit_parser.decode(ZERO_BEAT_FIXTURE.read_bytes())
    session, _records = mapping.to_canonical(messages)

    assert session.source_device is not None
    assert re.search(r"\bfw\S+", session.source_device), (
        f"source_device {session.source_device!r} lost its fw<version> segment"
    )
