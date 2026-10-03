"""F007 AC9: ``GET /sessions/{id}`` does not carry ``hr_sensor_serial`` (T251).

The detail route has no ``response_model``: it returns the dict
``db.get_session_detail`` builds by hand, so ``contracts/check_drift.py``
cannot see a key added to it. This module is the pin that makes a leak
visible. It asserts the exact key set at every nesting level that
``get_session_detail`` assembles itself -- the session object, each
``records`` entry and each ``rr_intervals`` entry -- against literals
copied from ``git show c2839b6:runcoach-api/src/runcoach_api/db.py``
(``get_session_detail``, the return dict and the two list comprehensions
above it), the last commit before F007 touched the schema. They were
NOT read off a live response, and they are NOT derived from
``models.Session``: the model now carries ``hr_sensor_serial``, so a key
set computed from it would carry the leak with it.

``summary`` and ``context`` (with ``context.provenance``) are JSON blobs
built by ``mapping``, not by ``get_session_detail``, so the key-set pin
cannot see inside them. The whole body is therefore also searched for the
field name and for the two corpus serials -- 3611410126 (the HRM-Pro Plus
in ``strap_hrv_capture.fit``) and 785102823 (the Polar strap in
``dev_fields_run.fit``) -- both measured absent from both bodies at
c2839b6.

Both halves are live. T249 has merged, so both fixtures ingest with
their strap's serial stored (3611410126 and 785102823), and the
value-absent half of the body check fails if either serial reaches the
body. The key-set half is proved by the detached-worktree perturbation
recorded in the T251 commit body (adding ``"hr_sensor_serial":
row["hr_sensor_serial"]`` to the dict and its SELECT turned this
module red).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from runcoach_api.main import app

# --- literals from c2839b6 db.get_session_detail ---------------------------
# The return dict, top to bottom, in source order.
SESSION_KEYS_AT_C2839B6 = frozenset(
    {
        "session_id",
        "athlete_id",
        "start_time",
        "sport",
        "activity_tag",
        "source_vendor",
        "source_device",
        "recording_interval",
        "hr_source",
        "rr_valid_fraction",
        "rmssd_precomputed",
        "resting_rmssd_ms",
        "hrv_source_tier",
        "rr_source",
        "quality_flags",
        "summary",
        "context",
        "records",
        "rr_intervals",
    }
)

# One entry of the ``records`` list comprehension.
RECORD_KEYS_AT_C2839B6 = frozenset(
    {
        "t",
        "lat",
        "lon",
        "distance",
        "speed",
        "heart_rate",
        "cadence",
        "altitude",
        "power",
        "power_model",
        "vertical_oscillation",
        "ground_contact_time",
        "gct_balance",
        "step_length",
        "temperature",
        "gps_degraded",
        "sample_quality",
    }
)

# One entry of the ``rr_intervals`` list comprehension.
RR_INTERVAL_KEYS_AT_C2839B6 = frozenset({"seq", "rr_ms", "rr_source", "rr_carrier", "is_artefact"})

LEAK_FIELD = "hr_sensor_serial"
# The connected-strap serials the two fixtures carry in their device_info
# (feature F007, census of 2026-10-03). Neither string appears anywhere in
# the pre-F007 detail body.
CORPUS_STRAP_SERIALS = ("3611410126", "785102823")

CANONICAL_FIXTURE = "strap_hrv_capture.fit"
POLAR_FIXTURE = "dev_fields_run.fit"


def _detail_response(client: TestClient, post_fit, filename: str):
    """Upload one fixture and return the raw ``GET /sessions/{id}`` response.

    ``conftest._ingest`` returns the parsed body only; the body-text checks
    below need the raw response, so the round trip is repeated here.
    """
    post = post_fit(client, filename)
    assert post.status_code == 201, post.text
    detail = client.get(f"/sessions/{post.json()['session_id']}")
    assert detail.status_code == 200, detail.text
    return detail


def test_session_detail_keys_match_the_pre_f007_set(post_fit) -> None:
    """Every hand-built level of the detail body equals its c2839b6 key set."""
    with TestClient(app) as client:
        body = _detail_response(client, post_fit, CANONICAL_FIXTURE).json()

    assert set(body) == SESSION_KEYS_AT_C2839B6
    assert LEAK_FIELD not in body

    # A strap RR capture has both records and beats, so the nested levels
    # are populated and the comparison is not over an empty list.
    assert body["records"], "fixture produced no records; the nested pin would be vacuous"
    assert body["rr_intervals"], "fixture produced no RR beats; the nested pin would be vacuous"
    assert {frozenset(r) for r in body["records"]} == {RECORD_KEYS_AT_C2839B6}
    assert {frozenset(b) for b in body["rr_intervals"]} == {RR_INTERVAL_KEYS_AT_C2839B6}


def test_pinned_key_sets_are_the_literals_not_the_model() -> None:
    """The literals above are the leak-free sets, independent of what the app returns.

    Guards the pin itself: if someone "updates" a literal by pasting a live
    response that already carries the field, this fails before any HTTP
    round trip, and the three sizes match the c2839b6 source (19, 17, 5).
    """
    assert LEAK_FIELD not in SESSION_KEYS_AT_C2839B6
    assert LEAK_FIELD not in RECORD_KEYS_AT_C2839B6
    assert LEAK_FIELD not in RR_INTERVAL_KEYS_AT_C2839B6
    assert len(SESSION_KEYS_AT_C2839B6) == 19
    assert len(RECORD_KEYS_AT_C2839B6) == 17
    assert len(RR_INTERVAL_KEYS_AT_C2839B6) == 5


@pytest.mark.parametrize("filename", [CANONICAL_FIXTURE, POLAR_FIXTURE])
def test_detail_body_carries_neither_the_field_nor_a_strap_serial(post_fit, filename: str) -> None:
    """Whole-body check that reaches into the JSON blobs the key-set pin cannot.

    Both halves are live: each fixture ingests with its strap's serial
    stored, so a serial reaching the body is caught (see the module docstring).
    """
    with TestClient(app) as client:
        text = _detail_response(client, post_fit, filename).text

    # Compared as an offset, not with ``not in``: on a miss pytest would
    # render ``needle not in text`` by running difflib over the whole
    # multi-thousand-record body, which took minutes in the T251
    # perturbation run. ``find`` reports the leak and where it sits.
    for needle in (LEAK_FIELD, *CORPUS_STRAP_SERIALS):
        at = text.find(needle)
        assert at == -1, f"{needle!r} leaked into the {filename} detail body at offset {at}: {text[at - 40:at + 60]!r}"
