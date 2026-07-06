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
