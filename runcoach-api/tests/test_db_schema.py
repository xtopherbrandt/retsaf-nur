"""Tests for runcoach_api.db: connection factory + schema DDL.

Covers T017's walking-skeleton requirements: get_connection() creates
the data dir and opens the SQLite file, and init_schema() creates all
four canonical-schema tables idempotently.
"""

from __future__ import annotations

from runcoach_api import db
from runcoach_api.models import RRInterval


def test_get_connection_creates_data_dir_and_db_file(isolated_data_dir):
    assert not isolated_data_dir.exists()

    conn = db.get_connection()
    conn.close()

    assert isolated_data_dir.exists()
    assert (isolated_data_dir / db.DB_FILENAME).exists()


def test_get_connection_enables_foreign_keys():
    conn = db.get_connection()
    try:
        cur = conn.execute("PRAGMA foreign_keys")
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def test_init_schema_creates_all_four_tables():
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        table_names = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    assert {"sessions", "records", "rr_intervals", "quarantine_sidecar"} <= table_names


def test_init_schema_is_idempotent():
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.init_schema(conn)  # must not raise
    finally:
        conn.close()


def test_sessions_unique_constraint_on_device_and_start_time():
    import sqlite3

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        conn.execute(
            """
            INSERT INTO sessions (session_id, start_time, sport, source_vendor, source_device)
            VALUES ('s1', '2026-01-01T00:00:00Z', 'run', 'garmin', 'dev-1')
            """
        )
        conn.commit()

        with_raises = False
        try:
            conn.execute(
                """
                INSERT INTO sessions (session_id, start_time, sport, source_vendor, source_device)
                VALUES ('s2', '2026-01-01T00:00:00Z', 'run', 'garmin', 'dev-1')
                """
            )
            conn.commit()
        except sqlite3.IntegrityError:
            with_raises = True

        assert with_raises
    finally:
        conn.close()



# ---------------------------------------------------------------------------
# Schema reconciliation for databases created before a column was added
# ---------------------------------------------------------------------------

# The verbatim schema as shipped at T017 (commit 12a8b5d) -- the oldest
# database vintage that can exist in the wild. Copied from that commit
# rather than hand-written, because a hand-written "old" schema only
# reflects which columns the author remembers being new: the first
# version of this test invented one that still had `context`, and so it
# passed while a real T017-era database still 500'd on every upload.
_T017_DDL = """
    CREATE TABLE sessions (
      session_id TEXT PRIMARY KEY, athlete_id TEXT, start_time TEXT NOT NULL,
      sport TEXT NOT NULL, activity_tag TEXT, source_vendor TEXT NOT NULL,
      source_device TEXT, recording_interval TEXT, hr_source TEXT,
      quality_flags TEXT, summary TEXT,
      UNIQUE (source_device, start_time)
    );
    CREATE TABLE records (
      session_id TEXT NOT NULL REFERENCES sessions(session_id), t REAL NOT NULL,
      lat REAL, lon REAL, distance REAL, speed REAL, heart_rate REAL, cadence REAL,
      altitude REAL, power REAL, power_model TEXT, vertical_oscillation REAL,
      ground_contact_time REAL, gct_balance REAL, step_length REAL, temperature REAL,
      sample_quality TEXT
    );
    CREATE TABLE rr_intervals (
      session_id TEXT NOT NULL REFERENCES sessions(session_id), seq INTEGER NOT NULL,
      rr_ms REAL NOT NULL, rr_source TEXT, is_artefact INTEGER DEFAULT 0
    );
    CREATE TABLE quarantine_sidecar (
      session_id TEXT NOT NULL REFERENCES sessions(session_id), field_name TEXT NOT NULL,
      value TEXT
    );
"""


def _columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_init_schema_adds_every_column_missing_from_a_t017_era_database():
    conn = db.get_connection()
    try:
        conn.executescript(_T017_DDL)
        conn.commit()

        assert "context" not in _columns(conn, "sessions")
        assert "gps_degraded" not in _columns(conn, "records")

        db.init_schema(conn)

        # Every column the current DDL declares must now be present --
        # asserted against the DDL itself, not a remembered list.
        for table, expected in db._expected_schema().items():
            assert expected.keys() <= _columns(conn, table), table
    finally:
        conn.close()


def test_real_ingest_succeeds_against_a_t017_era_database(isolated_data_dir):
    """The assertion the first version of this test was missing.

    Checking PRAGMA table_info proves the columns exist; it does not
    prove an ingest works. This runs the actual pipeline end to end
    against a stale database, which is what was really broken.
    """
    from pathlib import Path

    from runcoach_api.ingestion.pipeline import ingest_fit_bytes

    conn = db.get_connection()
    try:
        conn.executescript(_T017_DDL)
        conn.commit()
    finally:
        conn.close()

    conn = db.get_connection()
    try:
        db.init_schema(conn)
    finally:
        conn.close()

    fixture = Path(__file__).parent / "fixtures" / "sample_run.fit"
    result = ingest_fit_bytes(fixture.read_bytes())

    assert result.session_id

    conn = db.get_connection()
    try:
        detail = db.get_session_detail(conn, result.session_id)
    finally:
        conn.close()
    assert detail is not None
    assert len(detail["records"]) > 0


def test_init_schema_reconciliation_is_idempotent():
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.init_schema(conn)  # must not raise "duplicate column name"

        cols = [row["name"] for row in conn.execute("PRAGMA table_info(records)")]
        assert cols.count("gps_degraded") == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# T055 -- the additive ``resting_rmssd_ms`` column, and the no-backfill rule
#
# Adversarial-probe enumeration for the schema-reconciliation path, per
# .claude/rules/learnings/adversarial-input-probes-are-a-task-deliverable.md.
# ``_reconcile_columns`` reads exactly two things: the expected column set the
# DDL declares, and ``PRAGMA table_info`` for each live table. The degenerate
# forms of the live table, each driven end to end below:
#
#   | probe (live sessions table)                    | expected outcome            |
#   |------------------------------------------------|-----------------------------|
#   | missing the column, T017 vintage               | added, NULL                 |
#   | missing the column, sprint-003 vintage,        | added, and stays NULL --    |
#   |   holding a *successful* pre-amendment reading |   nothing read forward      |
#   | already has the column                         | no-op, no duplicate, no raise|
#   | carries a column the DDL never declared        | stranger untouched, add ok  |
#   | absent entirely (fresh CREATE)                 | created carrying the column |
#
# Row 2 is the one that matters. It is the only coverage of the amendment's
# single irreversible data decision, and a row of nulls cannot stand in for it:
# the scenario's Given is a reading that *succeeded* under the old predicate.
#
# These rows were enumerated from the F004 acceptance scenario and the
# ``_reconcile_columns`` docstring **before** the DDL was edited, not read back
# off the implementation afterwards -- the distinction
# [[contract-tables-need-an-independent-oracle]] asks tables to state.
# ---------------------------------------------------------------------------

# The ``sessions`` DDL exactly as sprint-003 released it: the current DDL minus
# ``resting_rmssd_ms``. Copied verbatim rather than paraphrased, for the same
# reason ``_T017_DDL`` above is -- an invented "old" schema contains only the
# columns whoever wrote it remembered were new.
_PRE_AMENDMENT_SESSIONS_DDL = """
    CREATE TABLE sessions (
      session_id TEXT PRIMARY KEY, athlete_id TEXT, start_time TEXT NOT NULL,
      sport TEXT NOT NULL, activity_tag TEXT, source_vendor TEXT NOT NULL,
      source_device TEXT, recording_interval TEXT, hr_source TEXT,
      rr_valid_fraction REAL, quality_flags TEXT, summary TEXT, context TEXT,
      rmssd_precomputed REAL, hrv_source_tier TEXT, rr_source TEXT,
      UNIQUE (source_device, start_time)
    );
"""


def test_resting_rmssd_ms_mirrors_rmssd_precomputed_nullable_real():
    """The resolved column takes the device-audit column's exact shape."""
    sessions = db._expected_schema()["sessions"]

    assert "resting_rmssd_ms" in sessions
    assert sessions["resting_rmssd_ms"] == sessions["rmssd_precomputed"] == "REAL"

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        info = {row["name"]: row for row in conn.execute("PRAGMA table_info(sessions)")}
    finally:
        conn.close()

    assert info["resting_rmssd_ms"]["notnull"] == info["rmssd_precomputed"]["notnull"] == 0
    assert info["resting_rmssd_ms"]["pk"] == 0
    assert info["resting_rmssd_ms"]["dflt_value"] is None


def test_a_pre_amendment_reading_is_not_backfilled_into_resting_rmssd_ms():
    """F004 @must "Pre-amendment readings are not carried forward".

    The Given is a **successful** pre-amendment Tier-1 reading, not a row of
    nulls: ``activity_tag = 'resting_hrv_check'``, ``hrv_source_tier =
    'chest_strap_raw'``, ``rmssd_precomputed`` NULL (Tier 1 never wrote it),
    and the system-computed value sitting in
    ``context.provenance.computed_resting_rmssd_ms`` -- the exact shape
    cycle-1 produced. Adding the column must leave it NULL. Those rows came
    from the inference predicate this amendment exists to discredit, so
    copying them into the column E003 is told to read would launder them:
    afterwards a cool-down walk and a genuine supine capture are the same
    column, same tier, indistinguishable.
    """
    import json

    context = {
        "ingested_at": "2026-09-05T06:12:00+00:00",
        "provenance": {
            "raw_sport_value": 1,
            "computed_resting_rmssd_ms": 41.52,
        },
        "env_temperature_c": None,
        "env_humidity_pct": None,
        "env_wind_ms": None,
        "env_wind_dir": None,
        "subjective": None,
    }

    conn = db.get_connection()
    try:
        conn.executescript(_PRE_AMENDMENT_SESSIONS_DDL)
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, start_time, sport, activity_tag, source_vendor,
                source_device, rr_valid_fraction, quality_flags, summary, context,
                rmssd_precomputed, hrv_source_tier, rr_source
            ) VALUES (
                'pre-amendment-1', '2026-09-05T06:10:00+00:00', 'running',
                'resting_hrv_check', 'garmin', 'FR945-LTE', 1.0, '[]',
                '{"duration_s": 150.797}', ?, NULL, 'chest_strap_raw',
                'chest_strap_ecg'
            )
            """,
            (json.dumps(context),),
        )
        conn.commit()

        assert "resting_rmssd_ms" not in _columns(conn, "sessions")

        db.init_schema(conn)

        assert "resting_rmssd_ms" in _columns(conn, "sessions")

        row = conn.execute(
            "SELECT resting_rmssd_ms FROM sessions WHERE session_id = 'pre-amendment-1'"
        ).fetchone()
        detail = db.get_session_detail(conn, "pre-amendment-1")
    finally:
        conn.close()

    # The reading is still a reading -- the row is neither deleted nor rewritten...
    assert detail is not None
    assert detail["activity_tag"] == "resting_hrv_check"
    assert detail["hrv_source_tier"] == "chest_strap_raw"
    assert detail["rmssd_precomputed"] is None
    # ...and the computed value is still sitting in provenance, unread.
    assert detail["context"]["provenance"]["computed_resting_rmssd_ms"] == 41.52

    # ...but nothing carried it forward. This is the assertion the task exists for.
    assert row["resting_rmssd_ms"] is None
    assert detail["resting_rmssd_ms"] is None


def test_the_residual_null_state_is_reachable_on_an_upgraded_database():
    """T077. The Data Model's structural claim is scoped to post-amendment rows
    **because this state exists**, and this test is the pin that keeps it named.

    F004's Data Model once said, unqualified, that "there is no successful
    reading for which this column is null", and authorised E003 to take
    ``ln(resting_rmssd_ms)`` unguarded on that basis. On any database upgraded
    across the 2026-09-06 amendment that is false: a pre-amendment Tier-1
    reading carries a non-null ``hrv_source_tier`` and, because there is no
    backfill, a null ``resting_rmssd_ms``. So the exact query the task names --

        SELECT count(*) FROM sessions
         WHERE hrv_source_tier IS NOT NULL AND resting_rmssd_ms IS NULL

    -- returns a non-zero count, which is [[IDEA-007]]'s null-shaped trap in
    precisely the state the contract claimed could not exist.

    The hazard is worse inside the window than the general one the claim was
    written to close, but it is **not** differential tier survival -- corrected
    2026-09-07 (code review iteration 3, S1). There is no backfill on *either*
    tier, so a pre-amendment Tier-2 row's ``resting_rmssd_ms`` is null exactly as
    a Tier-1 row's is, and both drop out of
    ``WHERE resting_rmssd_ms IS NOT NULL`` **together** --
    ``test_the_published_window_predicate_selects_the_window_and_nothing_else``
    below selects its ``pre-tier1`` and its ``pre-tier2`` case alike. What is left
    is an **era-wide hole**: every reading taken before the amendment disappears
    from the series at once, with no error and no empty result, and the trend
    resumes on the far side as though the eras were contiguous. §2.4.5's
    tier-change baseline reset is not the mechanism that fails -- no tier survives
    the filter for the series to change *from*. The differential-survival reading
    belonged to ``rmssd_precomputed``, whose Tier-1 rows really were the only null
    ones; it does not transfer to this column.

    This asserts **reachability**, not desirability. The residual state is the
    correct outcome of the no-backfill decision; what was wrong was the prose
    denying it. Pinning it here means a future amendment cannot quietly
    re-assert the unqualified invariant -- reinstating a backfill, or
    tightening the column, would turn this red and force the Data Model
    sentence to be reconciled in the same breath.
    """
    import json

    context = {
        "ingested_at": "2026-09-05T06:12:00+00:00",
        "provenance": {
            "raw_sport_value": 1,
            "computed_resting_rmssd_ms": 41.52,
        },
    }

    conn = db.get_connection()
    try:
        conn.executescript(_PRE_AMENDMENT_SESSIONS_DDL)
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, start_time, sport, activity_tag, source_vendor,
                source_device, rr_valid_fraction, quality_flags, summary, context,
                rmssd_precomputed, hrv_source_tier, rr_source
            ) VALUES (
                'pre-amendment-residual', '2026-09-05T06:10:00+00:00', 'running',
                'resting_hrv_check', 'garmin', 'FR945-LTE', 1.0, '[]',
                '{"duration_s": 150.797}', ?, NULL, 'chest_strap_raw',
                'chest_strap_ecg'
            )
            """,
            (json.dumps(context),),
        )
        conn.commit()

        db.init_schema(conn)

        residual = conn.execute(
            "SELECT count(*) AS n FROM sessions "
            "WHERE hrv_source_tier IS NOT NULL AND resting_rmssd_ms IS NULL"
        ).fetchone()["n"]
        resolved = conn.execute(
            "SELECT count(*) AS n FROM sessions "
            "WHERE resting_rmssd_ms IS NOT NULL"
        ).fetchone()["n"]
    finally:
        conn.close()

    # The residual state the corrected Data Model now names in prose.
    assert residual == 1, (
        "hrv_source_tier IS NOT NULL AND resting_rmssd_ms IS NULL must remain "
        "reachable on an upgraded database -- if this is 0, either a backfill "
        "was added or the no-backfill decision changed, and F004's Data Model "
        "claim must be re-scoped to match."
    )
    # ...and the row is invisible to the query E003 would naturally write,
    # which is the specific hazard: it vanishes rather than erroring.
    assert resolved == 0


def test_reconciling_a_database_that_already_has_the_column_is_a_no_op():
    """Probe: the column is already there. A blind ``ALTER TABLE ADD COLUMN``
    would raise "duplicate column name"; it must not be issued at all."""
    conn = db.get_connection()
    try:
        db.init_schema(conn)

        db.init_schema(conn)  # must not raise

        cols = [row["name"] for row in conn.execute("PRAGMA table_info(sessions)")]
    finally:
        conn.close()

    assert cols.count("resting_rmssd_ms") == 1


def test_reconciliation_leaves_an_unrelated_extra_column_alone():
    """Probe: the live table carries a column the DDL does not declare.

    ``_reconcile_columns`` is additive only -- it must neither drop the
    stranger nor let its presence stop the genuinely-missing column landing.
    """
    conn = db.get_connection()
    try:
        conn.executescript(_PRE_AMENDMENT_SESSIONS_DDL)
        conn.execute("ALTER TABLE sessions ADD COLUMN athlete_nickname TEXT")
        conn.execute(
            """
            INSERT INTO sessions (session_id, start_time, sport, source_vendor,
                                  source_device, athlete_nickname)
            VALUES ('stranger-1', '2026-01-02T00:00:00+00:00', 'running', 'garmin',
                    'dev-stranger', 'the athlete')
            """
        )
        conn.commit()

        db.init_schema(conn)

        cols = _columns(conn, "sessions")
        row = conn.execute(
            "SELECT athlete_nickname, resting_rmssd_ms FROM sessions"
            " WHERE session_id = 'stranger-1'"
        ).fetchone()
    finally:
        conn.close()

    assert "athlete_nickname" in cols
    assert "resting_rmssd_ms" in cols
    assert row["athlete_nickname"] == "the athlete"
    assert row["resting_rmssd_ms"] is None


def test_a_t017_era_database_also_gains_resting_rmssd_ms():
    """Probe: the oldest vintage in the wild clears every column at once."""
    conn = db.get_connection()
    try:
        conn.executescript(_T017_DDL)
        conn.commit()

        db.init_schema(conn)

        cols = _columns(conn, "sessions")
    finally:
        conn.close()

    assert "resting_rmssd_ms" in cols


# ---------------------------------------------------------------------------
# T076 -- the published amendment-window predicate, pinned in both directions.
# ---------------------------------------------------------------------------

# The predicate F004's Data Model and the CHANGELOG's "No backfill" paragraph
# publish for E003. Since T080 its one code home is
# ``db.PRE_AMENDMENT_WINDOW_PREDICATE``; the pin test below exercises that
# constant rather than a restated copy, and
# ``test_predicate_constant_matches_the_pinned_sql`` holds the published
# spelling so the constant and the prose cannot drift. Two terms, and T076
# confirmed empirically that a third is not needed -- see the test below.
WINDOW_PREDICATE = db.PRE_AMENDMENT_WINDOW_PREDICATE


def _varied_rr(n: int = 6) -> list[RRInterval]:
    """A chest-strap beat stream whose successive differences are non-zero, so
    Tier 1's strict pairwise rMSSD is strictly positive. Shared by the T076 pin
    and the T080 range read: both need a persisted row that carries a reading."""
    values = [800.0, 900.0, 850.0, 950.0, 870.0, 920.0]
    return [
        RRInterval(
            seq=i, rr_ms=values[i % len(values)], rr_source="chest_strap_ecg", is_artefact=False
        )
        for i in range(n)
    ]

# Every way a **post-amendment** row can be a resting-HRV capture the system
# recognised and yet carry no reading. This is the negative class of the
# published predicate: each row must NOT be selected by it.
#
# Oracle provenance, stated because it changes the rows' evidential weight
# (`.claude/rules/learnings/contract-tables-need-an-independent-oracle.md`):
# the rows were enumerated from **F004's own "A capture failing a quality gate
# yields no reading" Scenario Outline plus its two amendment scenarios**
# (computed zero; declared-but-beatless), which is the spec's own enumeration of
# "recognised, but no reading" -- not from reading the classifier's branches.
# They were then reconciled against ``hrv_classification.py``, and two cases the
# outline does not name were added from that reconciliation and are marked as
# such: the *override* route to beatlessness, and the Tier-2 unparseable
# ``rmssd_hrv``. The vetoed and undeclared rows are controls -- they are not
# recognised captures at all, so a predicate that selected one would be wrong
# for a different reason than the ones above.
#
# Each entry is (name, expected quality flag or None, expected activity_tag).
_POST_AMENDMENT_NON_READINGS = (
    # -- F004's Scenario Outline, Tier 1 ------------------------------------
    ("tier1_declared_too_short", "hrv_capture_too_short", "resting_hrv_check"),
    ("tier1_declared_low_quality", "hrv_capture_low_quality", "resting_hrv_check"),
    ("tier1_declared_beatless", "hrv_capture_no_beats", "resting_hrv_check"),
    ("tier1_declared_hrv_msgs_zero_beats", "hrv_capture_no_beats", "resting_hrv_check"),
    ("tier1_declared_computed_zero", "hrv_reading_unavailable", "resting_hrv_check"),
    ("tier1_declared_no_derivable_pair", "hrv_reading_unavailable", "resting_hrv_check"),
    # -- reconciliation addition: the *other* declaration route -------------
    ("tier1_override_beatless", "hrv_capture_no_beats", "resting_hrv_check"),
    # -- F004's Scenario Outline, Tier 2 ------------------------------------
    ("tier2_device_value_absent", "hrv_reading_unavailable", "health_snapshot"),
    ("tier2_device_value_zero", "hrv_reading_unavailable", "health_snapshot"),
    ("tier2_device_value_negative", "hrv_reading_unavailable", "health_snapshot"),
    # -- reconciliation addition: "the Tier-2 coercion keeps its own meaning"
    ("tier2_device_value_unparseable", "hrv_reading_unavailable", "health_snapshot"),
    # -- controls: never recognised as a capture, so never tagged or flagged
    ("undeclared_capture", None, None),
    ("declared_but_vetoed_duration", None, None),
    ("declared_but_vetoed_heart_rate", None, None),
)


def test_the_published_window_predicate_selects_the_window_and_nothing_else(
    synthetic, classified
):
    """T076. The two-sided pin on the predicate F004 and the CHANGELOG publish.

        SELECT session_id FROM sessions
         WHERE hrv_source_tier IS NOT NULL AND resting_rmssd_ms IS NULL

    T077 established the **positive** half -- that state is reachable on an
    upgraded database. Publishing the query as *the* window-exclusion predicate
    makes a second and much stronger claim that nothing had checked: that it
    selects **only** the window. E003 is told to exclude what this matches, so a
    legitimate post-amendment row inside it would be a real reading silently
    deleted from the readiness trend -- the same class of silent error the whole
    amendment exists to remove, pointed the other way.

    Both directions are therefore established here against **constructed rows**,
    not against a reading of the code:

    *Positive.* Two pre-amendment reading rows -- one of each tier -- are
    inserted into a pre-amendment ``sessions`` table and the database is then
    upgraded by ``init_schema``. Both are selected.

    *Negative.* Every post-amendment way a recognised capture can end with no
    reading is driven through the **real** ``mapping.to_canonical`` ->
    ``hrv_classification.classify`` pair and persisted through the real
    ``db.persist``, so it is the actual writers under test rather than a model
    of them. See ``_POST_AMENDMENT_NON_READINGS`` for the enumeration and where
    it came from. **None is selected**, and the reason is structural: both tiers
    write ``hrv_source_tier`` and ``resting_rmssd_ms`` at the same single
    success point, past every gate, so a post-amendment row cannot hold one
    without the other.

    **The conclusion, which is the deliverable: a third term is not needed.**
    Each candidate was checked and refused. ``activity_tag IS NOT NULL``
    excludes nothing the tier term does not -- a gated post-amendment capture
    is tagged too, as the table below asserts row by row.
    ``quality_flags = '[]'`` is actively wrong: ``quality_gates.apply`` runs
    *after* ``classify()`` and appends ``smart_recording`` / ``gps_degraded``
    / ``cadence_lock`` to any session, so a good pre-amendment reading recorded
    under smart recording would fall out of the window and its inference-era
    verdict would reach E003 unexcluded. A bound on
    ``json_extract(context, '$.ingested_at')`` does work, but it needs a
    per-install release timestamp and reads a JSON blob rather than a column,
    where the tier/reading pair separates the two eras structurally.

    The rows are asserted afterwards to be genuinely gated -- tagged, flagged,
    tier null -- so this cannot pass by having constructed nothing, the failure
    mode ``contract-tables-need-an-independent-oracle`` warns about.

    **Perturbation evidence** (delete the rule, watch the row go red), run at
    T076 and both times against the headline assertion rather than the guards:

    * Write ``hrv_source_tier`` on Tier 2's row-3 branch (absent / non-positive
      device value) and four constructed rows join the window.
    * Write ``hrv_source_tier`` before Tier 1's ``computed <= 0`` gate and the
      computed-zero row joins it.

    Neither perturbation reddens any other test in the ``db_schema`` selection,
    so this is the pin carrying that evidence and not a borrowed one.
    """
    import json
    from datetime import UTC, datetime, timedelta

    declared_profile = "HRV Snapshot"
    snapshot_sport = 60
    base = datetime(2026, 3, 1, tzinfo=UTC)

    def flat(rr_ms, n):
        """Beats whose successive differences are all exactly zero (or, with
        ``rr_ms`` ``None``, a stream from which no pair can contribute)."""
        return [
            RRInterval(seq=i, rr_ms=rr_ms, rr_source="chest_strap_ecg", is_artefact=False)
            for i in range(n)
        ]

    class _HrvMsg:
        """One ``hrv`` message whose beat array reconstructs to nothing -- R4's
        "beats present, zero beats reconstructed" state, which no corpus file
        occupies and which claims Tier 1 rather than falling through."""

        name = "hrv"

        def __init__(self) -> None:
            self.fields: list = []

        def get_value(self, name, fallback=None):
            return [None] if name == "time" else fallback

    def resting(n, **extra):
        values = {
            "total_timer_time": 150.0,
            "avg_heart_rate": 60,
            "sport_profile_name": declared_profile,
            "start_time": base + timedelta(minutes=n),
        }
        values.update(extra)
        return synthetic(**values)

    def snapshot(n, **extra):
        return synthetic(
            sport=snapshot_sport, start_time=base + timedelta(minutes=n), **extra
        )

    declared = [declared_profile]

    # Built in the same order as ``_POST_AMENDMENT_NON_READINGS``.
    non_readings = [
        classified(resting(1, total_timer_time=100.0), _varied_rr(), 1.0, declared),
        classified(resting(2), _varied_rr(), 0.5, declared),
        classified(resting(3), [], None, declared),
        classified(resting(4) + [_HrvMsg()], [], None, declared),
        classified(resting(5), flat(1000.0, 6), 1.0, declared),
        classified(resting(6), flat(None, 2), 1.0, declared),
        classified(
            synthetic(
                total_timer_time=150.0,
                avg_heart_rate=60,
                start_time=base + timedelta(minutes=7),
            ),
            [],
            None,
            [],
            True,
        ),
        classified(snapshot(8)),
        classified(snapshot(9, rmssd_hrv=0)),
        classified(snapshot(10, rmssd_hrv=-5)),
        classified(snapshot(11, rmssd_hrv=("crafted",))),
        classified(resting(12, sport_profile_name="Run"), _varied_rr(), 1.0, declared),
        classified(resting(13, total_timer_time=6000.0), _varied_rr(), 1.0, declared),
        classified(resting(14, avg_heart_rate=140), _varied_rr(), 1.0, declared),
    ]

    # The two post-amendment **successes**, which satisfy the scoped invariant
    # and must equally not be selected -- for the opposite reason.
    successes = [
        classified(resting(20), _varied_rr(), 1.0, declared),
        classified(snapshot(21, rmssd_hrv=37)),
    ]

    assert len(non_readings) == len(_POST_AMENDMENT_NON_READINGS)

    pre_amendment_context = json.dumps(
        {
            "ingested_at": "2026-09-05T06:12:00+00:00",
            "provenance": {"computed_resting_rmssd_ms": 41.52},
        }
    )

    conn = db.get_connection()
    try:
        conn.executescript(_PRE_AMENDMENT_SESSIONS_DDL)
        for session_id, start, tag, tier, rr_source, precomputed in (
            (
                "pre-tier1",
                "2026-09-05T06:10:00+00:00",
                "resting_hrv_check",
                "chest_strap_raw",
                "chest_strap_ecg",
                None,
            ),
            (
                "pre-tier2",
                "2026-09-05T06:40:00+00:00",
                "health_snapshot",
                "health_snapshot",
                "health_snapshot_ppg",
                37,
            ),
        ):
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id, start_time, sport, activity_tag, source_vendor,
                    source_device, rr_valid_fraction, quality_flags, summary,
                    context, rmssd_precomputed, hrv_source_tier, rr_source
                ) VALUES (?, ?, 'running', ?, 'garmin', 'FR945-LTE', 1.0, '[]',
                          '{"duration_s": 150.797}', ?, ?, ?, ?)
                """,
                (
                    session_id,
                    start,
                    tag,
                    pre_amendment_context,
                    precomputed,
                    tier,
                    rr_source,
                ),
            )
        conn.commit()

        # The upgrade the amendment performs on a real install.
        db.init_schema(conn)

        for session in non_readings + successes:
            db.persist(conn, session, [], [], {})

        selected = {
            row["session_id"]
            for row in conn.execute(
                f"SELECT session_id FROM sessions WHERE {WINDOW_PREDICATE}"
            )
        }
        stored = {
            row["session_id"]: row
            for row in conn.execute(
                "SELECT session_id, hrv_source_tier, resting_rmssd_ms, activity_tag,"
                " quality_flags FROM sessions"
            )
        }
    finally:
        conn.close()

    # --- direction 1: it selects the window --------------------------------
    assert {"pre-tier1", "pre-tier2"} <= selected, (
        "the published predicate must select every pre-amendment reading row, "
        "on either tier -- that is the window E003 is told to exclude"
    )

    # --- direction 2: it selects nothing else ------------------------------
    assert selected == {"pre-tier1", "pre-tier2"}, (
        "the published predicate selected a post-amendment row. E003 is told to "
        "exclude everything this matches, so a legitimate row inside it is a "
        "real reading silently deleted from the readiness trend. Unexpected: "
        f"{sorted(selected - {'pre-tier1', 'pre-tier2'})}"
    )

    # --- the constructed rows are genuinely what the table claims ----------
    # Without this the negative half could pass by having built nothing.
    for session, (name, flag, tag) in zip(non_readings, _POST_AMENDMENT_NON_READINGS):
        row = stored[session.session_id]
        assert row["activity_tag"] == tag, name
        flags = json.loads(row["quality_flags"])
        assert flags == ([flag] if flag else []), f"{name}: {flags}"
        assert row["hrv_source_tier"] is None, name
        assert row["resting_rmssd_ms"] is None, name

    for session in successes:
        row = stored[session.session_id]
        assert row["hrv_source_tier"] is not None
        assert row["resting_rmssd_ms"] > 0


# ---------------------------------------------------------------------------
# T080 -- the predicate has a code home, and the trend's range read.
# ---------------------------------------------------------------------------


def test_predicate_constant_matches_the_pinned_sql():
    """The published window predicate lives in ``db.py`` as a constant, spelled
    exactly as F004's Data Model and the CHANGELOG publish it. The literal is
    restated here on purpose: this test is the guard that nobody re-inlines or
    re-words the SQL in either place (IDEA-031 / IDEA-033 name that drift), and
    the pin test above imports the constant rather than a copy."""
    assert (
        db.PRE_AMENDMENT_WINDOW_PREDICATE
        == "hrv_source_tier IS NOT NULL AND resting_rmssd_ms IS NULL"
    )
    # The T076 pin exercises the published constant, not a restatement.
    assert WINDOW_PREDICATE is db.PRE_AMENDMENT_WINDOW_PREDICATE
    # It is a bare predicate, usable after WHERE without further dressing.
    assert not db.PRE_AMENDMENT_WINDOW_PREDICATE.lstrip().upper().startswith("WHERE")


def test_read_hrv_rows_returns_only_rows_in_the_utc_range(synthetic, classified):
    """``db.read_hrv_rows(conn, start_iso, end_iso)`` selects the four columns
    the trend needs over a **UTC** range that is inclusive at both bounds.

    Bounds are built with ``datetime.isoformat()`` on aware UTC values, the
    same form ``mapping.py`` stores (``+00:00``, never ``Z``), so the
    comparison is against the stored spelling and not a hand-built suffix.

    Rows are persisted through the real ``mapping`` -> ``classify`` ->
    ``db.persist`` chain. The range read does **not** filter on tier: an
    in-range row with no reading comes back too (tier and reading ``None``),
    because the trend lists such rows as excluded with a reason rather than
    never seeing them (T083).
    """
    from datetime import UTC, datetime, timedelta

    declared_profile = "HRV Snapshot"
    start = datetime(2026, 2, 10, 12, 0, tzinfo=UTC)
    end = start + timedelta(days=3)

    def reading(at):
        return classified(
            synthetic(
                total_timer_time=150.0,
                avg_heart_rate=60,
                sport_profile_name=declared_profile,
                start_time=at,
            ),
            _varied_rr(),
            1.0,
            [declared_profile],
        )

    before = reading(start - timedelta(seconds=1))
    at_start = reading(start)
    inside = reading(start + timedelta(days=1, hours=6))
    at_end = reading(end)
    after = reading(end + timedelta(seconds=1))
    # An ordinary run inside the range: recognised as nothing, stored anyway.
    plain_run = classified(synthetic(start_time=start + timedelta(days=2)))

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        for session in (after, before, at_end, plain_run, inside, at_start):
            db.persist(conn, session, [], [], {})
        rows = db.read_hrv_rows(conn, start.isoformat(), end.isoformat())
    finally:
        conn.close()

    by_id = {row["session_id"]: row for row in rows}
    assert set(by_id) == {
        at_start.session_id,
        inside.session_id,
        at_end.session_id,
        plain_run.session_id,
    }, "inclusive at both bounds; a row exactly at end_iso is returned"
    assert before.session_id not in by_id
    assert after.session_id not in by_id

    for row in rows:
        assert set(row.keys()) == {
            "session_id",
            "start_time",
            "resting_rmssd_ms",
            "hrv_source_tier",
        }

    assert by_id[at_end.session_id]["start_time"] == end.isoformat()
    assert by_id[at_end.session_id]["start_time"].endswith("+00:00")
    for session in (at_start, inside, at_end):
        assert by_id[session.session_id]["hrv_source_tier"] == "chest_strap_raw"
        assert by_id[session.session_id]["resting_rmssd_ms"] > 0
    assert by_id[plain_run.session_id]["hrv_source_tier"] is None
    assert by_id[plain_run.session_id]["resting_rmssd_ms"] is None

    # Rows come back in start_time order so the consumer never re-sorts.
    assert [r["start_time"] for r in rows] == sorted(r["start_time"] for r in rows)


# ---------------------------------------------------------------------------
# T248 (F007) -- the additive ``hr_sensor_serial`` column, and its no-backfill rule
# ---------------------------------------------------------------------------

# The ``sessions`` DDL exactly as sprint-009 released it (commit c2839b6,
# ``git show c2839b6:runcoach-api/src/runcoach_api/db.py``): the current DDL
# minus ``hr_sensor_serial``. Copied verbatim rather than paraphrased, for the
# same reason ``_T017_DDL`` and ``_PRE_AMENDMENT_SESSIONS_DDL`` above are -- an
# invented "old" schema contains only the columns whoever wrote it remembered
# were new. Only the ``--`` comment block above ``resting_rmssd_ms`` is
# stripped; every column is as at that commit.
_PRE_F007_SESSIONS_DDL = """
    CREATE TABLE sessions (
      session_id TEXT PRIMARY KEY, athlete_id TEXT, start_time TEXT NOT NULL,
      sport TEXT NOT NULL, activity_tag TEXT, source_vendor TEXT NOT NULL,
      source_device TEXT, recording_interval TEXT, hr_source TEXT,
      rr_valid_fraction REAL, quality_flags TEXT, summary TEXT, context TEXT,
      rmssd_precomputed REAL, hrv_source_tier TEXT, rr_source TEXT,
      resting_rmssd_ms REAL,
      UNIQUE (source_device, start_time)
    );
"""

# One row of that vintage, every column populated with a distinctive value so
# that "survives byte-for-byte" is asserted on each pre-existing column rather
# than on a row of nulls.
_PRE_F007_ROW = {
    "session_id": "pre-f007-1",
    "athlete_id": "athlete-a",
    "start_time": "2026-09-28T06:10:00+00:00",
    "sport": "running",
    "activity_tag": "resting_hrv_check",
    "source_vendor": "garmin",
    "source_device": "fr945_lte fw17.4",
    "recording_interval": "1hz",
    "hr_source": "chest_strap",
    "rr_valid_fraction": 0.98,
    "quality_flags": '["smart_recording"]',
    "summary": '{"duration_s": 150.797}',
    "context": '{"ingested_at": "2026-09-28T06:12:00+00:00", "provenance": {}}',
    "rmssd_precomputed": None,
    "hrv_source_tier": "chest_strap_raw",
    "rr_source": "chest_strap_ecg",
    "resting_rmssd_ms": 41.52,
}


def _pre_f007_database_with_one_row(conn) -> None:
    conn.executescript(_PRE_F007_SESSIONS_DDL)
    columns = ", ".join(_PRE_F007_ROW)
    placeholders = ", ".join(f":{name}" for name in _PRE_F007_ROW)
    conn.execute(f"INSERT INTO sessions ({columns}) VALUES ({placeholders})", _PRE_F007_ROW)
    conn.commit()


def test_init_schema_adds_hr_sensor_serial_to_a_pre_f007_database_without_data_loss():
    """F007 AC7. A database created before F007, holding a row, gains the
    column through ``_reconcile_columns`` in place: nullable INTEGER, no
    default, the row intact on every pre-existing column and NULL in the new
    one."""
    conn = db.get_connection()
    try:
        _pre_f007_database_with_one_row(conn)
        assert "hr_sensor_serial" not in _columns(conn, "sessions")

        db.init_schema(conn)

        info = {row["name"]: row for row in conn.execute("PRAGMA table_info(sessions)")}
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = 'pre-f007-1'"
        ).fetchone()
    finally:
        conn.close()

    assert "hr_sensor_serial" in info
    assert info["hr_sensor_serial"]["type"] == "INTEGER"
    assert info["hr_sensor_serial"]["notnull"] == 0
    assert info["hr_sensor_serial"]["dflt_value"] is None
    assert info["hr_sensor_serial"]["pk"] == 0

    assert row is not None
    for column, value in _PRE_F007_ROW.items():
        assert row[column] == value, column
    assert row["hr_sensor_serial"] is None


def test_hr_sensor_serial_is_not_backfilled_on_reinit():
    """F007 AC8. ``init_schema`` runs at startup and on every ingest, so it
    runs many times over the life of one database. A second run on the
    upgraded database must neither raise nor write anything into the column:
    the FIT bytes are not retained, so there is nothing to backfill from."""
    conn = db.get_connection()
    try:
        _pre_f007_database_with_one_row(conn)

        db.init_schema(conn)
        db.init_schema(conn)  # must not raise "duplicate column name"

        cols = [row["name"] for row in conn.execute("PRAGMA table_info(sessions)")]
        row = conn.execute(
            "SELECT hr_sensor_serial, resting_rmssd_ms FROM sessions"
            " WHERE session_id = 'pre-f007-1'"
        ).fetchone()
    finally:
        conn.close()

    assert cols.count("hr_sensor_serial") == 1
    assert row["hr_sensor_serial"] is None
    # ...and the upgrade touched nothing beside it.
    assert row["resting_rmssd_ms"] == 41.52


def test_insert_session_carries_hr_sensor_serial_to_the_row():
    """The insert path carries the field: a ``Session`` persisted with the
    HRM-Pro Plus serial reads back the same integer via a direct SELECT. The
    default is ``None``, so a writer that does not resolve it stores NULL
    (the dataclass default). ``get_session_detail`` is deliberately not consulted:
    F007 AC9 keeps the field out of the response."""
    from runcoach_api.models import Session

    assert Session.__dataclass_fields__["hr_sensor_serial"].default is None

    with_serial = Session(
        session_id="strap-1",
        sport="running",
        source_vendor="garmin",
        start_time="2026-10-01T06:00:00+00:00",
        source_device="fr945_lte fw17.4",
        hr_sensor_serial=3611410126,
    )
    without_serial = Session(
        session_id="wrist-1",
        sport="running",
        source_vendor="garmin",
        start_time="2026-10-02T06:00:00+00:00",
        source_device="fr945_lte fw17.4",
    )

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db._insert_session(conn, with_serial)
        db._insert_session(conn, without_serial)
        conn.commit()
        stored = {
            row["session_id"]: row["hr_sensor_serial"]
            for row in conn.execute("SELECT session_id, hr_sensor_serial FROM sessions")
        }
    finally:
        conn.close()

    assert stored == {"strap-1": 3611410126, "wrist-1": None}
    assert isinstance(stored["strap-1"], int)
