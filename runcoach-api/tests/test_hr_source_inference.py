"""F015: ``hr_source`` reads ``chest_strap`` when a heart-rate sensor was connected.

The rule (spec/02 section 2.4.2 step 1), resolved in ``mapping._infer_hr_source``:

- RR present -> ``chest_strap``.
- No RR, HR present (any record with a positive ``heart_rate``), and any
  ``device_info`` entry whose ``source_type`` is
  ``antplus`` or ``bluetooth_low_energy`` and whose device type is
  ``heart_rate`` -> ``chest_strap``. No serial is needed: pairing does not
  need identity.
- Otherwise ``None``, which ``quality_gates.apply`` fills as ``wrist_ppg``.

**The corpus, through upload.** ``hr_source_before`` is the value every fixture
was served when the rule read the RR stream alone, derived from
``rr_reconstruction.reconstruct`` on the decoded file; ``HR_SOURCE_AFTER`` is the
value served now, written from the device census (the ``device_info`` entries
and the RR beat count of each file) and the user's ruling of 2026-10-04 that
the strap produced the HR on ``sample_run``, ``wrist_ppg_run`` and
``hilly_long_run_17k_fr945``. Exactly five files differ, and each of the five
reconstructs no RR. The test prints each file's before and after beside the
served value.

**The cadence-lock tags.** On the three runs the before count is measured here,
not copied: the decoded file is mapped, ``hr_source`` is forced to
``wrist_ppg`` and ``quality_gates.apply`` runs, as it did when the rule read
the RR stream alone. The analyst counted 154, 157 and 36 at discuss time; the
test pins the measured counts against those figures and prints them beside
the served count after, which is 0.

**The edges, at the module seam.** No fixture has a Bluetooth-LE strap, a
strap without a serial, or RR without ``device_info``, so those rows are fed
to ``mapping._infer_hr_source`` as synthetic messages, and the ``None`` the
function returns is passed through ``quality_gates.apply`` so each row's
served value is the gate's, not a restatement of its default.

**What this file holds constant.** Every real file is from a Garmin FR945 or
FR955 with ANT+ sensors; none has a Bluetooth-LE strap, and none is a run
recorded on the wrist alone (the cadence-lock gate keeps only synthetic
coverage, ``test_quality_gates_wrist_ppg.py``). Three accepted limits read
``chest_strap``: a strap paired but not worn (not covered); an external optical
sensor over ANT+ or Bluetooth-LE, an armband or a watch broadcasting wrist HR,
because the heart-rate device type does not tell optical from ECG (pinned as
built by the ``optical-hr-broadcast-over-antplus-reads-as-strap`` seam row);
and a strap that drops out mid-activity, which marks the whole session (pinned
as built by the ``strap-dropped-out-before-the-end-strap-connected`` seam row).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api.ingestion import fit_parser, mapping, quality_gates, rr_reconstruction
from runcoach_api.main import app
from runcoach_api.models import Session

FIXTURES = Path(__file__).parent / "fixtures"

# The value served now: a connected ANT+ heart-rate sensor with HR present reads chest_strap.
HR_SOURCE_AFTER: dict[str, str] = {
    "dev_fields_run.fit": "chest_strap",
    "hilly_long_run_17k_fr945.fit": "chest_strap",
    "hilly_run_8k_fr945.fit": "chest_strap",
    "sample_health_snapshot.fit": "wrist_ppg",
    "sample_run.fit": "chest_strap",
    "strap_cool_down_walk.fit": "chest_strap",
    "strap_health_snapshot.fit": "chest_strap",
    "strap_health_snapshot_hrv.fit": "chest_strap",
    "strap_hrv_capture.fit": "chest_strap",
    "strap_hrv_sample_run.fit": "chest_strap",
    "strap_run_hrv.fit": "chest_strap",
    "wrist_ppg_hrv_snapshot.fit": "wrist_ppg",
    "wrist_ppg_run.fit": "chest_strap",
}

FLIPPED = (
    "hilly_long_run_17k_fr945.fit",
    "sample_run.fit",
    "strap_health_snapshot.fit",
    "strap_health_snapshot_hrv.fit",
    "wrist_ppg_run.fit",
)

# The three runs the strap recorded without RR: the cadence-lock count the analyst saw at discuss time.
ANALYST_CADENCE_LOCK_BEFORE: dict[str, int] = {
    "sample_run.fit": 154,
    "wrist_ppg_run.fit": 157,
    "hilly_long_run_17k_fr945.fit": 36,
}


def _served(filename: str) -> dict:
    """Upload ``filename`` and return its ``GET /sessions/{id}`` body."""
    with TestClient(app) as client:
        created = client.post("/sessions", files={"file": (filename, (FIXTURES / filename).read_bytes())})
        assert created.status_code == 201, created.text
        detail = client.get(f"/sessions/{created.json()['session_id']}")
    assert detail.status_code == 200, detail.text
    return detail.json()


@cache
def _messages(filename: str) -> list:
    return fit_parser.decode((FIXTURES / filename).read_bytes())


def hr_source_before(filename: str) -> str:
    """The value served when the rule read the RR stream alone: RR present -> ``chest_strap``,
    no RR -> the wrist default. Derived from ``rr_reconstruction.reconstruct``, never a hand table."""
    return "chest_strap" if rr_reconstruction.reconstruct(_messages(filename)) else "wrist_ppg"


def _cadence_lock_count(records) -> int:
    return sum(1 for r in records if "cadence_lock" in (r["sample_quality"] if isinstance(r, dict) else r.sample_quality))


# ---------------------------------------------------------------------------
# AC2: the corpus, through upload
# ---------------------------------------------------------------------------


def test_the_table_covers_the_whole_corpus() -> None:
    on_disk = sorted(p.name for p in FIXTURES.glob("*.fit"))
    assert sorted(HR_SOURCE_AFTER) == on_disk


def test_exactly_five_files_flip_to_chest_strap() -> None:
    before = {f: hr_source_before(f) for f in HR_SOURCE_AFTER}
    print(f"hr_source before (RR alone): {before}")
    changed = sorted(f for f in HR_SOURCE_AFTER if before[f] != HR_SOURCE_AFTER[f])
    print(f"hr_source flipped: {changed}")
    assert changed == sorted(FLIPPED)
    assert all(before[f] == "wrist_ppg" and HR_SOURCE_AFTER[f] == "chest_strap" for f in changed)


@pytest.mark.parametrize("filename", sorted(HR_SOURCE_AFTER))
def test_hr_source_matches_the_pinned_table(filename: str) -> None:
    served = _served(filename)["hr_source"]
    print(
        f"hr_source {filename}: before {hr_source_before(filename)} after {HR_SOURCE_AFTER[filename]} "
        f"served {served}"
    )
    assert served == HR_SOURCE_AFTER[filename]


@pytest.mark.parametrize("filename", FLIPPED)
def test_strap_connected_without_rr_reads_chest_strap(filename: str) -> None:
    """The five files at the seam: no RR, HR present, an ANT+ heart-rate entry."""
    messages = fit_parser.decode((FIXTURES / filename).read_bytes())
    rr = rr_reconstruction.reconstruct(messages)
    print(f"{filename}: RR beats {len(rr)}")

    assert rr == []
    assert mapping._infer_hr_source(messages) == "chest_strap"


@pytest.mark.parametrize("filename", sorted(ANALYST_CADENCE_LOCK_BEFORE))
def test_the_strap_runs_carry_no_cadence_lock_tag(filename: str) -> None:
    session, records = mapping.to_canonical(fit_parser.decode((FIXTURES / filename).read_bytes()))
    session.hr_source = "wrist_ppg"
    quality_gates.apply(session, records)
    before = _cadence_lock_count(records)

    served = _served(filename)
    after = _cadence_lock_count(served["records"])
    print(
        f"cadence_lock {filename}: before {before} (forced wrist_ppg; analyst saw "
        f"{ANALYST_CADENCE_LOCK_BEFORE[filename]}) after {after} (served hr_source {served['hr_source']})"
    )

    assert before == ANALYST_CADENCE_LOCK_BEFORE[filename]
    assert served["hr_source"] == "chest_strap"
    assert after == 0


# ---------------------------------------------------------------------------
# AC3: the edges, at the module seam
# ---------------------------------------------------------------------------


class _FakeMsg:
    """What ``_infer_hr_source`` and ``rr_reconstruction`` read: ``.name``, ``.fields``, ``get_value``."""

    def __init__(self, name: str, values: dict | None = None) -> None:
        self.name = name
        self._values = values or {}
        self.fields: list = []

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)


def _records(heart_rate: int | None) -> list[_FakeMsg]:
    return [_FakeMsg("record", {"heart_rate": heart_rate}) for _ in range(5)]


def _antplus(device_type: str, serial: int | None = 4242, **extra) -> _FakeMsg:
    return _FakeMsg(
        "device_info",
        {"source_type": "antplus", "antplus_device_type": device_type, "serial_number": serial, **extra},
    )


def _ble_heart_rate() -> _FakeMsg:
    # A faithful Bluetooth-LE entry: fitdecode resolves device_type through ``ble_device_type``.
    return _FakeMsg(
        "device_info",
        {"source_type": "bluetooth_low_energy", "ble_device_type": "heart_rate", "serial_number": 4242},
    )


def _creator() -> _FakeMsg:
    return _FakeMsg("device_info", {"source_type": "local", "serial_number": 9999})


def _rr() -> _FakeMsg:
    return _FakeMsg("hrv", {"time": (0.8,) * 11})


def _gate_value(messages) -> str:
    """The served value: the seam's answer, with ``None`` filled by the real gate default."""
    session = Session(
        session_id="seam",
        sport="running",
        source_vendor="garmin",
        start_time="2026-10-04T00:00:00Z",
        hr_source=mapping._infer_hr_source(messages),
    )
    quality_gates.apply(session, [])
    return session.hr_source


SEAM_ROWS = [
    pytest.param([_creator(), _ble_heart_rate(), *_records(150)], "chest_strap", id="ble-heart-rate-only"),
    pytest.param(
        [_creator(), _antplus("stride_speed_distance"), *_records(150)],
        "wrist_ppg",
        id="antplus-stride-speed-distance-only",
    ),
    pytest.param([*_records(150)], "wrist_ppg", id="no-device-info"),
    pytest.param([_rr(), *_records(150)], "chest_strap", id="rr-with-no-device-info"),
    pytest.param(
        [_creator(), _antplus("heart_rate", serial=None), *_records(150)],
        "chest_strap",
        id="antplus-heart-rate-with-no-serial",
    ),
    pytest.param([_creator(), *_records(150)], "wrist_ppg", id="creator-only"),
    pytest.param(
        [_creator(), _FakeMsg("device_info", {"device_type": "heart_rate", "serial_number": 4242}), *_records(150)],
        "wrist_ppg",
        id="heart-rate-type-with-no-source-type",
    ),
    pytest.param(
        [_creator(), _FakeMsg("device_info", {"source_type": "ant", "ant_device_type": 120}), *_records(150)],
        "wrist_ppg",
        id="legacy-ant-source-not-read",
    ),
    # No HR: as built before F015. RR still reads chest_strap; without RR the
    # connected strap does not matter, and the gate default fills wrist_ppg.
    pytest.param([_creator(), _antplus("heart_rate"), *_records(None)], "wrist_ppg", id="no-hr-strap-connected"),
    # HR present means a positive heart_rate: records that all read 0 are no HR.
    pytest.param([_creator(), _antplus("heart_rate"), *_records(0)], "wrist_ppg", id="zero-hr-strap-connected"),
    pytest.param([_creator(), _antplus("heart_rate")], "wrist_ppg", id="no-records-strap-connected"),
    pytest.param([_rr(), *_records(None)], "chest_strap", id="no-hr-rr-present"),
    # HR present means any record with a positive heart_rate, not every record:
    # the strap was acquired after the start, so the first records read None or 0.
    pytest.param(
        [_creator(), _antplus("heart_rate"), *_records(None), *_records(0), *_records(150)],
        "chest_strap",
        id="hr-acquired-after-start-strap-connected",
    ),
    # The mirror: the strap dropped out before the end, so the last records read
    # None. HR present is judged over every record, not the last, and the
    # session reads chest_strap (the dropout limit, pinned as built).
    pytest.param(
        [_creator(), _antplus("heart_rate"), *_records(150), *_records(None)],
        "chest_strap",
        id="strap-dropped-out-before-the-end-strap-connected",
    ),
    # Accepted limit (user, 2026-10-06), pinned as built: an external optical sensor
    # on ANT+ (here a watch broadcasting wrist HR, garmin_product 255, the shape
    # test_mapping_sensor_identity.py row 13 uses) presents device type heart_rate,
    # which does not tell optical from ECG, so it reads as the strap.
    pytest.param(
        [_creator(), _antplus("heart_rate", manufacturer="garmin", garmin_product=255), *_records(150)],
        "chest_strap",
        id="optical-hr-broadcast-over-antplus-reads-as-strap",
    ),
]


@pytest.mark.parametrize(("messages", "expected"), SEAM_ROWS)
def test_seam_row(messages, expected: str) -> None:
    served = _gate_value(messages)
    print(f"hr_source seam: expected {expected} served {served}")
    assert served == expected


def test_the_serial_rule_still_reads_antplus_only() -> None:
    """Pairing accepts Bluetooth-LE; the F007 serial rule does not widen with it."""
    by_name = mapping._group_by_name([_creator(), _ble_heart_rate(), *_records(150)])

    assert mapping._resolve_hr_sensor_serial(by_name) is None
    assert mapping._infer_hr_source([_creator(), _ble_heart_rate(), *_records(150)]) == "chest_strap"
