"""Schema-as-code integrity + frontend-mirror drift guard for the IFRS 17 movement
disclosure. Pure structural checks (no actuarial assertions — that is sign-off, plan §1).
"""

import subprocess
import sys
from pathlib import Path

from module2_engine.movement import mapping as M
from module2_engine.movement import schema as S

BACKEND = Path(__file__).resolve().parents[2]
SCHEMA_TS = BACKEND.parent / "sigma-17-dashboard" / "src" / "features" / "movement" / "schema.ts"


def test_schema_is_structurally_valid():
    assert S.validate_schema() == []


def test_mapping_is_consistent_with_schema():
    assert M.validate_mapping() == []


def test_both_sheets_present_with_five_buckets():
    assert set(S.SCHEMA.sheets) == {"Gross", "RI"}
    for sheet in S.SCHEMA.sheets.values():
        assert len(sheet.buckets) == 5  # 4 measurement buckets + Total
        assert len(sheet.value_buckets) == 4
        assert "Total" in sheet.buckets


def test_every_sheet_has_one_opening_and_one_closing():
    for sheet in S.SCHEMA.sheets.values():
        kinds = [ln.kind for ln in sheet.lines]
        assert kinds.count("opening") == 1
        assert kinds.count("closing") == 1


def test_line_ids_unique_per_sheet():
    for sheet in S.SCHEMA.sheets.values():
        ids = [ln.id for ln in sheet.lines]
        assert len(ids) == len(set(ids))


def test_schema_ts_mirror_is_in_sync():
    """schema.ts must equal a fresh generation — fail loudly so CI catches drift."""
    if not SCHEMA_TS.exists():  # dashboard repo may be absent in some CI checkouts
        return
    before = SCHEMA_TS.read_text(encoding="utf-8")
    subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "gen_movement_schema.py")],
        check=True,
        capture_output=True,
    )
    after = SCHEMA_TS.read_text(encoding="utf-8")
    assert before == after, "schema.ts is stale — run scripts/gen_movement_schema.py and commit"
