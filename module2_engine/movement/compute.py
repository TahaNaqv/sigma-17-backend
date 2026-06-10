"""Project the Module-2 process intermediates into the IFRS 17 movement disclosure.

``build_sama_movement`` consumes the in-memory ``ProcessFrames`` (IFRS Summary +
LC frames, both at (class, UWY) grain) and, per present (class, UWY) pair, fills the
Gross and RI roll-forward tables defined by the schema, driven by the proposed
mapping. It is *mapping-driven*: the engine machinery is fixed; the actuarial
source expressions live in mapping_source.json (sign-off, plan gate #1).

Roll-forward structure (faithful to the template):
    opening[b]               = Σ build-up lines @ _prev
    closing_independent[b]   = Σ build-up lines @ _curr        (EOP balances)
    closing_rollforward[b]   = opening[b] + Σ P&L[b] − Σ cashflow[b]
    residual[b]              = closing_independent[b] − closing_rollforward[b]
The residual is routed into the "Other methodology diff" line (plan § recon): it is
a reported quality signal, not a hard gate — manual lines (~⅓) default to 0 until the
override phase, so a residual is expected.

Pure pandas/stdlib; no Django.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from .mapping import _load as _load_mapping
from .schema import SCHEMA, Sheet

# ── safe expression resolver ────────────────────────────────────────────────
# Mapping sources reference columns whose names contain spaces, dashes and
# parentheses (e.g. "GROSS - Outstanding_prev"). We resolve by longest-match
# against the known column set, then evaluate a tiny arithmetic grammar
# (+ - * unary-minus parens, max(), min(), numeric literals). Unknown columns
# resolve to 0 and are recorded as warnings — consistent with computed-first.

_TOKEN_OPS = {"+", "-", "*", "(", ")", ","}


def _substitute(expr: str, *, p: str, big_p: str) -> str:
    return expr.replace("{p}", p).replace("{P}", big_p)


def _tokenize(expr: str, columns: set[str]) -> list[tuple[str, object]]:
    """Longest-match tokenizer: emit ('col', name) | ('num', float) |
    ('fn', 'max'|'min') | ('op', char). Unknown identifiers -> ('col', name)
    (resolved to 0 + warning at eval time)."""
    cols_by_len = sorted(columns, key=len, reverse=True)
    tokens: list[tuple[str, object]] = []
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch == " ":
            i += 1
            continue
        if ch in _TOKEN_OPS:
            tokens.append(("op", ch))
            i += 1
            continue
        # function names
        for fn in ("max", "min"):
            if expr.startswith(fn, i) and (i + len(fn) == n or not expr[i + len(fn)].isalnum()):
                tokens.append(("fn", fn))
                i += len(fn)
                break
        else:
            # numeric literal
            m = re.match(r"\d+(\.\d+)?", expr[i:])
            if m:
                tokens.append(("num", float(m.group())))
                i += m.end()
                continue
            # longest known column starting here
            matched = next((c for c in cols_by_len if expr.startswith(c, i)), None)
            if matched is None:
                # consume a bare identifier-ish run as an (unknown) column
                m2 = re.match(r"[^+\-*(),]+", expr[i:])
                matched = (m2.group() if m2 else expr[i]).strip()
                i += len(m2.group()) if m2 else 1
            else:
                i += len(matched)
            tokens.append(("col", matched))
    return tokens


def _to_rpn(tokens: list[tuple[str, object]]):
    """Shunting-yard → RPN, supporting unary minus and max/min(a, b)."""
    out, stack = [], []
    prec = {"u-": 4, "*": 3, "+": 2, "-": 2}
    prev = None
    for kind, val in tokens:
        if kind in ("col", "num"):
            out.append((kind, val))
        elif kind == "fn":
            stack.append(("fn", val))
        elif kind == "op" and val == ",":
            while stack and stack[-1] != ("op", "("):
                out.append(stack.pop())
        elif kind == "op" and val == "(":
            stack.append((kind, val))
        elif kind == "op" and val == ")":
            while stack and stack[-1] != ("op", "("):
                out.append(stack.pop())
            if stack:
                stack.pop()  # discard "("
            if stack and stack[-1][0] == "fn":
                out.append(stack.pop())
        else:  # arithmetic operator
            op = val
            if op == "-" and (prev is None or prev in (("op", "("), ("op", ","))
                              or (isinstance(prev, tuple) and prev[0] == "op" and prev[1] in "+-*")):
                op = "u-"  # unary
            while stack and stack[-1][0] == "op" and stack[-1][1] in prec and prec[stack[-1][1]] >= prec[op]:
                out.append(stack.pop())
            stack.append(("op", op))
        prev = (kind, val)
    while stack:
        out.append(stack.pop())
    return out


def _eval_rpn(rpn, row: dict, missing: set[str]) -> float:
    st: list[float] = []
    for kind, val in rpn:
        if kind == "num":
            st.append(float(val))
        elif kind == "col":
            v = row.get(val)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                if val not in row:
                    missing.add(val)
                v = 0.0
            st.append(float(v))
        elif kind == "fn":
            b, a = st.pop(), st.pop()
            st.append(max(a, b) if val == "max" else min(a, b))
        elif kind == "op":
            if val == "u-":
                st.append(-st.pop())
            else:
                b = st.pop()
                a = st.pop()
                st.append(a + b if val == "+" else a - b if val == "-" else a * b)
    return st[-1] if st else 0.0


def _resolve(expr: str, row: dict, columns: set[str], *, p: str, big_p: str, missing: set[str]) -> float:
    rpn = _to_rpn(_tokenize(_substitute(expr, p=p, big_p=big_p), columns))
    return _eval_rpn(rpn, row, missing)


# ── movement computation ────────────────────────────────────────────────────

_METHODOLOGY_DIFF_IDS = {"other_methodology_diff"}  # residual sink (per sheet)
_OPENING_SUFFIX_RE = re.compile(r"\{p\}|\{P\}")


@dataclass
class SheetResult:
    sheet: str
    opening: dict[str, float]
    closing_rollforward: dict[str, float]
    closing_independent: dict[str, float]
    residual: dict[str, float]
    line_values: dict[str, dict[str, float]]  # line_id -> {bucket: value}


@dataclass
class PairResult:
    reserving_class: str
    uwy: int
    sheets: dict[str, SheetResult] = field(default_factory=dict)


@dataclass
class MovementResult:
    pairs: list[PairResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_columns: set[str] = field(default_factory=set)


def _line_bucket(line) -> str | None:
    """The single measurement bucket an input line contributes to (its sign key,
    excluding Total). Multi-bucket lines (methodology diff) return None."""
    keys = [b for b in line.signs if b != "Total"]
    return keys[0] if len(keys) == 1 else None


def _classify(sheet: Sheet):
    """Partition input lines into opening build-up / P&L / cash-flow, by the
    'Cash flows' section header row and the presence of a {p}/{P} source."""
    cash_row = next((ln.row for ln in sheet.lines if ln.kind == "section"
                     and "cash flow" in ln.label.lower()), 10**9)
    mapping = _load_mapping()
    buildup, pnl, cashflow = [], [], []
    for ln in sheet.lines:
        if ln.kind != "input":
            continue
        m = mapping.get((sheet.name, ln.id))
        src = m.source if m else None
        if src and _OPENING_SUFFIX_RE.search(src):
            buildup.append(ln)
        elif ln.row > cash_row:
            cashflow.append(ln)
        else:
            pnl.append(ln)
    return buildup, pnl, cashflow


def build_sama_movement(frames, *, classes=None, uwys=None) -> MovementResult:
    """Project ProcessFrames → per-(class, UWY) Gross + RI movement tables."""
    ifrs = frames.ifrs_summary_df.copy()
    lc = frames.allocate_sheets.get("LC")
    if lc is not None:
        ifrs = ifrs.merge(lc, on=["RESERVINGCLASS", "UWY"], how="left", suffixes=("", "_lc"))
    ifrs["UWY"] = ifrs["UWY"].astype(int)
    # Additive CY/PY Paid split (movement-only; absent on legacy/synthetic frames).
    cy_py = getattr(frames, "cy_py_payment", None)
    if cy_py is not None:
        cy_py = cy_py.copy()
        cy_py["UWY"] = cy_py["UWY"].astype(int)
        ifrs = ifrs.merge(cy_py, on=["RESERVINGCLASS", "UWY"], how="left", suffixes=("", "_cypy"))
    columns = set(map(str, ifrs.columns))
    mapping = _load_mapping()
    result = MovementResult()

    pairs = ifrs[["RESERVINGCLASS", "UWY"]].drop_duplicates()
    if classes is not None:
        pairs = pairs[pairs["RESERVINGCLASS"].isin(classes)]
    if uwys is not None:
        pairs = pairs[pairs["UWY"].isin([int(u) for u in uwys])]

    for _, key in pairs.iterrows():
        rc, uwy = key["RESERVINGCLASS"], int(key["UWY"])
        sub = ifrs[(ifrs["RESERVINGCLASS"] == rc) & (ifrs["UWY"] == uwy)]
        row = {c: sub.iloc[0][c] for c in ifrs.columns} if len(sub) else {}
        pair = PairResult(reserving_class=str(rc), uwy=uwy)
        for sheet in SCHEMA.sheets.values():
            pair.sheets[sheet.name] = _build_sheet(sheet, row, columns, mapping, result)
        result.pairs.append(pair)
    return result


def _build_sheet(sheet: Sheet, row: dict, columns: set[str], mapping, result: MovementResult) -> SheetResult:
    vbuckets = list(sheet.value_buckets)
    line_values: dict[str, dict[str, float]] = {}
    buildup, pnl, cashflow = _classify(sheet)

    def val_at(line, p: str) -> float:
        # p is "prev" | "curr"; sources read "..._{p}" / "..._{P}" so {p}->prev/curr,
        # {P}->PY/CY (the column suffixes carry their own leading underscore).
        m = mapping.get((sheet.name, line.id))
        if not m or not m.source:
            return 0.0
        big_p = "CY" if p == "curr" else "PY"
        return _resolve(m.source, row, columns, p=p, big_p=big_p, missing=result.missing_columns)

    opening = {b: 0.0 for b in vbuckets}
    closing_indep = {b: 0.0 for b in vbuckets}
    for ln in buildup:
        b = _line_bucket(ln)
        if b is None:
            continue
        ov, cv = val_at(ln, "prev"), val_at(ln, "curr")
        line_values.setdefault(ln.id, {})[b] = ov
        opening[b] += ov
        closing_indep[b] += cv

    pnl_total = {b: 0.0 for b in vbuckets}
    for ln in pnl:
        b = _line_bucket(ln)
        v = val_at(ln, "curr")
        if b is not None:
            line_values.setdefault(ln.id, {})[b] = v
            pnl_total[b] += v

    cf_total = {b: 0.0 for b in vbuckets}
    for ln in cashflow:
        b = _line_bucket(ln)
        v = val_at(ln, "curr")
        if b is not None:
            line_values.setdefault(ln.id, {})[b] = v
            cf_total[b] += v

    closing_rf = {b: opening[b] + pnl_total[b] - cf_total[b] for b in vbuckets}
    residual = {b: closing_indep[b] - closing_rf[b] for b in vbuckets}
    return SheetResult(
        sheet=sheet.name,
        opening=opening,
        closing_rollforward=closing_rf,
        closing_independent=closing_indep,
        residual=residual,
        line_values=line_values,
    )
