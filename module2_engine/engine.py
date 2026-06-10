"""Headless port of desktop Module 2 calculations."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from core.excel import READ_ENGINE, WRITE_ENGINE
from core.profiling import stage_timer


def _read_required_sheet(path_or_bytes: str | bytes | io.BytesIO, sheet_name: str, **kwargs) -> pd.DataFrame:
    try:
        return _read_excel_any(path_or_bytes, sheet_name, **kwargs)
    except ValueError as exc:
        raise ValueError(f"Required sheet '{sheet_name}' is missing.") from exc


def _require_columns(df: pd.DataFrame, sheet_name: str, required: list[str]) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"Sheet '{sheet_name}' is missing required columns: {', '.join(missing)}."
        )


def _read_excel_any(path_or_bytes: str | bytes | io.BytesIO, sheet_name: str, **kwargs) -> pd.DataFrame:
    # An already-opened ExcelFile lets callers parse the workbook once and read
    # many sheets from it, instead of re-parsing the whole file per sheet.
    if isinstance(path_or_bytes, pd.ExcelFile):
        return pd.read_excel(path_or_bytes, sheet_name=sheet_name, **kwargs)
    if isinstance(path_or_bytes, bytes):
        return pd.read_excel(io.BytesIO(path_or_bytes), sheet_name=sheet_name, engine=READ_ENGINE, **kwargs)
    if hasattr(path_or_bytes, "read"):
        raw = path_or_bytes.read()
        if hasattr(path_or_bytes, "seek"):
            path_or_bytes.seek(0)
        return pd.read_excel(io.BytesIO(raw), sheet_name=sheet_name, engine=READ_ENGINE, **kwargs)
    return pd.read_excel(path_or_bytes, sheet_name=sheet_name, engine=READ_ENGINE, **kwargs)


def calculate_sequence(merged_df: pd.DataFrame) -> pd.Series:
    unique_periods = merged_df["Accident_Period"].unique()
    sorted_periods = sorted(
        unique_periods,
        key=lambda x: (int(str(x).split("-Q")[0]), int(str(x).split("-Q")[1])),
        reverse=True,
    )
    period_to_sequence = {period: idx for idx, period in enumerate(sorted_periods)}
    return merged_df["Accident_Period"].map(period_to_sequence)


def calculate_additional_matrix(df: pd.DataFrame) -> pd.DataFrame:
    # Vectorised equivalent of the original per-cell loop (see plan §A1). For each
    # row r=(RESERVINGCLASS, GROSS/RI, Age=a, Expected Unpaid %=e) and column c,
    # the cell is Incremental[a+c+1] / e where a row with that future Age exists in
    # the same (RESERVINGCLASS, GROSS/RI) group, else 0. Rows with e == 0 become
    # [1, 0, 0, ...]. We replace the O(N²·age) nested filter with a single keyed
    # lookup per column. Numerics are identical: the same scalar float64 division
    # incr / e, and the same first-match-wins on duplicate keys (drop_duplicates
    # keep="first" mirrors the original `future_row[...].values[0]`).
    max_age = int(df["Age"].max())
    cols = list(range(max_age + 1))
    additional_matrix = pd.DataFrame(0.0, index=df.index, columns=cols)

    keyed = df.drop_duplicates(subset=["RESERVINGCLASS", "GROSS/RI", "Age"], keep="first")
    incr_by_key = keyed.set_index(["RESERVINGCLASS", "GROSS/RI", "Age"])["Incremental"]

    rc = df["RESERVINGCLASS"].to_numpy()
    gr = df["GROSS/RI"].to_numpy()
    age = df["Age"].to_numpy()
    expected = df["Expected Unpaid %"].to_numpy()

    for c in cols:
        lookup = pd.MultiIndex.from_arrays([rc, gr, age + c + 1])
        incr = incr_by_key.reindex(lookup).to_numpy()  # NaN where no future row
        with np.errstate(divide="ignore", invalid="ignore"):
            additional_matrix[c] = np.where(np.isnan(incr), 0.0, incr / expected)

    zero_mask = expected == 0
    if zero_mask.any():
        additional_matrix.loc[zero_mask, :] = 0.0
        additional_matrix.loc[zero_mask, 0] = 1.0
    return additional_matrix


def convert_annual_to_quarterly(rate: pd.Series) -> pd.Series:
    return (1 + rate) ** (1 / 4) - 1


def convert_to_discount_rate(spot_rate: pd.Series) -> pd.Series:
    return 1 / (1 + spot_rate)


def calculate_discount_rates(data: pd.DataFrame, column_name: str) -> pd.DataFrame:
    quarterly_spot_rates = convert_annual_to_quarterly(data[column_name])
    quarterly_periods = np.arange(len(data) * 4)
    quarterly_data = pd.DataFrame(
        {
            "Quarterly": quarterly_periods,
            "Yearly Spot Rate": np.repeat(data[column_name], 4),
            "Quarterly Spot Rate": np.repeat(quarterly_spot_rates, 4),
        }
    )
    quarterly_data["Quarterly Discount Rate"] = convert_to_discount_rate(
        quarterly_data["Quarterly Spot Rate"]
    )
    quarterly_data["Cumulative Discount Rate"] = quarterly_data[
        "Quarterly Discount Rate"
    ].cumprod()
    return quarterly_data.drop(columns=["Yearly Spot Rate", "Quarterly Spot Rate"])


def _apply_selected_ulr(uw_summary: pd.DataFrame, selected_ulr_rows: list[dict[str, Any]] | None) -> pd.DataFrame:
    if not selected_ulr_rows:
        uw_summary["Selected ULR"] = uw_summary["Ult LR"]
    else:
        selected_map: dict[tuple[str, str], float] = {}
        for item in selected_ulr_rows:
            rc = str(item.get("reserving_class", "")).strip()
            uwy = str(item.get("uwy", "")).strip()
            val = item.get("selected_ulr")
            if rc and uwy and val is not None:
                selected_map[(rc, uwy)] = float(val)
        out = []
        for _, row in uw_summary.iterrows():
            rc = str(row["RESERVINGCLASS"]).strip()
            uwy = str(row["UWY"]).strip()
            out.append(selected_map.get((rc, uwy), float(row["Ult LR"])))
        uw_summary["Selected ULR"] = out
    uw_summary["Combined Ratio"] = (
        uw_summary["Selected ULR"] * (1 + uw_summary["RA %"])
        + uw_summary["Comm Ratio"]
        + uw_summary["Exp Ratio"]
    )
    return uw_summary


def _build_allocate_outputs(combined_summary_bytes: bytes, selected_ulr_rows: list[dict[str, Any]] | None = None) -> tuple[bytes, list[dict[str, Any]]]:
    # Parse the workbook once; each sheet read below reuses it (was 8 full
    # openpyxl re-parses of the same file — plan §B).
    with stage_timer("m2.allocate.read_workbook"):
        file_path = pd.ExcelFile(io.BytesIO(combined_summary_bytes), engine=READ_ENGINE)
        ibnr_summary = _read_required_sheet(file_path, "IBNR Summary")
        allocation_ep = _read_required_sheet(file_path, "Allocation EP")
        lic_os_summary = _read_required_sheet(file_path, "LIC (OS) Summary")
        ulae_ra_percentages = _read_required_sheet(file_path, "ULAE-RA")
        discount_rate = _read_required_sheet(file_path, "Discount Rate")
        upr_runoff = _read_required_sheet(file_path, "UPR Run-Off")
        uw_summary = _read_required_sheet(file_path, "UW Summary")
        combined_summary_sheet = _read_required_sheet(file_path, "Combined Summary")
    _require_columns(
        combined_summary_sheet,
        "Combined Summary",
        ["RESERVINGCLASS", "UWY", "Gross UPR", "DAC", "RI UPR", "UCR"],
    )
    _require_columns(
        uw_summary,
        "UW Summary",
        [
            "RESERVINGCLASS",
            "UWY",
            "GWP",
            "UPR",
            "GEP",
            "Commission Expense",
            "Paid Claims",
            "OS",
            "Incurred Claims",
            "Paid LR",
            "Inc LR",
            "Comm Ratio",
            "Exp Ratio",
            "RI %",
        ],
    )

    ibnr_values = ibnr_summary.iloc[:, [7, 16]]
    ibnr_names = ibnr_summary.iloc[:, [0, 18, 19, 20]]
    ibnr_names.iloc[:, 2] = ibnr_names.iloc[:, 2].replace(
        {"Payment": "Payment", "Subrogation": "S&S", "Salvage": "S&S"}
    )
    ibnr_combined = pd.concat([ibnr_names, ibnr_values], axis=1)
    ibnr_combined["IBNR"] = pd.to_numeric(ibnr_combined.iloc[:, -1], errors="coerce").fillna(0)
    ibnr_combined["Paid CDF"] = pd.to_numeric(ibnr_combined.iloc[:, -2], errors="coerce").fillna(0)
    ibnr_pivot = (
        ibnr_combined.pivot_table(
            index=["Accident_Period", "RESERVINGCLASS", "GROSS/RI"],
            columns="Payment/Recovery",
            values="IBNR",
            aggfunc="sum",
        )
        .reset_index()
    )
    ibnr_paid_cdf = ibnr_combined[
        (ibnr_combined["Payment/Recovery"] == "Payment")
        & (ibnr_combined["GROSS/RI"] == "GROSS")
    ][["Accident_Period", "RESERVINGCLASS", "Paid CDF"]].drop_duplicates(
        subset=["Accident_Period", "RESERVINGCLASS"]
    )
    ibnr_pivot = pd.merge(
        ibnr_pivot, ibnr_paid_cdf, on=["Accident_Period", "RESERVINGCLASS"], how="left"
    )
    ibnr_pivot.columns = [
        "Accident_Period",
        "RESERVINGCLASS",
        "GROSS/RI",
        "Payment",
        "S&S",
        "Paid CDF",
    ]
    ibnr_pivot[["Payment", "S&S"]] = ibnr_pivot[["Payment", "S&S"]].apply(
        pd.to_numeric, errors="coerce"
    ).fillna(0)

    allocation_values = allocation_ep.iloc[:, [4]]
    allocation_names = allocation_ep.iloc[:, [0, 1, 2, 3]]
    lic_values = lic_os_summary.iloc[:, [4, 5]]
    lic_names = lic_os_summary.iloc[:, [0, 1, 2, 3]]
    ulae_ra_values = ulae_ra_percentages.iloc[:, [2, 3]]
    ulae_ra_names = ulae_ra_percentages.iloc[:, [0, 1]]
    discount_rate_values = discount_rate.iloc[:, [1, 2]]
    discount_rate_names = discount_rate.iloc[:, [0]]

    allocation_names.columns = ["RESERVINGCLASS", "UWY", "Accident_Period", "GROSS/RI"]
    allocation_values.columns = ["EP_Percent"]
    lic_names.columns = ["RESERVINGCLASS", "UWY", "Accident_Period", "GROSS/RI"]
    lic_values.columns = ["Outstanding", "SS"]
    ulae_ra_names.columns = ["RESERVINGCLASS", "GROSS/RI"]
    ulae_ra_values.columns = ["ULAE %", "RA %"]
    discount_rate_names.columns = ["Time Period"]
    discount_rate_values.columns = ["CY Discount", "PY Discount"]

    allocation_ep_combined = pd.concat([allocation_names, allocation_values], axis=1)
    allocation_ep_combined["EP_Percent"] = pd.to_numeric(
        allocation_ep_combined["EP_Percent"], errors="coerce"
    ).fillna(0)
    lic_os_combined = pd.concat([lic_names, lic_values], axis=1)
    lic_os_combined[["Outstanding", "SS"]] = lic_os_combined[
        ["Outstanding", "SS"]
    ].apply(pd.to_numeric, errors="coerce").fillna(0)
    ulae_ra_combined = pd.concat([ulae_ra_names, ulae_ra_values], axis=1)
    ulae_ra_combined[["ULAE %", "RA %"]] = ulae_ra_combined[
        ["ULAE %", "RA %"]
    ].apply(pd.to_numeric, errors="coerce").fillna(0)
    discount_rate_combined = pd.concat([discount_rate_names, discount_rate_values], axis=1)
    discount_rate_combined["Time Period"] = pd.to_numeric(
        discount_rate_combined["Time Period"], errors="coerce"
    ).fillna(0).astype(int)
    discount_rate_combined["CY Discount"] = pd.to_numeric(
        discount_rate_combined["CY Discount"].astype(str).str.rstrip("%"), errors="coerce"
    ).fillna(0)
    discount_rate_combined["PY Discount"] = pd.to_numeric(
        discount_rate_combined["PY Discount"].astype(str).str.rstrip("%"), errors="coerce"
    ).fillna(0)

    cy_result_df = calculate_discount_rates(discount_rate_combined, "CY Discount")
    py_result_df = calculate_discount_rates(discount_rate_combined, "PY Discount")

    upr_dac_eop_df = combined_summary_sheet[
        ["RESERVINGCLASS", "UWY", "Gross UPR", "DAC", "RI UPR", "UCR"]
    ].copy()

    merged_df = pd.merge(
        allocation_ep_combined,
        lic_os_combined,
        on=["RESERVINGCLASS", "Accident_Period", "UWY", "GROSS/RI"],
        how="left",
    )
    merged_df = pd.merge(
        merged_df,
        ibnr_pivot,
        on=["RESERVINGCLASS", "Accident_Period", "GROSS/RI"],
        how="left",
    )
    merged_df = pd.merge(
        merged_df, ulae_ra_combined, on=["RESERVINGCLASS", "GROSS/RI"], how="left"
    )
    merged_df.fillna({"Outstanding": 0, "SS": 0, "Payment": 0, "S&S": 0}, inplace=True)

    merged_df["Payment"] = merged_df["Payment"] * merged_df["EP_Percent"]
    merged_df["S&S"] = merged_df["S&S"] * merged_df["EP_Percent"]
    merged_df["IBNR"] = merged_df["Payment"] + merged_df["S&S"]
    merged_df["ULAE"] = merged_df["ULAE %"] * (
        (merged_df["Outstanding"] + merged_df["SS"]) * 0.5 + merged_df["IBNR"]
    )
    merged_df["RA (OS)"] = merged_df["RA %"] * (merged_df["Outstanding"] + merged_df["SS"])
    merged_df["RA (IBNR)"] = merged_df["RA %"] * merged_df["IBNR"]
    merged_df["Future CF"] = (
        merged_df["IBNR"] + merged_df["ULAE"] + merged_df["Outstanding"] + merged_df["SS"]
    )
    merged_df["Cumulative %"] = 1 / merged_df["Paid CDF"].replace(0, float("nan"))
    merged_df["Cumulative %"] = merged_df["Cumulative %"].fillna(0)
    merged_df["Age"] = calculate_sequence(merged_df)
    merged_df["Expected Unpaid %"] = 1 - merged_df["Cumulative %"]
    with stage_timer("m2.allocate.incremental"):
        merged_df["Incremental"] = (
            merged_df.groupby(["RESERVINGCLASS", "UWY", "GROSS/RI"])
            .apply(lambda x: x.sort_values(by="Age")["Cumulative %"].diff().fillna(x["Cumulative %"]))
            .reset_index(level=[0, 1, 2], drop=True)
        )
    with stage_timer("m2.allocate.additional_matrix"):
        additional_matrix = calculate_additional_matrix(merged_df)
    additional_matrix.columns = [int(col) for col in additional_matrix.columns]
    merged_df = pd.concat([merged_df, additional_matrix], axis=1)

    future_cf_df = pd.DataFrame()
    for col in additional_matrix.columns:
        future_cf_df[col] = merged_df["Future CF"] * additional_matrix[col]
    future_cf_df = pd.concat(
        [merged_df[["RESERVINGCLASS", "UWY", "Accident_Period", "GROSS/RI"]], future_cf_df],
        axis=1,
    )
    discounted_cf_cy_df = future_cf_df.copy()
    discounted_cf_py_df = future_cf_df.copy()
    for col in additional_matrix.columns:
        quarter_index = int(col)
        discount_cy = cy_result_df.loc[
            cy_result_df["Quarterly"] == quarter_index, "Cumulative Discount Rate"
        ].values
        discount_py = py_result_df.loc[
            py_result_df["Quarterly"] == quarter_index, "Cumulative Discount Rate"
        ].values
        if len(discount_cy) > 0:
            discounted_cf_cy_df[col] = future_cf_df[col] * discount_cy[0]
            discounted_cf_py_df[col] = future_cf_df[col] * discount_py[0]
    merged_df["Discounting Impact"] = discounted_cf_cy_df[additional_matrix.columns].sum(
        axis=1
    ) - future_cf_df[additional_matrix.columns].sum(axis=1)
    merged_df["Change in Discounting Impact"] = discounted_cf_cy_df[
        additional_matrix.columns
    ].sum(axis=1) - discounted_cf_py_df[additional_matrix.columns].sum(axis=1)
    merged_df.drop(columns=["EP_Percent", "ULAE %", "RA %", "Paid CDF"], inplace=True)

    dynamic_columns = [col for col in additional_matrix.columns]
    gross_only = merged_df[merged_df["GROSS/RI"] == "GROSS"]
    sum_columns = gross_only.groupby("RESERVINGCLASS")[dynamic_columns].sum()
    total_sum = sum_columns.sum(axis=1)
    avg_df = (
        sum_columns.div(total_sum, axis=0)
        .rename(columns={col: int(col) for col in dynamic_columns})
        .reset_index()
    )

    ibnr_summary_by_class_uwy = (
        gross_only.groupby(["RESERVINGCLASS", "UWY"])["IBNR"].sum().reset_index()
    )
    uw_summary = uw_summary.merge(
        ibnr_summary_by_class_uwy, on=["RESERVINGCLASS", "UWY"], how="left"
    )
    ulae_ra_combined_gross = ulae_ra_combined[ulae_ra_combined["GROSS/RI"] == "GROSS"]
    uw_summary = uw_summary.merge(
        ulae_ra_combined_gross[["RESERVINGCLASS", "RA %"]], on=["RESERVINGCLASS"], how="left"
    )
    uw_summary["Ultimate Claims"] = uw_summary["Incurred Claims"] + uw_summary["IBNR"]
    uw_summary["Ult LR"] = uw_summary["Ultimate Claims"] / uw_summary["GEP"].replace(0, np.nan)
    uw_summary["Ult LR"] = uw_summary["Ult LR"].fillna(0)
    uw_summary = _apply_selected_ulr(uw_summary, selected_ulr_rows)
    loss_ratio_sheet = uw_summary[
        [
            "RESERVINGCLASS",
            "UWY",
            "GWP",
            "UPR",
            "GEP",
            "Commission Expense",
            "Paid Claims",
            "OS",
            "IBNR",
            "Incurred Claims",
            "Ultimate Claims",
            "Paid LR",
            "Inc LR",
            "Ult LR",
            "Selected ULR",
            "Comm Ratio",
            "Exp Ratio",
            "RA %",
            "Combined Ratio",
            "RI %",
        ]
    ].copy()

    # Run-off and LC are same desktop formulas
    upr_runoff_columns = upr_runoff.columns[2:]
    avg_df.iloc[:, 1:] = avg_df.iloc[:, 1:].apply(lambda x: x / 1)
    summary_data = {
        "Reserving class": [],
        "UWY": [],
        "PAA_LRC": [],
        "GMM LRC_Undiscounted": [],
        "GMM LRC_Discounted_CY": [],
        "GMM LRC_Discounted_PY": [],
    }
    # Pre-index discount factors by quarterly period for O(1) lookup instead of a
    # boolean-filter scan per (period, column). 'Quarterly' is a unique 0..N range
    # (calculate_discount_rates), so these dicts exactly reproduce the original
    # `.loc[... == discount_index].values[0]`.
    cy_disc = dict(zip(cy_result_df["Quarterly"], cy_result_df["Cumulative Discount Rate"]))
    py_disc = dict(zip(py_result_df["Quarterly"], py_result_df["Cumulative Discount Rate"]))
    # Accumulate rows then build the frame once; concatenating inside the loop was
    # O(n²) (plan §1.3).
    runoff_rows: list[dict[str, Any]] = []
    # Pre-index the payment-pattern multipliers by reserving class (was a boolean
    # scan of avg_df per (row, period)). Unique RESERVINGCLASS after the upstream
    # groupby, so .loc returns the same scalar as the original `.values[0]`.
    avg_by_rc = avg_df.set_index("RESERVINGCLASS")
    upr_col_list = list(upr_runoff_columns)
    with stage_timer("m2.allocate.runoff_loop"):
        for _, row in upr_runoff.iterrows():
            reserving_class = row["RESERVINGCLASS"]
            uwy = row["UWY"]
            if reserving_class in avg_df["RESERVINGCLASS"].values:
                combined_lr = uw_summary.loc[
                    (uw_summary["RESERVINGCLASS"] == reserving_class)
                    & (uw_summary["UWY"] == uwy),
                    "Combined Ratio",
                ].values[0]
                # Build rows as plain dicts (Series __setitem__/.copy() per cell was
                # the bottleneck); values are identical and so is column order.
                base = row.to_dict()
                for period in additional_matrix.columns:
                    period_int = int(period)
                    multiplier = avg_by_rc.loc[reserving_class, period_int]
                    new_row = dict(base)
                    new_row["Time Period"] = period_int
                    upr_multiplier_sum = 0
                    upr_combined_lr_multiplier_sum = 0
                    upr_combined_lr_multiplier_disc_sum_cy = 0
                    upr_combined_lr_multiplier_disc_sum_py = 0
                    for col in upr_col_list:
                        val = base[col]
                        upr_combined_lr_mult = val * combined_lr * multiplier
                        new_row[f"{col}_CombinedLR_Multiplier"] = upr_combined_lr_mult
                        upr_multiplier_sum += val * multiplier
                        upr_combined_lr_multiplier_sum += upr_combined_lr_mult
                    for col in upr_col_list:
                        upr_combined_lr_mult = new_row[f"{col}_CombinedLR_Multiplier"]
                        quarter_offset = upr_runoff_columns.get_loc(col)
                        discount_index = period_int + quarter_offset
                        disc_cy = cy_disc.get(discount_index)
                        disc_py = py_disc.get(discount_index)
                        new_row[f"{col}_Disc_CY"] = upr_combined_lr_mult * disc_cy if disc_cy is not None else 0
                        new_row[f"{col}_Disc_PY"] = upr_combined_lr_mult * disc_py if disc_py is not None else 0
                        upr_combined_lr_multiplier_disc_sum_cy += new_row[f"{col}_Disc_CY"]
                        upr_combined_lr_multiplier_disc_sum_py += new_row[f"{col}_Disc_PY"]
                    summary_data["Reserving class"].append(reserving_class)
                    summary_data["UWY"].append(uwy)
                    summary_data["PAA_LRC"].append(upr_multiplier_sum)
                    summary_data["GMM LRC_Undiscounted"].append(upr_combined_lr_multiplier_sum)
                    summary_data["GMM LRC_Discounted_CY"].append(
                        upr_combined_lr_multiplier_disc_sum_cy
                    )
                    summary_data["GMM LRC_Discounted_PY"].append(
                        upr_combined_lr_multiplier_disc_sum_py
                    )
                    runoff_rows.append(new_row)
    with stage_timer("m2.allocate.runoff_build"):
        upr_runoff_processed = (
            pd.DataFrame(runoff_rows).reset_index(drop=True) if runoff_rows else pd.DataFrame()
        )
        upr_runoff_processed.fillna(0, inplace=True)
    summary_df = pd.DataFrame(summary_data)
    pivot_summary_df = (
        summary_df.pivot_table(
            index=["Reserving class", "UWY"],
            values=[
                "PAA_LRC",
                "GMM LRC_Undiscounted",
                "GMM LRC_Discounted_CY",
                "GMM LRC_Discounted_PY",
            ],
            aggfunc="sum",
        )
        .reset_index()
    )
    pivot_summary_df["LC Undiscounted"] = pivot_summary_df.apply(
        lambda x: max(x["GMM LRC_Undiscounted"] - x["PAA_LRC"], 0), axis=1
    )
    pivot_summary_df["LC Discounted_CY"] = pivot_summary_df.apply(
        lambda x: max(x["GMM LRC_Discounted_CY"] - x["PAA_LRC"], 0), axis=1
    )
    pivot_summary_df["LC Discounted_PY"] = pivot_summary_df.apply(
        lambda x: max(x["GMM LRC_Discounted_PY"] - x["PAA_LRC"], 0), axis=1
    )
    pivot_summary_df = pivot_summary_df.merge(
        loss_ratio_sheet[["RESERVINGCLASS", "UWY", "RI %"]],
        left_on=["Reserving class", "UWY"],
        right_on=["RESERVINGCLASS", "UWY"],
        how="left",
    )
    pivot_summary_df["Loss Recovery Component"] = pivot_summary_df.apply(
        lambda x: max(0, x["RI %"] * x["LC Discounted_CY"]), axis=1
    )
    pivot_summary_df.drop(columns=["RESERVINGCLASS", "RI %"], inplace=True)
    pivot_summary_df.rename(columns={"Reserving class": "RESERVINGCLASS"}, inplace=True)
    pivot_summary_df = pivot_summary_df[
        [
            "RESERVINGCLASS",
            "UWY",
            "PAA_LRC",
            "GMM LRC_Undiscounted",
            "GMM LRC_Discounted_CY",
            "GMM LRC_Discounted_PY",
            "LC Undiscounted",
            "LC Discounted_CY",
            "LC Discounted_PY",
            "Loss Recovery Component",
        ]
    ]
    combined_discount_df = pd.DataFrame(
        {
            "Time Period": cy_result_df["Quarterly"],
            "CY Discount Factor": cy_result_df["Cumulative Discount Rate"],
            "PY Discount Factor": py_result_df["Cumulative Discount Rate"],
        }
    )

    out = io.BytesIO()
    # Single source of truth for the allocate sheets (insertion order == written
    # order). Returned so run_module2_process can reuse these frames instead of
    # re-reading the workbook it just wrote (plan §2.2).
    allocate_sheets: dict[str, pd.DataFrame] = {
        "Combined Summary": combined_summary_sheet,
        "MainSheet": merged_df,
        "FutureCF": future_cf_df,
        "Discounted CF CY": discounted_cf_cy_df,
        "Discounted CF PY": discounted_cf_py_df,
        "Payment Pattern": avg_df,
        "Run-off": upr_runoff_processed,
        "Loss Ratio": loss_ratio_sheet,
        "LC": pivot_summary_df,
        "CY-PY Discount": combined_discount_df,
        "UPR-DAC_eop": upr_dac_eop_df,
    }
    with stage_timer("m2.allocate.write_workbook"), pd.ExcelWriter(out, engine=WRITE_ENGINE) as writer:
        for sheet_name, frame in allocate_sheets.items():
            frame.to_excel(writer, index=False, sheet_name=sheet_name)

    ulr_rows: list[dict[str, Any]] = []
    for idx, row in loss_ratio_sheet.iterrows():
        ulr_rows.append(
            {
                "id": str(idx + 1),
                "reserving_class": str(row["RESERVINGCLASS"]),
                "uwy": str(row["UWY"]),
                "gwp": float(row["GWP"]),
                "upr": float(row["UPR"]),
                "gep": float(row["GEP"]),
                "paid_claims": float(row["Paid Claims"]),
                "os": float(row["OS"]),
                "ibnr": float(row["IBNR"]),
                "incurred_claims": float(row["Incurred Claims"]),
                "ultimate_claims": float(row["Ultimate Claims"]),
                "paid_lr": float(row["Paid LR"]),
                "inc_lr": float(row["Inc LR"]),
                "ult_lr": float(row["Ult LR"]),
                "commission_expense": float(row["Commission Expense"]),
                "comm_ratio": float(row["Comm Ratio"]),
                "exp_ratio": float(row["Exp Ratio"]),
                "ri_percent": float(row["RI %"]),
                "ra_percent": float(row["RA %"]),
                "selected_ulr": float(row["Selected ULR"]),
                "combined_ratio": float(row["Combined Ratio"]),
            }
        )
    return out.getvalue(), ulr_rows, allocate_sheets


def convert_accident_period_to_year(df: pd.DataFrame) -> pd.DataFrame:
    df["Accident_Period"] = df["Accident_Period"].fillna("0-0")
    df["Accident_Period"] = df["Accident_Period"].astype(str).apply(lambda x: int(x.split("-")[0]))
    return df


def classify_current_year(df: pd.DataFrame, accounting_period: int) -> pd.DataFrame:
    df["Accident_Period"] = df["Accident_Period"].astype(int)
    df["CY OS"] = df.apply(lambda row: row["Outstanding"] if row["Accident_Period"] == accounting_period else 0, axis=1)
    df["CY SS"] = df.apply(lambda row: row["SS"] if row["Accident_Period"] == accounting_period else 0, axis=1)
    df["CY Payment"] = df.apply(lambda row: row["Payment"] if row["Accident_Period"] == accounting_period else 0, axis=1)
    df["CY S&S"] = df.apply(lambda row: row["S&S"] if row["Accident_Period"] == accounting_period else 0, axis=1)
    df["CY ULAE"] = df.apply(lambda row: row["ULAE"] if row["Accident_Period"] == accounting_period else 0, axis=1)
    df["CY RA (OS)"] = df.apply(lambda row: row["RA (OS)"] if row["Accident_Period"] == accounting_period else 0, axis=1)
    df["CY RA (IBNR)"] = df.apply(lambda row: row["RA (IBNR)"] if row["Accident_Period"] == accounting_period else 0, axis=1)
    df["CY Discounting Impact"] = df.apply(lambda row: row["Discounting Impact"] if row["Accident_Period"] == accounting_period else 0, axis=1)
    df["CY O/S"] = df["CY OS"] + df["CY SS"]
    df["CY IBNR"] = df["CY Payment"] + df["CY S&S"]
    return df


def pivot_and_calculate_differences(current_df: pd.DataFrame, previous_df: pd.DataFrame, accounting_period: int) -> pd.DataFrame:
    current_df = convert_accident_period_to_year(current_df)
    previous_df = convert_accident_period_to_year(previous_df)
    current_df = classify_current_year(current_df, accounting_period)
    current_pivot = current_df.pivot_table(
        index=["RESERVINGCLASS", "UWY", "Accident_Period", "GROSS/RI"],
        values=[
            "Outstanding",
            "SS",
            "Payment",
            "S&S",
            "ULAE",
            "RA (OS)",
            "RA (IBNR)",
            "Discounting Impact",
            "Change in Discounting Impact",
            "CY OS",
            "CY SS",
            "CY Payment",
            "CY S&S",
            "CY ULAE",
            "CY RA (OS)",
            "CY RA (IBNR)",
            "CY Discounting Impact",
            "CY O/S",
            "CY IBNR",
        ],
        aggfunc="sum",
    ).reset_index()
    previous_pivot = previous_df.pivot_table(
        index=["RESERVINGCLASS", "UWY", "Accident_Period", "GROSS/RI"],
        values=["Outstanding", "SS", "Payment", "S&S", "ULAE", "RA (OS)", "RA (IBNR)", "Discounting Impact"],
        aggfunc="sum",
    ).reset_index()
    merged = pd.merge(
        current_pivot,
        previous_pivot,
        on=["RESERVINGCLASS", "UWY", "Accident_Period", "GROSS/RI"],
        how="outer",
        suffixes=("_curr", "_prev"),
    )
    merged.fillna(0, inplace=True)
    merged["PY OS"] = merged.apply(
        lambda row: row["Outstanding_curr"] - row["Outstanding_prev"] if row["Accident_Period"] != accounting_period else 0,
        axis=1,
    )
    merged["PY SS"] = merged.apply(
        lambda row: row["SS_curr"] - row["SS_prev"] if row["Accident_Period"] != accounting_period else 0,
        axis=1,
    )
    merged["PY Payment"] = merged.apply(
        lambda row: row["Payment_curr"] - row["Payment_prev"] if row["Accident_Period"] != accounting_period else 0,
        axis=1,
    )
    merged["PY S&S"] = merged.apply(
        lambda row: row["S&S_curr"] - row["S&S_prev"] if row["Accident_Period"] != accounting_period else 0,
        axis=1,
    )
    merged["PY ULAE"] = merged.apply(
        lambda row: row["ULAE_curr"] - row["ULAE_prev"] if row["Accident_Period"] != accounting_period else 0,
        axis=1,
    )
    merged["PY RA (OS)"] = merged.apply(
        lambda row: row["RA (OS)_curr"] - row["RA (OS)_prev"] if row["Accident_Period"] != accounting_period else 0,
        axis=1,
    )
    merged["PY RA (IBNR)"] = merged.apply(
        lambda row: row["RA (IBNR)_curr"] - row["RA (IBNR)_prev"] if row["Accident_Period"] != accounting_period else 0,
        axis=1,
    )
    merged["PY Discounting Impact"] = merged.apply(
        lambda row: row["Discounting Impact_curr"] - row["Discounting Impact_prev"] if row["Accident_Period"] != accounting_period else 0,
        axis=1,
    )
    merged["PY O/S"] = merged["PY OS"] + merged["PY SS"]
    merged["PY IBNR"] = merged["PY Payment"] + merged["PY S&S"]
    merged["Insurance Finance (Income)/Expense"] = (
        merged["Discounting Impact_curr"] - merged["Discounting Impact_prev"]
    )
    merged["Unwinding of Discount"] = (
        merged["Insurance Finance (Income)/Expense"] - merged["Change in Discounting Impact"]
    )
    merged.drop(columns=["CY OS", "CY SS", "CY Payment", "CY S&S", "PY OS", "PY SS", "PY Payment", "PY S&S"], inplace=True, errors="ignore")
    return merged


def create_ifrs_summary(diff_df: pd.DataFrame, combined_summary: pd.DataFrame) -> pd.DataFrame:
    combined_summary = combined_summary.rename(
        columns={
            "Gross UPR": "Gross UPR_curr",
            "DAC": "DAC_curr",
            "RI UPR": "RI UPR_curr",
            "UCR": "UCR_curr",
        }
    )
    id_cols = ["RESERVINGCLASS", "UWY", "GROSS/RI"]
    movement_cols = [
        col
        for col in diff_df.columns
        if col not in id_cols and pd.api.types.is_numeric_dtype(diff_df[col])
    ]
    movement_agg = (
        diff_df.groupby(["RESERVINGCLASS", "UWY", "GROSS/RI"])[movement_cols].sum().reset_index()
    )
    movement_pivot = movement_agg.pivot_table(
        index=["RESERVINGCLASS", "UWY"], columns="GROSS/RI", values=movement_cols, aggfunc="sum"
    )
    movement_pivot.columns = [f"{col[1]} - {col[0]}" for col in movement_pivot.columns]
    movement_pivot.reset_index(inplace=True)
    combined_summary["UWY"] = combined_summary["UWY"].astype(int)
    ifrs_summary = combined_summary.merge(
        movement_pivot, on=["RESERVINGCLASS", "UWY"], how="left"
    )
    ifrs_summary.drop(
        columns=[c for c in ["GROSS - Accident_Period", "RI - Accident_Period"] if c in ifrs_summary.columns],
        inplace=True,
        errors="ignore",
    )
    combined_cols = combined_summary.columns.tolist()
    move_cols = [c for c in ifrs_summary.columns if c not in combined_cols]
    return ifrs_summary[combined_cols + move_cols]


def run_module2_allocate(combined_summary_bytes: bytes) -> dict[str, Any]:
    out_bytes, ulr_rows, _ = _build_allocate_outputs(combined_summary_bytes, None)
    return {"workbook_bytes": out_bytes, "ulr_rows": ulr_rows}


def _derive_cy_py_payment(
    current_main: pd.DataFrame, previous_df: pd.DataFrame, accounting_period: int
) -> pd.DataFrame:
    """Derive the current-year / prior-year *Paid* split per (class, UWY) for the
    movement disclosure, WITHOUT touching the shared process output.

    ``pivot_and_calculate_differences`` drops ``CY Payment`` / ``PY Payment`` from
    ``result_df`` (engine.py — they never reach the "Movement Analysis" / "IFRS
    Summary" sheets), so changing that drop would alter bit-identical process output.
    Instead we recompute just these two measures here from the same raw frames and
    return an additive `(RESERVINGCLASS, UWY)` frame the movement layer merges into
    its own working copy. Replicates the engine's accident-year classification so the
    numbers match what the un-dropped columns would have been.
    """
    keys = ["RESERVINGCLASS", "UWY", "Accident_Period", "GROSS/RI"]
    cur = current_main.copy()
    prev = previous_df.copy()
    for df in (cur, prev):
        df["Accident_Period"] = (
            df["Accident_Period"].fillna("0-0").astype(str).apply(lambda x: int(x.split("-")[0]))
        )
    cur["CY Payment"] = cur.apply(
        lambda r: r["Payment"] if r["Accident_Period"] == accounting_period else 0, axis=1
    )
    cur_p = cur.pivot_table(index=keys, values=["Payment", "CY Payment"], aggfunc="sum").reset_index()
    prev_p = prev.pivot_table(index=keys, values=["Payment"], aggfunc="sum").reset_index()
    merged = cur_p.merge(prev_p, on=keys, how="outer", suffixes=("_curr", "_prev")).fillna(0)
    merged["PY Payment"] = merged.apply(
        lambda r: r["Payment_curr"] - r["Payment_prev"] if r["Accident_Period"] != accounting_period else 0,
        axis=1,
    )
    agg = (
        merged.groupby(["RESERVINGCLASS", "UWY", "GROSS/RI"])[["CY Payment", "PY Payment"]]
        .sum()
        .reset_index()
    )
    piv = agg.pivot_table(
        index=["RESERVINGCLASS", "UWY"], columns="GROSS/RI", values=["CY Payment", "PY Payment"], aggfunc="sum"
    )
    # Match the IFRS Summary naming: "<GROSS/RI> - <measure>" e.g. "GROSS - CY Payment".
    piv.columns = [f"{col[1]} - {col[0]}" for col in piv.columns]
    piv = piv.reset_index().fillna(0)
    piv["UWY"] = piv["UWY"].astype(int)
    return piv


@dataclass(frozen=True)
class ProcessFrames:
    """In-memory intermediates produced by the Module 2 process pipeline.

    Shared source of truth for both the standard process workbook and the IFRS 17
    movement disclosure (module2_engine.movement). Reusing these frames — rather
    than re-reading the serialized output — is required: the xlsx round-trip
    normalises dtypes and the movement/IFRS math is sensitive to it (see the NB in
    ``_process_intermediates``).
    """

    allocate_sheets: dict[str, "pd.DataFrame"]
    result_df: "pd.DataFrame"  # -> "Movement Analysis" sheet
    ifrs_summary_df: "pd.DataFrame"  # -> "IFRS Summary" sheet (incl. UPR + expense merges)
    # Additive, movement-only: CY/PY Paid split per (class, UWY). Not written to the
    # process workbook — keeps process output bit-identical. None for legacy callers.
    cy_py_payment: "pd.DataFrame | None" = None


def _process_intermediates(
    combined_summary_bytes: bytes,
    previous_period_bytes: bytes,
    expense_cf_bytes: bytes,
    accounting_period: int,
    selected_ulr_rows: list[dict[str, Any]],
) -> ProcessFrames:
    """Run the Module 2 process pipeline up to the IFRS Summary frame.

    Extracted from ``run_module2_process`` so the movement disclosure can consume
    the same in-memory frames without the dtype-sensitive xlsx round-trip.
    """
    # Rebuild allocate outputs with selected ULR; reuse the in-memory frames
    # instead of re-reading the workbook we just produced (plan §2.2).
    out_bytes, _, allocate_sheets = _build_allocate_outputs(combined_summary_bytes, selected_ulr_rows)
    # NB: read these two back from the serialized workbook rather than reusing the
    # in-memory frames. The xlsx round-trip normalises dtypes, and the downstream
    # movement/IFRS math is sensitive to that — reusing in-memory frames here
    # changed several computed cells (caught by the golden net). The final-workbook
    # *write* below still reuses allocate_sheets directly, which is bit-identical.
    current_main = _read_excel_any(out_bytes, "MainSheet")
    combined_summary = _read_excel_any(out_bytes, "Combined Summary")
    previous_df = _read_required_sheet(previous_period_bytes, "LIC_BOP")
    _require_columns(
        previous_df,
        "LIC_BOP",
        ["RESERVINGCLASS", "UWY", "Accident_Period", "GROSS/RI", "Outstanding", "SS", "Payment", "S&S", "ULAE", "RA (OS)", "RA (IBNR)", "Discounting Impact"],
    )
    previous_upr = _read_required_sheet(
        previous_period_bytes,
        "UPR-DAC_BOP",
        usecols=["RESERVINGCLASS", "UWY", "Gross UPR", "DAC", "RI UPR", "UCR"],
    )
    _require_columns(
        previous_upr,
        "UPR-DAC_BOP",
        ["RESERVINGCLASS", "UWY", "Gross UPR", "DAC", "RI UPR", "UCR"],
    )
    expense_cf = _read_required_sheet(expense_cf_bytes, "Expense-CF")
    _require_columns(expense_cf, "Expense-CF", ["RESERVINGCLASS", "UWY"])
    expense_cf["UWY"] = expense_cf["UWY"].astype(int)
    result_df = pivot_and_calculate_differences(current_main, previous_df, int(accounting_period))
    ifrs_summary_df = create_ifrs_summary(result_df, combined_summary)
    prev_upr = previous_upr.rename(
        columns={
            "Gross UPR": "Gross UPR_prev",
            "DAC": "DAC_prev",
            "RI UPR": "RI UPR_prev",
            "UCR": "UCR_prev",
        }
    )
    ifrs_summary_df = ifrs_summary_df.merge(prev_upr, on=["RESERVINGCLASS", "UWY"], how="left")
    ifrs_summary_df = ifrs_summary_df.merge(expense_cf, on=["RESERVINGCLASS", "UWY"], how="left")
    # Movement-only side frame; process output is unaffected.
    cy_py_payment = _derive_cy_py_payment(current_main, previous_df, int(accounting_period))
    return ProcessFrames(
        allocate_sheets=allocate_sheets,
        result_df=result_df,
        ifrs_summary_df=ifrs_summary_df,
        cy_py_payment=cy_py_payment,
    )


def run_module2_process(
    combined_summary_bytes: bytes,
    previous_period_bytes: bytes,
    expense_cf_bytes: bytes,
    accounting_period: int,
    selected_ulr_rows: list[dict[str, Any]],
) -> bytes:
    frames = _process_intermediates(
        combined_summary_bytes,
        previous_period_bytes,
        expense_cf_bytes,
        accounting_period,
        selected_ulr_rows,
    )
    allocate_sheets = frames.allocate_sheets
    result_df = frames.result_df
    ifrs_summary_df = frames.ifrs_summary_df

    def create_lrc_table(df: pd.DataFrame, components_dict: dict[str, int]) -> pd.DataFrame:
        table = df.groupby("RESERVINGCLASS")[list(components_dict.keys())].sum().reset_index()
        for col, sign in components_dict.items():
            table[col] = table[col] * sign
        table["Gross LRC"] = table[list(components_dict.keys())].sum(axis=1)
        return table

    prev_components = {
        "Gross UPR_prev": 1,
        "Rec_GOP_prev": -1,
        "DAC_prev": -1,
        "Comm_Payable_prev": 1,
        "Rec_Provision_prev": 1,
    }
    curr_components = {
        "Gross UPR_curr": 1,
        "Rec_GOP_curr": -1,
        "DAC_curr": -1,
        "Comm_Payable_curr": 1,
        "Rec_Provision_curr": 1,
    }
    lrc_prev = create_lrc_table(ifrs_summary_df, prev_components)
    lrc_curr = create_lrc_table(ifrs_summary_df, curr_components)
    lrc_prev_with_header = pd.DataFrame([["LRC BOP"] + [""] * (len(lrc_prev.columns) - 1)], columns=lrc_prev.columns)
    lrc_prev_with_header = pd.concat([lrc_prev_with_header, lrc_prev], ignore_index=True)
    lrc_curr_with_header = pd.DataFrame([["LRC EOP"] + [""] * (len(lrc_curr.columns) - 1)], columns=lrc_curr.columns)
    lrc_curr_with_header = pd.concat([lrc_curr_with_header, lrc_curr], ignore_index=True)
    startrow_lrc_curr = len(lrc_prev_with_header) + 2

    def create_lic_table(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
        lic_cols = [
            f"GROSS - Outstanding_{suffix}",
            f"GROSS - SS_{suffix}",
            f"GROSS - Payment_{suffix}",
            f"GROSS - S&S_{suffix}",
            f"GROSS - ULAE_{suffix}",
            f"GROSS - Discounting Impact_{suffix}",
            f"Claim_Pay_{suffix}",
            f"GROSS - RA (OS)_{suffix}",
            f"GROSS - RA (IBNR)_{suffix}",
        ]
        table = df.groupby("RESERVINGCLASS")[lic_cols].sum().reset_index()
        table["GROSS LIC"] = table[lic_cols].sum(axis=1)
        return table

    lic_prev = create_lic_table(ifrs_summary_df, "prev")
    lic_curr = create_lic_table(ifrs_summary_df, "curr")
    lic_prev_with_header = pd.DataFrame([["LIC BOP"] + [""] * (len(lic_prev.columns) - 1)], columns=lic_prev.columns)
    lic_prev_with_header = pd.concat([lic_prev_with_header, lic_prev], ignore_index=True)
    lic_curr_with_header = pd.DataFrame([["LIC EOP"] + [""] * (len(lic_curr.columns) - 1)], columns=lic_curr.columns)
    lic_curr_with_header = pd.concat([lic_curr_with_header, lic_curr], ignore_index=True)
    startrow_lic_curr = len(lic_prev_with_header) + 2

    final = io.BytesIO()
    # xlsxwriter handles the two-tables-per-sheet (startrow) reconciliation layout.
    with pd.ExcelWriter(final, engine=WRITE_ENGINE) as writer:
        # include allocate workbook sheets first (reuse in-memory frames instead of
        # re-parsing out_bytes once per sheet — plan §2.2)
        for sheet_name, frame in allocate_sheets.items():
            frame.to_excel(writer, index=False, sheet_name=sheet_name)
        result_df.to_excel(writer, index=False, sheet_name="Movement Analysis")
        ifrs_summary_df.to_excel(writer, index=False, sheet_name="IFRS Summary")
        lrc_prev_with_header.to_excel(writer, index=False, sheet_name="LRC BOP-EOP Reconciliation", startrow=0)
        lrc_curr_with_header.to_excel(writer, index=False, sheet_name="LRC BOP-EOP Reconciliation", startrow=startrow_lrc_curr)
        lic_prev_with_header.to_excel(writer, index=False, sheet_name="LIC BOP-EOP Reconciliation", startrow=0)
        lic_curr_with_header.to_excel(writer, index=False, sheet_name="LIC BOP-EOP Reconciliation", startrow=startrow_lic_curr)
    return final.getvalue()


def run_module2_movement(
    combined_summary_bytes: bytes,
    previous_period_bytes: bytes,
    expense_cf_bytes: bytes,
    accounting_period: int,
    selected_ulr_rows: list[dict[str, Any]],
    *,
    reporting_date: str | None = None,
    classes: list[str] | None = None,
    uwys: list[int] | None = None,
) -> tuple[bytes, Any]:
    """Produce the IFRS 17 movement-analysis disclosure workbook.

    Reuses the same in-memory ``ProcessFrames`` as ``run_module2_process`` (no
    dtype-sensitive round-trip), projects them into the per-(class, UWY) SAMA
    Gross + RI tables, and renders the workbook. Returns ``(xlsx_bytes, result)``
    where ``result`` is the MovementResult (carries warnings / missing columns).
    Mapping is PROPOSED pending actuarial sign-off (plan gate #1).
    """
    # Local imports: keep the movement package out of the engine import graph
    # until actually used (it pulls schema/mapping JSON at import).
    from module2_engine.movement.compute import build_sama_movement
    from module2_engine.movement.workbook import render_sama_workbook

    frames = _process_intermediates(
        combined_summary_bytes,
        previous_period_bytes,
        expense_cf_bytes,
        accounting_period,
        selected_ulr_rows,
    )
    result = build_sama_movement(frames, classes=classes, uwys=uwys)
    xlsx = render_sama_workbook(result, reporting_date=reporting_date)
    return xlsx, result
