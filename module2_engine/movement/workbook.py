"""Render the IFRS 17 movement disclosure to an .xlsx workbook (XlsxWriter).

One worksheet per reserving class; within each, the UWY sections are stacked, each
showing the Gross then RI roll-forward table (5 measurement buckets + Total). v1 renders
the full line detail plus the reliably-computed aggregates (opening, closing, P&L-changes
total, cash-flows total, and the reconciliation residual). The fine nested SAMA subtotals
(revenue / service-result / net lines) are intentionally left for a follow-up that pairs
formula-operator extraction with actuarial sign-off — v1 never prints a fabricated subtotal.

The closing-date label is dynamic (period-agnostic, plan §3b): pass ``reporting_date``.
"""

from __future__ import annotations

import io

from .compute import MovementResult, PairResult, SheetResult
from .schema import SCHEMA, SCHEMA_VERSION, Sheet


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
    import xlsxwriter  # local import: optional dep, mirrors core.excel WRITE_ENGINE

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "nan_inf_to_errors": True})

    f = {
        "title": wb.add_format({"bold": True, "font_size": 13}),
        "meta": wb.add_format({"italic": True, "font_color": "#666666"}),
        "bucket": wb.add_format({"bold": True, "bg_color": "#1F4E78", "font_color": "white",
                                 "border": 1, "align": "center", "valign": "vcenter"}),
        "colhdr": wb.add_format({"bold": True, "bg_color": "#D9E1F2", "border": 1, "align": "center"}),
        "label": wb.add_format({"border": 1}),
        "sign": wb.add_format({"border": 1, "align": "center", "font_color": "#888888"}),
        "num": wb.add_format({"border": 1, "num_format": "#,##0;(#,##0)"}),
        "open": wb.add_format({"bold": True, "border": 1, "bg_color": "#FCE4D6"}),
        "open_num": wb.add_format({"bold": True, "border": 1, "bg_color": "#FCE4D6", "num_format": "#,##0;(#,##0)"}),
        "close": wb.add_format({"bold": True, "border": 1, "bg_color": "#E2EFDA"}),
        "close_num": wb.add_format({"bold": True, "border": 1, "bg_color": "#E2EFDA", "num_format": "#,##0;(#,##0)"}),
        "agg": wb.add_format({"bold": True, "border": 1, "bg_color": "#F2F2F2"}),
        "agg_num": wb.add_format({"bold": True, "border": 1, "bg_color": "#F2F2F2", "num_format": "#,##0;(#,##0)"}),
        "resid_num": wb.add_format({"border": 1, "num_format": "#,##0;(#,##0)", "font_color": "#C00000"}),
        "sheet_tag": wb.add_format({"bold": True, "font_size": 11, "font_color": "#1F4E78"}),
    }

    by_class: dict[str, list[PairResult]] = {}
    for pr in result.pairs:
        by_class.setdefault(pr.reserving_class, []).append(pr)

    used: set[str] = set()
    for rc in sorted(by_class):
        ws = wb.add_worksheet(_safe_sheet_name(rc, used))
        ws.set_column(0, 0, 52)  # label
        ws.set_column(1, 1, 6)   # sign
        ws.set_column(2, 6, 18)  # 4 buckets + total
        ws.freeze_panes(0, 1)
        r = 0
        ws.write(r, 0, f"IFRS 17 Movement Analysis — {rc}", f["title"]); r += 1
        ws.write(r, 0, f"schema v{SCHEMA_VERSION} · PROPOSED mapping (pending actuarial sign-off)", f["meta"]); r += 2
        for pr in sorted(by_class[rc], key=lambda p: p.uwy):
            for sheet_name in ("Gross", "RI"):
                sres = pr.sheets.get(sheet_name)
                if sres is None:
                    continue
                ws.write(r, 0, f"UWY {pr.uwy} — {sheet_name}", f["sheet_tag"]); r += 1
                r = _render_table(ws, r, SCHEMA.sheets[sheet_name], sres, f, reporting_date)
                r += 2  # gap between tables
    wb.close()
    return buf.getvalue()


def _render_table(ws, r0: int, sheet: Sheet, sres: SheetResult, f, reporting_date) -> int:
    vbuckets = list(sheet.value_buckets)
    cols = vbuckets + ["Total"]
    r = r0
    # bucket header row
    ws.write(r, 0, "", f["bucket"])
    ws.write(r, 1, "", f["bucket"])
    for j, b in enumerate(cols):
        ws.write(r, 2 + j, b.replace("_", " "), f["bucket"])
    r += 1

    def value_row(label, values: dict, *, lbl_fmt, num_fmt, sign=None):
        nonlocal r
        ws.write(r, 0, label, lbl_fmt)
        ws.write(r, 1, sign or "", f["sign"])
        total = 0.0
        for j, b in enumerate(vbuckets):
            v = float(values.get(b, 0.0))
            total += v
            ws.write_number(r, 2 + j, v, num_fmt)
        ws.write_number(r, 2 + len(vbuckets), total, num_fmt)
        r += 1

    opening_id = next((ln.id for ln in sheet.lines if ln.kind == "opening"), None)
    closing_id = next((ln.id for ln in sheet.lines if ln.kind == "closing"), None)

    for ln in sheet.lines:
        # Only the closing line gets the dynamic reporting date; opening stays 01/01.
        raw = _closing_label(ln.label, reporting_date) if ln.kind == "closing" else ln.label
        label = "    " * ln.level + raw
        sign = next((s for b, s in ln.signs.items() if b != "Total"), "")
        if ln.kind == "opening":
            value_row(label, sres.opening, lbl_fmt=f["open"], num_fmt=f["open_num"])
        elif ln.kind == "closing":
            value_row(label, sres.closing_independent, lbl_fmt=f["close"], num_fmt=f["close_num"])
        elif ln.kind == "section":
            ws.write(r, 0, label, f["agg"]); r += 1
        elif ln.kind == "input":
            value_row(label, sres.line_values.get(ln.id, {}), lbl_fmt=f["label"], num_fmt=f["num"], sign=sign)
        else:  # subtotal — v1: label only (no fabricated nested total); see module docstring
            ws.write(r, 0, label, f["agg"]); r += 1

    # reliably-computed aggregates + reconciliation
    r += 1
    value_row("Closing (roll-forward = opening + ΔPnL − cash flows)", sres.closing_rollforward,
              lbl_fmt=f["agg"], num_fmt=f["agg_num"])
    value_row("Closing (independent EOP balances)", sres.closing_independent,
              lbl_fmt=f["agg"], num_fmt=f["agg_num"])
    value_row("Reconciliation residual (→ methodology diff)", sres.residual,
              lbl_fmt=f["label"], num_fmt=f["resid_num"])
    return r
