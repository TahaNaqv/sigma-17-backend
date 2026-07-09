"""Render the IFRS 17 movement disclosure to an .xlsx workbook (openpyxl) plus a
machine-readable JSON companion.

Faithful to the client's SAMA template: the exact Gross/RI line structure with the four
measurement buckets + Total, the direction sign column, and the real nested subtotals
(evaluated from the schema's Excel formulas — see ``_formula_for_row``; the Gross sheet's
flattened subtotals are supplied from the parallel RI structure in ``_FLATTENED_GROSS``).

Grains (plan Q1): an entity Total worksheet, then one worksheet per reserving class
carrying the class total followed by its per-cohort (UWY) detail — all by summation, so
every aggregate ties to the sum of its parts. The closing line is rendered as the
roll-forward (opening + ΔP&L − cash flows), matching the RI closing formula.

Uses openpyxl (a guaranteed dependency) — NOT xlsxwriter (optional, absent in prod) and
NOT the pandas WRITE_ENGINE (whose engine choice must stay free for the bit-identical
process pipeline).
"""

from __future__ import annotations

import ast
import io
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .compute import MovementResult, SheetResult, aggregated_views
from .schema import SCHEMA, SCHEMA_VERSION, Sheet

# Gross subtotals the client flattened to static values (formulas lost). Recovered from
# the SAMA structure / parallel RI formulas; column letters are irrelevant (the evaluator
# resolves by row). Closing = opening + total changes − total cash flows (roll-forward).
_FLATTENED_GROSS: dict[int, str] = {
    32: "=SUM(C33:C37)",   # Incurred claims and other expenses
    47: "=C48+C53",         # Past Service: Changes to LIC = Change in Ultimate + Other diff
    48: "=SUM(C49:C52)",   # Change in Ultimate for Past Service
    54: "=C55",             # Investment components = Change in Profit Commission
    61: "=SUM(C62:C63)",   # Other movements = Item 1 + Item 2 (Specify)
    72: "=C6+C64-C71",     # Closing = opening + total changes − total cash flows
}

_NUMFMT = "#,##0;(#,##0)"
_THIN = Side(style="thin", color="DDDDDD")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_CENTER = Alignment(horizontal="center", vertical="center")

_F = {
    "title": Font(bold=True, size=13),
    "meta": Font(italic=True, color="888888"),
    "sheet_tag": Font(bold=True, color="1F4E78"),
    "bucket_font": Font(bold=True, color="FFFFFF"),
    "bucket_fill": PatternFill("solid", fgColor="1F4E78"),
    "bold": Font(bold=True),
    "sign": Font(color="888888"),
    "open_fill": PatternFill("solid", fgColor="FCE4D6"),
    "close_fill": PatternFill("solid", fgColor="E2EFDA"),
    "sub_fill": PatternFill("solid", fgColor="F2F2F2"),
}


def _num(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f and f not in (float("inf"), float("-inf")) else 0.0


# ── subtotal formula evaluation ──────────────────────────────────────────────

def _formula_for_row(sheet: Sheet) -> dict[int, str]:
    """row -> Excel formula string for every non-input aggregate line."""
    out: dict[int, str] = {}
    for ln in sheet.lines:
        if ln.formulas:
            # any bucket's excel works — they reference the same rows, different columns
            out[ln.row] = next(iter(ln.formulas.values()))["excel"]
    if sheet.name == "Gross":
        out.update(_FLATTENED_GROSS)
    return out


def _to_arith(excel: str) -> str:
    """Excel subtotal formula -> pure arithmetic over row numbers.
    Expands ``X7:X25`` ranges, drops ``SUM``/column letters; every integer is a row ref."""
    expr = excel.lstrip("=")
    expr = re.sub(
        r"[A-Z]{1,3}(\d+):[A-Z]{1,3}(\d+)",
        lambda m: "(" + "+".join(str(r) for r in range(int(m.group(1)), int(m.group(2)) + 1)) + ")",
        expr,
    )
    expr = expr.replace("SUM", "")
    return re.sub(r"[A-Z]{1,3}(\d+)", r"\1", expr)


def _row_values(sheet: Sheet, sres: SheetResult) -> dict[int, dict[str, float]]:
    """Resolve every value line to its per-bucket amount: inputs from ``line_values``,
    aggregates by evaluating their formula (recursively, memoized)."""
    by_row = {ln.row: ln for ln in sheet.lines}
    formulas = _formula_for_row(sheet)
    vbuckets = list(sheet.value_buckets)
    memo: dict[tuple[int, str], float] = {}

    def value_of(row: int, bucket: str) -> float:
        key = (row, bucket)
        if key in memo:
            return memo[key]
        memo[key] = 0.0  # cycle guard
        ln = by_row.get(row)
        if ln is None:
            return 0.0
        if ln.kind == "input":
            v = _num(sres.line_values.get(ln.id, {}).get(bucket, 0.0))
        elif row in formulas:
            v = _eval(_to_arith(formulas[row]), bucket, value_of)
        elif ln.kind == "opening":
            v = _num(sres.opening.get(bucket, 0.0))
        else:
            v = 0.0
        memo[key] = v
        return v

    return {ln.row: {b: value_of(ln.row, b) for b in vbuckets}
            for ln in sheet.lines if ln.kind != "section"}


def _eval(arith: str, bucket: str, value_of) -> float:
    """Safely evaluate the row-arithmetic (only +, -, *, parens; integers are row refs)."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.BinOp):
            lhs, rhs = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return lhs + rhs
            if isinstance(node.op, ast.Sub):
                return lhs - rhs
            if isinstance(node.op, ast.Mult):
                return lhs * rhs
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return value_of(int(node.value), bucket)
        raise ValueError(f"unsupported subtotal expression node: {ast.dump(node)}")

    return ev(ast.parse(arith, mode="eval"))


# ── workbook rendering ───────────────────────────────────────────────────────

def _safe_sheet_name(name: str, used: set[str]) -> str:
    clean = "".join(c for c in name if c not in r"[]:*?/\\")[:31].strip() or "Sheet"
    base, candidate, i = clean[:28], clean, 2
    while candidate in used:
        candidate = f"{base}~{i}"
        i += 1
    used.add(candidate)
    return candidate


def _closing_label(label: str, reporting_date: str | None) -> str:
    if reporting_date and "as at" in label.lower():
        return label.rsplit("as at", 1)[0] + f"as at {reporting_date}"
    return label


def render_sama_workbook(
    result: MovementResult, *, reporting_date: str | None = None,
    levels: tuple[str, ...] = ("entity", "class", "cohort"),
) -> bytes:
    """Return .xlsx bytes: an Entity Total sheet, then one sheet per class (class total
    followed by its cohort detail)."""
    wb = Workbook()
    wb.remove(wb.active)
    views = aggregated_views(result, levels=levels)
    used: set[str] = set()

    for v in [x for x in views if x["level"] == "entity"]:
        _render_view(wb.create_sheet(_safe_sheet_name("Entity Total", used)), v, reporting_date)

    cohorts_by_class: dict[str, list] = {}
    for v in [x for x in views if x["level"] == "cohort"]:
        cohorts_by_class.setdefault(v["reserving_class"], []).append(v)
    class_views = [x for x in views if x["level"] == "class"]

    if class_views:
        for cv in class_views:
            ws = wb.create_sheet(_safe_sheet_name(cv["reserving_class"], used))
            r = _render_view(ws, cv, reporting_date)
            for coh in sorted(cohorts_by_class.get(cv["reserving_class"], []), key=lambda x: x["uwy"]):
                r = _render_view(ws, coh, reporting_date, start=r + 1)
    else:  # cohort-only: group cohorts onto one sheet per class
        for rc in sorted(cohorts_by_class):
            ws = wb.create_sheet(_safe_sheet_name(rc, used))
            r = 1
            for coh in sorted(cohorts_by_class[rc], key=lambda x: x["uwy"]):
                r = _render_view(ws, coh, reporting_date, start=r)

    if not wb.worksheets:  # nothing produced (empty result)
        wb.create_sheet("Movement Analysis")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _render_view(ws, view: dict, reporting_date: str | None, start: int = 1) -> int:
    ws.column_dimensions["A"].width = 54
    ws.column_dimensions["B"].width = 6
    for col in ("C", "D", "E", "F", "G"):
        ws.column_dimensions[col].width = 18
    r = start
    c = ws.cell(r, 1, f"IFRS 17 Movement Analysis — {view['label']}")
    c.font = _F["title"]
    r += 1
    c = ws.cell(r, 1, f"schema v{SCHEMA_VERSION} · authoritative mapping (client-signed)")
    c.font = _F["meta"]
    r += 2
    for sname in ("Gross", "RI"):
        sres = view["sheets"].get(sname)
        if sres is None:
            continue
        c = ws.cell(r, 1, sname)
        c.font = _F["sheet_tag"]
        r += 1
        r = _render_table(ws, r, SCHEMA.sheets[sname], sres, reporting_date) + 2
    return r


def _render_table(ws, r0: int, sheet: Sheet, sres: SheetResult, reporting_date) -> int:
    vbuckets = list(sheet.value_buckets)
    row_values = _row_values(sheet, sres)
    r = r0

    # bucket header row
    for j, label in enumerate(["", ""] + [b.replace("_", " ") for b in vbuckets] + ["Total"]):
        cell = ws.cell(r, 1 + j, label)
        cell.fill = _F["bucket_fill"]
        cell.border = _BORDER
        if j >= 2:
            cell.font = _F["bucket_font"]
            cell.alignment = _CENTER
    r += 1

    for ln in sheet.lines:
        raw = _closing_label(ln.label, reporting_date) if ln.kind == "closing" else ln.label
        label = "    " * ln.level + raw
        if ln.kind == "section":
            cell = ws.cell(r, 1, label)
            cell.font = _F["bold"]
            cell.fill = _F["sub_fill"]
            cell.border = _BORDER
            r += 1
            continue
        vals = row_values.get(ln.row, {})
        sign = next((s for b, s in ln.signs.items() if b != "Total"), "") if ln.kind == "input" else ""
        fill = {"opening": _F["open_fill"], "closing": _F["close_fill"]}.get(ln.kind)
        if ln.kind in ("opening", "subtotal", "closing"):
            fill = fill or _F["sub_fill"]
        bold = ln.kind in ("opening", "subtotal", "closing")
        _value_row(ws, r, label, vals, vbuckets, sign=sign, fill=fill, bold=bold)
        r += 1
    return r


def _value_row(ws, r, label, vals, vbuckets, *, sign="", fill=None, bold=False):
    lc = ws.cell(r, 1, label)
    lc.border = _BORDER
    if bold:
        lc.font = _F["bold"]
    if fill:
        lc.fill = fill
    sc = ws.cell(r, 2, sign or "")
    sc.border = _BORDER
    sc.alignment = _CENTER
    sc.font = _F["sign"]
    if fill:
        sc.fill = fill
    total = 0.0
    for j, b in enumerate(vbuckets):
        v = _num(vals.get(b, 0.0))
        total += v
        cell = ws.cell(r, 3 + j, v)
        cell.number_format = _NUMFMT
        cell.border = _BORDER
        if bold:
            cell.font = _F["bold"]
        if fill:
            cell.fill = fill
    tcell = ws.cell(r, 3 + len(vbuckets), total)
    tcell.number_format = _NUMFMT
    tcell.border = _BORDER
    if bold:
        tcell.font = _F["bold"]
    if fill:
        tcell.fill = fill


# ── machine-readable JSON companion (plan Q4: structured feed for preview/API) ──

def build_json_companion(
    result: MovementResult, *, reporting_date: str | None = None,
    levels: tuple[str, ...] = ("entity", "class", "cohort"),
) -> dict:
    """A structured mirror of the workbook: per grain-view, per sheet, the ordered lines
    with label/level/kind/sign and per-bucket values (subtotals resolved). Downstream/API
    consumers use this instead of cracking the xlsx."""
    out_views = []
    for v in aggregated_views(result, levels=levels):
        sheets = {}
        for sname, sres in v["sheets"].items():
            sh = SCHEMA.sheets[sname]
            row_values = _row_values(sh, sres)
            lines = []
            for ln in sh.lines:
                if ln.kind == "section":
                    lines.append({"id": ln.id, "label": ln.label, "level": ln.level, "kind": "section"})
                    continue
                vals = {b: round(row_values.get(ln.row, {}).get(b, 0.0), 2) for b in sh.value_buckets}
                lines.append({
                    "id": ln.id, "label": ln.label, "level": ln.level, "kind": ln.kind,
                    "sign": next((s for b, s in ln.signs.items() if b != "Total"), None) if ln.kind == "input" else None,
                    "buckets": vals, "total": round(sum(vals.values()), 2),
                })
            sheets[sname] = {"buckets": list(sh.value_buckets), "lines": lines}
        out_views.append({
            "level": v["level"], "label": v["label"],
            "reserving_class": v["reserving_class"], "uwy": v["uwy"], "sheets": sheets,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "reporting_date": reporting_date,
        "views": out_views,
    }
