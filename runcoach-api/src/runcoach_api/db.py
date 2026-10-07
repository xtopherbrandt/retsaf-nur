"""SQLite connection factory, schema DDL, and persistence for ingestion.

``AppConfig.data_dir`` (``config.py``) is the single source of truth
for where the SQLite file lives -- no separate config field is added
here. ``get_connection`` creates the directory if needed and returns a
connection with foreign keys enabled; ``init_schema`` is idempotent
DDL for the four canonical-schema tables and the athlete's entered
profile values (``profile_entries``); ``persist`` writes one
ingested session (session header, records, RR intervals, and
quarantined sidecar values) in a single transaction, and
``delete_session`` removes that same row set in one -- children first,
parent last -- which is what makes re-ingesting a file possible after
the ``UNIQUE (source_device, start_time)`` constraint has claimed its
slot.

TEXT-column serialization convention: ``sessions.quality_flags``,
``sessions.summary``, ``sessions.context``, and
``records.sample_quality`` are stored as ``json.dumps(...)`` and read
back via ``json.loads(...)``.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from runcoach_api import config as config_module
from runcoach_api import profile as profile_module
from runcoach_api.ingestion.exceptions import DuplicateSessionError
from runcoach_api.models import Record, RRInterval, Session

DB_FILENAME = "runcoach.db"


def _json_dump(value):
    """``json.dumps(value)``, passing ``None`` through unchanged."""
    return None if value is None else json.dumps(value)


def _json_load(value, default=None):
    """``json.loads(value)``, returning ``default`` when ``value`` is ``None``."""
    return default if value is None else json.loads(value)


def _bool_to_int(value):
    """Python ``bool``/``None`` -> SQLite ``INTEGER``, preserving ``None``."""
    return None if value is None else int(bool(value))


def _int_to_bool(value):
    """SQLite ``INTEGER``/``None`` -> Python ``bool``, preserving ``None``."""
    return None if value is None else bool(value)


# lru_cache(maxsize=1) below caches the parsed config across calls within a
# process (avoiding a re-read/re-parse of the TOML file on every request).
# Tests get isolation via the autouse `isolated_data_dir` fixture
# (tests/conftest.py), which monkeypatches `config_module.load_config`
# per-test *and* clears this cache before each test -- so a stale
# lru_cache'd config from a previous test's monkeypatch is never seen.
@functools.lru_cache(maxsize=1)
def _load_config_cached() -> config_module.AppConfig:
    return config_module.load_config()


def get_connection() -> sqlite3.Connection:
    """Open (creating if needed) the SQLite database under AppConfig.data_dir."""
    app_config = _load_config_cached()
    data_dir: Path = app_config.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / DB_FILENAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# The single source of truth for the canonical schema. Both table
# creation and the column reconciliation below are derived from this
# one string, so they cannot drift apart.
_SCHEMA_DDL = """
    CREATE TABLE IF NOT EXISTS sessions (
      session_id TEXT PRIMARY KEY, athlete_id TEXT, start_time TEXT NOT NULL,
      sport TEXT NOT NULL, activity_tag TEXT, source_vendor TEXT NOT NULL,
      source_device TEXT, recording_interval TEXT, hr_source TEXT,
      rr_valid_fraction REAL, quality_flags TEXT, summary TEXT, context TEXT,
      rmssd_precomputed REAL, hrv_source_tier TEXT, rr_source TEXT,
      -- resting_rmssd_ms is the RESOLVED field E003 reads on either tier
      -- (device value on Tier 2, system-computed on Tier 1);
      -- rmssd_precomputed above stays the device-only audit record. Same
      -- nullability and REAL affinity as that column deliberately. Added
      -- 2026-09-06; _reconcile_columns lands it on an existing database
      -- and no backfill fills it, so pre-amendment rows stay NULL.
      -- The invariant is scoped to what this feature writes AFTER that
      -- amendment (T077): for such a row, a non-null hrv_source_tier
      -- implies a non-null, strictly positive resting_rmssd_ms, because
      -- both tiers write the two fields at one success point past the
      -- non-positive gate. It is NOT a claim about the table: an
      -- upgraded database satisfies
      --   hrv_source_tier IS NOT NULL AND resting_rmssd_ms IS NULL
      -- for every pre-amendment reading row of *either* tier -- there is
      -- no backfill on either, so a pre-amendment Tier-2 reading is
      -- inside the window exactly as a Tier-1 one is -- which is why nothing here
      -- is expressed as a CHECK and why a consumer must guard the read
      -- across the amendment window rather than assume ln() is safe.
      resting_rmssd_ms REAL,
      -- hr_sensor_serial is the own unit serial of the ANT+ heart-rate
      -- sensor CONNECTED when the session was recorded (usually a chest
      -- strap; a watch broadcasting optical HR over ANT+ counts too), not
      -- the recording watch in source_device. It records the pairing, not
      -- the HR provenance: hr_source reads chest_strap for any connected
      -- heart-rate sensor with HR, so three accepted limits read so too:
      -- a strap paired but not worn, an external optical sensor (an
      -- armband, or that broadcasting watch: the device type does not
      -- tell optical from ECG), and a strap that drops out mid-activity,
      -- which marks the whole session. NULL means
      -- unknown, never "no sensor": no ANT+ heart-rate entry, no valid
      -- serial, conflicting serials, or a row stored before F007 (added
      -- 2026-10-03; _reconcile_columns lands it, nothing backfills it and
      -- no FIT bytes are kept, so such a row stays NULL until the session
      -- is deleted and re-uploaded). Unread (F007 AC6); not in GET (AC9).
      hr_sensor_serial INTEGER,
      -- F016: the profile settings the session's FIT file carried, as
      -- ingestion.profile_values maps them (NULL = the file had no value).
      -- Written with the row, so they go when the session is deleted.
      -- Added 2026-10-07; _reconcile_columns lands them and nothing
      -- backfills them (no FIT bytes are kept). Not in GET /sessions/{id}.
      sex TEXT, body_mass_kg REAL, height_cm INTEGER, resting_hr_bpm INTEGER,
      max_hr_bpm INTEGER, threshold_hr_bpm INTEGER, garmin_activity_class INTEGER,
      -- upload_order: 1 + the largest stored, assigned inside the insert
      -- transaction; it orders uploads that share a start time. Not rowid,
      -- which VACUUM may renumber. NULL on a row stored before F016.
      upload_order INTEGER,
      UNIQUE (source_device, start_time)
    );
    CREATE TABLE IF NOT EXISTS records (
      session_id TEXT NOT NULL REFERENCES sessions(session_id), t REAL NOT NULL,
      lat REAL, lon REAL, distance REAL, speed REAL, heart_rate REAL, cadence REAL,
      altitude REAL, power REAL, power_model TEXT, vertical_oscillation REAL,
      ground_contact_time REAL, gct_balance REAL, step_length REAL, temperature REAL,
      gps_degraded INTEGER, sample_quality TEXT
    );
    CREATE TABLE IF NOT EXISTS rr_intervals (
      session_id TEXT NOT NULL REFERENCES sessions(session_id), seq INTEGER NOT NULL,
      rr_ms REAL NOT NULL, rr_source TEXT, rr_carrier TEXT, is_artefact INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS quarantine_sidecar (
      session_id TEXT NOT NULL REFERENCES sessions(session_id), field_name TEXT NOT NULL,
      value TEXT
    );
    CREATE TABLE IF NOT EXISTS profile_entries (
      -- F016: the athlete's entered profile values, one row per change,
      -- written by write_profile_entries. value is JSON text; NULL is a
      -- clear. entry_id orders the changes (an INTEGER PRIMARY KEY, which
      -- VACUUM keeps). No session_id column, so delete_session never
      -- sweeps an entry.
      entry_id INTEGER PRIMARY KEY, field TEXT NOT NULL, value TEXT,
      set_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS anchor_versions (
      -- F016: per HR anchor (profile.ANCHOR_FIELDS), the last served value
      -- (JSON text) and its version, set by _sync_anchor_versions inside
      -- each write that can change an effective value. source and
      -- source_session_id name what served the value when the version
      -- began. Named source_session_id, with no foreign key, so
      -- _child_tables never lists this table and delete_session never
      -- sweeps the history; it may name a deleted session.
      anchor TEXT PRIMARY KEY, value TEXT NOT NULL, version INTEGER NOT NULL,
      source TEXT NOT NULL, source_session_id TEXT
    );
"""

# The entered-values table's name, for callers and tests that address it.
PROFILE_ENTRIES_TABLE = "profile_entries"

# The anchor version log's name, for callers and tests that address it.
ANCHOR_VERSIONS_TABLE = "anchor_versions"

# The amendment-window predicate F004 publishes for E003, as SQL usable
# directly after ``WHERE``. This is its one code home: F004's Data Model,
# the CHANGELOG's "No backfill" paragraph and the schema comment above all
# describe the same window, and the T076 pin
# (``tests/test_db_schema.py``) establishes in both directions that it
# selects every pre-amendment reading row of either tier and nothing
# written after the 2026-09-06 amendment. F005 excludes what this matches
# from the readiness trend -- ``ln()`` is never evaluated on such a row --
# and cites this constant rather than restating the SQL (IDEA-031,
# IDEA-033: one predicate, one spelling).
PRE_AMENDMENT_WINDOW_PREDICATE = "hrv_source_tier IS NOT NULL AND resting_rmssd_ms IS NULL"


def _expected_schema() -> dict[str, dict[str, str]]:
    """``{table: {column: decl_type}}`` as ``_SCHEMA_DDL`` defines it,
    read back from a throwaway in-memory database.

    Deriving this from the DDL itself (rather than hand-listing the
    columns added over time) is the point: a hand-maintained list only
    ever contains the columns whoever edited it remembered, and the
    first version of this function shipped missing ``sessions.context``
    for exactly that reason.
    """
    probe = sqlite3.connect(":memory:")
    try:
        probe.executescript(_SCHEMA_DDL)
        tables = [
            row[0]
            for row in probe.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        return {
            table: {row[1]: row[2] for row in probe.execute(f"PRAGMA table_info({table})")}
            for table in tables
        }
    finally:
        probe.close()


def _reconcile_columns(conn: sqlite3.Connection) -> None:
    """Additively add any column present in ``_SCHEMA_DDL`` but missing
    from an already-created table.

    ``CREATE TABLE IF NOT EXISTS`` is a no-op against an existing
    table, so a database created before a column was added keeps the
    old layout and every INSERT naming that column fails with "table X
    has no column named Y" -- a 500 on a perfectly valid upload. The
    test suite structurally cannot catch that, because conftest.py
    builds a fresh database per test where the CREATE path always
    includes every column.

    This is not a migration framework, and none is coming: F004 chose a
    documented corpus rebuild over one (see
    ``spec/references/F004-detection-and-quality-rules.md`` §7,
    following the T032 precedent), so the earlier forward reference to
    "the migration framework F003 deferred to F004" no longer describes
    anything that will be built. What this does is add nullable columns
    only -- which is all F004's four new session-level columns need
    (``resting_rmssd_ms`` joined them with the 2026-09-06 amendment). A
    rename or retype still needs a real migration, and will surface here
    as an error rather than being silently papered over.

    **Adding a column is all this does.** It never writes a value into
    one, and the 2026-09-06 Decision Log makes that explicit for
    ``resting_rmssd_ms``: pre-amendment ``resting_hrv_check`` rows keep
    it NULL even though their computed value is sitting in
    ``context.provenance.computed_resting_rmssd_ms``, because those rows
    were produced by the inference predicate that amendment exists to
    discredit. Recovery is re-ingestion under the declaration rule, not
    a backfill.
    """
    for table, expected in _expected_schema().items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table was just created with every column
        for column, decl_type in expected.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl_type}")


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the canonical-schema tables if they don't already exist,
    then additively reconcile any column added after a database was
    first created (see ``_reconcile_columns``), and version the HR anchors
    of a store whose sessions or entries predate the anchor version log
    (``_sync_anchor_versions``; a no-op on a store the write paths kept)."""
    conn.executescript(_SCHEMA_DDL)
    _reconcile_columns(conn)
    _sync_anchor_versions(conn)
    conn.commit()


def _insert_session(conn: sqlite3.Connection, session: Session) -> None:
    conn.execute(
        """
        INSERT INTO sessions (
            session_id, athlete_id, start_time, sport, activity_tag,
            source_vendor, source_device, recording_interval, hr_source,
            rr_valid_fraction, quality_flags, summary, context,
            rmssd_precomputed, resting_rmssd_ms, hrv_source_tier, rr_source,
            hr_sensor_serial, sex, body_mass_kg, height_cm, resting_hr_bpm,
            max_hr_bpm, threshold_hr_bpm, garmin_activity_class, upload_order
        ) VALUES (
            :session_id, :athlete_id, :start_time, :sport, :activity_tag,
            :source_vendor, :source_device, :recording_interval, :hr_source,
            :rr_valid_fraction, :quality_flags, :summary, :context,
            :rmssd_precomputed, :resting_rmssd_ms, :hrv_source_tier, :rr_source,
            :hr_sensor_serial, :sex, :body_mass_kg, :height_cm, :resting_hr_bpm,
            :max_hr_bpm, :threshold_hr_bpm, :garmin_activity_class,
            (SELECT COALESCE(MAX(upload_order), 0) + 1 FROM sessions)
        )
        """,
        {
            "session_id": session.session_id,
            "athlete_id": session.athlete_id,
            "start_time": session.start_time,
            "sport": session.sport,
            "activity_tag": session.activity_tag,
            "source_vendor": session.source_vendor,
            "source_device": session.source_device,
            "recording_interval": session.recording_interval,
            "hr_source": session.hr_source,
            "rr_valid_fraction": session.rr_valid_fraction,
            "rmssd_precomputed": session.rmssd_precomputed,
            # The resolved E003-facing value; rmssd_precomputed above is
            # the device-only audit. See models.Session.
            "resting_rmssd_ms": session.resting_rmssd_ms,
            "hrv_source_tier": session.hrv_source_tier,
            # Session-level (§2.2.3), not the per-beat rr_intervals.rr_source
            # written by _insert_rr_intervals -- see models.Session.
            "rr_source": session.rr_source,
            # The connected ANT+ heart-rate sensor's serial (F007); None
            # when unresolved -- see mapping._resolve_hr_sensor_serial.
            "hr_sensor_serial": session.hr_sensor_serial,
            # The file's profile settings (F016); see models.Session.
            "sex": session.sex,
            "body_mass_kg": session.body_mass_kg,
            "height_cm": session.height_cm,
            "resting_hr_bpm": session.resting_hr_bpm,
            "max_hr_bpm": session.max_hr_bpm,
            "threshold_hr_bpm": session.threshold_hr_bpm,
            "garmin_activity_class": session.garmin_activity_class,
            "quality_flags": _json_dump(session.quality_flags),
            "summary": _json_dump(session.summary),
            "context": _json_dump(dataclasses.asdict(session.context))
            if session.context
            else None,
        },
    )


def _insert_records(conn: sqlite3.Connection, session_id: str, records: list[Record]) -> None:
    conn.executemany(
        """
        INSERT INTO records (
            session_id, t, lat, lon, distance, speed, heart_rate, cadence,
            altitude, power, power_model, vertical_oscillation,
            ground_contact_time, gct_balance, step_length, temperature,
            gps_degraded, sample_quality
        ) VALUES (
            :session_id, :t, :lat, :lon, :distance, :speed, :heart_rate, :cadence,
            :altitude, :power, :power_model, :vertical_oscillation,
            :ground_contact_time, :gct_balance, :step_length, :temperature,
            :gps_degraded, :sample_quality
        )
        """,
        [
            {
                "session_id": session_id,
                "t": r.t,
                "lat": r.lat,
                "lon": r.lon,
                "distance": r.distance,
                "speed": r.speed,
                "heart_rate": r.heart_rate,
                "cadence": r.cadence,
                "altitude": r.altitude,
                "power": r.power,
                "power_model": r.power_model,
                "vertical_oscillation": r.vertical_oscillation,
                "ground_contact_time": r.ground_contact_time,
                "gct_balance": r.gct_balance,
                "step_length": r.step_length,
                "temperature": r.temperature,
                "gps_degraded": _bool_to_int(r.gps_degraded),
                "sample_quality": _json_dump(r.sample_quality),
            }
            for r in records
        ],
    )


def _insert_rr_intervals(
    conn: sqlite3.Connection, session_id: str, rr_intervals: list[RRInterval]
) -> None:
    conn.executemany(
        """
        INSERT INTO rr_intervals (
            session_id, seq, rr_ms, rr_source, rr_carrier, is_artefact
        )
        VALUES (:session_id, :seq, :rr_ms, :rr_source, :rr_carrier, :is_artefact)
        """,
        [
            {
                "session_id": session_id,
                "seq": rr.seq,
                "rr_ms": rr.rr_ms,
                "rr_source": rr.rr_source,
                "rr_carrier": rr.rr_carrier,
                "is_artefact": _bool_to_int(rr.is_artefact),
            }
            for rr in rr_intervals
        ],
    )


def _insert_quarantine_sidecar(
    conn: sqlite3.Connection, session_id: str, quarantine_values: dict
) -> None:
    conn.executemany(
        """
        INSERT INTO quarantine_sidecar (session_id, field_name, value)
        VALUES (:session_id, :field_name, :value)
        """,
        [
            {"session_id": session_id, "field_name": field_name, "value": _json_dump(value)}
            for field_name, value in quarantine_values.items()
        ],
    )


def persist(
    conn: sqlite3.Connection,
    session: Session,
    records: list[Record],
    rr_intervals: list[RRInterval],
    quarantine_values: dict,
) -> None:
    """Write one ingested session's rows in a single transaction.

    Relies on the ``UNIQUE (source_device, start_time)`` constraint on
    ``sessions`` as the sole dedup mechanism -- no application-level
    dedup-check-then-insert logic lives here (that would reintroduce a
    TOCTOU race under concurrent uploads). The insert is attempted
    directly; a resulting ``sqlite3.IntegrityError`` is caught here and
    translated into ``DuplicateSessionError``, carrying the
    ``session_id`` of the row that already occupies this
    ``(source_device, start_time)`` slot (looked up *after* the
    failure, never before it).

    The four inserts are split into private helpers (rather than
    inlined) so a mid-transaction failure can be simulated in tests by
    monkeypatching one of them -- ``sqlite3.Connection`` itself can't
    be monkeypatched (it's an immutable C type), so this is the seam
    that makes the chaos test possible without touching real DB
    internals.

    The HR anchor version log is updated last, in the same transaction
    (``_sync_anchor_versions``), so a refused or failed ingest leaves it
    untouched.
    """
    try:
        with conn:
            _insert_session(conn, session)
            _insert_records(conn, session.session_id, records)
            _insert_rr_intervals(conn, session.session_id, rr_intervals)
            _insert_quarantine_sidecar(conn, session.session_id, quarantine_values)
            _sync_anchor_versions(conn)
    except sqlite3.IntegrityError as exc:
        cur = conn.execute(
            "SELECT session_id FROM sessions WHERE source_device = ? AND start_time = ?",
            (session.source_device, session.start_time),
        )
        row = cur.fetchone()
        if row is None:
            # Not actually the dedup constraint -- e.g. a NOT NULL
            # violation (sessions.sport) blocked the insert before
            # anything committed. Fabricating a DuplicateSessionError
            # here would misattribute a real validation failure as a
            # 409 duplicate-upload response, so let the original
            # IntegrityError propagate instead.
            raise
        raise DuplicateSessionError(row[0]) from exc


def _child_tables() -> list[str]:
    """Every table other than ``sessions`` that carries a ``session_id``,
    read out of ``_SCHEMA_DDL`` rather than hand-listed.

    Derived for the same reason ``_expected_schema`` is: a hand-written
    list only ever holds the tables whoever edited it remembered, and a
    forgotten one leaves orphan rows behind a delete -- rows the next
    ingest of the same file cannot displace, which is a *worse* state
    than the 409 the delete route exists to relieve. Adding a table to
    the DDL with a ``session_id`` column is therefore all it takes to
    have ``delete_session`` clean it up.
    """
    return [
        table
        for table, columns in _expected_schema().items()
        if table != "sessions" and "session_id" in columns
    ]


def _delete_child_rows(conn: sqlite3.Connection, session_id: str) -> None:
    """Remove one session's rows from every child table.

    A private helper rather than an inlined loop for the same reason
    ``persist``'s four insert helpers are: it is the seam a test
    monkeypatches to simulate a mid-transaction failure, since
    ``sqlite3.Connection`` is an immutable C type and cannot be patched
    itself.
    """
    for table in _child_tables():
        # Table names come from the DDL, never from a caller; the
        # session_id -- which does -- is bound as a parameter.
        conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))


def delete_session(conn: sqlite3.Connection, session_id: str) -> bool:
    """Delete one session and all of its child rows in a single
    transaction. Returns ``True`` when a session row was removed and
    ``False`` when ``session_id`` did not exist, so the caller (the
    ``DELETE /sessions/{id}`` route) can turn that into a 404.

    **Children first, parent last, explicitly.** The DDL declares no
    ``ON DELETE`` action and ``get_connection`` runs
    ``PRAGMA foreign_keys = ON``, so deleting the parent first is not
    merely unsupported -- it raises ``IntegrityError``. Relying on a
    cascade was considered and rejected on those two facts read out of
    the code, not on SQLite's documented default (which is the opposite
    of what this connection does).

    This is a **hard** delete, deliberately. A tombstoned row would keep
    occupying its ``UNIQUE (source_device, start_time)`` slot and go on
    answering a re-upload with a 409 -- the exact problem the route
    exists to solve (F004, 2026-09-06 amendment: an athlete whose
    capture ingested during the upgrade window recovers by deleting the
    session and uploading the file again). Nothing here reclassifies or
    backfills anything; delete-and-re-ingest is the sanctioned recovery.

    ``with conn:`` is the whole atomicity story: SQLite opens an
    implicit transaction on the first DELETE and rolls it back if
    anything raises, so a failure part-way through leaves the session
    fully intact rather than stripped of its beats. The HR anchor version
    log is updated in the same transaction (``_sync_anchor_versions``),
    since the delete can change an effective value.
    """
    with conn:
        _delete_child_rows(conn, session_id)
        cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        # rowcount on the parent delete, rather than a preceding SELECT:
        # existence and removal are then decided by one statement inside
        # one transaction, so two concurrent deletes cannot both report
        # success (the second finds no row and returns False).
        deleted = cur.rowcount > 0
        if deleted:
            _sync_anchor_versions(conn)
        return deleted


def get_session_detail(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """Read one ingested session back in the canonical session/record/RR/context shape.

    Returns ``None`` when ``session_id`` doesn't exist so the caller
    (the ``GET /sessions/{id}`` route) can turn that into a 404. Does
    **not** include the quarantine sidecar -- that table is unrelated
    to the canonical schema (§2.3.6 keeps it deliberately separate).
    """
    cur = conn.execute(
        """
        SELECT session_id, athlete_id, start_time, sport, activity_tag,
               source_vendor, source_device, recording_interval, hr_source,
               rr_valid_fraction, quality_flags, summary, context,
               rmssd_precomputed, resting_rmssd_ms, hrv_source_tier, rr_source
        FROM sessions WHERE session_id = ?
        """,
        (session_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None

    records_cur = conn.execute(
        """
        SELECT t, lat, lon, distance, speed, heart_rate, cadence, altitude,
               power, power_model, vertical_oscillation, ground_contact_time,
               gct_balance, step_length, temperature, gps_degraded, sample_quality
        FROM records WHERE session_id = ? ORDER BY t
        """,
        (session_id,),
    )
    records = [
        {
            "t": r["t"],
            "lat": r["lat"],
            "lon": r["lon"],
            "distance": r["distance"],
            "speed": r["speed"],
            "heart_rate": r["heart_rate"],
            "cadence": r["cadence"],
            "altitude": r["altitude"],
            "power": r["power"],
            "power_model": r["power_model"],
            "vertical_oscillation": r["vertical_oscillation"],
            "ground_contact_time": r["ground_contact_time"],
            "gct_balance": r["gct_balance"],
            "step_length": r["step_length"],
            "temperature": r["temperature"],
            "gps_degraded": _int_to_bool(r["gps_degraded"]),
            "sample_quality": _json_load(r["sample_quality"], default=[]),
        }
        for r in records_cur.fetchall()
    ]

    rr_cur = conn.execute(
        """
        SELECT seq, rr_ms, rr_source, rr_carrier, is_artefact
        FROM rr_intervals WHERE session_id = ? ORDER BY seq
        """,
        (session_id,),
    )
    rr_intervals = [
        {
            "seq": r["seq"],
            "rr_ms": r["rr_ms"],
            "rr_source": r["rr_source"],
            "rr_carrier": r["rr_carrier"],
            "is_artefact": _int_to_bool(r["is_artefact"]),
        }
        for r in rr_cur.fetchall()
    ]

    return {
        "session_id": row["session_id"],
        "athlete_id": row["athlete_id"],
        "start_time": row["start_time"],
        "sport": row["sport"],
        "activity_tag": row["activity_tag"],
        "source_vendor": row["source_vendor"],
        "source_device": row["source_device"],
        "recording_interval": row["recording_interval"],
        "hr_source": row["hr_source"],
        "rr_valid_fraction": row["rr_valid_fraction"],
        "rmssd_precomputed": row["rmssd_precomputed"],
        # The resolved reading E003 reads regardless of tier. Distinct
        # from rmssd_precomputed above, which stays the device-only
        # audit -- a Tier-1 reading populates this and leaves that null.
        "resting_rmssd_ms": row["resting_rmssd_ms"],
        "hrv_source_tier": row["hrv_source_tier"],
        # Session-level (§2.2.3). The per-beat carrier of the same enum
        # is each entry's own "rr_source" under "rr_intervals" below.
        "rr_source": row["rr_source"],
        "quality_flags": _json_load(row["quality_flags"], default=[]),
        "summary": _json_load(row["summary"]),
        "context": _json_load(row["context"]),
        "records": records,
        "rr_intervals": rr_intervals,
    }


def read_hrv_rows(conn: sqlite3.Connection, start_iso: str, end_iso: str) -> list[sqlite3.Row]:
    """Read every session's resting-HRV columns over a UTC ``start_time`` range.

    The read behind F005's trend. Returns the four columns the trend consumes
    -- ``session_id``, ``start_time``, ``resting_rmssd_ms``, ``hrv_source_tier``
    -- as ``sqlite3.Row`` objects (``get_connection`` sets the row factory), so
    the consumer reads them **by key**, never by index. Rows are ordered by
    ``start_time`` ascending.

    **The range is UTC and inclusive at both bounds.** ``sessions.start_time``
    is stored as ``datetime.isoformat()`` on an aware UTC value
    (``mapping.py``), i.e. ``2026-01-01T00:00:00+00:00`` -- so callers build
    ``start_iso`` / ``end_iso`` with ``isoformat()`` on aware UTC datetimes and
    the comparison is a like-for-like string comparison against the stored
    spelling. Never hand a ``Z``-suffixed bound in: it does not compare
    correctly against ``+00:00``.

    **Bucketing into local days happens in Python, not here.** SQLite's
    ``date()``/``datetime()`` know fixed offsets and ``localtime`` but not
    IANA zones with DST, so the caller pads the range by at least +/-26 hours
    (UTC+14 through UTC-12) and buckets each row with ``astimezone(zone)``
    afterwards. This function deliberately does no day arithmetic.

    **No tier filter.** A row inside the range with no reading (``hrv_source_tier``
    and ``resting_rmssd_ms`` both null) or inside the pre-amendment window
    (``PRE_AMENDMENT_WINDOW_PREDICATE``) is returned too: the trend lists such
    rows as *excluded with a reason* rather than never seeing them, because
    ``research/00`` PRIN-12 requires a verdict to be reproducible by hand from
    its response, the unserved inputs being PRIN-12's OPEN exceptions
    (PRIN-24, PRIN-27). Selection is the consumer's decision.

    No index serves this scan -- the only composite index is
    ``UNIQUE (source_device, start_time)``, whose leading column is not
    ``start_time`` -- which is irrelevant at single-athlete scale and noted so
    nobody adds a speculative one.
    """
    cur = conn.execute(
        """
        SELECT session_id, start_time, resting_rmssd_ms, hrv_source_tier
        FROM sessions
        WHERE start_time >= ? AND start_time <= ?
        ORDER BY start_time
        """,
        (start_iso, end_iso),
    )
    return cur.fetchall()


def earliest_hrv_reading(conn: sqlite3.Connection, tiers: Sequence[str]) -> str | None:
    """The stored ``start_time`` of the earliest *reading* in the store, or
    ``None`` when there is none -- the one scalar F005's trend reads beside
    ``read_hrv_rows`` (T096, review cycle 3 G13).

    Why it exists: the coverage-gap rule must tell "no earlier reading
    exists" (a new athlete's first capture -- not a gap) from "no earlier
    reading was *read*" (a layoff longer than the 126 days the route reads
    back -- a gap), and the rows alone cannot, since the latest pre-layoff
    reading lies before the first row. This answers the question without
    widening the row read: the earliest reading precedes the rows' span
    exactly when a layoff does. ``MIN`` over the ISO ``+00:00`` spelling is
    chronological for the same reason ``read_hrv_rows``'s range compare is.

    **A reading, not a stored row**, screened here as
    ``hrv_trend._exclusion_reason`` screens rows in Python: a tier the enum
    names (``tiers``, the consumer's ``TIER_FIDELITY``, passed in so this
    module does not import the metric), and a value ``ln`` can take -- a
    null value is the pre-amendment window (``PRE_AMENDMENT_WINDOW_PREDICATE``)
    or an ordinary session, ``> 0`` refuses zero and negatives, and
    ``< 1e999`` refuses ``+inf`` (the literal is beyond REAL's range and
    SQLite reads it as infinity; there is no ``isfinite`` in SQL). ``nan``
    never reaches the comparison: SQLite stores it as ``NULL``. Counting a
    stored row here would report a new athlete's first capture as the end
    of a layoff.
    """
    placeholders = ", ".join("?" for _ in tiers)
    cur = conn.execute(
        f"""
        SELECT MIN(start_time)
        FROM sessions
        WHERE hrv_source_tier IN ({placeholders})
          AND resting_rmssd_ms > 0 AND resting_rmssd_ms < 1e999
        """,
        tuple(tiers),
    )
    return cur.fetchone()[0]


def read_session_feature_inputs(conn: sqlite3.Connection, session_id: str) -> tuple[dict, list[dict]] | None:
    """Read what ``metrics.session_features.compute_session_features`` consumes for one session.

    Returns ``None`` when ``session_id`` does not exist, so the features route
    can serve the same 404 as ``GET /sessions/{id}``. Otherwise a pair: the
    session mapping with exactly the keys ``session_id``, ``sport``,
    ``quality_flags`` (JSON-decoded, ``[]`` when null) and ``context``
    (JSON-decoded, ``{}`` when null; the ``env_*`` values live there), and the
    records the transform reads -- ``t``, ``distance``, ``speed``,
    ``altitude``, ``heart_rate``, ``cadence``, ``power``, ``power_model``,
    ``gps_degraded`` and ``sample_quality`` (JSON-decoded) -- ``ORDER BY t,
    rowid``, so records with equal ``t`` keep stored order (F013 reference,
    section 2). Nothing else is selected: no other session column reaches the
    features response through this reader, and ``get_session_detail`` and its
    ordering are untouched. Read-only: no ``init_schema``, no write.
    """
    cur = conn.execute(
        "SELECT session_id, sport, quality_flags, context FROM sessions WHERE session_id = ?",
        (session_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    session = {
        "session_id": row["session_id"],
        "sport": row["sport"],
        "quality_flags": _json_load(row["quality_flags"], default=[]),
        "context": _json_load(row["context"], default={}) or {},
    }

    records_cur = conn.execute(
        """
        SELECT t, distance, speed, altitude, heart_rate, cadence, power, power_model,
               gps_degraded, sample_quality
        FROM records WHERE session_id = ? ORDER BY t, rowid
        """,
        (session_id,),
    )
    rows = [
        {
            "t": r["t"],
            "distance": r["distance"],
            "speed": r["speed"],
            "altitude": r["altitude"],
            "heart_rate": r["heart_rate"],
            "cadence": r["cadence"],
            "power": r["power"],
            "power_model": r["power_model"],
            "gps_degraded": _int_to_bool(r["gps_degraded"]),
            "sample_quality": _json_load(r["sample_quality"], default=[]),
        }
        for r in records_cur.fetchall()
    ]
    return session, rows


def write_profile_entries(
    conn: sqlite3.Connection, values: Mapping[str, object], set_at: str | None = None
) -> str:
    """Write the athlete's entered profile values, one ``profile_entries`` row per field.

    ``values`` maps a field of ``profile.ENTERED_FIELDS`` to its entered value,
    or to ``None``, which writes a clear (a row whose value is NULL). Every row
    of one call shares ``set_at`` (an ISO timestamp; default: now in UTC,
    ``isoformat()``), which is returned. An empty mapping writes nothing. A
    field outside ``ENTERED_FIELDS`` raises ``ValueError`` before any row is
    written; the rows of one call are one transaction. Values are not
    validated here: ``PATCH /me`` screens them and calls this. The caller has
    run ``init_schema``, as ``persist``'s callers do. The HR anchor version
    log is updated in the same transaction (``_sync_anchor_versions``).
    """
    unknown = sorted(set(values) - set(profile_module.ENTERED_FIELDS))
    if unknown:
        raise ValueError(f"not an entered profile field: {', '.join(unknown)}")
    if set_at is None:
        set_at = datetime.now(UTC).isoformat()
    with conn:
        conn.executemany(
            f"INSERT INTO {PROFILE_ENTRIES_TABLE} (field, value, set_at) VALUES (?, ?, ?)",
            [(field, _json_dump(value), set_at) for field, value in values.items()],
        )
        _sync_anchor_versions(conn)
    return set_at


def read_profile_inputs(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """Read what ``profile.resolve`` consumes: every stored session's profile columns and
    every entered-value row.

    Returns a pair. The sessions: one mapping per stored session with exactly
    ``session_id``, ``start_time``, ``upload_order``, ``sport`` and the six
    ``profile.FIT_FIELDS`` columns, ``ORDER BY start_time, upload_order``. The
    entries: one mapping per ``profile_entries`` row with ``entry_id``,
    ``field``, ``value`` (JSON-decoded, ``None`` for a clear) and ``set_at``,
    ``ORDER BY entry_id``. Read-only: no ``init_schema``, no write; nothing is
    cached, so the profile is recomputed from whatever is stored when read.
    """
    columns = ("session_id", "start_time", "upload_order", "sport", *profile_module.FIT_FIELDS)
    sessions = [
        {c: r[c] for c in columns}
        for r in conn.execute(
            f"SELECT {', '.join(columns)} FROM sessions ORDER BY start_time, upload_order"
        ).fetchall()
    ]
    entries = [
        {
            "entry_id": r["entry_id"],
            "field": r["field"],
            "value": _json_load(r["value"]),
            "set_at": r["set_at"],
        }
        for r in conn.execute(
            f"SELECT entry_id, field, value, set_at FROM {PROFILE_ENTRIES_TABLE} ORDER BY entry_id"
        ).fetchall()
    ]
    return sessions, entries


def _read_anchor_log(conn: sqlite3.Connection) -> dict[str, tuple[object, int]]:
    """The anchor version log as ``{anchor: (value, version)}``, value JSON-decoded."""
    return {
        r["anchor"]: (_json_load(r["value"]), r["version"])
        for r in conn.execute(f"SELECT anchor, value, version FROM {ANCHOR_VERSIONS_TABLE}")
    }


def _write_anchor_log(
    conn: sqlite3.Connection, changes: Mapping[str, profile_module.AnchorLogChange]
) -> None:
    """Set the version log rows ``profile.next_versions`` returned. A private helper so a
    test can fail the transaction straight after the log write."""
    conn.executemany(
        f"""
        INSERT INTO {ANCHOR_VERSIONS_TABLE} (anchor, value, version, source, source_session_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (anchor) DO UPDATE SET
            value = excluded.value, version = excluded.version,
            source = excluded.source, source_session_id = excluded.source_session_id
        """,
        [
            (field, _json_dump(c.value), c.version, c.source, c.source_session_id)
            for field, c in changes.items()
        ],
    )


def _sync_anchor_versions(conn: sqlite3.Connection) -> None:
    """Recompute the four HR anchors from what is stored and move each one's version
    when its served value differs from the value the log holds.

    Called inside the transaction of every write that can change an effective
    value -- ``persist``, ``delete_session`` and ``write_profile_entries`` -- so
    the log sees each change, including one a later write reverses before
    anything reads it. An unavailable anchor leaves its row untouched, so
    188, unavailable, 188 keeps one version. Commits nothing itself.
    """
    sessions, entries = read_profile_inputs(conn)
    resolved = profile_module.resolve(sessions, entries)
    _write_anchor_log(conn, profile_module.next_versions(resolved, _read_anchor_log(conn)))


def read_hr_anchors(conn: sqlite3.Connection) -> dict[str, profile_module.Anchor]:
    """The four HR anchors as ``profile.anchors`` serves them: the effective values under
    the ordering rule, each served one with its version from the log.

    Read-only. Raises ``ValueError`` when the log does not hold a served value,
    which only a write outside ``persist``, ``delete_session`` and
    ``write_profile_entries`` can cause; ``init_schema`` repairs such a store.
    """
    sessions, entries = read_profile_inputs(conn)
    resolved = profile_module.resolve(sessions, entries)
    return profile_module.anchors(resolved, _read_anchor_log(conn))
