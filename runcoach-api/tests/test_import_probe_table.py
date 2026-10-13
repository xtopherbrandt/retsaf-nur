"""F020 AC9: the reference section 10 adversarial probe table, driven through ``POST /imports``.

One parametrised test, ``test_probe_table``, with one id per row. Each row builds its archive in
``tmp_path``, posts it to the real app under ``with TestClient(app)`` (the store is the autouse
``isolated_data_dir``), drains the worker with ``job.wait_idle`` and asserts what happened. The
table of what was fed, what happened and why that was intended is kept with the task's notes; the
reader-level rows (``archive.scan`` alone) live in ``test_import_archive_reader.py``.

The rows were written alongside these tests, from the reference's section 10 list and the archive
reader's probe table, by the same author: they are not an independent oracle. One expectation was
changed after a run: the hostile-names row first expected backslash labels verbatim, and on
Windows zipfile reads a backslash in an entry name as "/" (``ZipInfo`` normalises ``os.sep``), so
the row now expects the names as zipfile reads them.

Fixtures cited by the provenance table's kind: ``sample_run``, ``strap_run_hrv``,
``wrist_ppg_run``, ``dev_fields_run`` and ``hilly_run_8k_fr945`` (real runs). Hand-built FIT files
come from ``import_archive.minimal_fit``: a lone ``file_id`` message, which the pipeline refuses
with the 400 ``POST /sessions`` gives it.

The caps are lowered with ``monkeypatch`` on ``archive.INNER_ZIP_CAP`` and
``archive.FIT_ENTRY_CAP`` (read at call time), so no row writes a gigabyte.
"""

from __future__ import annotations

import importlib.util
import io
import sqlite3
import struct
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import fitdecode
import pytest
from fastapi.testclient import TestClient
from fitdecode.utils import compute_crc
from runcoach_api import db
from runcoach_api.imports import archive, job
from runcoach_api.main import _MULTIPART_FRAMING_ALLOWANCE_BYTES, MAX_UPLOAD_BYTES, app

_SPEC = importlib.util.spec_from_file_location(
    "import_archive", Path(__file__).parent / "support" / "import_archive.py"
)
assert _SPEC is not None and _SPEC.loader is not None
import_archive = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(import_archive)

IA = import_archive
_FP_SPEC = importlib.util.spec_from_file_location(
    "fit_patch", Path(__file__).parent / "support" / "fit_patch.py"
)
assert _FP_SPEC is not None and _FP_SPEC.loader is not None
fit_patch = importlib.util.module_from_spec(_FP_SPEC)
_FP_SPEC.loader.exec_module(fit_patch)

FIXTURES = Path(__file__).parent / "fixtures"
DRAIN_TIMEOUT_S = 60.0
FILE_TYPE_ACTIVITY = 4
FILE_ID, TIME_CREATED_FIELD = 0, 4
FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)
INVALID_UINT32 = 0xFFFFFFFF


@pytest.fixture(autouse=True)
def _drain_workers():
    yield
    assert job.wait_idle(DRAIN_TIMEOUT_S), "an import worker outlived its test"


def _drain() -> None:
    assert job.wait_idle(DRAIN_TIMEOUT_S), "the import worker did not finish"


def _fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.fit").read_bytes()


def _fit_seconds(year: int, month: int = 6, day: int = 1) -> int:
    return int((datetime(year, month, day, tzinfo=UTC) - FIT_EPOCH).total_seconds())


def _activity(year: int, month: int = 6) -> bytes:
    """A lone activity ``file_id``: screened as an activity, refused by the pipeline (400)."""
    return IA.minimal_fit(FILE_TYPE_ACTIVITY, time_created=_fit_seconds(year, month))


def _session_start(name: str) -> datetime:
    """The ``session`` message's ``start_time`` of a corpus fixture, decoded with fitdecode."""
    with fitdecode.FitReader(io.BytesIO(_fixture(name))) as reader:
        starts = [
            frame.get_value("start_time")
            for frame in reader
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "session"
        ]
    (start,) = starts
    return start


def _stamped(name: str, raw_time_created: int) -> bytes:
    """A corpus fixture with ``file_id.time_created`` set to ``raw_time_created`` (CRC repaired)."""
    return fit_patch.set_field(_fixture(name), FILE_ID, TIME_CREATED_FIELD, raw_time_created)


def _zip(entries: list[tuple[str, bytes]], **kwargs) -> bytes:
    buffer = io.BytesIO()
    kwargs.setdefault("compression", zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(buffer, "w", **kwargs) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buffer.getvalue()


def _encrypt_flag(data: bytes, name: bytes) -> bytes:
    """Set general-purpose bit 0 (encrypted) on one entry in both headers, as a password-protected zip carries."""
    out = bytearray(data)
    for signature, flag_offset, name_offset in ((b"PK\x03\x04", 6, 30), (b"PK\x01\x02", 8, 46)):
        start = 0
        while (at := out.find(signature, start)) >= 0:
            length = struct.unpack_from("<H", out, at + (26 if signature == b"PK\x03\x04" else 28))[0]
            if bytes(out[at + name_offset : at + name_offset + length]) == name:
                flags = struct.unpack_from("<H", out, at + flag_offset)[0]
                struct.pack_into("<H", out, at + flag_offset, flags | 0x1)
            start = at + 4
    return bytes(out)


def _rename_entry(data: bytes, old: bytes, new: bytes) -> bytes:
    """Swap an entry name's bytes (same length) in the local header and the central directory."""
    assert len(old) == len(new) and data.count(old) == 2
    return data.replace(old, new)


def _post(client: TestClient, raw: bytes, name: str = "export.zip"):
    return client.post("/imports", files={"file": (name, raw, "application/zip")})


def _run(client: TestClient, raw: bytes, name: str = "export.zip") -> dict:
    """Post an archive that must be accepted, drain the worker and return the finished import."""
    response = _post(client, raw, name)
    assert response.status_code == 202, response.text
    _drain()
    done = client.get(f"/imports/{response.json()['import_id']}").json()
    print("counts:", done["counts"])
    print("files:", [(f["seq"], f["entry"], f["outcome"], f["status"], f["detail"]) for f in done["files"]])
    assert done["status"] == "completed", done["error"]
    assert not job.job_dir(db._load_config_cached().data_dir, done["import_id"]).exists()
    return done


def _import_rows(data_dir: Path) -> list[tuple]:
    conn = sqlite3.connect(data_dir / db.DB_FILENAME)
    try:
        return conn.execute("SELECT import_id, status FROM imports").fetchall()
    finally:
        conn.close()


def _temp_children(data_dir: Path) -> list[str]:
    root = data_dir / "imports"
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def _outcomes(done: dict) -> list[tuple[str, str]]:
    return [(row["entry"], row["outcome"]) for row in done["files"]]


def _empty_counts(done: dict) -> bool:
    c = done["counts"]
    return (c["stored"], c["already_stored"], c["failed"], c["skipped_sport"], c["non_activity"]) == (
        0,
        0,
        0,
        {},
        {},
    )


# --- the rows ----------------------------------------------------------------------------------


def _refused(client: TestClient, data_dir: Path, raw: bytes) -> None:
    response = _post(client, raw)
    print("response:", response.status_code, response.json())
    assert response.status_code == 400, response.text
    assert response.json()["detail"].startswith("not a zip archive: ")
    assert client.get("/imports").json() == {"items": []}
    assert _import_rows(data_dir) == []
    assert _temp_children(data_dir) == []


def row_zero_byte(client, data_dir, tmp_path, monkeypatch) -> None:
    _refused(client, data_dir, b"")


def row_non_zip(client, data_dir, tmp_path, monkeypatch) -> None:
    _refused(client, data_dir, _fixture("sample_run"))


def row_empty_zip(client, data_dir, tmp_path, monkeypatch) -> None:
    done = _run(client, _zip([]))
    assert done["files"] == []
    assert (
        _empty_counts(done)
        and done["counts"]["not_fit"] == {}
        and done["counts"]["unreadable_archives"] == []
    )
    assert done["progress"] == {"fit_files_scanned": 0, "activities_total": 0, "activities_processed": 0}


def row_non_fit_only(client, data_dir, tmp_path, monkeypatch) -> None:
    done = _run(
        client,
        _zip([("a/b.tcx", b"x"), ("README", b"x"), ("folder/", b""), ("x.GPX", b"x"), ("c.json", b"{}")]),
    )
    assert done["files"] == [] and _empty_counts(done)
    assert done["counts"]["not_fit"] == {"tcx": 1, "(none)": 1, "gpx": 1, "json": 1}


def row_truncated_inner_zip(client, data_dir, tmp_path, monkeypatch) -> None:
    inner = _zip([("a.fit", _fixture("strap_run_hrv"))])
    done = _run(
        client, _zip([("cut/Part1.zip", inner[: len(inner) // 2]), ("loose.fit", _fixture("sample_run"))])
    )
    (unreadable,) = done["counts"]["unreadable_archives"]
    assert unreadable["label"] == "cut/Part1.zip" and "BadZipFile" in unreadable["reason"]
    assert _outcomes(done) == [("loose.fit", "stored")]


def row_inner_zip_past_cap(client, data_dir, tmp_path, monkeypatch) -> None:
    cap = 1024 * 1024
    monkeypatch.setattr(archive, "INNER_ZIP_CAP", cap)
    bomb = _zip(
        [("zeros.bin", b"\0" * (4 * cap)), ("run.fit", _fixture("strap_run_hrv"))],
        compression=zipfile.ZIP_STORED,
    )
    raw = _zip([("Bomb/Part1.zip", bomb), ("loose.fit", _fixture("sample_run"))])
    print("outer archive bytes:", len(raw), "inner declared:", len(bomb), "cap:", cap)
    assert len(raw) < cap < len(bomb)
    done = _run(client, raw)
    (unreadable,) = done["counts"]["unreadable_archives"]
    assert unreadable["label"] == "Bomb/Part1.zip" and f"{cap} byte limit" in unreadable["reason"]
    assert _outcomes(done) == [("loose.fit", "stored")]


def row_fit_entry_over_cap(client, data_dir, tmp_path, monkeypatch) -> None:
    run = _fixture("sample_run")
    cap = len(run) - 1
    monkeypatch.setattr(archive, "FIT_ENTRY_CAP", cap)
    done = _run(client, _zip([("big.fit", run), ("small.fit", _fixture("strap_run_hrv")[:cap])]))
    rows = {row["entry"]: row for row in done["files"]}
    assert (rows["big.fit"]["outcome"], rows["big.fit"]["status"]) == ("failed", 413)
    assert rows["big.fit"]["detail"] == f"upload exceeds {cap} byte limit"
    # At the cap it is read and goes on to the pipeline (a cut file: whatever POST /sessions says, not 413).
    assert rows["small.fit"]["status"] != 413


def row_three_levels(client, data_dir, tmp_path, monkeypatch) -> None:
    nested = _zip([("deep.fit", _fixture("sample_run"))])
    done = _run(client, _zip([("Part1.zip", _zip([("nested.zip", nested)]))]))
    assert done["files"] == [] and _empty_counts(done)
    assert done["counts"]["not_fit"] == {"zip": 1}


HOSTILE_BASES = (
    "probe_up_q7.fit",
    "probe_back_q7.fit",
    "probe_drive_q7.fit",
    "probe_drive_back_q7.fit",
    "probe_abs_q7.fit",
    "probe_deeper_q7.fit",
    "probe_inner_q7.zip",
)


def _tree(root: Path) -> set[str]:
    return {str(p) for p in root.rglob("*")}


def row_hostile_names(client, data_dir, tmp_path, monkeypatch) -> None:
    hostile = [
        ("../../probe_up_q7.fit", _fixture("sample_run")),
        # zipfile writes os.sep as "/" on Windows, so these two get their backslashes below.
        ("..#..#probe_back_q7.fit", _fixture("strap_run_hrv")),
        ("C:/probe_drive_q7.fit", _fixture("wrist_ppg_run")),
        ("C:#probe_drive_back_q7.fit", _activity(2018, 9)),
        ("/abs/probe_abs_q7.fit", _activity(2018, 10)),
        ("NAME_XX.fit", _fixture("dev_fields_run")),
        ("../probe_inner_q7.zip", _zip([("../../probe_deeper_q7.fit", _fixture("hilly_run_8k_fr945"))])),
    ]
    raw = _rename_entry(_zip(hostile), b"NAME_XX", b"NAME_\x82\xff")
    raw = _rename_entry(raw, b"..#..#probe_back", b"..\\..\\probe_back")
    raw = _rename_entry(raw, b"C:#probe_drive_back", b"C:\\probe_drive_back")
    assert b"..\\..\\probe_back" in raw and b"C:\\probe_drive_back" in raw and b"NAME_\x82\xff" in raw
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        # The labels the reference asks for are the names as zipfile reads them: on Windows it turns
        # a backslash into "/" (ZipInfo normalises os.sep), and it decodes a non-UTF-8 name as cp437.
        outer_names = zf.namelist()
    print("entry names as zipfile reads them:", outer_names)
    expected = {n for n in outer_names if not n.endswith(".zip")} | {
        "../probe_inner_q7.zip!../../probe_deeper_q7.fit"
    }
    (odd,) = [n for n in outer_names if n.startswith("NAME_")]
    before = _tree(tmp_path)
    done = _run(client, raw)
    new_paths = sorted(_tree(tmp_path) - before)
    print("new paths under the scratch root:", new_paths)
    assert set(new_paths) <= {str(data_dir / "imports")}, new_paths
    cwd = Path.cwd()
    landing = {data_dir, data_dir.parent, tmp_path.parent, cwd, cwd.parent, cwd.parent.parent}
    landing |= {Path("C:/"), Path("/abs"), Path(cwd.anchor) / "abs"}
    hits = [d / b for d in landing for b in HOSTILE_BASES if (d / b).exists()]
    print("landing dirs checked:", sorted(map(str, landing)), "hits:", hits)
    assert hits == []
    outcomes = dict(_outcomes(done))
    assert set(outcomes) == expected and len(done["files"]) == 7
    by_base = {e.replace("\\", "/").rsplit("/", 1)[-1]: o for e, o in outcomes.items()}
    assert by_base == {
        "probe_up_q7.fit": "stored",
        "probe_back_q7.fit": "stored",
        "probe_drive_q7.fit": "stored",
        "probe_drive_back_q7.fit": "failed",
        "probe_abs_q7.fit": "failed",
        odd: "stored",
        "probe_deeper_q7.fit": "stored",
    }
    rows = {row["entry"].replace("\\", "/").rsplit("/", 1)[-1]: row for row in done["files"]}
    refused = "sport could not be determined: no session or sport message found -- cannot determine sport"
    for base in ("probe_drive_back_q7.fit", "probe_abs_q7.fit"):
        assert (rows[base]["status"], rows[base]["detail"]) == (400, refused), rows[base]


@pytest.mark.filterwarnings("ignore:Duplicate name")
def row_duplicate_names(client, data_dir, tmp_path, monkeypatch) -> None:
    done = _run(client, _zip([("run.fit", _fixture("sample_run")), ("run.fit", _fixture("strap_run_hrv"))]))
    assert _outcomes(done) == [("run.fit", "stored"), ("run.fit", "stored")]
    ids = [row["session_id"] for row in done["files"]]
    assert len(set(ids)) == 2
    starts = sorted(client.get(f"/sessions/{i}").json()["start_time"] for i in ids)
    assert starts[0] != starts[1]


def row_upper_case_fit(client, data_dir, tmp_path, monkeypatch) -> None:
    inner = _zip([("X.Fit", _fixture("strap_run_hrv"))])
    done = _run(client, _zip([("RUN.FIT", _fixture("sample_run")), ("Part1.ZIP", inner)]))
    assert sorted(_outcomes(done)) == [("Part1.ZIP!X.Fit", "stored"), ("RUN.FIT", "stored")]


def row_encrypted_entry(client, data_dir, tmp_path, monkeypatch) -> None:
    raw = _zip(
        [
            ("locked.fit", _fixture("sample_run")),
            ("locked.zip", _zip([("in.fit", _fixture("wrist_ppg_run"))])),
            ("open.fit", _fixture("strap_run_hrv")),
        ]
    )
    raw = _encrypt_flag(_encrypt_flag(raw, b"locked.fit"), b"locked.zip")
    done = _run(client, raw)
    rows = {row["entry"]: row for row in done["files"]}
    assert set(rows) == {"locked.fit", "open.fit"}
    assert (rows["locked.fit"]["outcome"], rows["locked.fit"]["status"]) == ("failed", None)
    assert rows["locked.fit"]["detail"] == "entry is encrypted"
    assert rows["open.fit"]["outcome"] == "stored"
    assert done["counts"]["unreadable_archives"] == [{"label": "locked.zip", "reason": "entry is encrypted"}]
    # The failed row comes after every activity (its file_id was never read).
    assert [row["entry"] for row in done["files"]] == ["open.fit", "locked.fit"]


def row_zip64(client, data_dir, tmp_path, monkeypatch) -> None:
    inner = _zip([("in.fit", _fixture("strap_run_hrv"))])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for name, data in (("run.fit", _fixture("sample_run")), ("p.zip", inner)):
            with zf.open(name, "w", force_zip64=True) as fh:
                fh.write(data)
    raw = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        versions = [info.extract_version for info in zf.infolist()]
    print("extract versions:", versions)
    assert versions == [zipfile.ZIP64_VERSION] * 2  # each entry written with ZIP64 extra fields
    done = _run(client, raw)
    assert sorted(_outcomes(done)) == [("p.zip!in.fit", "stored"), ("run.fit", "stored")]


def row_no_file_id(client, data_dir, tmp_path, monkeypatch) -> None:
    body = bytearray(IA.minimal_fit(FILE_TYPE_ACTIVITY)[:-2])
    struct.pack_into("<H", body, 14 + 3, 20)  # the definition's global message number: record
    data = bytes(body) + struct.pack("<H", compute_crc(bytes(body)))
    done = _run(client, _zip([("no_file_id.fit", data), ("run.fit", _fixture("sample_run"))]))
    assert _outcomes(done) == [("run.fit", "stored"), ("no_file_id.fit", "failed")]
    row = done["files"][1]
    assert row["status"] is None and "no file_id" in row["detail"]


def row_no_time_created(client, data_dir, tmp_path, monkeypatch) -> None:
    done = _run(
        client,
        _zip(
            [
                ("undated.fit", _stamped("sample_run", INVALID_UINT32)),
                ("dated.fit", _fixture("strap_run_hrv")),
            ]
        ),
    )
    assert _outcomes(done) == [("dated.fit", "stored"), ("undated.fit", "stored")]
    assert done["files"][0]["time_created"] is not None
    assert done["files"][1]["time_created"] is None


def row_fit_epoch_stamp(client, data_dir, tmp_path, monkeypatch) -> None:
    done = _run(
        client, _zip([("dated.fit", _fixture("strap_run_hrv")), ("epoch.fit", _stamped("sample_run", 0))])
    )
    assert _outcomes(done) == [("epoch.fit", "stored"), ("dated.fit", "stored")]
    assert datetime.fromisoformat(done["files"][0]["time_created"]) == FIT_EPOCH


def row_stamp_2119(client, data_dir, tmp_path, monkeypatch) -> None:
    raw = _zip(
        [
            ("future.fit", _stamped("wrist_ppg_run", _fit_seconds(2119))),
            ("a.fit", _fixture("sample_run")),
            ("b.fit", _fixture("strap_run_hrv")),
        ]
    )
    done = _run(client, raw)
    assert [o for _, o in _outcomes(done)] == ["stored"] * 3
    assert done["files"][-1]["entry"] == "future.fit"
    assert datetime.fromisoformat(done["files"][-1]["time_created"]).year == 2119
    session = client.get(f"/sessions/{done['files'][-1]['session_id']}").json()
    started = datetime.fromisoformat(session["start_time"])
    print("2119-stamped file's session start:", started)
    assert started == _session_start("wrist_ppg_run")
    # Processed after runs that start later than it does: the accepted cost of ordering by time_created.
    earlier = [datetime.fromisoformat(r["time_created"]) for r in done["files"][:-1]]
    assert all(t.year < 2119 for t in earlier)


def row_trailing_slash_over_50mb(client, data_dir, tmp_path, monkeypatch) -> None:
    declared = MAX_UPLOAD_BYTES + _MULTIPART_FRAMING_ALLOWANCE_BYTES + 1
    over = client.post(
        "/imports/", headers={"content-length": str(declared)}, content=b"tiny", follow_redirects=False
    )
    print("declared", declared, "->", over.status_code, over.headers.get("location"))
    assert over.status_code == 307, over.text  # past the middleware (not its 413), redirected
    assert over.headers["location"].endswith("/imports")
    assert _import_rows(data_dir) == []
    # Followed, a real archive posted to the trailing-slash path reaches the route and is imported.
    raw = _zip([("run.fit", _fixture("sample_run"))])
    followed = client.post(
        "/imports/", files={"file": ("export.zip", raw, "application/zip")}, follow_redirects=True
    )
    assert followed.status_code == 202, followed.text
    _drain()
    done = client.get(f"/imports/{followed.json()['import_id']}").json()
    assert done["status"] == "completed" and _outcomes(done) == [("run.fit", "stored")]


ROWS: dict[str, Callable] = {
    "zero_byte": row_zero_byte,
    "non_zip": row_non_zip,
    "empty_zip": row_empty_zip,
    "non_fit_only": row_non_fit_only,
    "truncated_inner_zip": row_truncated_inner_zip,
    "inner_zip_past_cap": row_inner_zip_past_cap,
    "fit_entry_over_cap": row_fit_entry_over_cap,
    "three_levels": row_three_levels,
    "hostile_names": row_hostile_names,
    "duplicate_names": row_duplicate_names,
    "upper_case_fit": row_upper_case_fit,
    "encrypted_entry": row_encrypted_entry,
    "zip64": row_zip64,
    "no_file_id": row_no_file_id,
    "no_time_created": row_no_time_created,
    "fit_epoch_stamp": row_fit_epoch_stamp,
    "stamp_2119": row_stamp_2119,
    "trailing_slash_over_50mb": row_trailing_slash_over_50mb,
}


@pytest.mark.filterwarnings("ignore:Duplicate name")
@pytest.mark.parametrize("row", list(ROWS), ids=list(ROWS))
def test_probe_table(
    row: str, isolated_data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(app) as client:
        ROWS[row](client, isolated_data_dir, tmp_path, monkeypatch)
    _drain()
