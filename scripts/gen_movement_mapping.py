"""Generate module2_engine/movement/mapping_source.json from schema_source.json.

The mapping says, for each disclosure line, where its value comes from: a column of
the Module-2 `IFRS Summary` frame (tier D), a derived formula over such columns (Δ),
or manual/override (M). `{p}` = `_prev` (opening) / `_curr` (closing); `{P}` = `_PY` /
`_CY`. Gross "source" names the GROSS column; the RI sheet uses the parallel `RI …`
column shown in its own table below.

⚠️ PROPOSED — requires actuarial sign-off before it is authoritative (plan §1, gate #1).
Run: python scripts/gen_movement_mapping.py
"""

import json
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "module2_engine" / "movement"
SCHEMA = json.loads((PKG / "schema_source.json").read_text(encoding="utf-8"))
OUT = PKG / "mapping_source.json"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


# label(normalized) -> (tier, source-expr | None).  tier in {D, Δ, M, residual}.
GROSS = {
    "upr": ("D", "Gross UPR_{p}"),
    "premiumreceivable": ("M", None),
    "dac": ("D", "-DAC_{p}"),
    "commissionpayable": ("D", "Comm_Payable_{p}"),
    "premiumsreceivedinadvance": ("M", None),
    "premiumdeficiencyreserve": ("D", "LC Discounted_{P}"),
    "osclaims": ("D", "GROSS - Outstanding_{p}"),
    "ibnr": ("Δ", "GROSS - Payment_{p} + GROSS - S&S_{p}"),
    "ulae": ("D", "GROSS - ULAE_{p}"),
    "ssos": ("D", "GROSS - SS_{p}"),
    "ssibnr": ("D", "GROSS - S&S_{p}"),
    "claimspayablepaymentsinpipeline": ("D", "Claim_Pay_{p}"),
    "prudencemargininactuarialbe": ("M", None),
    "mgmtmarginoveractuarialbe": ("M", None),
    "discountingimpact": ("D", "GROSS - Discounting Impact_{p}"),
    "advancepaymentstootherpartieshospitalsetc": ("M", None),
    "provisionfordoubtfuldebt": ("M", None),
    "provisionforprofitcommissionpayabletopolicyholders": ("M", None),
    "othermethodologydiff": ("residual", None),
    "riskadjustmentra": ("D", "GROSS - RA (OS)_{p} + GROSS - RA (IBNR)_{p}"),
    "insurancerevenue": ("subtotal", None),
    "writtenpremium": ("D", "GWP"),
    "changeinunearnedpremiumreserves": ("Δ", "-(Gross UPR_curr - Gross UPR_prev)"),
    "changeinpremiumdebtorsprovisionnetofwriteoffs": ("M", None),
    "insuranceserviceexpenses": ("subtotal", None),
    "incurredclaimsandotherexpenses": ("subtotal", None),
    "incurredincypaidincy": ("Δ", "GROSS - CY Payment"),
    "incurredincyosatendcy": ("D", "GROSS - CY O/S"),
    "incurredincyibnratendcy": ("D", "GROSS - CY IBNR"),
    "ulaeforosibnratendcy": ("D", "GROSS - CY ULAE"),
    "directlyattributableexpensesexcludinginsuranceacquisitioncashflows": ("D", "Directly Attributable Expenses, excluding Insurance Acquisition cash flows"),
    "insuranceacquisitioncashflowsonnewcontractsamortizationofinsuranceacquisitioncashflows": ("subtotal", None),
    "commissiononwrittenpremium": ("D", "Commission Expense"),
    "otheracquistioncashflows": ("D", "Other Acquistion Cash Flows"),
    "changeindac": ("Δ", "-(DAC_curr - DAC_prev)"),
    "futureservicelossesononerouscontractsandreversalsofthoselosses": ("subtotal", None),
    "lossessonnewonerouscontracts": ("Δ", "max(LC Discounted_CY - LC Discounted_PY, 0)"),
    "reversaloflossesonexistingonerouscontracts": ("subtotal", None),
    "reversalamortizationoflossesfollowinganassumedpatttern": ("Δ", "min(LC Discounted_CY - LC Discounted_PY, 0)"),
    "changeinassumptionsaffectingonerosity": ("M", None),
    "pastservicechangestoliabilitiesforincurredclaims": ("subtotal", None),
    "changeinultimateforpastservice": ("subtotal", None),
    "paidincy": ("Δ", "GROSS - PY Payment"),
    "changeinosincy": ("D", "GROSS - PY O/S"),
    "changeinibnrincy": ("D", "GROSS - PY IBNR"),
    "changeinulaeforosibnrclaims": ("D", "GROSS - PY ULAE"),
    "investmentcomponents": ("M", None),
    "changeinprofitcommission": ("M", None),
    "insuranceserviceresult": ("subtotal", None),
    "insurancefinanceexpensesincome": ("D", "GROSS - Insurance Finance (Income)/Expense + GROSS - Unwinding of Discount"),
    "insurancefinanceexpensesincomepl": ("M", None),
    "insurancefinanceexpensesincomeoci": ("M", None),
    "effectofmovementsinexchangerates": ("M", None),
    "othermovements": ("subtotal", None),
    "item1specify": ("M", None),
    "item2specify": ("M", None),
    "totalchangesinthestatementofprofitorlossandoci": ("subtotal", None),
    "cashflows": ("section", None),
    "premiumreceived": ("D", "Premium Received"),
    "claimspaid": ("D", "Claims Paid"),
    "directlyattributableexpensespaidexcludinginsuranceacquisitioncashflows": ("D", "Directly Attributable Expenses, excluding Insurance Acquisition cash flows"),
    "insuranceacquisitioncashflows": ("D", "Insurance Acquisition Cash flows"),
    "othercashflows": ("D", "Other Cash Flows"),
    "totalcashflows": ("subtotal", None),
    "insurancecontractliabilitiesassetsasat0101": ("opening", None),
    "insurancecontractliabilitiesassetsasat3006": ("closing", None),
}

RI = {
    "upr": ("D", "RI UPR_{p}"),
    "premiumpayable": ("M", None),
    "dac": ("D", "RI Commission"),
    "commissionreceivable": ("D", "RI Rec Provision_{p}"),
    "premiumspaidinadvance": ("M", None),
    "osclaims": ("D", "RI - Outstanding_{p}"),
    "ibnr": ("Δ", "RI - Payment_{p} + RI - S&S_{p}"),
    "ssos": ("D", "RI - SS_{p}"),
    "ssibnr": ("D", "RI - S&S_{p}"),
    "claimsreceivableinpipeline": ("D", "RI_Payable_{p}"),
    "prudencemargininactuarialbe": ("M", None),
    "mgmtmarginoveractuarialbe": ("M", None),
    "discounting": ("D", "RI - Discounting Impact_{p}"),
    "provisionfornonperformancerisk": ("D", "RI_Rec_GOP_{p}"),
    "provisionforprofitcommissionslidingscalecommissionfromreinsurers": ("M", None),
    "othermethodologydiff": ("residual", None),
    "amountsallocatedtoreinsurance": ("subtotal", None),
    "cededpremium": ("D", "RI GWP"),
    "changeinriunearnedpremiumreserves": ("Δ", "-(RI UPR_curr - RI UPR_prev)"),
    "reinsurancefixedcommission": ("D", "RI Commission"),
    "changeinriunearnedfixedcommission": ("M", None),
    "amountsrecoverablefromreinsurance": ("subtotal", None),
    "incurredclaimsandotherexpenses": ("subtotal", None),
    "incurredincypaidincy": ("Δ", "RI - CY Payment"),
    "incurredincyosatendcy": ("D", "RI - CY O/S"),
    "incurredincyibnratendcy": ("D", "RI - CY IBNR"),
    "provisionforriskofnonperformanceonnewclaims": ("D", "RI_Rec_GOP_{p}"),
    "futureservicelrcfornewonerouscontractsandreversalonexistingonerouscontracts": ("subtotal", None),
    "lossrecoverycomponentfornewunderlyingonerouscontracts": ("Δ", "max(Loss Recovery Component_CY - Loss Recovery Component_PY, 0)"),
    "reversaloflossrecoverycomponentforexistingunderlyingonerouscontracts": ("subtotal", None),
    "reversalamortizationoflrcfollowinganassumedpatttern": ("Δ", "min(Loss Recovery Component_CY - Loss Recovery Component_PY, 0)"),
    "changeinlrcduetochangesinassumptionsforunderlyingonerouscontracts": ("M", None),
    "pastservicechangestoliabilitiesforincurredclaims": ("subtotal", None),
    "changeinultimateforpastservice": ("subtotal", None),
    "paidincy": ("Δ", "RI - PY Payment"),
    "changeinosincy": ("D", "RI - PY O/S"),
    "changeinibnrincy": ("D", "RI - PY IBNR"),
    "changeinprovisionforriskofrinonperformance": ("Δ", "RI_Rec_GOP_curr - RI_Rec_GOP_prev"),
    "investmentcomponents": ("M", None),
    "changeinprofitcommissionslidingscalecommission": ("M", None),
    "reinsuranceserviceresult": ("subtotal", None),
    "reinsurancefinanceexpensesincome": ("D", "RI - Insurance Finance (Income)/Expense + RI - Unwinding of Discount"),
    "reinsurancefinanceexpensesincomepl": ("M", None),
    "reinsurancefinanceexpensesincomeoci": ("M", None),
    "effectofmovementsinexchangerates": ("M", None),
    "othermovements": ("subtotal", None),
    "item1specify": ("M", None),
    "item2specify": ("M", None),
    "totalchangesinthestatementofprofitorlossandoci": ("subtotal", None),
    "cashflows": ("section", None),
    "premiumpaid": ("D", "RI Premium Paid"),
    "claimsreceived": ("D", "RI Claims received"),
    "fixedcommissionreceived": ("D", "RI Fixed Commission received"),
    "profitcommissionslidingscalecommissionreceived": ("M", None),
    "othercashflows": ("D", "Other Cash Flows"),
    "totalcashflows": ("subtotal", None),
    "riskadjustment": ("D", "RI - RA (OS)_{p} + RI - RA (IBNR)_{p}"),
    "reinsurancecontractassetsliabilitiesasat0101": ("opening", None),
    "reinsurancecontractassetsliabilitiesasat3006": ("closing", None),
}

TABLES = {"Gross": GROSS, "RI": RI}
STRUCTURAL = {"subtotal", "opening", "closing", "section"}


def build():
    out = {
        "_meta": {
            "status": "PROPOSED — requires actuarial sign-off (plan gate #1)",
            "source": "Module-2 IFRS Summary frame (111 cols) + LC frame",
            "legend": "tier D=direct column, Δ=derived, M=manual/override, residual=balancing; "
            "{p}=_prev/_curr, {P}=_PY/_CY. RI uses the parallel 'RI …' column.",
            "schema_version": SCHEMA["schema_version"],
        }
    }
    for sheet, table in TABLES.items():
        lines = {}
        cov = {"D": 0, "Δ": 0, "M": 0, "residual": 0, "structural": 0, "unmapped": 0}
        for ln in SCHEMA["sheets"][sheet]["lines"]:
            spec = table.get(norm(ln["label"]))
            tier, source = spec if spec else ("unmapped", None)
            bucket = "structural" if tier in STRUCTURAL else (tier if tier in cov else "unmapped")
            cov[bucket] += 1
            if tier != "structural" or spec is None:  # keep structural lines too, for completeness
                lines[ln["id"]] = {"label": ln["label"], "kind": ln["kind"], "tier": tier, "source": source}
            else:
                lines[ln["id"]] = {"label": ln["label"], "kind": ln["kind"], "tier": tier, "source": None}
        out[sheet] = {"coverage": cov, "lines": lines}
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    for sheet in TABLES:
        c = out[sheet]["coverage"]
        value = c["D"] + c["Δ"] + c["M"] + c["residual"] + c["unmapped"]
        backed = c["D"] + c["Δ"]
        unmapped = [
            lid for lid, v in out[sheet]["lines"].items() if v["tier"] == "unmapped"
        ]
        print(
            f"{sheet}: {len(out[sheet]['lines'])} lines | D={c['D']} Δ={c['Δ']} M={c['M']} "
            f"residual={c['residual']} structural={c['structural']} unmapped={c['unmapped']} "
            f"| data-backed {backed}/{value} value-lines"
        )
        if unmapped:
            print(f"   UNMAPPED {sheet}: {unmapped}")
    print("wrote", OUT)


if __name__ == "__main__":
    build()
