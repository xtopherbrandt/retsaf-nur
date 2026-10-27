"""The 27-night data file matches the provenance record written beside it.

``tests/data/overnight-27-nights.provenance.json`` records the sha256 of
``tests/data/overnight-27-nights.csv`` as committed (LF line ends). A Windows working copy
under ``core.autocrlf`` is CRLF, so the file is hashed with ``\\r\\n`` turned to ``\\n``; on a
Linux checkout that replacement changes nothing. An edit to any value in the csv, or a
provenance record left behind by a re-decode, turns this test red.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

DATA = Path(__file__).parent / "data"
NIGHTS_CSV = DATA / "overnight-27-nights.csv"
PROVENANCE = DATA / "overnight-27-nights.provenance.json"


def _committed_form_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_the_27_night_csv_matches_its_provenance_hash():
    record = json.loads(PROVENANCE.read_text(encoding="utf-8"))["rows"]
    recorded = record["sha256"]
    computed = _committed_form_sha256(NIGHTS_CSV)
    print(f"[slice compared] provenance sha256: {recorded}")
    print(f"[slice compared] csv sha256 (LF):   {computed}")
    assert record["path"] == "runcoach-api/tests/data/overnight-27-nights.csv"
    assert computed == recorded
    data_rows = NIGHTS_CSV.read_text(encoding="utf-8").splitlines()[1:]
    print(f"[slice compared] csv data rows: {len(data_rows)}; provenance row_count: {record['row_count']}")
    assert len(data_rows) == record["row_count"] == 27
