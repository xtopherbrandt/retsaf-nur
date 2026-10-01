"""SQLite connection factory, schema DDL, and persistence for ingestion.

``AppConfig.data_dir`` (``config.py``) is the single source of truth
for where the SQLite file lives -- no separate config field is added
here. ``get_connection`` creates the directory if needed and returns a
connection with foreign keys enabled; ``init_schema`` is idempotent
DDL for the four canonical-schema tables; ``persist`` writes one
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
from collections.abc import Sequence
from pathlib import Path

from runcoach_api import config as config_module
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
"""

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
    first created (see ``_reconcile_columns``)."""
    conn.executescript(_SCHEMA_DDL)
    _reconcile_columns(conn)
    conn.commit()


def _insert_session(conn: sqlite3.Connection, session: Session) -> None:
    conn.execute(
        """
        INSERT INTO sessions (
            session_id, athlete_id, start_time, sport, activity_tag,
            source_vendor, source_device, recording_interval, hr_source,
            rr_valid_fraction, quality_flags, summary, context,
            rmssd_precomputed, resting_rmssd_ms, hrv_source_tier, rr_source
        ) VALUES (
            :session_id, :athlete_id, :start_time, :sport, :activity_tag,
            :source_vendor, :source_device, :recording_interval, :hr_source,
            :rr_valid_fraction, :quality_flags, :summary, :context,
            :rmssd_precomputed, :resting_rmssd_ms, :hrv_source_tier, :rr_source
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
    """
    try:
        with conn:
            _insert_session(conn, session)
            _insert_records(conn, session.session_id, records)
            _insert_rr_intervals(conn, session.session_id, rr_intervals)
            _insert_quarantine_sidecar(conn, session.session_id, quarantine_values)
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
    fully intact rather than stripped of its beats.
    """
    with conn:
        _delete_child_rows(conn, session_id)
        cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        # rowcount on the parent delete, rather than a preceding SELECT:
        # existence and removal are then decided by one statement inside
        # one transaction, so two concurrent deletes cannot both report
        # success (the second finds no row and returns False).
        return cur.rowcount > 0


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
    (PRIN-27). Selection is the consumer's decision.

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
