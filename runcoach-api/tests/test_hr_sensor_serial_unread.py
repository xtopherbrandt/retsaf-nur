"""T250 (F007 AC6): nothing outside storage reads ``sessions.hr_sensor_serial``.

F007 adds one nullable INTEGER column and promises a downstream reader that
the column changes no output ("additive storage only"). A promise like that
needs a test whose failure *is* the promise breaking, so this module pins it
three ways, from the outside in:

1. **Behaviourally**, over every metric route (``GET /metrics/hrv`` and
   ``GET /metrics/load``; the load route takes ``from``/``to`` the same way
   and reads the saved loads the startup fill gave the seeded captures, none
   of which is a running session, so it answers the empty chart in every
   state -- the walk pins that the column does not change that answer, not
   the curves themselves). One isolated store is seeded
   once through the real ``to_canonical -> classify -> db.persist`` path and
   then **mutated in place** -- never copied, never re-pointed -- through
   three states that differ *only* in ``hr_sensor_serial``:

   - **A**: as ingested (every row ``NULL``, the pre-F007 and the
     unresolved posture alike);
   - **B**: one serial on every row (``UPDATE sessions SET ...``);
   - **C**: two serials alternating by ``start_time`` order -- the "device
     swap" shape a future consumer of the field would be built to react to.

   Each state is read sequentially over three **fixed past** windows, and
   the response bytes must equal state A's byte for byte. Before every
   request a witness reads ``SELECT DISTINCT hr_sensor_serial`` on the very
   DB the app resolves (``db.get_connection()``) and prints it as a
   ``[slice compared]`` line, then asserts it is the state's expected set.
   Without the witness the walk passes vacuously if A, B and C all read an
   unmutated store.

2. **Structurally**, at the one read behind the trend: ``db.read_hrv_rows``
   returns exactly the four columns the metric consumes, so a widened
   ``SELECT`` reddens here before any consumer can lean on the fifth.

3. **Lexically**, over every ``.py`` module under ``src/runcoach_api``,
   recursively, except the three storage modules allowed to name it --
   ``db.py``, ``ingestion/mapping.py`` and ``models.py`` -- no token outside
   a comment mentions the column. Inverted from a listed reader set, so a
   read in ``pipeline.py``, ``quality_gates.py``, ``cli.py`` or a module
   added later is scanned without editing this file. The behavioural walk cannot
   see a classifier branch (classification runs at seed time, before the
   mutation) or a response-model field on a route that is not a metric, so
   this pin covers what the walk cannot reach.

The route set is enumerated from ``app.routes`` and held equal to a literal,
so a new metric route reddens this module until it is added to the walk.

Axes held constant (per ``a-sweep-must-name-the-axes-it-holds-constant``):
tier ``chest_strap_raw``; **daily** capture density; one era, no tier
switch; one fixed 70-day series ending on ``SERIES_END``; the three windows;
the ``UTC`` athlete zone; the declared profile name. The varied axis is
``hr_sensor_serial`` and nothing else. The windows are fixed past dates
because ``to`` defaults to wall-clock today and future days are withheld,
so a defaulted request would compare whatever today happens to serve.
"""

from __future__ import annotations

import io
import tokenize
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from runcoach_api import db as db_module
from runcoach_api.main import app

STRAP = "chest_strap_raw"
PROFILE = "HRV Snapshot"
ZONE = ZoneInfo("UTC")

# A fixed past series: 70 daily captures ending here, so the baseline window
# (D-66 .. D-7) is fully populated on the last day and the verdict is a real
# band comparison, not ``hrv_unavailable``. The date is deliberately before
# any sprint of this project ran.
SERIES_END = date(2025, 6, 30)
SERIES_DAYS = 70
SERIES_START = SERIES_END - timedelta(days=SERIES_DAYS - 1)

# Three fixed windows spanning the series: the first week (no baseline yet,
# every day unavailable), a mid-series fortnight (the band forming) and the
# last week (an established band).
WINDOWS: tuple[tuple[date, date], ...] = (
    (SERIES_START, SERIES_START + timedelta(days=6)),
    (SERIES_END - timedelta(days=20), SERIES_END - timedelta(days=7)),
    (SERIES_END - timedelta(days=6), SERIES_END),
)

# Two real strap serials from the fixture corpus: the Garmin HRM-Pro Plus
# and the Polar strap in ``dev_fields_run.fit``.
SERIAL_GARMIN = 3611410126
SERIAL_POLAR = 785102823

# The metric routes, as ``(method, path)``. Enumerated at test time from the
# app and held equal to this literal; extend the literal *and* the walk when
# a metric route is added.
METRIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {("GET", "/metrics/hrv"), ("GET", "/metrics/load")}
)

# The one route whose body carries a verdict; the last-day verdict check
# reads it there and nowhere else.
VERDICT_ROUTE = ("GET", "/metrics/hrv")

# The four columns ``read_hrv_rows`` serves the trend, in SELECT order.
HRV_ROW_COLUMNS = ["session_id", "start_time", "resting_rmssd_ms", "hrv_source_tier"]

# The lexical pin scans every module under ``src/runcoach_api``, recursively,
# except the storage modules -- the only files allowed to name the column.
_SRC = Path(__file__).resolve().parents[1] / "src" / "runcoach_api"
STORAGE_FILES: frozenset[str] = frozenset({"db.py", "ingestion/mapping.py", "models.py"})
ALL_SOURCE_FILES: tuple[Path, ...] = tuple(sorted(_SRC.rglob("*.py")))
READER_FILES: tuple[Path, ...] = tuple(
    p for p in ALL_SOURCE_FILES if p.relative_to(_SRC).as_posix() not in STORAGE_FILES
)
COLUMN = "hr_sensor_serial"


def _metric_routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/metrics/"):
            found.update((method, route.path) for method in sorted(route.methods))
    return found


def _distinct_serials(conn) -> set[int | None]:
    return {row[0] for row in conn.execute(f"SELECT DISTINCT {COLUMN} FROM sessions")}


def _configure_declared(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``declared_config`` posture inline: the strap seed needs the
    profile declared on the session (the seeder passes it), and the route
    needs a config whose zone is fixed. Both halves -- the setattr and the
    cache clear -- are required, as ``conftest.declared_config`` explains."""
    from runcoach_api.config import AppConfig

    config = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=isolated_data_dir,
        resting_hrv_profile_names=[PROFILE],
        athlete_timezone="UTC",
    )
    monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: config)
    db_module._load_config_cached.cache_clear()


def _seed(seed_hrv_series) -> int:
    result = seed_hrv_series(
        end=SERIES_END,
        days=SERIES_DAYS,
        suppress_last=0,
        tier=STRAP,
        zone=ZONE,
        profile_names=[PROFILE],
    )
    assert len(result.sessions) == SERIES_DAYS
    return len(result.sessions)


def test_metric_routes_are_the_enumerated_set() -> None:
    """A new metric route reddens this until it is added to the walk."""
    found = _metric_routes()
    print(f"\n[slice compared] routes scanned={len(app.routes)} metric routes={sorted(found)}")
    assert found == set(METRIC_ROUTES)
    assert found, "the walk below would compare nothing"


def test_every_metric_route_serves_identical_bytes_across_the_three_serial_states(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch, seed_hrv_series
) -> None:
    """F007 AC6 through HTTP: states A, B and C of one store, three windows,
    every metric route, byte-identical responses."""
    _configure_declared(isolated_data_dir, monkeypatch)
    seeded = _seed(seed_hrv_series)

    states: list[tuple[str, str | None, tuple, set[int | None]]] = [
        ("A", None, (), {None}),
        ("B", f"UPDATE sessions SET {COLUMN} = ?", (SERIAL_GARMIN,), {SERIAL_GARMIN}),
        (
            "C",
            f"""
            UPDATE sessions SET {COLUMN} = (
                SELECT CASE WHEN r.rn % 2 = 0 THEN ? ELSE ? END
                FROM (SELECT session_id, ROW_NUMBER() OVER (ORDER BY start_time) AS rn FROM sessions) AS r
                WHERE r.session_id = sessions.session_id
            )
            """,
            (SERIAL_GARMIN, SERIAL_POLAR),
            {SERIAL_GARMIN, SERIAL_POLAR},
        ),
    ]

    baseline: dict[tuple[str, str, date, date], bytes] = {}
    compared = 0
    verdicts_on_last_day: set[str] = set()
    with TestClient(app) as client:
        for name, sql, params, expected_serials in states:
            if sql is not None:
                conn = db_module.get_connection()
                try:
                    changed = conn.execute(sql, params).rowcount
                    conn.commit()
                finally:
                    conn.close()
                assert changed == seeded, f"state {name} mutated {changed} rows, not all {seeded}"

            for method, path in sorted(METRIC_ROUTES):
                for from_, to in WINDOWS:
                    # The witness, before every request, on the DB the app resolves.
                    conn = db_module.get_connection()
                    try:
                        serials = _distinct_serials(conn)
                    finally:
                        conn.close()
                    print(
                        f"\n[slice compared] state={name} serials={sorted(serials, key=str)} "
                        f"route={method} {path} window={from_.isoformat()}..{to.isoformat()} rows={seeded}"
                    )
                    assert serials == expected_serials, f"state {name} did not take: {serials}"

                    response = client.request(
                        method, path, params={"from": from_.isoformat(), "to": to.isoformat()}
                    )
                    assert response.status_code == 200, response.text
                    key = (method, path, from_, to)
                    if name == "A":
                        baseline[key] = response.content
                        if to == SERIES_END and (method, path) == VERDICT_ROUTE:
                            verdicts_on_last_day.add(response.json()["verdict"])
                    else:
                        assert response.content == baseline[key], (
                            f"{method} {path} {from_}..{to} differs between state A and state {name}"
                        )
                        compared += 1

    # The comparison was not trivial: the last window judged a real band, and
    # every (route, window) pair was compared in both mutated states.
    assert verdicts_on_last_day and verdicts_on_last_day != {"hrv_unavailable"}, verdicts_on_last_day
    assert compared == 2 * len(METRIC_ROUTES) * len(WINDOWS)
    print(f"\n[slice compared] responses compared={compared} last-day verdicts={sorted(verdicts_on_last_day)}")


def test_read_hrv_rows_serves_only_the_four_trend_columns(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch, seed_hrv_series
) -> None:
    """The one read behind the trend does not carry the column, so no
    consumer can read it by key without widening this SELECT first."""
    _configure_declared(isolated_data_dir, monkeypatch)
    seeded = _seed(seed_hrv_series)
    conn = db_module.get_connection()
    try:
        conn.execute(f"UPDATE sessions SET {COLUMN} = ?", (SERIAL_GARMIN,))
        conn.commit()
        rows = db_module.read_hrv_rows(conn, "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
    finally:
        conn.close()
    columns = sorted({tuple(row.keys()) for row in rows})
    print(f"\n[slice compared] read_hrv_rows rows={len(rows)} column tuples={columns}")
    assert len(rows) == seeded
    assert columns == [tuple(HRV_ROW_COLUMNS)]


def test_no_reader_module_mentions_the_column_outside_comments() -> None:
    """Every non-comment token of every non-storage module is scanned; a
    dict key, an SQL fragment, an attribute or a pydantic field all surface
    here."""
    hits: list[str] = []
    tokens_scanned = 0
    for path in READER_FILES:
        source = path.read_text(encoding="utf-8")
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                continue
            tokens_scanned += 1
            if COLUMN in tok.string:
                hits.append(f"{path.relative_to(_SRC).as_posix()}:{tok.start[0]}: {tok.string.strip()[:80]}")
    names = [p.relative_to(_SRC).as_posix() for p in READER_FILES]
    storage = sorted(p.relative_to(_SRC).as_posix() for p in ALL_SOURCE_FILES if p not in READER_FILES)
    print(
        f"\n[slice compared] scanned files={len(names)} of {len(ALL_SOURCE_FILES)} "
        f"(allowed storage={storage}) {names} tokens={tokens_scanned} hits={len(hits)}"
    )
    # Every storage file still exists, so the allow-list names real files and
    # exactly those three are held out; everything else is scanned.
    assert storage == sorted(STORAGE_FILES)
    assert len(names) == len(ALL_SOURCE_FILES) - len(STORAGE_FILES)
    assert "pipeline.py" in {p.name for p in READER_FILES}
    assert hits == []
