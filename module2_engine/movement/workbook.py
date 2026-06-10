"""Render the IFRS 17 movement disclosure to an .xlsx workbook (openpyxl).

One worksheet per reserving class; within each, the UWY sections are stacked, each
showing the Gross then RI roll-forward table (5 measurement buckets + Total). v1 renders
the full line detail plus the reliably-computed aggregates (opening, closing, and the
reconciliation residual). The fine nested SAMA subtotals (revenue / service-result / net
lines) are intentionally left for a follow-up that pairs formula-operator extraction with
actuarial sign-off — v1 never prints a fabricated subtotal.

The closing-date label is dynamic (period-agnostic, plan §3b): pass ``reporting_date``.

Uses openpyxl (a guaranteed dependency) directly — NOT xlsxwriter (optional, absent in
prod) and NOT the pandas WRITE_ENGINE (whose engine choice must stay free for the
bit-identical process pipeline).
"""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .compute import MovementResult, SheetResult
from .schema import SCHEMA, SCHEMA_VERSION, Sheet

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
    "resid": Font(color="C00000"),
    "open_fill": PatternFill("solid", fgColor="FCE4D6"),
    "close_fill": PatternFill("solid", fgColor="E2EFDA"),
    "agg_fill": PatternFill("solid", fgColor="F2F2F2"),
}


def _num(v) -> float:
    """Coerce to a finite float (openpyxl cannot write nan/inf)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f and f not in (float("inf"), float("-inf")) else 0.0


def _safe_sheet_name(name: str, used: set[str]) -> str:
    # Excel sheet names: <=31 chars, no []:*?/\, unique.
    clean = "".join(c for c in name if c not in r"[]:*?/\\")[:31].strip() or "Sheet"
    base, candidate, i = clean[:28], clean, 2
    while candidate in used:
        candidate = f"{base}~{i}"
        i += 1
    used.add(candidate)
    return candidate


def _closing_label(line_label: str, reporting_date: str | None) -> str:
    if reporting_date and "as at" in line_label.lower():
        return line_label.rsplit("as at", 1)[0] + f"as at {reporting_date}"
    return line_label


def render_sama_workbook(result: MovementResult, *, reporting_date: str | None = None) -> bytes:
    """Return .xlsx bytes for the whole movement result (all class×UWY pairs)."""
    wb = Workbook()
    wb.remove(wb.active)  # drop the default empty sheet

    by_class: dict[str, list] = {}
    for pr in result.pairs:
        by_class.setdefault(pr.reserving_class, []).append(pr)

    used: set[str] = set()
    for rc in sorted(by_class):
        ws = wb.create_sheet(_safe_sheet_name(rc, used))
        ws.column_dimensions["A"].width = 52
        ws.column_dimensions["B"].width = 6
        for col in ("C", "D", "E", "F", "G"):
            ws.column_dimensions[col].width = 18
        ws.freeze_panes = "B1"

        r = 1
        c = ws.cell(r, 1, f"IFRS 17 Movement Analysis — {rc}")
        c.font = _F["title"]
        r += 1
        c = ws.cell(r, 1, f"schema v{SCHEMA_VERSION} · PROPOSED mapping (pending actuarial sign-off)")
        c.font = _F["meta"]
        r += 2

        for pr in sorted(by_class[rc], key=lambda p: p.uwy):
            for sheet_name in ("Gross", "RI"):
                sres = pr.sheets.get(sheet_name)
                if sres is None:
                    continue
                c = ws.cell(r, 1, f"UWY {pr.uwy} — {sheet_name}")
                c.font = _F["sheet_tag"]
                r += 1
                r = _render_table(ws, r, SCHEMA.sheets[sheet_name], sres, reporting_date)
                r += 2

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _render_table(ws, r0: int, sheet: Sheet, sres: SheetResult, reporting_date) -> int:
    vbuckets = list(sheet.value_buckets)
    cols = vbuckets + ["Total"]
    r = r0

    # bucket header row
    for j in range(2 + len(cols)):
        cell = ws.cell(r, 1 + j)
        cell.fill = _F["bucket_fill"]
        cell.border = _BORDER
    for j, b in enumerate(cols):
        cell = ws.cell(r, 3 + j, b.replace("_", " "))
        cell.font = _F["bucket_font"]
        cell.fill = _F["bucket_fill"]
        cell.alignment = _CENTER
        cell.border = _BORDER
    r += 1

    def value_row(label, values, *, fill=None, label_font=None, num_font=None, sign=""):
        nonlocal r
        lc = ws.cell(r, 1, label)
        lc.border = _BORDER
        if label_font:
            lc.font = label_font
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
            v = _num(values.get(b, 0.0))
            total += v
            cell = ws.cell(r, 3 + j, v)
            cell.number_format = _NUMFMT
            cell.border = _BORDER
            if num_font:
                cell.font = num_font
            if fill:
                cell.fill = fill
        tcell = ws.cell(r, 3 + len(vbuckets), total)
        tcell.number_format = _NUMFMT
        tcell.border = _BORDER
        if num_font:
            tcell.font = num_font
        if fill:
            tcell.fill = fill
        r += 1

    for ln in sheet.lines:
        # Only the closing line gets the dynamic reporting date; opening stays 01/01.
        raw = _closing_label(ln.label, reporting_date) if ln.kind == "closing" else ln.label
        label = "    " * ln.level + raw
        sign = next((s for b, s in ln.signs.items() if b != "Total"), "")
        if ln.kind == "opening":
            value_row(label, sres.opening, fill=_F["open_fill"], label_font=_F["bold"], num_font=_F["bold"])
        elif ln.kind == "closing":
            value_row(label, sres.closing_independent, fill=_F["close_fill"], label_font=_F["bold"], num_font=_F["bold"])
        elif ln.kind == "section":
            cell = ws.cell(r, 1, label)
            cell.font = _F["bold"]
            cell.fill = _F["agg_fill"]
            cell.border = _BORDER
            r += 1
        elif ln.kind == "input":
            value_row(label, sres.line_values.get(ln.id, {}), sign=sign)
        else:  # subtotal — v1: label only (no fabricated nested total)
            cell = ws.cell(r, 1, label)
            cell.font = _F["bold"]
            cell.fill = _F["agg_fill"]
            cell.border = _BORDER
            r += 1

    # reliably-computed aggregates + reconciliation
    r += 1
    value_row("Closing (roll-forward = opening + ΔPnL − cash flows)", sres.closing_rollforward,
              fill=_F["agg_fill"], label_font=_F["bold"], num_font=_F["bold"])
    value_row("Closing (independent EOP balances)", sres.closing_independent,
              fill=_F["agg_fill"], label_font=_F["bold"], num_font=_F["bold"])
    value_row("Reconciliation residual (→ methodology diff)", sres.residual, num_font=_F["resid"])
    return r
