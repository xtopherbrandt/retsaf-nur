"""The values a saved load used: per field, the session's own file, else the anchor at upload (F017 AC3, AC4, AC9).

User ruling R1 with C1: each of resting, max, threshold HR and sex comes from
the session's own FIT file when it carried one, else from F016's effective
anchor as it stood inside the upload's transaction, with the anchor's version;
an anchor F016 marks ``missing`` or ``order_conflict`` is no value, and its
reason shows in ``inputs.<field>.anchor_unavailable``. The saved body never
moves afterwards: a later ``PATCH /me`` or upload that changes the anchor
leaves it byte-identical, and only a delete and re-upload recomputes it.

Every row here is a real upload through ``POST /sessions`` of ``sample_run``
patched in memory with ``tests/support/fit_patch.py``: ``remove_messages(raw,
ZONES_TARGET)`` drops the file's max and threshold so both fall to the anchor,
and ``set_field(raw, USER_PROFILE, GENDER, ...)`` makes the file female or
sexless. Entered anchors go through ``PATCH /me``. The saved body is read from
the store (``db.read_session_load_body``) and, where the route matters, from
``GET /sessions/{id}/load``.

**The expected TRIMP of a mixed pair** comes from ``tests/support/trimp_oracle.py``'s
pieces (``decode_run``, ``time_basis``, ``trimp``, ``threshold_hour_reference``),
which import nothing from ``runcoach_api``: the file's HR time and average with
the file's resting 47 and the anchor's max, so the expectation is not the
module's own arithmetic read back.

AC9's large-max rows (300, 2**63, 10**400) are entered through ``PATCH /me``
before the upload; the exact integer used is shown in ``inputs`` and saved as
that integer in the JSON body, and ``10**400`` reaches gate 9
(``not_representable``) rather than an ``OverflowError`` and a 500.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api import db
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORT = Path(__file__).parent / "support"
RUN = "sample_run.fit"
TOLERANCE = 0.01

# FIT global message numbers and field numbers patched below.
USER_PROFILE, ZONES_TARGET = 3, 7
GENDER = 1  # user_profile.gender: raw 0 female, 1 male; any other value stores absent
GENDER_FEMALE, GENDER_UNKNOWN = 0, 2

HR_FIELDS = ("resting_hr_bpm", "max_hr_bpm", "threshold_hr_bpm", "sex")
EMPTY_BLOCK = {"value": None, "source": None, "session_id": None, "anchor_version": None, "anchor_unavailable": None}


def _load_support(name: str):
    """``tests/`` is not a package (importlib mode), so support modules load from their path."""
    spec = importlib.util.spec_from_file_location(name, SUPPORT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


trimp_oracle = _load_support("trimp_oracle")
fit_patch = _load_support("fit_patch")


def _raw(name: str = RUN) -> bytes:
    return (FIXTURES / name).read_bytes()


def _without_zones(name: str = RUN) -> bytes:
    """The file with no ``zones_target`` message: no max and no threshold of its own."""
    return fit_patch.remove_messages(_raw(name), ZONES_TARGET)


def _upload(client: TestClient, raw: bytes, name: str = RUN) -> str:
    response = client.post("/sessions", files={"file": (name, raw)})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _enter(client: TestClient, **values) -> None:
    response = client.patch("/me", json=values)
    assert response.status_code == 200, response.text


def _saved_text(session_id: str) -> str:
    """The ``session_loads.body`` column as stored, undecoded."""
    conn = db.get_connection()
    try:
        row = conn.execute(f"SELECT body FROM {db.SESSION_LOADS_TABLE} WHERE session_id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None, f"no saved load for {session_id}"
    return row["body"]


def _saved(session_id: str) -> dict:
    conn = db.get_connection()
    try:
        body = db.read_session_load_body(conn, session_id)
    finally:
        conn.close()
    assert body is not None, f"no saved load for {session_id}"
    return body


def _anchor(field_name: str):
    conn = db.get_connection()
    try:
        return db.read_hr_anchors(conn)[field_name]
    finally:
        conn.close()


def _file_block(value, session_id: str) -> dict:
    return {"value": value, "source": "session_file", "session_id": session_id, "anchor_version": None, "anchor_unavailable": None}


def _anchor_block(value, version: int) -> dict:
    return {"value": value, "source": "anchor", "session_id": None, "anchor_version": version, "anchor_unavailable": None}


def _unavailable_block(reason: str) -> dict:
    return {**EMPTY_BLOCK, "anchor_unavailable": reason}


def _oracle_mix(resting: int, max_hr: int, threshold: int, sex: str = "male") -> tuple[float, float, float]:
    """``(trimp, reference, session_load)`` for ``sample_run``'s HR time and average with these settings."""
    run = trimp_oracle.decode_run(FIXTURES / RUN)
    hr_time_s, avg_hr = trimp_oracle.time_basis(run.records)
    span = float(max_hr - resting)
    value = trimp_oracle.trimp(hr_time_s / 60.0, (avg_hr - resting) / span, sex)
    reference = trimp_oracle.threshold_hour_reference((threshold - resting) / span, sex)
    return value, reference, value / reference * 100.0


def _print_load(label: str, body: dict) -> None:
    trimp = body["metrics"]["hr_trimp"]
    print(
        f"{label}: trimp {trimp['value']} reference {trimp['threshold_hour_reference']} "
        f"load {body['session_load']} flags {body['flags']} | "
        + " ".join(f"{f}={body['inputs'][f]['value']}/{body['inputs'][f]['source']}/v{body['inputs'][f]['anchor_version']}" for f in HR_FIELDS)
    )


# --- AC3: each field falls back on its own, to the anchor as it stood at upload ------------------


def test_a_missing_max_falls_back_to_the_anchor_at_upload() -> None:
    """Entered max 189 and threshold 169 are the anchors (version 1 each). ``sample_run`` with no
    ``zones_target`` saves max 189 and threshold 169 from the anchor, resting 47 and sex from its
    own file, and the TRIMP of that mix (the oracle's pieces on 47/189/169)."""
    with TestClient(app) as client:
        _enter(client, max_hr_bpm=189, threshold_hr_bpm=169)
        max_version = _anchor("max_hr_bpm").version
        threshold_version = _anchor("threshold_hr_bpm").version
        session_id = _upload(client, _without_zones())
        served = client.get(f"/sessions/{session_id}/load")
    assert served.status_code == 200, served.text
    body = _saved(session_id)
    assert served.json() == body
    _print_load("mix 47/189/169", body)
    assert body["inputs"]["max_hr_bpm"] == _anchor_block(189, max_version)
    assert body["inputs"]["threshold_hr_bpm"] == _anchor_block(169, threshold_version)
    assert body["inputs"]["resting_hr_bpm"] == _file_block(47, session_id)
    assert body["inputs"]["sex"] == _file_block("male", session_id)
    trimp, reference, load = _oracle_mix(47, 189, 169)
    print(f"  oracle mix: trimp {trimp:.4f} reference {reference:.4f} load {load:.4f}")
    assert body["metrics"]["hr_trimp"]["value"] == pytest.approx(trimp, abs=TOLERANCE)
    assert body["metrics"]["hr_trimp"]["threshold_hour_reference"] == pytest.approx(reference, abs=TOLERANCE)
    assert body["session_load"]["value"] == pytest.approx(load, abs=TOLERANCE)
    assert body["session_load"]["driver"] == "hr_trimp"
    assert body["metrics"]["hr_trimp"]["unavailable"] is None
    # The mix differs from the file's own pair (47/188/169 gives 47.36): the anchor was used.
    assert body["session_load"]["value"] != pytest.approx(47.36, abs=TOLERANCE)


def test_a_file_carrying_every_value_borrows_nothing_from_an_entered_anchor() -> None:
    """The fallback is per field and only for a field the file lacks: with entered anchors set,
    the unpatched file still saves all four from ``session_file``."""
    with TestClient(app) as client:
        _enter(client, resting_hr_bpm=60, max_hr_bpm=199, threshold_hr_bpm=180, sex="female")
        session_id = _upload(client, _raw())
    body = _saved(session_id)
    _print_load("file carries all four", body)
    for field_name, value in zip(HR_FIELDS, (47, 188, 169, "male")):
        assert body["inputs"][field_name] == _file_block(value, session_id), field_name
    assert body["session_load"]["value"] == pytest.approx(47.36, abs=TOLERANCE)


def test_a_later_anchor_change_leaves_the_saved_body_byte_identical() -> None:
    """User ruling C1: after the upload, the max anchor moves 189 -> 190 (version 2) through
    ``PATCH /me``; the stored JSON text is unchanged and the route still serves 189 at version 1."""
    with TestClient(app) as client:
        _enter(client, max_hr_bpm=189, threshold_hr_bpm=169)
        session_id = _upload(client, _without_zones())
        before_text = _saved_text(session_id)
        before = client.get(f"/sessions/{session_id}/load").json()
        _enter(client, max_hr_bpm=190)
        moved = _anchor("max_hr_bpm")
        after_text = _saved_text(session_id)
        after = client.get(f"/sessions/{session_id}/load").json()
    print(f"anchor now {moved.value} v{moved.version}; saved max {after['inputs']['max_hr_bpm']}")
    assert (moved.value, moved.version) == (190, 2)
    assert after_text == before_text
    assert after == before
    assert after["inputs"]["max_hr_bpm"] == _anchor_block(189, 1)


def test_delete_and_re_upload_saves_the_new_anchor_value() -> None:
    """The one way to recompute (C2): after the anchor moves to 190, ``DELETE`` and re-upload the
    same bytes; the new saved body uses 190 at version 2 and a different TRIMP."""
    with TestClient(app) as client:
        _enter(client, max_hr_bpm=189, threshold_hr_bpm=169)
        raw = _without_zones()
        session_id = _upload(client, raw)
        first = _saved(session_id)
        _enter(client, max_hr_bpm=190)
        assert client.delete(f"/sessions/{session_id}").status_code == 204
        again = _upload(client, raw)
    assert again == session_id
    second = _saved(session_id)
    _print_load("re-uploaded", second)
    assert second["inputs"]["max_hr_bpm"] == _anchor_block(190, 2)
    assert second["inputs"]["resting_hr_bpm"] == _file_block(47, session_id)
    trimp, _, load = _oracle_mix(47, 190, 169)
    assert second["metrics"]["hr_trimp"]["value"] == pytest.approx(trimp, abs=TOLERANCE)
    assert second["session_load"]["value"] == pytest.approx(load, abs=TOLERANCE)
    assert second["metrics"]["hr_trimp"]["value"] != pytest.approx(first["metrics"]["hr_trimp"]["value"], abs=1e-6)


def test_an_order_conflict_anchor_shows_its_reason_and_is_missing_anchor() -> None:
    """Entered max 40 sits below the file's resting 47, so F016 marks resting and max
    ``order_conflict``. The file's own resting is still used (``session_file``); max has no value,
    ``anchor_unavailable`` names ``order_conflict``, and gate 4 reports ``missing_anchor`` with
    ``unavailable_fields`` ``["max_hr_bpm"]``, not a bare missing."""
    with TestClient(app) as client:
        _enter(client, max_hr_bpm=40, threshold_hr_bpm=169)
        session_id = _upload(client, _without_zones())
        assert _anchor("max_hr_bpm").reason == "order_conflict"
    body = _saved(session_id)
    _print_load("order_conflict anchor", body)
    assert body["inputs"]["max_hr_bpm"] == _unavailable_block("order_conflict")
    assert body["inputs"]["resting_hr_bpm"] == _file_block(47, session_id)
    assert body["metrics"]["hr_trimp"]["unavailable"] == "missing_anchor"
    assert body["metrics"]["hr_trimp"]["unavailable_fields"] == ["max_hr_bpm"]
    assert body["metrics"]["hr_trimp"]["value"] is None
    assert body["session_load"] == {"value": None, "unavailable": "missing_anchor", "driver": None}


def test_a_missing_anchor_shows_missing_and_names_both_fields_when_both_lack_a_value() -> None:
    """No entry at all: max and threshold have no anchor (``missing``); resting comes from the file,
    so ``unavailable_fields`` names max alone."""
    with TestClient(app) as client:
        session_id = _upload(client, _without_zones())
    body = _saved(session_id)
    _print_load("no anchor", body)
    assert body["inputs"]["max_hr_bpm"] == _unavailable_block("missing")
    assert body["inputs"]["threshold_hr_bpm"] == _unavailable_block("missing")
    assert body["metrics"]["hr_trimp"]["unavailable"] == "missing_anchor"
    assert body["metrics"]["hr_trimp"]["unavailable_fields"] == ["max_hr_bpm"]


def test_the_save_reuses_the_anchor_syncs_profile_read() -> None:
    """``_sync_anchor_versions`` already resolves the profile inside the upload; the load's anchor
    fallback reuses that result rather than reading the profile inputs again. Every profile read
    during the upload belongs to an anchor sync (``init_schema`` runs one on each upload, ``persist``
    another): the reads and the syncs count the same, with the fallback taken."""
    reads: list[str] = []
    syncs: list[str] = []
    read_original, sync_original = db.read_profile_inputs, db._sync_anchor_versions

    def counting_read(conn):
        reads.append("read")
        return read_original(conn)

    def counting_sync(conn):
        syncs.append("sync")
        return sync_original(conn)

    with pytest.MonkeyPatch.context() as m:
        m.setattr(db, "read_profile_inputs", counting_read)
        m.setattr(db, "_sync_anchor_versions", counting_sync)
        with TestClient(app) as client:
            reads.clear()
            syncs.clear()
            session_id = _upload(client, _without_zones())
    assert _saved(session_id)["inputs"]["max_hr_bpm"]["anchor_unavailable"] == "missing"
    print(f"profile reads during the upload: {len(reads)}, anchor syncs: {len(syncs)}")
    assert len(syncs) >= 1
    assert len(reads) == len(syncs)


# --- AC4: sex chooses both coefficient pairs ----------------------------------------------------


def test_a_female_file_uses_the_female_pair_in_trimp_and_the_reference() -> None:
    raw = fit_patch.set_field(_raw(), USER_PROFILE, GENDER, GENDER_FEMALE)
    with TestClient(app) as client:
        session_id = _upload(client, raw)
    body = _saved(session_id)
    _print_load("female file", body)
    assert body["inputs"]["sex"] == _file_block("female", session_id)
    assert body["metrics"]["hr_trimp"]["coefficients"] == "female"
    assert body["flags"] == []
    assert body["metrics"]["hr_trimp"]["value"] == pytest.approx(93.29, abs=TOLERANCE)
    assert body["metrics"]["hr_trimp"]["threshold_hour_reference"] == pytest.approx(189.38, abs=TOLERANCE)
    assert body["session_load"]["value"] == pytest.approx(49.26, abs=TOLERANCE)


def test_no_sex_from_the_file_and_no_anchor_defaults_to_male_and_flags_it() -> None:
    raw = fit_patch.set_field(_raw(), USER_PROFILE, GENDER, GENDER_UNKNOWN)
    with TestClient(app) as client:
        session_id = _upload(client, raw)
        assert _anchor("sex").reason == "missing"
    body = _saved(session_id)
    _print_load("no sex, no anchor", body)
    assert body["inputs"]["sex"] == _unavailable_block("missing")
    assert body["metrics"]["hr_trimp"]["coefficients"] == "male"
    assert body["flags"] == ["sex_defaulted"]
    assert body["session_load"]["value"] == pytest.approx(47.36, abs=TOLERANCE)


def test_no_sex_from_the_file_and_an_entered_unspecified_defaults_to_male_and_flags_it() -> None:
    """An entered ``unspecified`` is a ``missing`` anchor to F016; the load treats it the same."""
    raw = fit_patch.set_field(_raw(), USER_PROFILE, GENDER, GENDER_UNKNOWN)
    with TestClient(app) as client:
        _enter(client, sex="unspecified")
        session_id = _upload(client, raw)
    body = _saved(session_id)
    _print_load("no sex, entered unspecified", body)
    assert body["inputs"]["sex"] == _unavailable_block("missing")
    assert body["metrics"]["hr_trimp"]["coefficients"] == "male"
    assert body["flags"] == ["sex_defaulted"]


def test_no_sex_from_the_file_takes_an_entered_female_anchor() -> None:
    raw = fit_patch.set_field(_raw(), USER_PROFILE, GENDER, GENDER_UNKNOWN)
    with TestClient(app) as client:
        _enter(client, sex="female")
        session_id = _upload(client, raw)
    body = _saved(session_id)
    _print_load("no sex, entered female", body)
    assert body["inputs"]["sex"] == _anchor_block("female", 1)
    assert body["metrics"]["hr_trimp"]["coefficients"] == "female"
    assert body["flags"] == []
    assert body["session_load"]["value"] == pytest.approx(49.26, abs=TOLERANCE)


# --- AC9: large entered max values ----------------------------------------------------------------


def _upload_with_entered_max(client: TestClient, max_hr: int) -> str:
    _enter(client, max_hr_bpm=max_hr, threshold_hr_bpm=169)
    assert _anchor("max_hr_bpm").value == max_hr
    return _upload(client, _without_zones())


def test_an_entered_max_of_300_gives_a_load_near_54_point_1() -> None:
    with TestClient(app) as client:
        session_id = _upload_with_entered_max(client, 300)
    body = _saved(session_id)
    _print_load("max 300", body)
    assert body["inputs"]["max_hr_bpm"] == _anchor_block(300, 1)
    assert body["session_load"]["value"] == pytest.approx(54.1, abs=0.05)
    assert body["session_load"]["value"] == pytest.approx(_oracle_mix(47, 300, 169)[2], abs=TOLERANCE)


def test_an_entered_max_of_two_to_the_63_gives_a_tiny_trimp_and_a_load_of_64_point_06() -> None:
    """The exact integer 2**63 is used, shown in ``inputs`` and saved as that integer; TRIMP is
    finite and below 1e-12 while ``session_load`` tends to the max-independent limit (64.06)."""
    with TestClient(app) as client:
        session_id = _upload_with_entered_max(client, 2**63)
        served = client.get(f"/sessions/{session_id}/load")
    assert served.status_code == 200, served.text
    body = _saved(session_id)
    _print_load("max 2**63", body)
    trimp = body["metrics"]["hr_trimp"]["value"]
    assert trimp is not None and 0.0 < trimp < 1e-12
    assert body["session_load"]["value"] == pytest.approx(64.06, abs=TOLERANCE)
    assert body["session_load"]["driver"] == "hr_trimp"
    assert body["inputs"]["max_hr_bpm"]["value"] == 2**63
    assert isinstance(body["inputs"]["max_hr_bpm"]["value"], int)
    assert f'"value": {2**63}' in _saved_text(session_id)
    assert served.json()["inputs"]["max_hr_bpm"]["value"] == 2**63


def test_an_entered_max_of_ten_to_the_400_is_not_representable_and_never_a_500() -> None:
    """``float(10**400)`` raises ``OverflowError``; the gate catches it: the upload is a 201, the
    read a 200 with ``not_representable`` on ``hr_trimp`` and ``session_load``, and the exact
    integer is shown in ``inputs``."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _enter(client, max_hr_bpm=10**400, threshold_hr_bpm=169)
        upload = client.post("/sessions", files={"file": (RUN, _without_zones())})
        assert upload.status_code == 201, upload.text
        session_id = upload.json()["session_id"]
        served = client.get(f"/sessions/{session_id}/load")
    assert served.status_code == 200, served.text
    body = served.json()
    _print_load("max 10**400", body)
    assert body == _saved(session_id)
    assert body["metrics"]["hr_trimp"]["unavailable"] == "not_representable"
    assert body["metrics"]["hr_trimp"]["value"] is None
    assert body["session_load"] == {"value": None, "unavailable": "not_representable", "driver": None}
    assert body["inputs"]["max_hr_bpm"]["value"] == 10**400
    assert body["inputs"]["max_hr_bpm"]["source"] == "anchor"
    assert json.loads(_saved_text(session_id))["inputs"]["max_hr_bpm"]["value"] == 10**400
