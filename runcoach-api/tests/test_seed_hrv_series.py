"""T086: the seed harness F005's demo probe depends on.

``tests/support/seed_hrv_series.py`` writes a resting-HRV series through the
**real** ``mapping.to_canonical -> hrv_classification.classify -> db.persist``
path and states, in its own ``--print-expected`` output, the band that series
must produce -- computed from its own generated values with the estimator the
decision log fixed (sample SD of ln rMSSD), never imported from
``metrics.hrv_trend``. That independence is the whole point
(``contract-tables-need-an-independent-oracle.md``): a probe whose expectation
came from the endpoint it checks could not fail.

The generator lives in ``conftest.py`` as a plain function (``_seed_hrv_series``),
reachable from tests through the ``seed_hrv_series`` fixture and from the
script through ``importlib.util.spec_from_file_location`` -- one
implementation, two entry points, because ``--import-mode=importlib`` keeps
``tests/`` off ``sys.path`` and no test module can ``import seed_hrv_series``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from runcoach_api import db as db_module
from runcoach_api.metrics import hrv_trend

STRAP = "chest_strap_raw"
SNAPSHOT = "health_snapshot"
PROFILE = "HRV Snapshot"
AUCKLAND = ZoneInfo("Pacific/Auckland")

# The demo probe's arguments: the fixture's Auckland date, a 70-day series,
# the last five days suppressed.
END = date(2026, 9, 7)
DAYS = 70
SUPPRESS_LAST = 5

SCRIPT = Path(__file__).parent / "support" / "seed_hrv_series.py"


def _judge(rows, end: date) -> tuple[hrv_trend.HrvSeries, hrv_trend.HrvVerdict]:
    series = hrv_trend.build_series(rows, AUCKLAND, end)
    return series, hrv_trend.judge(series)


def _probe_args(seed_hrv_series, **overrides):
    args = {
        "end": END,
        "days": DAYS,
        "suppress_last": SUPPRESS_LAST,
        "tier": STRAP,
        "zone": AUCKLAND,
        "profile_names": [PROFILE],
    }
    args.update(overrides)
    return seed_hrv_series(**args)


def test_the_generator_produces_the_band_it_claims(seed_hrv_series) -> None:
    """A 70-day series with the last 5 days suppressed, its rows fed straight
    to the trend computation: the band the module builds must equal the band
    the generator states, and the verdict must be the suppression the demo
    probe asserts. Red: neither the module nor the directory exists."""
    result = _probe_args(seed_hrv_series)
    expected = result.expected

    series, verdict = _judge(result.rows, END)

    assert series.tier == expected["tier"] == STRAP
    assert len(series.baseline) == expected["n"] == 60
    assert [d.isoformat() for d in series.baseline_window] == expected["baseline_window"]
    assert verdict.band is not None
    assert verdict.band.lo == pytest.approx(expected["band_lo"], abs=1e-9)
    assert verdict.band.hi == pytest.approx(expected["band_hi"], abs=1e-9)
    assert verdict.band.mean == pytest.approx(expected["band_mean"], abs=1e-9)
    assert verdict.band.floored is expected["floored"] is False
    assert verdict.established is True
    assert verdict.verdict == hrv_trend.VERDICT_SUPPRESSED
    assert verdict.readings_in_window == 7


def test_the_rows_reach_the_store_through_the_real_persist_path(seed_hrv_series) -> None:
    """What the generator returns is what ``db.read_hrv_rows`` reads back: one
    row per local day, every one a Tier-1 reading with a positive resolved
    value, and the verdict from the read-back rows is the same suppression."""
    result = _probe_args(seed_hrv_series)

    conn = db_module.get_connection()
    try:
        stored = db_module.read_hrv_rows(conn, "0001-01-01T00:00:00+00:00", "9999-12-31T00:00:00+00:00")
    finally:
        conn.close()

    assert len(stored) == DAYS == len(result.rows)
    assert {row["session_id"] for row in stored} == {row["session_id"] for row in result.rows}
    assert all(row["hrv_source_tier"] == STRAP and row["resting_rmssd_ms"] > 0 for row in stored)
    _series, verdict = _judge(stored, END)
    assert verdict.verdict == hrv_trend.VERDICT_SUPPRESSED
    assert verdict.band.lo == pytest.approx(result.expected["band_lo"], abs=1e-9)


def test_the_last_local_day_is_end_in_the_configured_zone(seed_hrv_series) -> None:
    """``--end`` is a *local* date. Auckland's 2026-09-07 morning is
    2026-09-06 in UTC; a seed computed in UTC would put its last reading on
    the day before ``to`` and thin the window."""
    result = _probe_args(seed_hrv_series)
    series, _verdict = _judge(result.rows, END)

    assert series.series[-1].date == END
    # The stored instant is the UTC day before: the seed was placed in Auckland's morning.
    assert series.series[-1].start_time.date() == date(2026, 9, 6)
    # 70 days end-inclusive: the first row buckets to end - 69 in Auckland. The
    # three oldest fall before the baseline window and are ``outside_windows``.
    first = result.rows[0]
    first_day, _instant = hrv_trend.local_day(first["session_id"], first["start_time"], AUCKLAND)
    assert first_day == date(2026, 6, 30)
    assert series.series[0].date == date(2026, 7, 3)
    assert sum(e.reason == hrv_trend.REASON_OUTSIDE_WINDOWS for e in series.excluded) == 3


def test_suppress_last_zero_is_the_normal_verdict(seed_hrv_series) -> None:
    """The probe's negative class: the same generator with nothing suppressed
    reads ``hrv_normal`` against the same band, so the suppression above is
    the suppressed days' doing and not the generator's."""
    result = _probe_args(seed_hrv_series, suppress_last=0)
    _series, verdict = _judge(result.rows, END)

    assert verdict.verdict == hrv_trend.VERDICT_NORMAL
    assert verdict.band.lo == pytest.approx(result.expected["band_lo"], abs=1e-9)


def test_the_tier_flag_seeds_health_snapshot_readings(seed_hrv_series) -> None:
    """``--tier health_snapshot`` goes through the Tier-2 route (``sport``
    60 plus a device ``rmssd_hrv``) and states the same kind of band."""
    result = _probe_args(seed_hrv_series, tier=SNAPSHOT, profile_names=[])
    series, verdict = _judge(result.rows, END)

    assert series.tier == result.expected["tier"] == SNAPSHOT
    assert verdict.verdict == hrv_trend.VERDICT_SUPPRESSED
    assert verdict.band.lo == pytest.approx(result.expected["band_lo"], abs=1e-9)


def _write_api_toml(home: Path, data_dir: Path) -> None:
    (home / ".runcoach").mkdir(parents=True)
    (home / ".runcoach" / "api.toml").write_text(
        "\n".join(
            [
                'host = "127.0.0.1"',
                "port = 8000",
                f'data_dir = "{data_dir.as_posix()}"',
                f'resting_hrv_profile_names = ["{PROFILE}"]',
                'athlete_timezone = "Pacific/Auckland"',
                "",
            ]
        )
    )


def test_the_script_prints_the_resolved_data_dir_before_writing(tmp_path: Path, seed_hrv_series) -> None:
    """Run as a subprocess against a ``tmp_path`` (the probe's shape), the
    script names the data dir it resolved -- on **stderr**, so ``stdout`` is
    the expected-band JSON alone and ``jq --slurpfile`` can read it -- and
    that dir is the requested one, inside ``tmp_path``, not the ``api.toml``
    decoy. ``isolate-the-environment-before-the-first-request.md``: an
    unverified export is an intention, not an isolation."""
    home = tmp_path / "home"
    decoy = tmp_path / "decoy"
    # Not ``tmp_path / "data"``: that is the autouse fixture's isolated store,
    # which the in-process seed at the bottom writes to.
    requested = tmp_path / "script-data"
    _write_api_toml(home, decoy)
    # A minimal environment: the interpreter's own directory on PATH, the
    # scratch home, and nothing inherited that could redirect the config.
    env = {
        "PATH": str(Path(sys.executable).parent),
        "HOME": str(home),
        "USERPROFILE": str(home),
    }
    if "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--data-dir",
            str(requested),
            "--tier",
            STRAP,
            "--end",
            END.isoformat(),
            "--days",
            str(DAYS),
            "--suppress-last",
            str(SUPPRESS_LAST),
            "--print-expected",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    first_line = completed.stderr.splitlines()[0]
    assert "data dir" in first_line
    assert Path(first_line.split(":", 1)[1].strip()).resolve() == requested.resolve()
    assert (requested / db_module.DB_FILENAME).exists()
    assert not decoy.exists()

    printed = json.loads(completed.stdout)
    assert printed == _probe_args(seed_hrv_series).expected
    assert printed["tier"] == STRAP and printed["n"] == 60
    assert printed["band_lo"] < printed["band_mean"] < printed["band_hi"]


def test_the_script_seeds_several_eras_and_states_each_ones_band(tmp_path: Path, seed_hrv_series) -> None:
    """T096: the demo probe needs a two-tier history -- a snapshot era with a
    strap trial inside it, a genuine switch, one stray -- so the script takes
    ``--era TIER:END:DAYS[:SUPPRESS_LAST[:LOCAL_HOUR]]`` repeatedly and writes
    each era through the same generator. ``--print-expected`` then prints
    ``{"eras": [...]}``, one expectation per era in the order given, each the
    band the generator states for that era at its own ``END`` -- computed
    from the intended values, never read back from the store (IDEA-057). The
    local hour keeps two tiers captured on one morning from colliding on
    ``session_id`` (derived from ``(source_device, start_time)``, and the
    synthetic device is one string); the trial here sits inside the snapshot
    era at 07:00 and both survive in the store."""
    home = tmp_path / "home"
    requested = tmp_path / "script-data"
    _write_api_toml(home, tmp_path / "decoy")
    env = {"PATH": str(Path(sys.executable).parent), "HOME": str(home), "USERPROFILE": str(home)}
    if "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    eras = [
        (SNAPSHOT, date(2026, 3, 22), 81, 7, 6),
        (STRAP, date(2026, 3, 1), 14, 0, 7),
    ]

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--data-dir", str(requested), "--print-expected"]
        + [
            arg
            for tier, end, days, suppress, hour in eras
            for arg in ("--era", f"{tier}:{end.isoformat()}:{days}:{suppress}:{hour}")
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    printed = json.loads(completed.stdout)
    assert list(printed) == ["eras"] and len(printed["eras"]) == 2
    for era, (tier, end, days, suppress, hour) in zip(printed["eras"], eras):
        expected = seed_hrv_series(
            end=end, days=days, suppress_last=suppress, tier=tier, zone=AUCKLAND,
            profile_names=[PROFILE], local_hour=hour,
        ).expected
        assert era == expected
        assert era["tier"] == tier and era["end"] == end.isoformat()
    assert printed["eras"][0]["n"] == 60 and printed["eras"][0]["first_day"] == "2026-01-01"
    assert printed["eras"][1]["first_day"] == "2026-02-16"
    with sqlite3.connect(requested / db_module.DB_FILENAME) as conn:
        rows = conn.execute(
            "SELECT hrv_source_tier, COUNT(*) FROM sessions GROUP BY hrv_source_tier ORDER BY 1"
        ).fetchall()
    assert rows == [(STRAP, 14), (SNAPSHOT, 81)]
