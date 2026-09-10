"""Generate module2_engine/movement/mapping_source.json — the per-(line, bucket)
source map for the IFRS 17 movement disclosure.

Data-driven and reproducible: transforms the committed, verified client extraction
(``client_source_extract.json`` — the authoritative per-cell source map lifted verbatim
from the client's ``Module2_Final_Output.xlsx`` Gross/RI sheets) joined to the schema
(``schema_source.json``, by Excel row) into a mapping keyed by canonical schema line id.

Each value line carries a ``buckets`` dict; each (bucket) entry has:
    sign   — "+" | "-" | "+/-" | "-/+"  (compute applies it: mult = -1 if sign starts "-").
    tier   — D (direct column) | Δ (derived expr) | O (manual override input) | M (manual/0).
    source — column/expression over IFRS Summary columns, or null. Opening build-up
             lines are templated with ``{p}`` (=_prev opening / _curr closing) so the
             roll-forward can compute both the opening balance and the independent EOP
             balance for the reconciliation control.
    apply_sign — emitted (false) when the source column is already stored signed by
             Module 2 (``mapping.PRE_SIGNED_SOURCE_COLUMNS``); compute then passes the
             value through instead of negating an already-negative figure.
    override_key — for tier O, the stable key of the class×cohort override input.
    amended — present when the client corrected the formula after sign-off; the value is
             the rationale (see ``CLIENT_AMENDMENTS``).

Structural lines (opening/closing/subtotal/section) carry no bucket sources; their
per-bucket SUM formulas are preserved under ``subtotal_formulas`` for faithful rendering.

Authoritative as of the client's file (sha in the extract _meta). Run:
    python scripts/gen_movement_mapping.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from module2_engine.movement.mapping import is_pre_signed  # noqa: E402

PKG = BACKEND / "module2_engine" / "movement"
EXTRACT = json.loads((PKG / "client_source_extract.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((PKG / "schema_source.json").read_text(encoding="utf-8"))
OUT = PKG / "mapping_source.json"

OVERRIDE_KEYS = EXTRACT["_meta"]["override_inputs_RI"]  # Template-Info column letter -> key
STRUCTURAL = {"opening", "closing", "subtotal", "section"}

#: Formula corrections the client issued *after* signing off Module2_Final_Output.xlsx,
#: applied on top of the verbatim extract so ``client_source_extract.json`` stays a faithful
#: copy of their file. Keyed by (sheet, Excel row, bucket) -> (expected expr, corrected expr,
#: rationale). The expected expr is asserted: if the client re-issues the workbook with the
#: fix already in it, generation fails loudly here instead of silently double-applying.
CLIENT_AMENDMENTS: dict[tuple[str, int, str], tuple[str, str, str]] = {
    ("Gross", 29, "LRC_excl_LC"): (
        "Rec_GOP_curr-Rec_Provision_prev",
        "Rec_Provision_curr-Rec_Provision_prev",
        "2026-09-10 client correction: 'IFRS Summary'!DA1 -> DF1. The line is the "
        "period-over-period change in the premium debtors' provision, but the signed file "
        "referenced Rec_GOP_curr (premium receivable) against Rec_Provision_prev, "
        "subtracting the whole receivable balance from insurance revenue. Now the same "
        "curr-minus-prev shape as row 28 (Change in Unearned Premium Reserves).",
    ),
    ("Gross", 41, "LRC_excl_LC"): (
        "DAC_curr-DAC_prev",
        "DAC_prev-DAC_curr",
        "2026-09-10 client correction: 'IFRS Summary'!F1-CH1 -> CH1-F1 (operand order). "
        "DAC amortisation enters insurance service expenses as the decrease in DAC over "
        "the period, so the line is prev minus curr.",
    ),
}


def _amend(sheet: str, row: int, bucket: str, cell: dict) -> dict:
    """Apply a post-sign-off client correction to one extracted bucket cell."""
    amendment = CLIENT_AMENDMENTS.get((sheet, row, bucket))
    if not amendment:
        return cell
    expected, corrected, _why = amendment
    actual = cell.get("source_expr")
    if actual != expected:
        raise SystemExit(
            f"amendment for {sheet} row {row} {bucket} expected source_expr {expected!r} "
            f"but the extract holds {actual!r} — re-check CLIENT_AMENDMENTS against the "
            f"current client file before regenerating."
        )
    return {**cell, "source_expr": corrected, "amended": True}


def _opening_rows(client_lines: list[dict]) -> set[int]:
    """Rows that feed the 'as at 01/01' opening subtotal (parsed from its SUM range)."""
    opening = next((ln for ln in client_lines if ln["kind"] in ("subtotal", "opening")
                    and "as at 01/01" in ln["label"].lower()), None)
    if not opening or not opening.get("total_formula"):
        return set()
    m = re.search(r"\(?:?[A-Z]*?(\d+):[A-Z]*?(\d+)\)", opening["total_formula"])
    return set(range(int(m.group(1)), int(m.group(2)) + 1)) if m else set()


def _is_direct(expr: str) -> bool:
    """A single column reference (no arithmetic). Internal ' - ' inside a column name
    (e.g. 'GROSS - Outstanding_prev') is not an operator."""
    return not re.search(r"[+\-*]", expr.replace(" - ", " – "))


def _tier_and_source(bucket: dict, *, in_opening: bool):
    """Map one client bucket cell -> (tier, source, override_key)."""
    typ = bucket.get("type")
    if typ == "computed":
        expr = bucket["source_expr"]
        if in_opening:
            expr = expr.replace("_prev", "_{p}")
        return ("D" if _is_direct(expr) else "Δ"), expr, None
    if typ == "override":
        return "O", None, OVERRIDE_KEYS.get(bucket["detail"], bucket["detail"])
    # const/empty with a sign, or a flattened value -> manual (override candidate, defaults 0)
    return "M", None, None


def build() -> dict:
    out: dict = {
        "_meta": {
            "status": "AUTHORITATIVE — encoded from the client's disclosure file (per-(line,bucket))",
            "source": EXTRACT["_meta"]["source_file"],
            "source_sha256": EXTRACT["_meta"]["source_sha256"],
            "generated_by": "scripts/gen_movement_mapping.py from client_source_extract.json + schema_source.json",
            "legend": "sign applied by compute (mult=-1 if sign starts '-', unless apply_sign=false "
            "— source already stored signed); {p}=_prev(open)/_curr(close); "
            "tiers D=direct column, Δ=derived expr, O=class×cohort override input, M=manual/0.",
            "schema_version": SCHEMA["schema_version"],
            "client_amendments": [
                f"{sheet} row {row} {bucket}: {old} -> {new_}"
                for (sheet, row, bucket), (old, new_, _why) in CLIENT_AMENDMENTS.items()
            ],
        }
    }
    for sname, sh in SCHEMA["sheets"].items():
        client = EXTRACT["sheets"][sname]
        by_row = {ln["row"]: ln for ln in client}
        opening_rows = _opening_rows(client)
        value_buckets = sh["value_buckets"]
        lines: dict = {}
        cov = {"D": 0, "Δ": 0, "O": 0, "M": 0, "structural": 0}
        for sln in sh["lines"]:
            cln = by_row.get(sln["row"], {})
            kind = sln["kind"]
            entry: dict = {"label": sln["label"], "row": sln["row"], "kind": kind}
            if kind in STRUCTURAL:
                cov["structural"] += 1
                formulas = {b: d.get("detail") for b, d in (cln.get("buckets") or {}).items()
                            if d.get("type") == "subtotal"}
                if formulas:
                    entry["subtotal_formulas"] = formulas
                lines[sln["id"]] = entry
                continue
            buckets: dict = {}
            in_opening = sln["row"] in opening_rows
            for b in value_buckets:
                cell = (cln.get("buckets") or {}).get(b)
                if not cell:
                    continue
                cell = _amend(sname, sln["row"], b, cell)
                tier, source, okey = _tier_and_source(cell, in_opening=in_opening)
                bentry: dict = {"sign": cell.get("sign"), "tier": tier, "source": source}
                if cell.get("amended"):
                    bentry["amended"] = CLIENT_AMENDMENTS[(sname, sln["row"], b)][2]
                if is_pre_signed(source):
                    bentry["apply_sign"] = False
                if okey:
                    bentry["override_key"] = okey
                buckets[b] = bentry
                cov[tier] = cov.get(tier, 0) + 1
            entry["buckets"] = buckets
            lines[sln["id"]] = entry
        out[sname] = {"coverage": cov, "lines": lines}
    return out


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for sheet in ("Gross", "RI"):
        c = data[sheet]["coverage"]
        print(f"{sheet}: {len(data[sheet]['lines'])} lines  coverage={c}")
    print(f"wrote {OUT}")
