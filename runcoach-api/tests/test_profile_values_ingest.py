"""F016 AC1: each session stores the profile values its FIT file carries.

Two halves:

1. **Every fixture, end to end.** Each file in ``tests/fixtures`` is uploaded
   through ``POST /sessions`` and the seven stored columns are read straight
   off the ``sessions`` row. The oracle is an independent ``fitdecode``
   census of the same bytes, written here and importing nothing from
   ``runcoach_api``: it reads the first ``user_profile`` and the first
   ``zones_target`` message and applies the reference's mapping table to the
   fields' raw values. A per-file table of both sides is printed (run with
   ``-s``), and two rows are pinned to literals so the census cannot drift
   with the extractor.
2. **The mapping rules, one unit row each**, on synthetic message lists fed
   to ``profile_values.extract``.

The fake message below mirrors ``fitdecode.FitDataMessage.get_value``'s real
signature (``fitdecode/records.py``): keyword-only ``fallback`` and
``raw_value``, returning ``fallback`` when the field is absent. A FIT invalid
value decodes as ``None`` in both ``value`` and ``raw_value``.
"""

from __future__ import annotations

from pathlib import Path

import fitdecode
import pytest
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api.ingestion import profile_values
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
ALL_FIXTURES = sorted(p.name for p in FIXTURES.glob("*.fit"))

COLUMNS = (
    "sex",
    "body_mass_kg",
    "height_cm",
    "resting_hr_bpm",
    "max_hr_bpm",
    "threshold_hr_bpm",
    "garmin_activity_class",
)


# --- the independent census ---------------------------------------------------


def _census(path: Path) -> dict:
    """The seven values the reference's table maps, read with ``fitdecode`` alone."""
    profile = zones = None
    with fitdecode.FitReader(str(path)) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "user_profile" and profile is None:
                profile = {f.name: f.raw_value for f in frame.fields}
            elif frame.name == "zones_target" and zones is None:
                zones = {f.name: f.raw_value for f in frame.fields}
    profile = profile or {}
    zones = zones or {}

    def positive(raw):
        return raw if isinstance(raw, int) and raw > 0 else None

    weight_raw = profile.get("weight")
    activity_class = profile.get("activity_class")
    return {
        "sex": {0: "female", 1: "male"}.get(profile.get("gender")),
        # FIT weight is uint16 at scale 10; 0xFFFE means "calculating".
        "body_mass_kg": weight_raw / 10
        if isinstance(weight_raw, int) and weight_raw > 0 and weight_raw != 0xFFFE
        else None,
        "height_cm": positive(profile.get("height")),
        "resting_hr_bpm": positive(profile.get("resting_heart_rate")),
        "max_hr_bpm": positive(zones.get("max_heart_rate")),
        "threshold_hr_bpm": positive(zones.get("threshold_heart_rate")),
        "garmin_activity_class": activity_class if isinstance(activity_class, int) else None,
    }


def _stored(session_id: str) -> dict:
    conn = db_module.get_connection()
    try:
        row = conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, session_id
    return {c: row[c] for c in COLUMNS}


def _upload(client: TestClient, filename: str) -> str:
    raw = (FIXTURES / filename).read_bytes()
    response = client.post("/sessions", files={"file": (filename, raw)})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _print_row(filename: str, stored: dict, census: dict) -> None:
    print(f"\n[profile values] {filename}")
    for column in COLUMNS:
        mark = "ok" if stored[column] == census[column] else "DIFF"
        print(f"  {column:<22} stored={stored[column]!r:<10} census={census[column]!r:<10} {mark}")


def test_the_corpus_is_the_thirteen_fixtures_the_census_covers() -> None:
    """Guards the parametrisation below from passing over an empty or shrunken glob."""
    assert len(ALL_FIXTURES) == 13, ALL_FIXTURES


@pytest.mark.parametrize("filename", ALL_FIXTURES)
def test_every_fixture_stores_the_profile_values_its_file_carries(filename: str) -> None:
    census = _census(FIXTURES / filename)
    # Every corpus file carries both messages; a census of all-absent values
    # would make the comparison vacuous.
    assert census["max_hr_bpm"] is not None and census["sex"] is not None, census

    with TestClient(app) as client:
        session_id = _upload(client, filename)
    stored = _stored(session_id)

    _print_row(filename, stored, census)
    assert stored == census
    # Type fidelity, not only equality: SQLite would compare 180 == 180.0.
    for column in ("height_cm", "resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm", "garmin_activity_class"):
        assert type(stored[column]) is int, (column, stored[column])
    assert type(stored["body_mass_kg"]) is float


PINNED = {
    "sample_run.fit": {
        "sex": "male",
        "body_mass_kg": 71.7,
        "height_cm": 180,
        "resting_hr_bpm": 47,
        "max_hr_bpm": 188,
        "threshold_hr_bpm": 169,
        "garmin_activity_class": 50,
    },
    "dev_fields_run.fit": {
        "sex": "male",
        "body_mass_kg": 82.6,
        "height_cm": 189,
        "resting_hr_bpm": 47,
        "max_hr_bpm": 189,
        "threshold_hr_bpm": 162,
        "garmin_activity_class": 70,
    },
}


@pytest.mark.parametrize("filename", sorted(PINNED))
def test_two_fixtures_store_the_literal_values_ac1_pins(filename: str) -> None:
    with TestClient(app) as client:
        session_id = _upload(client, filename)
    stored = _stored(session_id)
    print(f"\n[pinned] {filename} {stored}")
    assert stored == PINNED[filename]


def test_upload_order_counts_up_across_uploads_and_is_set_in_the_insert() -> None:
    """The explicit upload-order column the start-time tie-break reads: MAX + 1
    at insert time, independent of rowid."""
    with TestClient(app) as client:
        first = _upload(client, "sample_run.fit")
        second = _upload(client, "dev_fields_run.fit")
        third = _upload(client, "wrist_ppg_run.fit")

    conn = db_module.get_connection()
    try:
        order = dict(conn.execute("SELECT session_id, upload_order FROM sessions").fetchall())
    finally:
        conn.close()
    print(f"\n[upload order] {order}")
    assert [order[first], order[second], order[third]] == [1, 2, 3]


# --- extractor unit rows -------------------------------------------------------


class _Msg:
    """``fitdecode.FitDataMessage`` stand-in: ``fields`` maps a name to ``(value, raw_value)``."""

    def __init__(self, name: str, fields: dict) -> None:
        self.name = name
        self._fields = fields

    def get_value(self, field_name, *, fallback=None, raw_value=False):
        if field_name not in self._fields:
            return fallback
        value, raw = self._fields[field_name]
        return raw if raw_value else value


def _profile(**overrides):
    fields = {
        "gender": ("male", 1),
        "weight": (71.7, 717),
        "height": (1.8, 180),
        "resting_heart_rate": (47, 47),
        "activity_class": (50, 50),
    }
    fields.update(overrides)
    return _Msg("user_profile", {k: v for k, v in fields.items() if v is not ...})


def _zones(**overrides):
    fields = {"max_heart_rate": (188, 188), "threshold_heart_rate": (169, 169)}
    fields.update(overrides)
    return _Msg("zones_target", {k: v for k, v in fields.items() if v is not ...})


FULL = {
    "sex": "male",
    "body_mass_kg": 71.7,
    "height_cm": 180,
    "resting_hr_bpm": 47,
    "max_hr_bpm": 188,
    "threshold_hr_bpm": 169,
    "garmin_activity_class": 50,
}


def test_extract_maps_a_full_pair_of_messages() -> None:
    assert profile_values.extract([_profile(), _zones()]) == FULL


def test_extract_accepts_an_empty_message_list() -> None:
    assert profile_values.extract([]) == dict.fromkeys(COLUMNS)


def test_extract_returns_exactly_the_seven_fields() -> None:
    assert tuple(profile_values.extract([])) == COLUMNS
    assert profile_values.FIELDS == COLUMNS


@pytest.mark.parametrize(
    ("message", "absent"),
    [
        ("user_profile", ("sex", "body_mass_kg", "height_cm", "resting_hr_bpm", "garmin_activity_class")),
        ("zones_target", ("max_hr_bpm", "threshold_hr_bpm")),
    ],
)
def test_a_missing_message_stores_its_fields_absent(message: str, absent: tuple) -> None:
    messages = [_zones()] if message == "user_profile" else [_profile()]
    got = profile_values.extract(messages)
    assert got == {c: (None if c in absent else FULL[c]) for c in COLUMNS}


FIELD_SOURCES = {
    "sex": ("user_profile", "gender"),
    "body_mass_kg": ("user_profile", "weight"),
    "height_cm": ("user_profile", "height"),
    "resting_hr_bpm": ("user_profile", "resting_heart_rate"),
    "garmin_activity_class": ("user_profile", "activity_class"),
    "max_hr_bpm": ("zones_target", "max_heart_rate"),
    "threshold_hr_bpm": ("zones_target", "threshold_heart_rate"),
}


def _with(column: str, value) -> list:
    """A full pair of messages with ``column``'s FIT field replaced (``...`` removes it)."""
    message, fit_field = FIELD_SOURCES[column]
    if message == "user_profile":
        return [_profile(**{fit_field: value}), _zones()]
    return [_profile(), _zones(**{fit_field: value})]


@pytest.mark.parametrize("column", COLUMNS)
def test_a_fit_invalid_value_stores_absent(column: str) -> None:
    got = profile_values.extract(_with(column, (None, None)))
    assert got[column] is None
    assert {c: got[c] for c in COLUMNS if c != column} == {c: FULL[c] for c in COLUMNS if c != column}


@pytest.mark.parametrize("column", COLUMNS)
def test_a_missing_field_stores_absent(column: str) -> None:
    got = profile_values.extract(_with(column, ...))
    assert got[column] is None
    assert {c: got[c] for c in COLUMNS if c != column} == {c: FULL[c] for c in COLUMNS if c != column}


@pytest.mark.parametrize(
    ("column", "zero"),
    [
        ("body_mass_kg", (0.0, 0)),
        ("height_cm", (0.0, 0)),
        ("resting_hr_bpm", (0, 0)),
        ("max_hr_bpm", (0, 0)),
        ("threshold_hr_bpm", (0, 0)),
    ],
)
def test_zero_stores_absent_for_the_five_numeric_hr_and_body_fields(column: str, zero) -> None:
    assert profile_values.extract(_with(column, zero))[column] is None


@pytest.mark.parametrize(
    ("column", "negative"),
    [
        ("body_mass_kg", (-70.0, -700)),
        ("body_mass_kg", (-0.1, -1)),
        ("height_cm", (-0.01, -1)),
        ("resting_hr_bpm", (-5, -5)),
        ("max_hr_bpm", (-1, -1)),
        ("threshold_hr_bpm", (-40, -40)),
    ],
)
def test_a_negative_value_stores_absent_for_the_five_numeric_hr_and_body_fields(
    column: str, negative
) -> None:
    """A FIT definition may declare one of these fields with a signed base type, and then a
    negative value decodes as itself: zero or negative stores absent."""
    got = profile_values.extract(_with(column, negative))
    assert got[column] is None
    assert {c: got[c] for c in COLUMNS if c != column} == {c: FULL[c] for c in COLUMNS if c != column}


@pytest.mark.parametrize(
    "weight", [float("inf"), float("-inf"), float("nan")], ids=["inf", "minus_inf", "nan"]
)
def test_a_non_finite_weight_stores_absent(weight: float) -> None:
    got = profile_values.extract([_profile(weight=(weight, 0)), _zones()])
    assert got["body_mass_kg"] is None


def test_zero_is_a_value_for_gender_and_activity_class() -> None:
    got = profile_values.extract([_profile(gender=("female", 0), activity_class=(0, 0)), _zones()])
    assert got["sex"] == "female"
    assert got["garmin_activity_class"] == 0
    assert type(got["garmin_activity_class"]) is int


@pytest.mark.parametrize(
    ("gender", "expected"),
    [
        (("female", 0), "female"),
        (("male", 1), "male"),
        ((2, 2), None),
        ((255, 255), None),
        ((None, None), None),
        (("male", "male"), None),  # a non-integer raw value is not a FIT gender
    ],
)
def test_gender_maps_0_to_female_1_to_male_and_anything_else_absent(gender, expected) -> None:
    assert profile_values.extract([_profile(gender=gender), _zones()])["sex"] == expected


@pytest.mark.parametrize(
    ("decoded", "raw"),
    [
        (0, 0),
        ("level_max", 100),
        (178, 0x80 + 50),
        ("athlete", 0x80),
    ],
)
def test_activity_class_stores_the_raw_integer_not_the_enum_string(decoded, raw) -> None:
    got = profile_values.extract([_profile(activity_class=(decoded, raw)), _zones()])
    assert got["garmin_activity_class"] == raw
    assert type(got["garmin_activity_class"]) is int


def test_height_stores_the_raw_whole_cm_not_a_rounded_float() -> None:
    # A decoded metre value whose round(m * 100) would differ from the raw
    # integer: the raw integer wins.
    got = profile_values.extract([_profile(height=(1.7849, 179)), _zones()])
    assert got["height_cm"] == 179
    assert type(got["height_cm"]) is int


def test_weight_reported_as_calculating_stores_absent() -> None:
    got = profile_values.extract([_profile(weight=("calculating", 0xFFFE)), _zones()])
    assert got["body_mass_kg"] is None


def test_only_the_first_message_of_each_type_is_read() -> None:
    later_profile = _profile(gender=("female", 0), weight=(60.0, 600), resting_heart_rate=(52, 52))
    later_zones = _zones(max_heart_rate=(199, 199), threshold_heart_rate=(150, 150))
    sparse_first_profile = _profile(height=...)
    got = profile_values.extract(
        [_Msg("record", {}), sparse_first_profile, _zones(), later_profile, later_zones]
    )
    assert got == {**FULL, "height_cm": None}
