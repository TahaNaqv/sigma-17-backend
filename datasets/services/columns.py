"""Column name mappings between the snake_case DB schema and the
mixed-case headers the actuarial engine reads from Excel.

Single source of truth — both the import service (Excel → DB) and the
engine adapter (DB → Excel) reference these maps so neither can drift.
"""

from ..models import Dataset


# DB column name → Excel header. Keys mirror Django field names on the
# row models; values mirror what `engine.py` looks up in `df.columns`.
PREMIUM_DB_TO_EXCEL = {
    "policy_number": "POLICYNUMBER",
    "policy_start_date": "POLICYSTARTDATE",
    "policy_end_date": "POLICYENDDATE",
    "risk_start_date": "RiskStartDate",
    "risk_end_date": "RiskEndDate",
    "issue_date": "ISSUEDATE",
    "reserving_class": "RESERVINGCLASS",
    "policy_class": "POLICYCLASS",
    "product_type": "PRODUCTTYPE",
    "ri_treaty_type": "RI_TREATY_TYPE",
    "premium_amount": "PREMIUMAMOUNT",
    "commission_amount": "COMMISSIONAMOUNT",
}

CLAIMS_PAID_DB_TO_EXCEL = {
    "amount_paid": "AMOUNTPAID",
    "amount_recovered": "AMOUNTRECOVERED",
    "issue_date": "ISSUEDATE",
    "loss_date": "LOSSDATE",
    "payment_date": "PAYMENTDATE",
    "reserving_class": "RESERVINGCLASS",
    "policy_class": "POLICYCLASS",
    "ri_treaty_type": "RI_TREATY_TYPE",
    "head_of_damage": "HEADOFDAMAGE",
}

CLAIMS_OS_DB_TO_EXCEL = {
    "amount_outstanding": "AMOUNTOUTSTANDING",
    "issue_date": "ISSUEDATE",
    "loss_date": "LOSSDATE",
    "as_at": "As at",  # engine reads this header literally, space and all
    "reserving_class": "RESERVINGCLASS",
    "policy_class": "POLICYCLASS",
    "ri_treaty_type": "RI_TREATY_TYPE",
    "head_of_damage": "HEADOFDAMAGE",
}

EXPENSE_CF_DB_TO_EXCEL = {
    "reserving_class": "RESERVINGCLASS",
    "uwy": "UWY",
    "comm_payable_prev": "Comm_Payable_prev",
    "comm_payable_curr": "Comm_Payable_curr",
    "rec_gop_prev": "Rec_GOP_prev",
    "rec_gop_curr": "Rec_GOP_curr",
    "rec_provision_prev": "Rec_Provision_prev",
    "rec_provision_curr": "Rec_Provision_curr",
    # Cash-flow lines (current period). Headers read verbatim by the engine —
    # note the preserved "Acquistion" typo in "Other Acquistion Cash Flows".
    "premium_received": "Premium Received",
    "claims_paid": "Claims Paid",
    "insurance_acquisition_cash_flows": "Insurance Acquisition Cash flows",
    "other_cash_flows": "Other Cash Flows",
    "ri_premium_paid": "RI Premium Paid",
    "ri_claims_received": "RI Claims received",
    "ri_fixed_commission_received": "RI Fixed Commission received",
    "directly_attributable_expenses": "Directly Attributable Expenses, excluding Insurance Acquisition cash flows",
    "other_acquisition_cash_flows": "Other Acquistion Cash Flows",
    # Prev/curr balance components (LIC pipeline + RI provisions).
    "ri_rec_gop_prev": "RI_Rec_GOP_prev",
    "ri_rec_gop_curr": "RI_Rec_GOP_curr",
    "claim_pay_prev": "Claim_Pay_prev",
    "claim_pay_curr": "Claim_Pay_curr",
    "ri_payable_prev": "RI_Payable_prev",
    "ri_payable_curr": "RI_Payable_curr",
    "ri_rec_provision_prev": "RI Rec Provision_prev",
    "ri_rec_provision_curr": "RI Rec Provision_curr",
}

# LIC_BOP sheet — engine reads these column headers verbatim (with spaces
# and parentheses), so the map preserves them exactly.
PREVIOUS_PERIOD_LIC_DB_TO_EXCEL = {
    "reserving_class": "RESERVINGCLASS",
    "uwy": "UWY",
    "accident_period": "Accident_Period",
    "gross_ri": "GROSS/RI",
    "outstanding": "Outstanding",
    "ss": "SS",
    "payment": "Payment",
    "s_and_s": "S&S",
    "ulae": "ULAE",
    "ra_os": "RA (OS)",
    "ra_ibnr": "RA (IBNR)",
    "discounting_impact": "Discounting Impact",
}

PREVIOUS_PERIOD_UPR_DB_TO_EXCEL = {
    "reserving_class": "RESERVINGCLASS",
    "uwy": "UWY",
    "gross_upr": "Gross UPR",
    "dac": "DAC",
    "ri_upr": "RI UPR",
    "ucr": "UCR",
}

# IFRS 17 movement override inputs. The Excel headers use the disclosure line labels
# (readable for the actuary); the db field name is the movement mapping's override_key.
MOVEMENT_OVERRIDE_DB_TO_EXCEL = {
    "reserving_class": "RESERVINGCLASS",
    "uwy": "UWY",
    "ri_loss_recovery_new_onerous": "Loss Recovery Component for new underlying onerous contracts",
    "ri_loss_recovery_reversal_amortization": "Reversal/amortization of LRC following an assumed pattern",
    "ri_loss_recovery_assumption_change": "Change in LRC due to changes in assumptions for underlying onerous contracts",
    "ri_provision_nonperformance_change": "Change in Provision for risk of RI non-performance",
    "ri_finance_pnl": "Reinsurance finance expenses/income - P&L",
    "ri_pdr_accrual_reserve_bop": "Other methodology diff (PDR) BOP / RI Accrual Reserve BOP",
    "ri_methodology_diff_loss_recovery_bop": "Other methodology diff BOP / Loss Recovery component BOP",
    "ri_accrual_reserve_specify": "RI Accrual Reserve",
}


DB_TO_EXCEL_FOR_KIND = {
    Dataset.Kind.PREMIUM: PREMIUM_DB_TO_EXCEL,
    Dataset.Kind.CLAIMS_PAID: CLAIMS_PAID_DB_TO_EXCEL,
    Dataset.Kind.CLAIMS_OS: CLAIMS_OS_DB_TO_EXCEL,
    Dataset.Kind.EXPENSE_CF: EXPENSE_CF_DB_TO_EXCEL,
    Dataset.Kind.PREVIOUS_PERIOD_LIC: PREVIOUS_PERIOD_LIC_DB_TO_EXCEL,
    Dataset.Kind.PREVIOUS_PERIOD_UPR: PREVIOUS_PERIOD_UPR_DB_TO_EXCEL,
    Dataset.Kind.MOVEMENT_OVERRIDE: MOVEMENT_OVERRIDE_DB_TO_EXCEL,
}


def excel_to_db_for_kind(kind: str) -> dict[str, str]:
    """Inverse mapping for a kind — used by the import service."""
    return {excel: db for db, excel in DB_TO_EXCEL_FOR_KIND[kind].items()}


# Fields that are required (non-empty) on import. Anything else is
# optional and will be left null. The engine itself handles partial data,
# but enforcing these at the boundary catches malformed Excel early.
REQUIRED_FIELDS_FOR_KIND = {
    Dataset.Kind.PREMIUM: (
        "reserving_class",
        "ri_treaty_type",
    ),
    Dataset.Kind.CLAIMS_PAID: (
        "reserving_class",
        "ri_treaty_type",
    ),
    Dataset.Kind.CLAIMS_OS: (
        "reserving_class",
        "ri_treaty_type",
    ),
    Dataset.Kind.EXPENSE_CF: (
        "reserving_class",
        "uwy",
    ),
    Dataset.Kind.PREVIOUS_PERIOD_LIC: (
        "reserving_class",
        "uwy",
        "accident_period",
        "gross_ri",
    ),
    Dataset.Kind.PREVIOUS_PERIOD_UPR: (
        "reserving_class",
        "uwy",
    ),
    Dataset.Kind.MOVEMENT_OVERRIDE: (
        "reserving_class",
        "uwy",
    ),
}
