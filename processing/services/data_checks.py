"""Actuarial data checks for Module 1 reserving inputs (client checklist, Table A.6).

The client's valuation report carries a fixed checklist: general checks (1.x), premium checks
(2.x) and claims checks (3.x). Each has a pass/fail result, and every failing premium or claims
check is summarised as *number of records* and *total NWP / net paid claims* of those records
(Tables A.7 / A.8), followed by the company's explanation. This module produces exactly that,
from the same staged frames the engine reads.

Informational, never a gate: a discrepancy here is something the company explains ("endorsements
issued after expiry are valid"), not something that stops a run. Class reconciliation, which
does stop a run, stays in ``preflight``.

Definitions (also written into the workbook's Basis sheet, so the numbers are auditable):

* **Premium record** — one policy transaction, keyed ``POLICYNUMBER`` + ``ENDORSEMENTNUMBER``
  (when present). **NWP** of a record = gross premium − ceded premium, from the ``GROSS`` and
  ``RI`` rows of ``RI_TREATY_TYPE``. Date checks read the record's **gross** rows: the ``RI``
  rows carry the cession's own dates (on the reference book 73% of RI issue dates differ from
  the policy's), which would otherwise report every cession as a policy defect.
* **Claims record** — one claim (``CLAIMNUMBER``), across the paid and outstanding files.
  **Net paid** = gross paid − RI paid, using the amount the engine consumes (recovery heads
  substituted, as in ``preflight._engine_amount``).
* **Valid date** — parses as a date between 1900-01-01 and 2100-12-31.
* **"After"** — for policy periods (2.4, 2.5) strictly after: a zero-length policy period is a
  defect. For claim events (3.4, 3.5) on or after: a same-day report or payment is normal.
  Comparisons skip rows whose dates are missing or invalid; those are counted by 2.1-2.3 /
  3.1-3.3 instead, so one bad date is never reported twice under different headings.
* A check whose input column is absent reports **not run** with the reason — never a pass.

Pure pandas: no Django, no I/O, no engine import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

import numpy as np
import pandas as pd

from core.normalize import canonical_key

from .preflight import CLASS_COLUMN, _engine_amount

# Result statuses.
PASS = "pass"
FAIL = "fail"
NOT_RUN = "not_run"

CATEGORY_GENERAL = "general"
CATEGORY_PREMIUM = "premium"
CATEGORY_CLAIMS = "claims"
CATEGORY_LABELS = {
    CATEGORY_GENERAL: "General",
    CATEGORY_PREMIUM: "Premiums data",
    CATEGORY_CLAIMS: "Claims data",
}

#: Amount basis per category (Table A.7 / A.8 column).
AMOUNT_BASIS = {CATEGORY_PREMIUM: "nwp", CATEGORY_CLAIMS: "net_paid"}

#: Provenance columns the loaders add, so every discrepant row can be traced to its file/row.
SOURCE_FILE = "__source_file"
SOURCE_ROW = "__source_row"
_INTERNAL = (SOURCE_FILE, SOURCE_ROW)

#: Rows per check written to the detail workbook; the summary counts are never capped.
DETAIL_ROW_CAP = 100_000
#: Records per check kept in the job record for the in-app drill-down.
SAMPLE_SIZE = 20

_MIN_DATE = pd.Timestamp("1900-01-01")
_MAX_DATE = pd.Timestamp("2100-12-31")

#: Numeric amount columns per input (1.3). Only the ones present are checked.
NUMERIC_COLUMNS = {
    "premium": ("PREMIUMAMOUNT", "COMMISSIONAMOUNT", "SUMINSURED"),
    "claims_paid": ("AMOUNTPAID", "AMOUNTRECOVERED", "AMOUNTOUTSTANDING"),
    "claims_os": ("AMOUNTOUTSTANDING", "AMOUNTPAID", "AMOUNTRECOVERED"),
}
#: Class columns that must always be populated (2.11): the modelling class and the
#: reconciliation classes. Only the ones present are checked.
PREMIUM_CLASS_COLUMNS = ("RESERVINGCLASS", "POLICYCLASS", "IFRSCLASS")
INPUT_LABELS = {"premium": "Premium", "claims_paid": "Claims paid", "claims_os": "Claims outstanding"}


# ── result types ─────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    id: str
    category: str
    description: str
    status: str
    records: int = 0
    rows: int = 0
    amount: float | None = None
    basis: str = ""  # how the check was evaluated / why it did not run
    sample: list[dict[str, Any]] = field(default_factory=list)
    # Failing rows for the detail workbook. Not serialised.
    detail: pd.DataFrame | None = field(default=None, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "description": self.description,
            "status": self.status,
            "records": self.records,
            "rows": self.rows,
            "amount": self.amount,
            "amount_basis": AMOUNT_BASIS.get(self.category),
            "basis": self.basis,
            "sample": self.sample,
        }


@dataclass
class DataChecksReport:
    valuation_date: str | None
    results: list[CheckResult]
    input_schema: dict[str, dict[str, str]]
    #: The earlier run check 1.2 compared against: {"job_id", "eop"}, or None.
    compared_with: dict[str, Any] | None = None

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == FAIL]

    def as_dict(self) -> dict[str, Any]:
        counts = {s: sum(1 for r in self.results if r.status == s) for s in (PASS, FAIL, NOT_RUN)}
        return {
            "version": 1,
            "valuation_date": self.valuation_date,
            "checks": [r.as_dict() for r in self.results],
            "counts": counts,
            "input_schema": self.input_schema,
            "compared_with": self.compared_with,
        }


# ── helpers ──────────────────────────────────────────────────────────────────


def _blank(series: pd.Series) -> pd.Series:
    """True where a cell is empty: NaN/None or whitespace-only text."""
    return series.isna() | series.astype(str).str.strip().isin(("", "nan", "NaT", "None"))


def _dates(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    # "mixed": text dates in one column need not share a format (and day-first, as in KSA).
    return pd.to_datetime(series, errors="coerce", dayfirst=True, format="mixed")


def _valid_date_mask(series: pd.Series) -> pd.Series:
    d = _dates(series)
    return d.notna() & (d >= _MIN_DATE) & (d <= _MAX_DATE)


def _type_family(series: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    if pd.api.types.is_numeric_dtype(series):
        return "number"
    return "text"


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return None if pd.isna(value) else value.isoformat()[:10]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value if isinstance(value, (int, str, bool)) else str(value)


def _user_columns(frame: pd.DataFrame) -> list[str]:
    return [c for c in frame.columns if c not in _INTERNAL]


# ── record models ────────────────────────────────────────────────────────────


@dataclass
class _Premium:
    frame: pd.DataFrame  # every premium row, with "_key"
    basis_rows: pd.DataFrame  # gross rows (plus rows of records that have no gross row)
    nwp: pd.Series  # key -> NWP
    gross: pd.Series  # key -> gross premium

    @classmethod
    def build(cls, premium: pd.DataFrame) -> "_Premium":
        f = premium.copy()
        if "POLICYNUMBER" in f.columns:
            policy = f["POLICYNUMBER"].astype(str).str.strip()
            policy = policy.where(~_blank(f["POLICYNUMBER"]), "row:" + f.index.astype(str))
        else:
            policy = "row:" + f.index.astype(str)
        if "ENDORSEMENTNUMBER" in f.columns:
            endo = f["ENDORSEMENTNUMBER"].where(~_blank(f["ENDORSEMENTNUMBER"]), "")
            policy = policy + "|" + endo.astype(str)
        f["_key"] = policy
        treaty = (f["RI_TREATY_TYPE"] if "RI_TREATY_TYPE" in f.columns
                  else pd.Series("gross", index=f.index)).map(canonical_key)
        amount = (pd.to_numeric(f["PREMIUMAMOUNT"], errors="coerce").fillna(0.0)
                  if "PREMIUMAMOUNT" in f.columns else pd.Series(0.0, index=f.index))
        is_gross, is_ri = treaty.eq("gross"), treaty.eq("ri")
        gross = amount.where(is_gross, 0.0).groupby(f["_key"]).sum()
        ceded = amount.where(is_ri, 0.0).groupby(f["_key"]).sum()
        has_gross = is_gross.groupby(f["_key"]).transform("any")
        basis = f[is_gross | ~has_gross]
        return cls(frame=f, basis_rows=basis, nwp=gross - ceded, gross=gross)


@dataclass
class _Claims:
    frame: pd.DataFrame  # paid + OS rows, with "_key" and "_file"
    net_paid: pd.Series  # key -> net paid

    @classmethod
    def build(cls, paid: pd.DataFrame | None, os_frame: pd.DataFrame | None) -> "_Claims":
        parts = []
        for name, frame in (("claims_paid", paid), ("claims_os", os_frame)):
            if frame is not None and len(frame):
                part = frame.copy()
                part["_file"] = name
                parts.append(part)
        if not parts:
            return cls(frame=pd.DataFrame(columns=["_key", "_file"]), net_paid=pd.Series(dtype=float))
        f = pd.concat(parts, ignore_index=True)
        if "CLAIMNUMBER" in f.columns:
            key = f["CLAIMNUMBER"].astype(str).str.strip()
            key = key.where(~_blank(f["CLAIMNUMBER"]), "row:" + f.index.astype(str))
        else:
            key = "row:" + f.index.astype(str)
        f["_key"] = key
        paid_rows = f[f["_file"] == "claims_paid"]
        net = pd.Series(0.0, index=pd.Index(f["_key"].unique()))
        if len(paid_rows):
            amount = _engine_amount(paid_rows, is_os=False)
            treaty = (paid_rows["RI_TREATY_TYPE"] if "RI_TREATY_TYPE" in paid_rows.columns
                      else pd.Series("gross", index=paid_rows.index)).map(canonical_key)
            signed = amount.where(treaty.eq("gross"), 0.0) - amount.where(treaty.eq("ri"), 0.0)
            net = net.add(signed.groupby(paid_rows["_key"]).sum(), fill_value=0.0)
        return cls(frame=f, net_paid=net)


# ── check context ────────────────────────────────────────────────────────────


@dataclass
class _Context:
    inputs: dict[str, pd.DataFrame | None]
    valuation: pd.Timestamp | None
    previous_schema: dict[str, dict[str, str]] | None
    premium: _Premium | None
    claims: _Claims


CheckFn = Callable[[_Context], CheckResult]


@dataclass(frozen=True)
class CheckDef:
    id: str
    category: str
    description: str  # the client's wording (Table A.6)
    fn: CheckFn


def _not_run(cd: CheckDef, reason: str) -> CheckResult:
    return CheckResult(cd.id, cd.category, cd.description, NOT_RUN, basis=reason)


def _record_result(cd: CheckDef, ctx: _Context, failing_rows: pd.DataFrame, basis: str,
                   columns: tuple[str, ...]) -> CheckResult:
    """Premium/claims result from the failing rows: records, rows, amount, sample, detail."""
    if cd.category == CATEGORY_PREMIUM:
        amounts = ctx.premium.nwp if ctx.premium else pd.Series(dtype=float)
    else:
        amounts = ctx.claims.net_paid
    keys = pd.Index(failing_rows["_key"].unique()) if len(failing_rows) else pd.Index([])
    amount = float(amounts.reindex(keys).fillna(0.0).sum()) if len(keys) else 0.0
    status = FAIL if len(keys) else PASS
    result = CheckResult(cd.id, cd.category, cd.description, status, records=len(keys),
                         rows=len(failing_rows), amount=amount, basis=basis)
    if len(failing_rows):
        shown = [c for c in ("_key", *columns) if c in failing_rows.columns]
        extra = [c for c in (SOURCE_FILE, SOURCE_ROW, "_file") if c in failing_rows.columns]
        detail = failing_rows[extra + shown].copy()
        detail["record_amount"] = detail["_key"].map(amounts).fillna(0.0)
        detail = detail.rename(columns={"_key": "record", "_file": "input"})
        result.detail = detail
        first = detail.drop_duplicates("record").head(SAMPLE_SIZE)
        result.sample = [{k: _jsonable(v) for k, v in row.items()}
                         for row in first.to_dict("records")]
    return result


# ── general checks ───────────────────────────────────────────────────────────


def _check_lobs_received(cd: CheckDef, ctx: _Context) -> CheckResult:
    premium, paid, os_frame = (ctx.inputs.get(k) for k in ("premium", "claims_paid", "claims_os"))
    if premium is None or CLASS_COLUMN not in premium.columns:
        return _not_run(cd, "No premium data with a RESERVINGCLASS column was supplied.")

    def classes(frame):
        if frame is None or CLASS_COLUMN not in frame.columns:
            return {}
        out: dict[str, str] = {}
        for v in frame[CLASS_COLUMN].dropna().astype(str):
            out.setdefault(canonical_key(v), v)
        out.pop("", None)
        return out

    p, pd_, o = classes(premium), classes(paid), classes(os_frame)
    rows = []
    for key in sorted(set(p) | set(pd_) | set(o)):
        missing = [label for label, s in (("premium", p), ("claims paid", pd_),
                                          ("claims outstanding", o)) if key not in s]
        if missing:
            rows.append({"class": p.get(key) or pd_.get(key) or o.get(key),
                         "in_premium": key in p, "in_claims_paid": key in pd_,
                         "in_claims_os": key in o, "missing_from": ", ".join(missing)})
    result = CheckResult(cd.id, cd.category, cd.description, FAIL if rows else PASS,
                         records=len(rows), rows=len(rows),
                         basis="Every reserving class appears in premium, claims paid and "
                               "claims outstanding (after class aliases).")
    if rows:
        result.detail = pd.DataFrame(rows)
        result.sample = rows[:SAMPLE_SIZE]
    return result


def _schema_of(inputs: dict[str, pd.DataFrame | None]) -> dict[str, dict[str, str]]:
    return {
        kind: {c: _type_family(frame[c]) for c in _user_columns(frame)}
        for kind, frame in inputs.items() if frame is not None
    }


def _check_same_format(cd: CheckDef, ctx: _Context) -> CheckResult:
    if not ctx.previous_schema:
        return _not_run(cd, "No previous valuation on record to compare against.")
    current = _schema_of(ctx.inputs)
    rows = []
    for kind in sorted(set(current) | set(ctx.previous_schema)):
        now, before = current.get(kind, {}), ctx.previous_schema.get(kind, {})
        if not now or not before:
            continue  # an input absent in one run is a 1.1 matter, not a format change
        for col in sorted(set(before) - set(now)):
            rows.append({"input": INPUT_LABELS.get(kind, kind), "column": col,
                         "change": "missing (was present previously)"})
        for col in sorted(set(now) - set(before)):
            rows.append({"input": INPUT_LABELS.get(kind, kind), "column": col,
                         "change": "new column"})
        for col in sorted(set(now) & set(before)):
            if now[col] != before[col]:
                rows.append({"input": INPUT_LABELS.get(kind, kind), "column": col,
                             "change": f"type changed: {before[col]} -> {now[col]}"})
    result = CheckResult(cd.id, cd.category, cd.description, FAIL if rows else PASS,
                         records=len(rows), rows=len(rows),
                         basis="Column names and types (number / date / text) of each input "
                               "compared with the previous valuation's inputs.")
    if rows:
        result.detail = pd.DataFrame(rows)
        result.sample = rows[:SAMPLE_SIZE]
    return result


def _check_numeric(cd: CheckDef, ctx: _Context) -> CheckResult:
    parts, checked = [], []
    for kind, cols in NUMERIC_COLUMNS.items():
        frame = ctx.inputs.get(kind)
        if frame is None:
            continue
        for col in cols:
            if col not in frame.columns:
                continue
            checked.append(f"{INPUT_LABELS[kind]}.{col}")
            raw = frame[col]
            bad = ~_blank(raw) & pd.to_numeric(raw, errors="coerce").isna()
            if bad.any():
                hit = frame.loc[bad, [c for c in _INTERNAL if c in frame.columns]].copy()
                hit["input"], hit["column"], hit["value"] = INPUT_LABELS[kind], col, raw[bad].astype(str)
                parts.append(hit)
    if not checked:
        return _not_run(cd, "None of the amount columns were supplied.")
    detail = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    result = CheckResult(cd.id, cd.category, cd.description, FAIL if len(detail) else PASS,
                         records=len(detail), rows=len(detail),
                         basis="Non-blank cells that are not numbers, in: " + ", ".join(checked) + ".")
    if len(detail):
        result.detail = detail
        result.sample = [{k: _jsonable(v) for k, v in r.items()}
                         for r in detail.head(SAMPLE_SIZE).to_dict("records")]
    return result


def _check_duplicates(cd: CheckDef, ctx: _Context) -> CheckResult:
    parts, by_input = [], []
    for kind, frame in ctx.inputs.items():
        if frame is None or not len(frame):
            continue
        cols = _user_columns(frame)
        dup = frame.duplicated(subset=cols, keep="first")
        by_input.append(f"{INPUT_LABELS.get(kind, kind)}: {int(dup.sum()):,}")
        if dup.any():
            hit = frame.loc[dup].copy()
            hit.insert(0, "input", INPUT_LABELS.get(kind, kind))
            parts.append(hit)
    if not by_input:
        return _not_run(cd, "No input data was supplied.")
    detail = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    result = CheckResult(cd.id, cd.category, cd.description, FAIL if len(detail) else PASS,
                         records=len(detail), rows=len(detail),
                         basis="Rows identical in every column to an earlier row of the same "
                               "input (extra copies counted). " + "; ".join(by_input) + ".")
    if len(detail):
        result.detail = detail
        result.sample = [{k: _jsonable(v) for k, v in r.items()}
                         for r in detail.head(SAMPLE_SIZE).to_dict("records")]
    return result


# ── premium checks ───────────────────────────────────────────────────────────

_PREMIUM_COLS = ("RESERVINGCLASS", "RI_TREATY_TYPE", "ISSUEDATE", "POLICYSTARTDATE",
                 "POLICYENDDATE", "PREMIUMAMOUNT")


def _premium_date_populated(column: str, label: str):
    def fn(cd: CheckDef, ctx: _Context) -> CheckResult:
        if ctx.premium is None:
            return _not_run(cd, "No premium data was supplied.")
        rows = ctx.premium.basis_rows
        if column not in rows.columns:
            return _not_run(cd, f"Premium data has no {column} column.")
        failing = rows[~_valid_date_mask(rows[column])]
        return _record_result(cd, ctx, failing,
                              f"{label} ({column}) is present and a date between 1900 and 2100, "
                              f"on each record's gross rows.", _PREMIUM_COLS)
    return fn


def _premium_order(later: str, earlier: str, *, strict: bool, label: str):
    def fn(cd: CheckDef, ctx: _Context) -> CheckResult:
        if ctx.premium is None:
            return _not_run(cd, "No premium data was supplied.")
        rows = ctx.premium.basis_rows
        missing = [c for c in (later, earlier) if c not in rows.columns]
        if missing:
            return _not_run(cd, f"Premium data has no {', '.join(missing)} column.")
        a, b = _dates(rows[later]), _dates(rows[earlier])
        valid = _valid_date_mask(rows[later]) & _valid_date_mask(rows[earlier])
        bad = valid & ((a <= b) if strict else (a < b))
        rel = "strictly after" if strict else "on or after"
        return _record_result(cd, ctx, rows[bad], f"{label}: {later} {rel} {earlier}.", _PREMIUM_COLS)
    return fn


def _premium_before_valuation(column: str, label: str):
    def fn(cd: CheckDef, ctx: _Context) -> CheckResult:
        if ctx.premium is None:
            return _not_run(cd, "No premium data was supplied.")
        if ctx.valuation is None:
            return _not_run(cd, "No valuation date was given.")
        rows = ctx.premium.basis_rows
        if column not in rows.columns:
            return _not_run(cd, f"Premium data has no {column} column.")
        bad = _valid_date_mask(rows[column]) & (_dates(rows[column]) > ctx.valuation)
        return _record_result(cd, ctx, rows[bad],
                              f"{label} ({column}) on or before the valuation date "
                              f"{ctx.valuation.date().isoformat()}.", _PREMIUM_COLS)
    return fn


def _check_zero_gross_net(cd: CheckDef, ctx: _Context) -> CheckResult:
    if ctx.premium is None:
        return _not_run(cd, "No premium data was supplied.")
    p = ctx.premium
    bad_keys = p.gross.index[(p.gross.abs() < 0.005) & (p.nwp.abs() >= 0.005)]
    failing = p.frame[p.frame["_key"].isin(bad_keys)]
    return _record_result(cd, ctx, failing,
                          "Records whose gross premium is zero but whose net premium "
                          "(gross − ceded) is not.", _PREMIUM_COLS)


def _check_gross_exceeds_net(cd: CheckDef, ctx: _Context) -> CheckResult:
    if ctx.premium is None:
        return _not_run(cd, "No premium data was supplied.")
    p = ctx.premium
    bad_keys = p.gross.index[p.nwp.abs() > p.gross.abs() + 0.005]
    failing = p.frame[p.frame["_key"].isin(bad_keys)]
    return _record_result(cd, ctx, failing,
                          "Records whose |net premium| exceeds |gross premium|.", _PREMIUM_COLS)


def _check_classes_populated(cd: CheckDef, ctx: _Context) -> CheckResult:
    if ctx.premium is None:
        return _not_run(cd, "No premium data was supplied.")
    f = ctx.premium.frame
    cols = [c for c in PREMIUM_CLASS_COLUMNS if c in f.columns]
    if not cols:
        return _not_run(cd, "Premium data has none of " + ", ".join(PREMIUM_CLASS_COLUMNS) + ".")
    bad = pd.Series(False, index=f.index)
    for c in cols:
        bad |= _blank(f[c])
    return _record_result(cd, ctx, f[bad], "Populated on every premium row: " + ", ".join(cols) + ".",
                          (*cols, "RI_TREATY_TYPE", "PREMIUMAMOUNT"))


def _check_endorsement_populated(cd: CheckDef, ctx: _Context) -> CheckResult:
    if ctx.premium is None:
        return _not_run(cd, "No premium data was supplied.")
    f = ctx.premium.frame
    if "ENDORSEMENTNUMBER" not in f.columns:
        return _not_run(cd, "Premium data has no ENDORSEMENTNUMBER column.")
    return _record_result(cd, ctx, f[_blank(f["ENDORSEMENTNUMBER"])],
                          "ENDORSEMENTNUMBER populated on every premium row.",
                          ("ENDORSEMENTNUMBER", *_PREMIUM_COLS))


# ── claims checks ────────────────────────────────────────────────────────────

_CLAIM_COLS = ("RESERVINGCLASS", "RI_TREATY_TYPE", "LOSSDATE", "REPORTEDDATE", "PAYMENTDATE",
               "As at", "AMOUNTPAID", "AMOUNTOUTSTANDING")


def _claim_rows(ctx: _Context, *, paid_only: bool) -> pd.DataFrame:
    f = ctx.claims.frame
    return f[f["_file"] == "claims_paid"] if paid_only else f


def _claim_date_populated(column: str, label: str, *, paid_only: bool):
    def fn(cd: CheckDef, ctx: _Context) -> CheckResult:
        rows = _claim_rows(ctx, paid_only=paid_only)
        if not len(rows):
            return _not_run(cd, "No claims data was supplied.")
        if column not in rows.columns:
            return _not_run(cd, f"Claims data has no {column} column.")
        where = "claims paid" if paid_only else "claims paid and outstanding"
        return _record_result(cd, ctx, rows[~_valid_date_mask(rows[column])],
                              f"{label} ({column}) present and a valid date, in {where}.", _CLAIM_COLS)
    return fn


def _claim_order(later: str, earlier: str, label: str, *, paid_only: bool):
    def fn(cd: CheckDef, ctx: _Context) -> CheckResult:
        rows = _claim_rows(ctx, paid_only=paid_only)
        if not len(rows):
            return _not_run(cd, "No claims data was supplied.")
        missing = [c for c in (later, earlier) if c not in rows.columns]
        if missing:
            return _not_run(cd, f"Claims data has no {', '.join(missing)} column.")
        valid = _valid_date_mask(rows[later]) & _valid_date_mask(rows[earlier])
        bad = valid & (_dates(rows[later]) < _dates(rows[earlier]))
        where = "claims paid" if paid_only else "claims paid and outstanding"
        return _record_result(cd, ctx, rows[bad], f"{label}: {later} on or after {earlier}, in {where}.",
                              _CLAIM_COLS)
    return fn


def _check_loss_before_valuation(cd: CheckDef, ctx: _Context) -> CheckResult:
    rows = _claim_rows(ctx, paid_only=False)
    if not len(rows):
        return _not_run(cd, "No claims data was supplied.")
    if ctx.valuation is None:
        return _not_run(cd, "No valuation date was given.")
    if "LOSSDATE" not in rows.columns:
        return _not_run(cd, "Claims data has no LOSSDATE column.")
    bad = _valid_date_mask(rows["LOSSDATE"]) & (_dates(rows["LOSSDATE"]) > ctx.valuation)
    return _record_result(cd, ctx, rows[bad],
                          f"LOSSDATE on or before the valuation date {ctx.valuation.date().isoformat()}.",
                          _CLAIM_COLS)


# ── catalogue ────────────────────────────────────────────────────────────────


#: The client's checklist (Table A.6), in report order. IDs and descriptions are the client's.
#: 2.10 and 3.7-3.18 are not in the checklist we have received; add them here when supplied.
CATALOGUE: tuple[CheckDef, ...] = (
    CheckDef("1.1", CATEGORY_GENERAL, "Data for all LOBs under consideration have been received", _check_lobs_received),
    CheckDef("1.2", CATEGORY_GENERAL, "Data is in the same format with previous valuations", _check_same_format),
    CheckDef("1.3", CATEGORY_GENERAL, "All number fields in number format", _check_numeric),
    CheckDef("1.4", CATEGORY_GENERAL, "Check for duplicates in data", _check_duplicates),
    CheckDef("2.1", CATEGORY_PREMIUM, "All policy issue dates are populated & valid",
             _premium_date_populated("ISSUEDATE", "Issue date")),
    CheckDef("2.2", CATEGORY_PREMIUM, "All policy effective dates are populated & valid",
             _premium_date_populated("POLICYSTARTDATE", "Effective date")),
    CheckDef("2.3", CATEGORY_PREMIUM, "All policy expiry dates are populated & valid",
             _premium_date_populated("POLICYENDDATE", "Expiry date")),
    CheckDef("2.4", CATEGORY_PREMIUM, "Policy expiry date is always after policy issue date",
             _premium_order("POLICYENDDATE", "ISSUEDATE", strict=True, label="Expiry after issue")),
    CheckDef("2.5", CATEGORY_PREMIUM, "Policy expiry date is always after policy effective date",
             _premium_order("POLICYENDDATE", "POLICYSTARTDATE", strict=True, label="Expiry after effective")),
    CheckDef("2.6", CATEGORY_PREMIUM, "Policy issue date is always before valuation date",
             _premium_before_valuation("ISSUEDATE", "Issue date")),
    CheckDef("2.7", CATEGORY_PREMIUM, "Policy effective date is always before valuation date",
             _premium_before_valuation("POLICYSTARTDATE", "Effective date")),
    CheckDef("2.8", CATEGORY_PREMIUM, "Where Gross Premiums are zero, Net Premiums are zero as well",
             _check_zero_gross_net),
    CheckDef("2.9", CATEGORY_PREMIUM, "Gross Premiums are always bigger than Net Premiums (absolute values)",
             _check_gross_exceeds_net),
    CheckDef("2.11", CATEGORY_PREMIUM, "Reconciliation and Modelling Classes are always populated (i.e no blanks)",
             _check_classes_populated),
    CheckDef("2.12", CATEGORY_PREMIUM, "Endorsement indicators are always populated (i.e no blanks)",
             _check_endorsement_populated),
    CheckDef("3.1", CATEGORY_CLAIMS, "All loss dates are populated & Valid",
             _claim_date_populated("LOSSDATE", "Loss date", paid_only=False)),
    CheckDef("3.2", CATEGORY_CLAIMS, "All payment dates are populated & Valid",
             _claim_date_populated("PAYMENTDATE", "Payment date", paid_only=True)),
    CheckDef("3.3", CATEGORY_CLAIMS, "All reporting dates are populated & Valid",
             _claim_date_populated("REPORTEDDATE", "Reporting date", paid_only=False)),
    CheckDef("3.4", CATEGORY_CLAIMS, "Payment date is always after loss date",
             _claim_order("PAYMENTDATE", "LOSSDATE", "Payment after loss", paid_only=True)),
    CheckDef("3.5", CATEGORY_CLAIMS, "Reporting date is always after loss date",
             _claim_order("REPORTEDDATE", "LOSSDATE", "Reporting after loss", paid_only=False)),
    CheckDef("3.6", CATEGORY_CLAIMS, "Loss dates are all before valuation date", _check_loss_before_valuation),
)


#: Plain-language basis, written to the workbook's Basis sheet (mirrors the module docstring).
BASIS_TEXT: tuple[str, ...] = (
    "Premium record: one policy transaction, keyed POLICYNUMBER + ENDORSEMENTNUMBER (when present).",
    "NWP of a record = gross premium − ceded premium, from the GROSS and RI rows of RI_TREATY_TYPE.",
    "Premium date checks read each record's GROSS rows. RI rows carry the cession's own dates, "
    "which would otherwise report every cession as a policy defect.",
    "Claims record: one claim (CLAIMNUMBER), across the claims paid and claims outstanding files.",
    "Net paid claims of a record = gross paid − RI paid, using the amount the reserving engine "
    "consumes (recovery heads substituted).",
    "Valid date: parses as a date between 1900-01-01 and 2100-12-31.",
    "\"After\" for policy periods (2.4, 2.5) means strictly after: a zero-length policy period is "
    "a defect. For claim events (3.4, 3.5) it means on or after: a same-day report or payment "
    "is normal.",
    "Date comparisons skip rows whose dates are missing or invalid; those rows are counted under "
    "the 'populated & valid' checks instead, so one bad date is never reported twice.",
    "Valuation date: the reserving run's end of period (EOP).",
    "Duplicates (1.4): rows identical in every column to an earlier row of the same input; "
    "each extra copy is one record.",
    "A check whose input column is not supplied is reported as 'Not run' with the reason, never "
    "as passed.",
    "Amounts are shown in thousands; the cells hold the exact values.",
)


def check_ids() -> list[str]:
    return [cd.id for cd in CATALOGUE]


# ── entry point ──────────────────────────────────────────────────────────────


def parse_valuation_date(value: Any) -> pd.Timestamp | None:
    """The run's EOP as a timestamp. Job metadata stores it day-first (``31-12-2017``)."""
    if value in (None, ""):
        return None
    ts = pd.to_datetime(value, errors="coerce", dayfirst=True)
    return None if pd.isna(ts) else pd.Timestamp(ts).normalize()


def run_data_checks(
    premium: pd.DataFrame | None,
    claims_paid: pd.DataFrame | None,
    claims_os: pd.DataFrame | None,
    *,
    valuation_date: Any = None,
    baseline: dict[str, Any] | None = None,
) -> DataChecksReport:
    """Run the client checklist over the inputs as supplied (after class aliases).

    ``baseline`` is the previous valuation for check 1.2: ``{"job_id", "eop", "input_schema"}``
    (see ``data_checks_store.previous_baseline``). A check that raises is reported as not run
    with the error, rather than taking the other checks — or the reserving run — down with it.
    """
    inputs = {"premium": premium, "claims_paid": claims_paid, "claims_os": claims_os}
    valuation = parse_valuation_date(valuation_date)
    ctx = _Context(
        inputs=inputs,
        valuation=valuation,
        previous_schema=(baseline or {}).get("input_schema"),
        premium=_Premium.build(premium) if premium is not None and len(premium) else None,
        claims=_Claims.build(claims_paid, claims_os),
    )
    results = []
    for cd in CATALOGUE:
        try:
            results.append(cd.fn(cd, ctx))
        except Exception as exc:  # noqa: BLE001 — one faulty check must not hide the rest
            results.append(_not_run(cd, f"Check could not be evaluated: {exc}"))
    return DataChecksReport(
        valuation_date=valuation.date().isoformat() if valuation is not None else None,
        results=results,
        input_schema=_schema_of(inputs),
        compared_with=({"job_id": baseline.get("job_id"), "eop": baseline.get("eop")}
                       if baseline else None),
    )
