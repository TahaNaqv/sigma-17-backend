"""Provenance guard: the committed client extracts must still match the client workbook.

``client_source_extract.json`` and ``notes_source.json`` are the authoritative record of
what the client's disclosure file says, and the movement mapping is generated from the
first of them. Without this test the extractor exists but nothing runs it, so a hand-edit
to either artifact — or a client file that no longer says what we recorded — would pass CI
silently. That is the exact failure mode the extractor was written to close.

Skipped when the client workbook is not checked out (it lives outside the backend repo),
matching how the schema.ts mirror test handles an absent dashboard.
"""

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND / "scripts" / "extract_client_disclosure.py"
CLIENT_XLSX = (
    BACKEND.parent / "sigma-17-desktop-app" / "Output Module 2" / "Module2_Final_Output.xlsx"
)


@pytest.mark.skipif(not CLIENT_XLSX.exists(), reason="client workbook not checked out")
def test_committed_extracts_reproduce_the_client_workbook():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(CLIENT_XLSX)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "the committed extracts no longer match the client workbook:\n"
        f"{result.stdout}\n{result.stderr}\n"
        "Re-run scripts/extract_client_disclosure.py --write and review the diff — "
        "a change here means the mapping's source of truth moved."
    )
    # Guard the counts too: a silently truncated extraction would still 'reproduce'.
    assert "265 bucket cells" in result.stdout, result.stdout
    assert "187 source cells" in result.stdout, result.stdout


@pytest.mark.skipif(not CLIENT_XLSX.exists(), reason="client workbook not checked out")
def test_recorded_source_hash_matches_the_workbook():
    """The extract records which file it came from; that must be the file we have."""
    import hashlib
    import json

    extract = json.loads(
        (BACKEND / "module2_engine" / "movement" / "client_source_extract.json").read_text(
            encoding="utf-8"
        )
    )
    actual = hashlib.sha256(CLIENT_XLSX.read_bytes()).hexdigest()
    assert extract["_meta"]["source_sha256"] == actual, (
        "client_source_extract.json records a different workbook than the one present — "
        "the client has sent a new file; re-extract and review before trusting the mapping."
    )
