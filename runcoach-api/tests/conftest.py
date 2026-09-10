"""Shared pytest fixtures for runcoach-api tests.

``isolated_data_dir`` is an autouse fixture that redirects
``db.get_connection()``'s config lookup to a per-test ``tmp_path``, so
no test -- here or in any later F003 task that touches the DB -- ever
opens or writes the real ``~/.runcoach/runcoach.db``. It works by
monkeypatching ``runcoach_api.db.config_module.load_config`` to return
a fake ``AppConfig`` pointed at a tmp_path-derived data dir; it's
autouse so every test gets isolation for free without needing to
request it explicitly.

``db.get_connection()`` wraps ``config_module.load_config`` in an
``lru_cache`` (maxsize=1) so the config is only read from disk once per
process. That cache is cleared here, before each test's monkeypatch
takes effect, so a stale cached config from a previous test never
leaks into this one.

``declared_config`` (T063) layers on top for the suites that need the
athlete's Tier-1 declaration: it rebuilds the same ``AppConfig`` with
``resting_hrv_profile_names`` populated and clears the cache again. It is
opt-in, never autouse -- a declaration is the whole subject of the tests that
want one, and handing it to every test would silently re-route captures four
other modules assert are refused.

Everything below ``declared_config`` is the resting-HRV helper set --
one ``_FakeMsg``, one ``_synthetic``, one ``_classified``, one
``_ingest`` -- shared by the five ``test_resting_hrv_*.py`` modules that
previously each carried their own drifting copy. See the section comment
there for why they are fixtures rather than importable helpers, and why
none of them is autouse.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from runcoach_api import db as db_module
from runcoach_api.config import AppConfig
from runcoach_api.ingestion import hrv_classification, mapping
from runcoach_api.models import RRInterval, Session


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    # `resting_hrv_profile_names=[]` is the default *test* posture, and it is
    # an explicit declaration rather than a default: it says no activity
    # profile means a resting capture, so Tier 1 routes nothing here. A suite
    # that needs a declaration opts in by building its own `AppConfig` with
    # the profile names it wants.
    #
    # `athlete_timezone="UTC"` is a fixed zone, not the developer's: tests must
    # not depend on where the machine lives. A suite that needs a particular
    # zone (F005's bucketing tests) builds its own `AppConfig`, as
    # `declared_config` does for the profile names.
    fake_config = AppConfig(
        host="127.0.0.1",
        port=8000,
        data_dir=data_dir,
        resting_hrv_profile_names=[],
        athlete_timezone="UTC",
    )
    monkeypatch.setattr(db_module.config_module, "load_config", lambda *a, **k: fake_config)
    db_module._load_config_cached.cache_clear()
    return data_dir


@pytest.fixture
def declared_config(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """``declared_config("HRV Snapshot")`` -- the athlete's Tier-1 declaration.

    Rebuilds the ``AppConfig`` ``isolated_data_dir`` installed, this time with
    ``resting_hrv_profile_names`` populated, and clears
    ``db._load_config_cached`` so ``pipeline.ingest_fit_bytes`` reads the new
    list rather than the ``lru_cache(maxsize=1)``'d empty one. Both halves are
    required: that cache is exactly why editing ``api.toml`` needs a server
    restart in production, and it would make this fixture a silent no-op
    without the clear.

    ``data_dir`` stays pointed at the same per-test ``tmp_path`` the autouse
    fixture chose, so declaring a profile never un-isolates the store.

    Called with no arguments it declares the **empty list** explicitly -- the
    "athlete who uses only Health Snapshot" posture, which differs from never
    calling the fixture only in that it re-clears the cache.
    """

    def _declare(*profile_names: str) -> AppConfig:
        declared = AppConfig(
            host="127.0.0.1",
            port=8000,
            data_dir=isolated_data_dir,
            resting_hrv_profile_names=list(profile_names),
            athlete_timezone="UTC",
        )
        monkeypatch.setattr(
            db_module.config_module, "load_config", lambda *a, **k: declared
        )
        db_module._load_config_cached.cache_clear()
        return declared

    return _declare


# ---------------------------------------------------------------------------
# The resting-HRV test helpers
#
# ``_FakeMsg``, ``_synthetic``, ``_classified`` and ``_ingest`` were each
# re-implemented in five ``test_resting_hrv_*.py`` modules, and the copies
# had already drifted: ``quality_gates``'s ``_classified`` grew a third
# ``valid_fraction`` parameter the others lacked, and ``_synthetic``
# defaulted ``sport`` to ``"running"`` in two files, to ``60`` in a third,
# and made it required in a fourth. They were no longer interchangeable,
# so a fix to the stand-in had to land in five places. This is the same
# consolidation T052 did for the CLI package.
#
# They are exposed as **fixtures returning the callable** rather than as
# importable module-level helpers, exactly as ``install_mock_client`` is in
# ``runcoach-cli/tests/conftest.py``, and for a harder reason here: this
# workspace runs pytest with ``--import-mode=importlib`` (see the root
# ``pyproject.toml``), under which the ``tests/`` directories are never
# placed on ``sys.path``, so ``from conftest import _FakeMsg`` raises
# ``ModuleNotFoundError`` outright. A fixture is the only supported channel.
#
# None of these is autouse -- deliberately. ``isolated_data_dir`` above is,
# because isolation must not be opt-in; these are plain helpers, and making
# them autouse would build messages and open uploads for every test in the
# package that neither needs nor names them.
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"

# The start timestamp every synthetic capture is pinned to. The value
# carries no meaning beyond being fixed: session-id determinism is tested
# elsewhere, and holding it constant keeps synthetic captures in one module
# distinguishable only by what the test actually varies.
SYNTHETIC_START = datetime(2026, 1, 1, tzinfo=timezone.utc)

# The empirically observed Garmin Health Snapshot ``sport`` value -- the
# Tier-2 identity signal. Modules that need it pass it explicitly.
SNAPSHOT_SPORT = 60


class _FakeMsg:
    """Minimal stand-in for a ``fitdecode.FitDataMessage``.

    The shape ``test_mapping_sport_handling.py`` established: only
    ``.name``, ``get_value(name, fallback=None)`` and ``.fields`` are read
    by ``mapping.to_canonical`` and by ``hrv_classification.classify``.
    """

    def __init__(self, name: str, values: dict) -> None:
        self.name = name
        self._values = values
        self.fields: list = []

    def get_value(self, name, fallback=None):
        return self._values.get(name, fallback)


def _synthetic(sport="running", **session_extra):
    """A two-message file: one ``session`` roll-up plus one ``record``.

    Routed through the real ``mapping.to_canonical``, so ``_build_summary``'s
    ``None``-stripping applies exactly as it does to a real file: omit
    ``total_distance`` and the summary has **no** ``distance_m`` key at all --
    not a key holding ``None``. Both Health Snapshot fixtures are in that
    state, so subscripting rather than ``.get()`` would turn every indoor
    capture into a 500. ``sport`` likewise goes through the real mapping
    code, so ``context.provenance["raw_sport_value"]`` -- the corroborating
    identity signal Tier 2 reads -- is written by ``mapping.py`` itself
    rather than hand-injected by the test.

    The ``"running"`` default is the Tier-1 modules' one, where ``sport``
    carries no signal and is deliberately not gated on. Tier-2 and
    tier-precedence captures need the Health Snapshot value and pass
    ``SNAPSHOT_SPORT`` explicitly -- one helper, no second default.
    """
    values = {"sport": sport, "start_time": SYNTHETIC_START}
    values.update(session_extra)
    return [
        _FakeMsg("session", values),
        _FakeMsg("record", {"timestamp": SYNTHETIC_START, "heart_rate": 60}),
    ]


def _multi_session(*sessions):
    """A file carrying **more than one** ``session`` message, plus one ``record``.

    Real Garmin multisport and multi-lap files carry one ``session`` roll-up per
    leg. ``mapping.to_canonical`` builds its canonical ``Session`` from
    ``_first_of(by_name, "session")`` -- the *first* one only -- while
    ``rr_reconstruction.reconstruct`` walks every ``hrv`` message in the file. The
    summary and the beat stream therefore describe different spans, which is what
    ``hrv_classification`` refuses to classify on (F004 ref doc §2.1).

    Each positional argument is one session message's field dict; ``sport`` and
    ``start_time`` default the way ``_synthetic`` defaults them, and either may be
    overridden per session.
    """
    messages = []
    for session_extra in sessions:
        values = {"sport": "running", "start_time": SYNTHETIC_START}
        values.update(session_extra)
        messages.append(_FakeMsg("session", values))
    messages.append(_FakeMsg("record", {"timestamp": SYNTHETIC_START, "heart_rate": 60}))
    return messages


def _classified(
    messages,
    rr_intervals=None,
    valid_fraction=None,
    profile_names=None,
    resting_capture_override=False,
):
    """Map, then classify -- optionally with the ``rr_valid_fraction`` the
    pipeline would have set, and optionally with the athlete's declaration.

    ``pipeline.py`` assigns ``session.rr_valid_fraction`` between mapping
    and ``classify()``, so setting it here reproduces the real call order
    rather than inventing one. Passing no ``valid_fraction`` leaves the
    attribute exactly as ``mapping.to_canonical`` left it, which is the
    state the four modules that never set it were already asserting
    against.

    ``profile_names`` and ``resting_capture_override`` are T063's declaration
    seam, forwarded **by keyword** exactly as ``pipeline.ingest_fit_bytes``
    forwards them. They default to "no profile declares Tier 1, and this
    upload claimed nothing", which is the posture every pre-declaration module
    was already written against -- so the suites that never pass them keep
    their existing meaning rather than acquiring a declaration by accident.
    """
    session, _records = mapping.to_canonical(messages)
    if valid_fraction is not None:
        session.rr_valid_fraction = valid_fraction
    hrv_classification.classify(
        messages,
        session,
        rr_intervals or [],
        profile_names=profile_names,
        resting_capture_override=resting_capture_override,
    )
    return session


def _post_fit(client, filename: str, data=None):
    """POST one fixture to ``/sessions`` and return the raw response.

    For tests asserting on the *upload* outcome itself -- a 409 duplicate,
    a rejected capture -- where a helper that asserted 201 would swallow
    the very thing under test.

    ``data`` carries any extra multipart form part -- ``{"resting_capture":
    "true"}`` for T063's upload-time override. It is omitted from the request
    entirely when ``None``, so the default upload is byte-for-byte the one
    every pre-T063 test already sent.
    """
    raw = (FIXTURES / filename).read_bytes()
    return client.post("/sessions", files={"file": (filename, raw)}, data=data)


def _ingest(client, filename: str, data=None) -> dict:
    """Upload one fixture and return its canonical ``GET /sessions/{id}`` body.

    The round trip is the point: the tags and tiers these modules assert on
    are what E003 reads back out of the store, not what ``classify()`` left
    in memory.
    """
    post = _post_fit(client, filename, data)
    assert post.status_code == 201, post.text
    detail = client.get(f"/sessions/{post.json()['session_id']}")
    assert detail.status_code == 200, detail.text
    return detail.json()


@pytest.fixture
def fake_msg() -> type[_FakeMsg]:
    """The ``fitdecode.FitDataMessage`` stand-in class itself."""
    return _FakeMsg


@pytest.fixture
def synthetic():
    """``synthetic(sport="running", **session_extra)`` -> message list."""
    return _synthetic


@pytest.fixture
def multi_session():
    """``multi_session(*session_field_dicts)`` -> message list with N ``session`` messages."""
    return _multi_session


@pytest.fixture
def classified():
    """``classified(messages, rr_intervals=None, valid_fraction=None,
    profile_names=None, resting_capture_override=False)``."""
    return _classified


@pytest.fixture
def ingest():
    """``ingest(client, filename, data=None)`` -> canonical GET body."""
    return _ingest


@pytest.fixture
def post_fit():
    """``post_fit(client, filename, data=None)`` -> raw POST response."""
    return _post_fit


# ---------------------------------------------------------------------------
# The resting-HRV series seed (T086)
#
# F005's demo probe needs a 60-day baseline, which no fixture corpus carries,
# so ``tests/support/seed_hrv_series.py`` writes one through the real
# ``mapping.to_canonical -> hrv_classification.classify -> db.persist`` path
# and states the band that series must produce. The generator lives *here*,
# as the plain function ``_seed_hrv_series``, for the same ``importlib``
# reason as the helpers above: no test module can import the script, and a
# ``@pytest.fixture`` cannot be called directly (pytest >= 8 raises). The
# ``seed_hrv_series`` fixture wraps it for tests; the script loads this file
# with ``importlib.util.spec_from_file_location`` and calls the same
# function. One implementation, two entry points, so they cannot drift.
#
# The expected band is computed below from the generator's **intended**
# values with ``statistics.stdev`` (sample SD, the estimator the F005
# decision log fixed on 2026-09-09) and never imported from
# ``metrics.hrv_trend`` -- an expectation authored by the thing it checks is
# not a check (``contract-tables-need-an-independent-oracle.md``). The
# generator does verify, per capture, that the classifier resolved exactly
# the value it intended, so the independence is of the *formula*, not of the
# data path.
# ---------------------------------------------------------------------------

SEED_TIER_STRAP = "chest_strap_raw"
SEED_TIER_SNAPSHOT = "health_snapshot"
SEED_TIERS = (SEED_TIER_STRAP, SEED_TIER_SNAPSHOT)

# An ordinary athlete's day-to-day dispersion: alternating 38/44 ms around
# 41 ms gives 0.5 * SD(ln) ~ 0.037, well clear of T084's 0.01 floor, so the
# computed half-width -- not the floor -- is what the probe compares.
SEED_NORMAL_RMSSD_MS = (38.0, 44.0)
# A suppressed morning: ln 25 ~ 3.22 against a band floor near ln 38.5; five
# of them in the seven-day window put the mean well below ``band.lo`` even
# when the demo probe's real upload displaces the seeded capture on ``end``.
SEED_SUPPRESSED_RMSSD_MS = 25.0
# Captures are placed at this local wall-clock hour: a morning reading, and
# later than the demo probe's fixture (05:48 Auckland on 2026-09-07), so on
# ``end`` the real upload is the day's reading and the seed the
# ``same_day_later_capture``.
SEED_LOCAL_HOUR = 6
# Tier-1 beats: a two-value alternating series ``a, b, a, b, ...`` has the
# exact closed form ``rMSSD = |a - b|`` under ``rmssd.resting_rmssd``'s plain
# pairwise RMS, so the target value is hit exactly rather than approximately.
SEED_BASE_RR_MS = 900.0
SEED_BEATS = 160
# The Tier-1 capture's shape: inside the resting duration gate (120..300 s),
# a resting heart rate, no distance.
SEED_CAPTURE_DURATION_S = 150.0
SEED_CAPTURE_AVG_HR = 60

# Local-day arithmetic of the baseline the band is stated over -- restated
# here from the construction reference (``[D-66, D-7]``), deliberately not
# read from ``hrv_trend`` (see the section comment).
_SEED_BASELINE_FIRST_OFFSET = 66
_SEED_BASELINE_LAST_OFFSET = 7
_SEED_SWC_FACTOR = 0.5
_SEED_BAND_FLOOR = 0.01


@dataclass
class SeedResult:
    """What one seed run produced: the persisted sessions, the four-column
    rows the trend reads (as ``build_series`` takes them), and the band the
    generator claims for them."""

    sessions: list[Session]
    rows: list[dict]
    expected: dict


def _seed_rmssd_values(days: int, suppress_last: int) -> list[float]:
    """The intended resolved rMSSD for each of ``days`` local days, oldest
    first: the alternating normal pair, then ``suppress_last`` suppressed
    mornings."""
    if days < 0 or suppress_last < 0 or suppress_last > days:
        raise ValueError(f"need 0 <= suppress_last <= days, got days={days} suppress_last={suppress_last}")
    first_suppressed = days - suppress_last
    return [
        SEED_SUPPRESSED_RMSSD_MS if i >= first_suppressed else SEED_NORMAL_RMSSD_MS[i % 2] for i in range(days)
    ]


def _seed_beats(target_rmssd_ms: float) -> list[RRInterval]:
    """An alternating chest-strap beat series whose rMSSD is exactly ``target_rmssd_ms``."""
    values = (SEED_BASE_RR_MS, SEED_BASE_RR_MS + target_rmssd_ms)
    return [
        RRInterval(seq=i, rr_ms=values[i % 2], rr_source="chest_strap_ecg", is_artefact=False)
        for i in range(SEED_BEATS)
    ]


def _seed_expected(end: date, days: int, suppress_last: int, tier: str, zone: ZoneInfo, values) -> dict:
    """The band the series must produce, from the intended values alone."""
    first_day = end - timedelta(days=days - 1) if days else end
    baseline_window = (
        end - timedelta(days=_SEED_BASELINE_FIRST_OFFSET),
        end - timedelta(days=_SEED_BASELINE_LAST_OFFSET),
    )
    by_day = {first_day + timedelta(days=i): value for i, value in enumerate(values)}
    baseline = [math.log(by_day[day]) for day in sorted(by_day) if baseline_window[0] <= day <= baseline_window[1]]
    expected = {
        "tier": tier,
        "timezone": zone.key,
        "end": end.isoformat(),
        "days": days,
        "suppress_last": suppress_last,
        "first_day": first_day.isoformat() if days else None,
        "normal_rmssd_ms": list(SEED_NORMAL_RMSSD_MS),
        "suppressed_rmssd_ms": SEED_SUPPRESSED_RMSSD_MS,
        "baseline_window": [d.isoformat() for d in baseline_window],
        "n": len(baseline),
        "band_mean": None,
        "half_width": None,
        "band_lo": None,
        "band_hi": None,
        "floored": None,
    }
    if len(baseline) >= 2:
        mean = statistics.fmean(baseline)
        computed = _SEED_SWC_FACTOR * statistics.stdev(baseline)
        floored = computed < _SEED_BAND_FLOOR
        half_width = _SEED_BAND_FLOOR if floored else computed
        expected.update(
            band_mean=mean,
            half_width=half_width,
            band_lo=mean - half_width,
            band_hi=mean + half_width,
            floored=floored,
        )
    return expected


def _seed_hrv_series(
    conn,
    *,
    end: date,
    days: int,
    suppress_last: int,
    tier: str,
    zone: ZoneInfo,
    profile_names=(),
) -> SeedResult:
    """Seed one resting-HRV capture per local day for the ``days`` days ending
    on local ``end`` (in ``zone``), the last ``suppress_last`` of them
    suppressed, through the real ingestion path, and state the expected band.

    ``tier`` is ``"chest_strap_raw"`` (a declared Tier-1 capture: the first
    of ``profile_names`` on the session, a synthetic beat series engineered
    to the target rMSSD) or ``"health_snapshot"`` (Tier 2: ``sport`` 60 and a
    device ``rmssd_hrv``). Each capture is placed at ``SEED_LOCAL_HOUR`` local
    time and stored as the UTC instant, which is what makes ``end`` a local
    date: Auckland's morning of ``end`` is the UTC day before.

    Raises rather than seeding a series whose readings differ from the ones
    the expected band was computed over: a Tier-1 capture that did not
    resolve to its target, or a Tier-1 request with no profile to declare.
    """
    if tier not in SEED_TIERS:
        raise ValueError(f"tier must be one of {SEED_TIERS}, got {tier!r}")
    profile_names = list(profile_names)
    if tier == SEED_TIER_STRAP and not profile_names:
        raise ValueError("a chest_strap_raw seed needs at least one declared resting-HRV profile name")

    values = _seed_rmssd_values(days, suppress_last)
    first_day = end - timedelta(days=days - 1) if days else end

    sessions: list[Session] = []
    beats_by_id: dict[str, list[RRInterval]] = {}
    for i, value in enumerate(values):
        day = first_day + timedelta(days=i)
        when = datetime(day.year, day.month, day.day, SEED_LOCAL_HOUR, tzinfo=zone).astimezone(UTC)
        if tier == SEED_TIER_STRAP:
            messages = _synthetic(
                total_timer_time=SEED_CAPTURE_DURATION_S,
                avg_heart_rate=SEED_CAPTURE_AVG_HR,
                sport_profile_name=profile_names[0],
                start_time=when,
            )
            beats = _seed_beats(value)
            session = _classified(messages, beats, 1.0, profile_names)
        else:
            beats = []
            session = _classified(_synthetic(SNAPSHOT_SPORT, rmssd_hrv=value, start_time=when))
        if session.hrv_source_tier != tier or session.resting_rmssd_ms != value:
            raise RuntimeError(
                f"the classifier resolved {session.hrv_source_tier!r}/{session.resting_rmssd_ms!r} on {day}, "
                f"not the intended {tier!r}/{value!r}; the stated band would not describe the store"
            )
        sessions.append(session)
        beats_by_id[session.session_id] = beats

    db_module.init_schema(conn)
    for session in sessions:
        db_module.persist(conn, session, [], beats_by_id[session.session_id], {})

    rows = [
        {
            "session_id": s.session_id,
            "start_time": s.start_time,
            "resting_rmssd_ms": s.resting_rmssd_ms,
            "hrv_source_tier": s.hrv_source_tier,
        }
        for s in sessions
    ]
    expected = _seed_expected(end, days, suppress_last, tier, zone, values)
    return SeedResult(sessions=sessions, rows=rows, expected=expected)


@pytest.fixture
def seed_hrv_series():
    """``seed_hrv_series(end=, days=, suppress_last=, tier=, zone=, profile_names=())``
    -> ``SeedResult``, written to the isolated store."""

    def _seed(**kwargs) -> SeedResult:
        conn = db_module.get_connection()
        try:
            return _seed_hrv_series(conn, **kwargs)
        finally:
            conn.close()

    return _seed
