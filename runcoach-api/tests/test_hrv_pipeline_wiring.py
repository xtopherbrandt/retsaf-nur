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

_NEW_SESSION_FIELDS = ("rmssd_precomputed", "hrv_source_tier", "rr_source")


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
        # T038 writes nothing; T039 onwards populate these.
        assert body[name] is None, f"{name} should still be null after T038"


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

    assert {"rmssd_precomputed", "hrv_source_tier", "rr_source"} <= session_columns
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
        assert detail[name] is None


def test_new_columns_are_added_to_a_preexisting_database() -> None:
    """``_reconcile_columns`` handles additive nullable columns, so a
    database created before F004 must gain the three without a migration."""
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
        assert {"rmssd_precomputed", "hrv_source_tier", "rr_source"} <= columns

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
