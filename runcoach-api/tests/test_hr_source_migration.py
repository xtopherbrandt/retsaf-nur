"""F015's migration path: a session stored under the RR-only rule is re-derived by delete and re-upload.

The CHANGELOG's "Migration required" note says sessions stored before F015
keep their stored ``hr_source`` and ``cadence_lock`` sample tags, that nothing
is backfilled, and that deleting such a session and re-uploading the original
file re-derives both under the same session id, with a duplicate upload
refused by 409 until the delete. This file drives that path through the app.

The stored-before state is made by uploading ``sample_run.fit`` with
``mapping._infer_hr_source`` replaced by the rule as it read before F015: RR
present -> ``chest_strap``, otherwise ``None``, which the gate fills as
``wrist_ppg``. ``sample_run.fit`` reconstructs no RR, so it stores as
``wrist_ppg`` with ``cadence_lock`` tags. The replacement is removed before
the re-upload, so the re-derived values are the shipped rule's.

The data dir is the per-test ``tmp_path`` the autouse ``isolated_data_dir``
fixture binds before the first request.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runcoach_api.ingestion import mapping, rr_reconstruction
from runcoach_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"
FILENAME = "sample_run.fit"


def _rr_only_rule(messages) -> str | None:
    """The rule before F015: RR present -> ``chest_strap``, otherwise ``None`` (the gate fills ``wrist_ppg``)."""
    return "chest_strap" if rr_reconstruction.reconstruct(messages) else None


def _cadence_lock_count(records: list[dict]) -> int:
    return sum(1 for r in records if "cadence_lock" in r["sample_quality"])


def _post(client: TestClient):
    return client.post("/sessions", files={"file": (FILENAME, (FIXTURES / FILENAME).read_bytes())})


def test_delete_and_re_upload_re_derives_hr_source_and_the_cadence_lock_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_size = (FIXTURES / FILENAME).stat().st_size
    with TestClient(app) as client:
        with monkeypatch.context() as patch:
            patch.setattr(mapping, "_infer_hr_source", _rr_only_rule)
            stored = _post(client)
        assert stored.status_code == 201, stored.text
        session_id = stored.json()["session_id"]
        before = client.get(f"/sessions/{session_id}").json()
        before_tags = _cadence_lock_count(before["records"])

        # Nothing is backfilled: the shipped rule meets the stored session as a duplicate.
        duplicate = _post(client)

        deleted = client.delete(f"/sessions/{session_id}")
        reloaded = _post(client)
        assert reloaded.status_code == 201, reloaded.text
        after = client.get(f"/sessions/{reloaded.json()['session_id']}").json()
        after_tags = _cadence_lock_count(after["records"])

    print(
        f"[slice compared] {FILENAME} ({raw_size} bytes): stored {before['hr_source']} with {before_tags} "
        f"cadence_lock tags; re-POST {duplicate.status_code}; DELETE {deleted.status_code}; re-upload "
        f"{reloaded.status_code} same id {reloaded.json()['session_id'] == session_id}, {after['hr_source']} "
        f"with {after_tags} cadence_lock tags"
    )
    assert before["hr_source"] == "wrist_ppg"
    assert before_tags > 0
    assert duplicate.status_code == 409, duplicate.text
    assert deleted.status_code == 204
    assert reloaded.json()["session_id"] == session_id
    assert after["hr_source"] == "chest_strap"
    assert after_tags == 0
