"""``tests/support/session_load_inputs.py``: hand-built inputs for ``compute_session_load`` (F017 tests).

Loaded by tests with ``importlib.util.spec_from_file_location``, as the other ``tests/support``
modules are.

One builder for the pure-function rows of the formula, gate and wrist suites, so the three build
an input the same way: a 1 Hz run of constant HR, the four HR settings given as the session's own
file values (``source: session_file``), and ``segment_rows`` and ``features`` computed by F013's
``session_features`` over the records, the way ``db.read_session_load_inputs`` builds them. Every
row isolates one rule because nothing else about the run varies.

``outcome`` and ``wrist_outcome`` read the triple each suite compares.
"""

from __future__ import annotations

from runcoach_api.metrics import session_features
from runcoach_api.metrics.session_load import ANCHOR_FIELDS, EMPTY_VALUE

HR_FIELDS = ANCHOR_FIELDS
SYNTHETIC_ID = "synthetic"
_UNSET = object()


def from_file(value, session_id: str = SYNTHETIC_ID) -> dict:
    """One ``inputs.<field>`` block for a value the session's own file carried, or the empty block."""
    if value is None:
        return dict(EMPTY_VALUE)
    return {**EMPTY_VALUE, "value": value, "source": "session_file", "session_id": session_id}


def inputs_for(session: dict, rows: list[dict], **overrides) -> dict:
    """The mapping ``compute_session_load`` takes, built as the upload path builds it, then ``overrides``."""
    inputs = {
        "session_id": session["session_id"],
        "sport": session["sport"],
        "activity_tag": session.get("activity_tag"),
        "hr_source": session.get("hr_source"),
        "quality_flags": list(session.get("quality_flags") or []),
        "timer_time_s": session.get("timer_time_s"),
        "segment_rows": session_features.segment_rows(session, rows),
        "features": session_features.compute_session_features(session, rows),
    }
    for field_name in HR_FIELDS:
        inputs[field_name] = from_file(session.get(field_name), session["session_id"])
    inputs.update(overrides)
    return inputs


def synthetic(
    *,
    seconds: int = 600,
    heart_rate: float | None = 150.0,
    sport: str = "running",
    activity_tag: str | None = None,
    hr_source: str | None = "chest_strap",
    resting=50,
    max_hr=190,
    threshold=170,
    sex="male",
    timer_time_s: object = _UNSET,
    **overrides,
) -> dict:
    """A 1 Hz run of ``seconds`` records at a constant HR, with the settings given as the file's own.

    ``timer_time_s`` defaults to ``float(seconds)``; any other keyword replaces that input whole.
    """
    rows = [{"t": float(t), "distance": 3.0 * t, "heart_rate": heart_rate, "sample_quality": []} for t in range(seconds + 1)]
    session = {
        "session_id": SYNTHETIC_ID,
        "sport": sport,
        "activity_tag": activity_tag,
        "hr_source": hr_source,
        "quality_flags": [],
        "context": {},
        "timer_time_s": float(seconds) if timer_time_s is _UNSET else timer_time_s,
        "resting_hr_bpm": resting,
        "max_hr_bpm": max_hr,
        "threshold_hr_bpm": threshold,
        "sex": sex,
    }
    return inputs_for(session, rows, **overrides)


def outcome(body: dict) -> tuple[str | None, str | None, bool]:
    """``(hr_trimp.unavailable, session_load.unavailable, hr_trimp.value is served)``."""
    return (
        body["metrics"]["hr_trimp"]["unavailable"],
        body["session_load"]["unavailable"],
        body["metrics"]["hr_trimp"]["value"] is not None,
    )


def wrist_outcome(body: dict) -> tuple[str | None, str | None, bool]:
    """``(hr_trimp.unavailable, session_load.unavailable, wrist_hr is flagged)``."""
    return (
        body["metrics"]["hr_trimp"]["unavailable"],
        body["session_load"]["unavailable"],
        "wrist_hr" in body["flags"],
    )
