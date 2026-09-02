"""Classify output-workbook columns so the in-app preview can format them.

The actuarial engines store ratio columns as RAW FRACTIONS (e.g. 0.96) and set
no Excel percent number-format, so ratios, money, and development factors are
indistinguishable at the cell level. Detection therefore has to be by column
identity, which is a minefield: "LRC" contains "LR" but is money; "Paid CDF" is
a development factor (~1-2x), not a percentage; the same header can mean
different things in different sheets.

So this classifier is intentionally CONSERVATIVE and EXACT-MATCH:
  - a curated allowlist of headers that are genuinely 0..1 ratios  -> "ratio"
  - a curated allowlist of development/discount factors            -> "factor"
  - a couple of sheet-scoped rules for numeric-named ratio columns
  - everything else                                                -> "number"

Anything not explicitly recognised falls back to "number" (plain money-style
formatting), so we never fabricate a misleading percentage on an unknown column.
The frontend renders: ratio -> percent, factor -> 3 decimals, number -> default.
"""

from __future__ import annotations

RATIO = "ratio"
FACTOR = "factor"
NUMBER = "number"
#: Integer tallies (`Factor Count`). Distinct from NUMBER so "3 factors averaged" renders as
#: `3` rather than money-style `3.00`.
COUNT = "count"


def _norm(header) -> str:
    """Lower-cased, whitespace-collapsed header for exact matching."""
    return " ".join(str(header).split()).strip().lower()


# Headers that are simple fractions meant to be shown as a percentage.
# (Loss ratios, ULR, expense/commission/combined ratios, RI/RA/ULAE %, EP %.)
_RATIO_HEADERS = {
    _norm(h)
    for h in (
        "Paid LR",
        "Inc LR",
        "Ult LR",
        "Reported LR",
        "Implied LR",
        "ULR",
        "Selected ULR",
        "Comm Ratio",
        "Combined Ratio",
        "Exp Ratio",
        "RI %",
        "RA %",
        "ULAE %",
        "EP_Percent",
        "Cumulative %",
        "Expected Unpaid %",
    )
}

# Development / discount FACTORS: ~1-2x age-to-age or cumulative factors, and
# discount factors (<=1). These are NOT percentages — showing a 1.85 CDF as
# "185%" would be wrong. Rendered as plain numbers but at higher precision.
_FACTOR_HEADERS = {
    _norm(h)
    for h in (
        "CDF",
        "Paid CDF",
        "Reported CDF",
        "Selected CDF",
        "Selected LDF",
        "Simple Avg CDF",
        "Simple Avg LDF",
        "Weighted Avg CDF",
        "Weighted Avg LDF",
        "CY Discount Factor",
        "PY Discount Factor",
    )
}

# Sheets whose numeric-named columns (e.g. "0", "1", "2") are ratio weights
# rather than money. Keyed by normalized sheet name.
_RATIO_NUMERIC_SHEETS = {_norm("Payment Pattern")}


def _is_integer_named(header) -> bool:
    """True for development-period style headers like 0, 1, 2 (or "0")."""
    s = str(header).strip()
    if not s:
        return False
    try:
        int(s)
        return True
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Row-kind classification (triangle sheets)
# ---------------------------------------------------------------------------
#
# Column classification cannot describe a triangle sheet. Its columns are development
# periods, and its KIND VARIES BY ROW — the same column holds a cumulative amount
# (3,463,357), an age-to-age factor (1.015748) and a factor count (3). Classified by column,
# every triangle column is `number`, and the preview's per-column decimal heuristic then
# renders the factor as `1.01`: an actuary cannot read their own development factors.
#
# So triangle sheets get a second, row-wise pass keyed on the column-1 label. Every other
# sheet returns None and is unaffected.

#: Sheets whose kind varies by row rather than by column.
_ROW_KIND_SHEETS = {_norm("Paid Claims Triangle"), _norm("Reported Triangle")}

#: Labels naming a block; rows below one inherit its kind until the next label.
_BLOCK_ROW_KINDS = {
    _norm("Incremental Triangle"): NUMBER,
    _norm("Cumulative Triangle"): NUMBER,
    _norm("Age-to-Age Factors"): FACTOR,
}

#: The leading block of each triangle sheet, which has no label row above it (a label there
#: would become the pandas header). Mirrors `module1_engine.engine.LEADING_BLOCK`.
_LEADING_BLOCK_KIND = {
    _norm("Paid Claims Triangle"): NUMBER,   # incremental
    _norm("Reported Triangle"): NUMBER,      # cumulative
}

#: `Factor Count` is a tally. `Accident Period` is a repeated block HEADER whose cells hold the
#: development-period numbers 0, 1, 2 ... — integers, and emphatically not factors, which is
#: what they would become by inheriting the age-to-age block's kind.
_COUNT_ROW_LABELS = {_norm("Factor Count"), _norm("Accident Period")}


def _benchmark_row_kind(label: str) -> str | None:
    """`Simple Avg LDF`, `Ex-Hi-Lo Avg CDF`, `Selected LDF`, `Median CDF`, ... are all factors.

    Matched by suffix rather than by an allowlist of every basis name: WP1 grew this block
    from four rows to thirteen, and a new average basis must not silently render as money.
    """
    if label in _COUNT_ROW_LABELS:
        return COUNT
    if label.endswith(" ldf") or label.endswith(" cdf") or label in {"median ldf", "median cdf"}:
        return FACTOR
    return None


def classify_rows(sheet_name: str, row_labels: list) -> list[str] | None:
    """Per-row kinds for a sheet whose kind varies by row, else ``None``.

    ``None`` — not an empty list — so a caller can tell "this sheet is column-classified"
    from "this sheet has no rows".
    """
    sheet = _norm(sheet_name)
    if sheet not in _ROW_KIND_SHEETS:
        return None

    current = _LEADING_BLOCK_KIND.get(sheet, NUMBER)
    kinds: list[str] = []
    for label in row_labels:
        key = _norm(label)
        block = _BLOCK_ROW_KINDS.get(key)
        if block is not None:
            # The label row itself carries no data.
            current = block
            kinds.append(NUMBER)
            continue
        benchmark = _benchmark_row_kind(key)
        kinds.append(benchmark if benchmark is not None else current)
    return kinds


def classify_columns(sheet_name: str, headers: list) -> list[str]:
    """Return a kind ("ratio" | "factor" | "number") for each header.

    Exact, case-insensitive header matching against curated allowlists, plus a
    sheet-scoped rule for numeric-named ratio columns. Order matches `headers`.
    """
    sheet = _norm(sheet_name)
    numeric_ratio_sheet = sheet in _RATIO_NUMERIC_SHEETS
    kinds: list[str] = []
    for header in headers:
        key = _norm(header)
        if key in _FACTOR_HEADERS:
            kinds.append(FACTOR)
        elif key in _RATIO_HEADERS:
            kinds.append(RATIO)
        elif numeric_ratio_sheet and _is_integer_named(header):
            kinds.append(RATIO)
        else:
            kinds.append(NUMBER)
    return kinds
