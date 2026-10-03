"""T249 (F007 AC1-AC4): ``hr_sensor_serial`` -- the connected ANT+ heart-rate
sensor's serial, resolved from the file's ``device_info`` entries.

The rule (user ruling, 2026-10-03, verbatim in the task): collect the
distinct non-null ``serial_number`` values over every ``device_info``
entry with ``source_type == 'antplus'`` and ``antplus_device_type ==
'heart_rate'``. Exactly one -> store it. Zero -> ``None``. Two or more ->
``None``. Manufacturer, product, ``garmin_product``, ``software_version``
and ``device_index`` are not inputs. The field records the *pairing*, not
the HR stream's provenance (``hr_source`` holds that); see the
``hr_sensor_serial`` row of spec/02 section 2.2.1.

Adversarial probe table
-----------------------

Enumerated from the F007 ruling before the resolver existed (the
independent-oracle rule: the table was written from the spec text, then
the resolver was written to it, not the other way round). Each row is a
parametrized synthetic case fed through
``mapping._resolve_hr_sensor_serial(mapping._group_by_name([...]))``; the
"what routed" column is what the resolver returned when the module first
went green, and each result is argued, never merely observed.

A ``creator`` entry (``source_type: local``, serial C = 9999) is present
in every row unless the row says otherwise, because every real file has
one and the no-creator-fallback rule (AC3) must be exercised in the
presence of a creator, not in its absence.

== ==================================================== ========= =========================================
#  input ``device_info`` entries (besides the creator)  expected  what routed, and why it is intended
== ==================================================== ========= =========================================
1  one antplus heart_rate, serial S                     S         S: the single-sensor case, AC1.
2  the same sensor re-emitted 3x, serial S each time     S         S: start/lap/end re-emissions of one
                                                                  ``device_index`` collapse to one distinct
                                                                  value; repetition is not conflict (AC4).
3  heart_rate serial None, then serial S (same index)   S         S: the None is discarded before counting,
                                                                  so a serial absent on one emission and
                                                                  present on another resolves (AC1).
4  heart_rate entries, every serial None                None      None: unresolvable, not a guess (AC3).
5  two heart_rate entries, serials S1 != S2             None      None: a conflict is NULL, never the first
                                                                  or the last value (AC4).
6  heart_rate S1, plus stride_speed_distance S2         S1        S1: only heart_rate entries are read; the
                                                                  footpod's serial is invisible (AC4).
7  heart_rate absent; stride_speed_distance and         None      None: no sibling-channel fallback (AC3),
   device type 30, both serial S                                  even though the same strap owns both
                                                                  channels in the corpus.
8  bike_power with serial S only                        None      None: wrong device type (AC4).
9  source_type bluetooth_low_energy,                    None      None: antplus only. A faithful BLE fake
   ble_device_type heart_rate, serial S                           carries no ``antplus_device_type`` key
                                                                  (see the row 9 note), so this row is
                                                                  excluded by the device-type check before
                                                                  the ``source_type`` check is reached.
10 no device_info at all; also no file_id               None      None, no exception, and ``db.persist``
                                                                  succeeds (AC2/AC3 "ingestion succeeds").
11 only the creator entry (local), serial C             None      None: no creator fallback (AC3). A watch
                                                                  identity in a sensor field is worse than
                                                                  nothing.
12 heart_rate, manufacturer polar_electro, product      S         S: manufacturer and product are not
   raw int 2, serial S                                            inputs (AC1; dev_fields_run.fit).
13 heart_rate, garmin_product 255 (OHR broadcast),      S         S: the Negative Class row -- a watch
   serial S                                                       broadcasting optical HR over ANT+ is a
                                                                  connected heart-rate sensor; product is
                                                                  excluded by ruling.
14 heart_rate whose serial is the uint32z invalid       None      Not a test row: ``serial_number`` is
   value (raw 0)                                        (row 4)   uint32z and fitdecode's base-type parser
                                                                  maps raw 0 to ``None`` before any field
                                                                  is read (``fitdecode/types.py`` line 379,
                                                                  ``BaseType(name='uint32z', ...,
                                                                  parse=lambda x: None if x == 0 else x)``),
                                                                  so the case reaches the resolver as row
                                                                  4. No ``0`` stub row: no rule covers a
                                                                  raw 0 and the decoder never delivers one.
15 heart_rate, manufacturer None, serial S              S         S: manufacturer absent is still a
                                                                  heart-rate entry with one serial (AC1).
16 device_type heart_rate (raw 120) with no             None      None: ``source_type`` is not antplus. This
   ``source_type`` field at all, serial S                         is the in-between population -- a
                                                                  third-party or older head unit -- named
                                                                  so it is seen, not accepted by accident.
== ==================================================== ========= =========================================

Row 9 note. In real fitdecode, ``device_info.device_type`` (def_num 1) is a
field with four subfields selected by ``source_type`` (def_num 25):
``ant_device_type`` when ``source_type`` is ``ant``, ``antplus_device_type``
when ``antplus``, ``ble_device_type`` when ``bluetooth_low_energy`` and
``local_device_type`` when ``local`` (``fitdecode/profile.py`` lines
9690-9731, the ``ReferenceField(name='source_type', ...)`` entries). So
``get_value("antplus_device_type")`` resolves only on an antplus entry, and
a faithful BLE fake has no ``antplus_device_type`` key. Row 9 therefore
cannot by itself detect a resolver that dropped the ``source_type ==
'antplus'`` clause: it is already excluded by the device-type check. Row 16
cannot either, for the same reason, when it lacks ``antplus_device_type``.
The ``source_type`` clause is kept because it is the ruling's wording and
because it is what makes the resolver's inputs legible; perturbation (d) in
the commit body records that deleting it survives this module on real
fitdecode shapes, and why.

Real-fixture table
------------------

Census of 2026-10-03 (every fixture through the real decoder; the hand
application of the ruling to the printed ``device_info`` entries agreed
with the task's table before the resolver was written):

- ``dev_fields_run.fit`` 785102823 -- Polar strap; manufacturer not an input.
- ``sample_health_snapshot.fit`` None -- no antplus heart-rate entry.
- ``sample_run.fit`` 3611410126 -- strap connected, HR from wrist: pairing,
  not provenance.
- ``strap_cool_down_walk.fit`` 3611410126.
- ``strap_health_snapshot.fit`` 3611410126.
- ``strap_health_snapshot_hrv.fit`` 3611410126 -- first emission
  ``garmin_product: 21``; product not an input.
- ``strap_hrv_capture.fit`` 3611410126 -- same; the index-6 footpod channel
  is ignored.
- ``strap_hrv_sample_run.fit`` 3611410126 -- same.
- ``strap_run_hrv.fit`` 3611410126.
- ``wrist_ppg_hrv_snapshot.fit`` None -- no antplus heart-rate entry.
- ``wrist_ppg_run.fit`` 3611410126 -- strap connected, HR from wrist.

The end-to-end rows drive ``pipeline.ingest_fit_bytes`` into the isolated
database and read the column back; the upgrade-path row builds the
database from the verbatim pre-F007 ``sessions`` DDL first (AC7 meets AC1).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runcoach_api import db
from runcoach_api.ingestion import fit_parser, mapping, pipeline

FIXTURES = Path(__file__).parent / "fixtures"

# Synthetic serials. Distinct from every real corpus serial so a row can
# never pass by coincidence with a fixture value.
S = 4242
S1 = 1111
S2 = 2222
C = 9999  # the creator (watch) serial -- never the answer

# Every real file's first device_info: the head unit itself.
_CREATOR = {
    "device_index": "creator",
    "source_type": "local",
    "manufacturer": "garmin",
    "garmin_product": "fr945_lte",
    "serial_number": C,
    "software_version": 17.4,
}


def _hr(serial, index=2, **extra):
    """One antplus heart-rate ``device_info`` entry, the HRM-Pro-Plus shape."""
    values = {
        "device_index": index,
        "source_type": "antplus",
        "antplus_device_type": "heart_rate",
        "manufacturer": "garmin",
        "garmin_product": "hrm_pro_plus",
        "serial_number": serial,
    }
    values.update(extra)
    return values


def _antplus(device_type, serial, index):
    """A non-heart-rate antplus entry (footpod, device type 30, power meter)."""
    return {
        "device_index": index,
        "source_type": "antplus",
        "antplus_device_type": device_type,
        "manufacturer": "garmin",
        "garmin_product": "hrm_pro_plus",
        "serial_number": serial,
    }


# (row id, device_info value dicts besides the creator, expected)
_PROBE_ROWS = [
    pytest.param([_hr(S)], S, id="row01-one-heart-rate-entry-resolves"),
    pytest.param([_hr(S), _hr(S), _hr(S)], S, id="row02-same-sensor-re-emitted-three-times"),
    pytest.param([_hr(None), _hr(S)], S, id="row03-serial-absent-then-present-same-index"),
    pytest.param([_hr(None), _hr(None)], None, id="row04-unresolvable-every-serial-none"),
    pytest.param([_hr(S1), _hr(S2, index=3)], None, id="row05-conflict-two-distinct-serials"),
    pytest.param(
        [_hr(S1), _antplus("stride_speed_distance", S2, 6)],
        S1,
        id="row06-footpod-serial-not-read",
    ),
    pytest.param(
        [_antplus("stride_speed_distance", S, 6), _antplus(30, S, 8)],
        None,
        id="row07-absent-no-sibling-channel-fallback",
    ),
    pytest.param([_antplus("bike_power", S, 4)], None, id="row08-wrong-device-type-bike-power"),
    pytest.param(
        [
            {
                "device_index": 2,
                "source_type": "bluetooth_low_energy",
                "ble_device_type": "heart_rate",
                "serial_number": S,
            }
        ],
        None,
        id="row09-ble-heart-rate-antplus-only",
    ),
    pytest.param([], None, id="row11-creator-only-no-creator-fallback"),
    pytest.param(
        [_hr(S, index=3, manufacturer="polar_electro", garmin_product=None, product=2)],
        S,
        id="row12-polar-manufacturer-not-an-input",
    ),
    pytest.param(
        [_hr(S, garmin_product=255)],
        S,
        id="row13-garmin-product-255-ohr-broadcast-product-not-an-input",
    ),
    pytest.param(
        [_hr(S, manufacturer=None, garmin_product=None)],
        S,
        id="row15-manufacturer-none-still-resolves",
    ),
    pytest.param(
        [{"device_index": 2, "device_type": 120, "serial_number": S}],
        None,
        id="row16-no-source-type-field-in-between-population",
    ),
]


@pytest.mark.parametrize("entries, expected", _PROBE_ROWS)
def test_probe_table_row(fake_msg, entries, expected) -> None:
    """Each synthetic row, through the resolver over the grouped messages
    exactly as ``to_canonical`` groups them (the
    ``_build_source_device(_group_by_name([...]))`` call shape of
    ``test_duplicate_upload.py``)."""
    messages = [fake_msg("file_id", {"garmin_product": "fr945_lte"})]
    messages.append(fake_msg("device_info", dict(_CREATOR)))
    messages.extend(fake_msg("device_info", values) for values in entries)

    assert mapping._resolve_hr_sensor_serial(mapping._group_by_name(messages)) == expected


def test_row10_no_device_info_and_no_file_id_resolves_none_without_exception() -> None:
    """Row 10 at the resolver: an empty grouping has no ``device_info``
    bucket at all, and the resolver must treat that as absence."""
    assert mapping._resolve_hr_sensor_serial(mapping._group_by_name([])) is None


def test_row10_no_device_info_ingests_and_persists_null(synthetic, isolated_data_dir) -> None:
    """Row 10 through the real path: ``to_canonical`` on a file with no
    ``device_info`` and no ``file_id`` yields ``None``, and ``db.persist``
    stores NULL -- AC3's "ingestion succeeds", not only "the resolver returns
    None"."""
    session, records = mapping.to_canonical(synthetic())
    assert session.hr_sensor_serial is None

    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.persist(conn, session, records, [], {})
        stored = conn.execute(
            "SELECT hr_sensor_serial FROM sessions WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()
    finally:
        conn.close()

    assert stored is not None
    assert stored["hr_sensor_serial"] is None


# fixture name -> expected hr_sensor_serial (census 2026-10-03; see the docstring).
REAL_FIXTURE_SERIAL: dict[str, int | None] = {
    "dev_fields_run.fit": 785102823,
    "sample_health_snapshot.fit": None,
    "sample_run.fit": 3611410126,
    "strap_cool_down_walk.fit": 3611410126,
    "strap_health_snapshot.fit": 3611410126,
    "strap_health_snapshot_hrv.fit": 3611410126,
    "strap_hrv_capture.fit": 3611410126,
    "strap_hrv_sample_run.fit": 3611410126,
    "strap_run_hrv.fit": 3611410126,
    "wrist_ppg_hrv_snapshot.fit": None,
    "wrist_ppg_run.fit": 3611410126,
}


def test_real_fixture_table_covers_the_whole_corpus() -> None:
    """A fixture added without a row would fall outside the pin silently."""
    assert sorted(p.name for p in FIXTURES.glob("*.fit")) == sorted(REAL_FIXTURE_SERIAL)


@pytest.mark.parametrize("fixture_name", sorted(REAL_FIXTURE_SERIAL))
def test_real_fixture_hr_sensor_serial(fixture_name: str) -> None:
    """Per file, through the real decoder and ``to_canonical``, no database."""
    session, _records = mapping.to_canonical(
        fit_parser.decode((FIXTURES / fixture_name).read_bytes())
    )

    assert session.hr_sensor_serial == REAL_FIXTURE_SERIAL[fixture_name]


def _resolved_data_dir(isolated_data_dir: Path, tmp_path: Path) -> Path:
    # The autouse fixture must have pointed the DB layer at tmp_path before
    # init_schema runs; verify the resolved path rather than trusting it.
    resolved = db._load_config_cached().data_dir
    assert resolved == isolated_data_dir
    assert tmp_path in resolved.parents
    return resolved


@pytest.mark.parametrize(
    "fixture_name, expected",
    [
        pytest.param("strap_hrv_capture.fit", 3611410126, id="strap-capture-stores-the-serial"),
        pytest.param("wrist_ppg_hrv_snapshot.fit", None, id="wrist-snapshot-stores-null"),
    ],
)
def test_ingest_persists_hr_sensor_serial_end_to_end(
    fixture_name: str, expected: int | None, isolated_data_dir: Path, tmp_path: Path
) -> None:
    """``pipeline.ingest_fit_bytes`` into the isolated database, then the
    column read back by the session's own id."""
    data_dir = _resolved_data_dir(isolated_data_dir, tmp_path)

    result = pipeline.ingest_fit_bytes((FIXTURES / fixture_name).read_bytes())

    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        row = conn.execute(
            "SELECT hr_sensor_serial FROM sessions WHERE session_id = ?",
            (result.session_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == expected


# The ``sessions`` DDL exactly as sprint-009 released it (commit c2839b6,
# ``git show c2839b6:runcoach-api/src/runcoach_api/db.py``), copied again
# here because test modules cannot import each other under
# ``--import-mode=importlib``. Only the ``--`` comment block above
# ``resting_rmssd_ms`` is stripped; every column is as at that commit.
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


def test_ingest_into_a_pre_f007_database_fills_the_new_row_and_leaves_the_old_null(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    """AC7 meets AC1: a database built from the pre-F007 DDL and holding one
    old row gains the column through the ingest's own ``init_schema``
    reconcile; the ingested row carries the strap's serial and the old row
    stays NULL (AC8: no backfill, the bytes are gone)."""
    data_dir = _resolved_data_dir(isolated_data_dir, tmp_path)

    conn = db.get_connection()
    try:
        conn.executescript(_PRE_F007_SESSIONS_DDL)
        columns = ", ".join(_PRE_F007_ROW)
        placeholders = ", ".join(f":{name}" for name in _PRE_F007_ROW)
        conn.execute(f"INSERT INTO sessions ({columns}) VALUES ({placeholders})", _PRE_F007_ROW)
        conn.commit()
        pre_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
    finally:
        conn.close()
    assert "hr_sensor_serial" not in pre_columns

    result = pipeline.ingest_fit_bytes((FIXTURES / "strap_hrv_capture.fit").read_bytes())

    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        by_id = dict(conn.execute("SELECT session_id, hr_sensor_serial FROM sessions").fetchall())
    finally:
        conn.close()

    assert by_id[result.session_id] == 3611410126
    assert by_id["pre-f007-1"] is None
    assert len(by_id) == 2
