# IFRS 17 Movement Disclosure — Source Reconciliation (Phase 0 sign-off artifact)

Extracted verbatim from the client file `Module2_Final_Output.xlsx` (sheets `Gross`, `RI`).
This is the authoritative per-`(line, bucket)` source map. `⚠` marks where it differs from our current `mapping_source.json`.

**Source types:** `computed`=from an IFRS Summary column/expression · `OVERRIDE`=manual class×cohort input (override Dataset) · `subtotal`=SUM of children · `manual/0`=judgment line, no source (override candidate).


## Gross

| Row | Line | Bucket | Sign | Source | vs current |
|----:|------|--------|:---:|--------|-----------|
| 6 | Insurance contract liabilities/ (assets) as at 01/01 | LRC excl LC |  | subtotal `=SUM(C7:C25)` |  |
|  |  | Loss Component |  | subtotal `=SUM(E7:E25)` |  |
|  |  | LIC excl RA |  | subtotal `=SUM(G7:G25)` |  |
|  |  | Risk Adjustment |  | subtotal `=SUM(I7:I25)` |  |
| 7 | UPR | LRC excl LC | + | `[Gross UPR_prev]` |  |
| 8 | Premium receivable | LRC excl LC | - | `[Rec_GOP_prev]` | ⚠ was manual |
| 9 | DAC | LRC excl LC | - | `[DAC_prev]` |  |
| 10 | Commission Payable | LRC excl LC | + | `[Comm_Payable_prev]` |  |
| 11 | Premiums received in Advance | LRC excl LC | + | manual/0 |  |
| 12 | Premium Deficiency Reserve | Loss Component | + | manual/0 |  |
| 13 | O/S Claims | LIC excl RA | + | `[GROSS - Outstanding_prev]` |  |
| 14 | IBNR | LIC excl RA | + | `[GROSS - Payment_prev]` |  |
| 15 | ULAE | LIC excl RA | + | `[GROSS - ULAE_prev]` |  |
| 16 | S&S O/S | LIC excl RA | - | `[GROSS - S&S_prev]` |  |
| 17 | S&S IBNR | LIC excl RA | - | `[GROSS - SS_prev]` |  |
| 18 | Claims payable (payments in pipeline) | LIC excl RA | + | `[Claim_Pay_prev]` |  |
| 19 | Prudence Margin in Actuarial BE | LIC excl RA | - | manual/0 |  |
|  |  | Risk Adjustment | + | manual/0 |  |
| 20 | Mgmt Margin over Actuarial BE | Risk Adjustment | + | manual/0 |  |
| 21 | Discounting impact | LRC excl LC | - | manual/0 |  |
|  |  | Loss Component | - | manual/0 |  |
|  |  | LIC excl RA | - | `[GROSS - Discounting Impact_prev]` |  |
|  |  | Risk Adjustment | - | manual/0 |  |
| 22 | Advance payments to Other parties (hospitals, etc.) | LIC excl RA | - | manual/0 |  |
| 23 | Provision for Doubtful Debt | LRC excl LC | + | `[Rec_Provision_prev]` | ⚠ was manual |
|  |  | LIC excl RA | + | manual/0 |  |
| 24 | Provision for Profit Commission payable to policyholders | LIC excl RA | + | manual/0 |  |
| 25 | Other methodology diff | LRC excl LC | +/- | manual/0 |  |
|  |  | Loss Component | +/- | manual const `-10721029.493254356` |  |
|  |  | LIC excl RA | +/- | manual/0 |  |
|  |  | Risk Adjustment | +/- | `[GROSS - RA (OS)_prev]+[GROSS - RA (IBNR)_prev]` |  |
| 26 | Insurance revenue | LRC excl LC |  | subtotal `=SUM(C27:C30)` |  |
|  |  | Loss Component |  | subtotal `=SUM(E27:E30)` |  |
|  |  | LIC excl RA |  | subtotal `=SUM(G27:G30)` |  |
|  |  | Risk Adjustment |  | subtotal `=SUM(I27:I30)` |  |
| 27 | Written premium | LRC excl LC | + | `[GWP]` |  |
| 28 | Change in Unearned Premium Reserves | LRC excl LC | -/+ | `[Gross UPR_curr]-[Gross UPR_prev]` |  |
| 29 | Change in Premium Debtors' Provision (net of write-offs) | LRC excl LC | -/+ | `[Rec_GOP_curr]-[Rec_Provision_prev]` | ⚠ was manual |
| 30 | Other methodology diff | LRC excl LC | -/+ | manual/0 |  |
| 31 | Insurance service expenses | LRC excl LC |  | subtotal `=SUM(C32+C42+C47+C53+C54)` |  |
|  |  | Loss Component |  | subtotal `=SUM(E32+E42+E47+E53+E54)` |  |
|  |  | LIC excl RA |  | subtotal `=SUM(G32+G42+G47+G53+G54)` |  |
|  |  | Risk Adjustment |  | subtotal `=SUM(I32+I42+I47+I53+I54)` |  |
| 32 | Incurred claims and other expenses | LRC excl LC |  | manual const `107844353.35412507` |  |
|  |  | Loss Component |  | manual/0 |  |
|  |  | LIC excl RA |  | manual const `434313688.47299093` |  |
|  |  | Risk Adjustment |  | manual const `6043430.410871526` |  |
| 33 | Incurred in CY, Paid in CY | LIC excl RA | + | `[Gross CY Paid]` |  |
| 34 | Incurred in CY, OS at end-CY | LIC excl RA | + | `[GROSS - CY O/S]` |  |
|  |  | Risk Adjustment | + | `[GROSS - CY RA (OS)]` |  |
| 35 | Incurred in CY, IBNR at end-CY | LIC excl RA | + | `[GROSS - CY IBNR]` |  |
|  |  | Risk Adjustment | + | `[GROSS - CY RA (IBNR)]` |  |
| 36 | ULAE for OS & IBNR at end-CY | LIC excl RA | + | `[GROSS - CY ULAE]` |  |
| 37 | Directly Attributable Expenses, excluding Insurance Acquisition cash flows | LIC excl RA | + | `[Directly Attributable Expenses, excluding Insurance Acquisition cash flows]` |  |
| 38 | Insurance Acquisition cash flows on New Contracts & Amortization of insurance acquisition cash flows | LRC excl LC |  | manual const `107844353.35412507` |  |
|  |  | Loss Component |  | manual/0 |  |
|  |  | LIC excl RA |  | manual/0 |  |
|  |  | Risk Adjustment |  | manual/0 |  |
| 39 | Commission on Written Premium | LRC excl LC | + | `[Commission Expense]` |  |
| 40 | Other Acquistion Cash Flows | LRC excl LC | + | manual/0 |  |
| 41 | Change in DAC | LRC excl LC | +/- | `[DAC_curr]-[DAC_prev]` |  |
| 42 | Future Service: Losses on onerous contracts and reversals of those losses | LRC excl LC |  | manual/0 |  |
|  |  | Loss Component |  | manual const `3503396.9974000826` |  |
|  |  | LIC excl RA |  | manual/0 |  |
|  |  | Risk Adjustment |  | manual/0 |  |
| 43 | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Lossess on new onerous contracts | Loss Component | + | manual/0 |  |
| 44 | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Reversal of losses on existing onerous contracts | LRC excl LC |  | manual/0 |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Loss Component |  | manual const `-10732428.87912421` |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | LIC excl RA |  | manual/0 |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Risk Adjustment |  | manual/0 |  |
| 45 | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Reversal/amortization of losses following an assumed patttern | Loss Component | - | manual/0 |  |
| 46 | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Change in assumptions affecting onerosity | Loss Component | +/- | manual/0 |  |
| 47 | Past Service: Changes to liabilities for incurred claims | LRC excl LC |  | manual/0 |  |
|  |  | Loss Component |  | manual/0 |  |
|  |  | LIC excl RA |  | manual const `-44046641.70156728` |  |
|  |  | Risk Adjustment |  | manual const `-6547731.471386652` |  |
| 48 | Change in Ultimate for Past Service | LRC excl LC |  | manual/0 |  |
|  |  | Loss Component |  | manual/0 |  |
|  |  | LIC excl RA |  | manual const `-31883812.222043484` |  |
|  |  | Risk Adjustment |  | manual const `-6547731.471386652` |  |
| 49 | Paid in CY | LIC excl RA | + | `[Gross PY Paid]` |  |
| 50 | Change in OS in CY | LIC excl RA | +/- | `[GROSS - PY O/S]` |  |
|  |  | Risk Adjustment | +/- | `[GROSS - PY RA (OS)]` |  |
| 51 | Change in IBNR in CY | LIC excl RA | +/- | `[GROSS - PY IBNR]` |  |
|  |  | Risk Adjustment | +/- | `[GROSS - PY RA (IBNR)]` |  |
| 52 | Change in ULAE for O/S & IBNR claims | LIC excl RA | +/- | `[GROSS - PY ULAE]` |  |
| 53 | Other methodology diff | LIC excl RA | +/- | manual/0 |  |
|  |  | Risk Adjustment | +/- | manual/0 |  |
| 54 | Investment components | LRC excl LC |  | manual/0 |  |
|  |  | Loss Component |  | manual/0 |  |
|  |  | LIC excl RA |  | manual/0 |  |
|  |  | Risk Adjustment |  | manual/0 |  |
| 55 | Change in Profit Commission | LIC excl RA | +/- | manual/0 |  |
| 56 | Insurance service result | LRC excl LC |  | subtotal `=C26-C31` |  |
|  |  | Loss Component |  | subtotal `=E26-E31` |  |
|  |  | LIC excl RA |  | subtotal `=G26-G31` |  |
|  |  | Risk Adjustment |  | subtotal `=I26-I31` |  |
| 57 | Insurance finance expenses/income | LRC excl LC |  | manual/0 |  |
|  |  | Loss Component |  | manual/0 |  |
|  |  | LIC excl RA |  | manual const `7853583.018769694` |  |
|  |  | Risk Adjustment |  | manual/0 |  |
| 58 | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Insurance finance expenses/income - P&L | LRC excl LC | +/- | manual/0 |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Loss Component | +/- | manual/0 |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | LIC excl RA | +/- | `[GROSS - Insurance Finance (Income)/Expense]` |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Risk Adjustment | +/- | manual/0 |  |
| 59 | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Insurance finance expenses/income - OCI | LRC excl LC | +/- | manual/0 |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Loss Component | +/- | manual/0 |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | LIC excl RA | +/- | manual/0 |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Risk Adjustment | +/- | manual/0 |  |
| 60 | Effect of movements in exchange rates | LRC excl LC | +/- | manual/0 |  |
|  |  | Loss Component | +/- | manual/0 |  |
|  |  | LIC excl RA | +/- | manual/0 |  |
|  |  | Risk Adjustment | +/- | manual/0 |  |
| 61 | Other movements | LRC excl LC |  | manual/0 |  |
|  |  | Loss Component |  | manual/0 |  |
|  |  | LIC excl RA |  | manual/0 |  |
|  |  | Risk Adjustment |  | manual/0 |  |
| 62 | Item 1 (Specify) | LRC excl LC | +/- | manual/0 |  |
| 63 | Item 2 (Specify) | LRC excl LC | +/- | manual/0 |  |
| 64 | Total changes in the statement of profit or loss and OCI | LRC excl LC |  | subtotal `=C61+C60+C57+C56` |  |
|  |  | Loss Component |  | subtotal `=E61+E60+E57+E56` |  |
|  |  | LIC excl RA |  | subtotal `=G61+G60+G57+G56` |  |
|  |  | Risk Adjustment |  | subtotal `=I61+I60+I57+I56` |  |
| 65 | **Cash flows** _(section)_ | | | | |
| 66 | Premium Received | LRC excl LC | + | `[Premium Received]` |  |
| 67 | Claims paid | LIC excl RA | - | `[Claims Paid]` |  |
| 68 | Directly Attributable Expenses paid (excluding insurance acquisition cash flows) | LIC excl RA | - | `[Directly Attributable Expenses, excluding Insurance Acquisition cash flows]` |  |
| 69 | Insurance Acquisition Cash flows | LRC excl LC | - | `[Insurance Acquisition Cash flows]` |  |
| 70 | Other Cash Flows | LRC excl LC | +/- | `[Other Cash Flows]` |  |
|  |  | LIC excl RA | +/- | manual/0 |  |
| 71 | Total Cash Flows | LRC excl LC |  | subtotal `=SUM(C66:C70)` |  |
|  |  | Loss Component |  | subtotal `=SUM(E66:E70)` |  |
|  |  | LIC excl RA |  | subtotal `=SUM(G66:G70)` |  |
|  |  | Risk Adjustment |  | subtotal `=SUM(I66:I70)` |  |
| 72 | Insurance contract liabilities/(assets) as at 30/06 | LRC excl LC |  | manual const `115381497.72644304` |  |
|  |  | Loss Component |  | manual const `14235825.876524292` |  |
|  |  | LIC excl RA |  | manual const `266384925.43983477` |  |
|  |  | Risk Adjustment |  | manual const `8872606.387292383` |  |

## RI

| Row | Line | Bucket | Sign | Source | vs current |
|----:|------|--------|:---:|--------|-----------|
| 4 | Reinsurance contract assets/(liabilities) as at 01/01 | Assets Remaining Coverage |  | subtotal `=SUM(D5:D20)` |  |
|  |  | Loss Recovery Component |  | subtotal `=SUM(F5:F20)` |  |
|  |  | Amounts Recoverable IC |  | subtotal `=SUM(H5:H20)` |  |
|  |  | Risk Adjustment |  | subtotal `=SUM(J5:J20)` |  |
| 5 | UPR | Assets Remaining Coverage | + | `[RI UPR_prev]` |  |
| 6 | Premium payable | Assets Remaining Coverage | - | `[RI Premium Paid]` | ⚠ was manual |
| 7 | DAC | Assets Remaining Coverage | - | `[UCR_prev]` |  |
| 8 | Commission receivable | Assets Remaining Coverage | + | manual/0 |  |
| 9 | Premiums Paid in Advance | Assets Remaining Coverage | + | manual/0 |  |
| 10 | O/S Claims | Amounts Recoverable IC | + | `[RI - Outstanding_prev]` |  |
| 11 | IBNR | Amounts Recoverable IC | + | `[RI - Payment_prev]` |  |
| 12 | S&S O/S | Amounts Recoverable IC | - | `[RI - S&S_prev]` |  |
| 13 | S&S IBNR | Amounts Recoverable IC | - | `[RI - SS_prev]` |  |
| 14 | Claims receivable (in pipeline) | Amounts Recoverable IC | + | `[RI_Rec_GOP_prev]` |  |
| 15 | Prudence Margin in Actuarial BE | Amounts Recoverable IC | - | manual/0 |  |
|  |  | Risk Adjustment | + | manual/0 |  |
| 16 | Mgmt Margin over Actuarial BE | Risk Adjustment | + | manual/0 |  |
| 17 | Discounting | Assets Remaining Coverage | - | manual/0 |  |
|  |  | Loss Recovery Component | - | manual/0 |  |
|  |  | Amounts Recoverable IC | - | `[RI - Discounting Impact_prev]` |  |
|  |  | Risk Adjustment | - | manual/0 |  |
| 18 | Provision for non-performance risk | Assets Remaining Coverage | - | manual/0 |  |
|  |  | Amounts Recoverable IC | - | `[RI Rec Provision_prev]` |  |
| 19 | Provision for Profit Commission /Sliding scale commission from Reinsurers | Amounts Recoverable IC | + | manual/0 |  |
| 20 | Other methodology diff | Assets Remaining Coverage | +/- | manual/0 |  |
|  |  | Loss Recovery Component | +/- | **OVERRIDE** → `DX` (Methodology diff BOP / Loss Recovery BOP) |  |
|  |  | Amounts Recoverable IC | +/- | **OVERRIDE** → `DV` (PDR BOP / RI Accrual Reserve BOP) |  |
|  |  | Risk Adjustment | +/- | `[RI - RA (OS)_curr]+[RI - RA (IBNR)_prev]` |  |
| 21 | Amounts Allocated to Reinsurance | Assets Remaining Coverage |  | subtotal `=SUM(D22:D26)` |  |
|  |  | Loss Recovery Component |  | subtotal `=SUM(F22:F26)` |  |
|  |  | Amounts Recoverable IC |  | subtotal `=SUM(H22:H26)` |  |
|  |  | Risk Adjustment |  | subtotal `=SUM(J22:J26)` |  |
| 22 | Ceded premium | Assets Remaining Coverage | + | `[RI GWP]` |  |
| 23 | Change in RI Unearned Premium Reserves | Assets Remaining Coverage | +/- | `[RI UPR_curr]-[RI UPR_prev]` |  |
| 24 | Reinsurance (fixed) commission | Assets Remaining Coverage | - | `[RI Commission]` |  |
| 25 | Change in RI unearned (fixed) commission | Assets Remaining Coverage | +/- | `[UCR_curr]-[UCR_prev]` | ⚠ was manual |
| 26 | Other methodology diff | Assets Remaining Coverage | +/- | manual/0 |  |
| 27 | Amounts Recoverable from Reinsurance | Assets Remaining Coverage |  | subtotal `=SUM(D28+D33+D38+D44+D45)` |  |
|  |  | Loss Recovery Component |  | subtotal `=SUM(F28+F33+F38+F44+F45)` |  |
|  |  | Amounts Recoverable IC |  | subtotal `=SUM(H28+H33+H38+H44+H45)` |  |
|  |  | Risk Adjustment |  | subtotal `=SUM(J28+J33+J38+J44+J45)` |  |
| 28 | Incurred claims and other expenses | Assets Remaining Coverage |  | subtotal `=SUM(D29:D32)` |  |
|  |  | Loss Recovery Component |  | subtotal `=SUM(F29:F32)` |  |
|  |  | Amounts Recoverable IC |  | subtotal `=SUM(H29:H32)` |  |
|  |  | Risk Adjustment |  | subtotal `=SUM(J29:J32)` |  |
| 29 | Incurred in CY, Paid in CY | Amounts Recoverable IC | + | `[RI CY Paid]` |  |
| 30 | Incurred in CY, OS at end-CY | Amounts Recoverable IC | + | `[RI - CY O/S]` |  |
|  |  | Risk Adjustment | + | `[RI - CY RA (OS)]` |  |
| 31 | Incurred in CY, IBNR at end-CY | Amounts Recoverable IC | + | `[RI - CY IBNR]` |  |
|  |  | Risk Adjustment | + | `[RI - CY RA (IBNR)]` |  |
| 32 | Provision for risk of non-performance on new claims | Amounts Recoverable IC | - | manual/0 |  |
| 33 | Future Service: LRC for new onerous contracts and reversal on existing onerous contracts | Assets Remaining Coverage |  | subtotal `=SUM(D34:D35)` |  |
|  |  | Loss Recovery Component |  | subtotal `=SUM(F34:F35)` |  |
|  |  | Amounts Recoverable IC |  | subtotal `=SUM(H34:H35)` |  |
|  |  | Risk Adjustment |  | subtotal `=SUM(J34:J35)` |  |
| 34 | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Loss Recovery Component for new underlying onerous contracts | Loss Recovery Component | + | **OVERRIDE** → `BI` (Loss Recovery Component (new onerous)) | ⚠ override input |
| 35 | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Reversal of Loss Recovery Component for existing underlying  onerous contracts | Assets Remaining Coverage |  | subtotal `=SUM(D36:D37)` |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Loss Recovery Component |  | subtotal `=SUM(F36:F37)` |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Amounts Recoverable IC |  | subtotal `=SUM(H36:H37)` |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Risk Adjustment |  | subtotal `=SUM(J36:J37)` |  |
| 36 | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Reversal/amortization of LRC following an assumed patttern | Loss Recovery Component | - | **OVERRIDE** → `BJ` (Reversal/amortization of LRC) | ⚠ override input |
| 37 | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Change in LRC due to changes in assumptions for underlying onerous contracts | Loss Recovery Component | +/- | **OVERRIDE** → `BK` (Change in LRC (assumptions)) | ⚠ override input |
| 38 | Past Service: Changes to liabilities for incurred claims | Assets Remaining Coverage |  | subtotal `=D39+D43` |  |
|  |  | Loss Recovery Component |  | subtotal `=F39+F43` |  |
|  |  | Amounts Recoverable IC |  | subtotal `=H39+H43` |  |
|  |  | Risk Adjustment |  | subtotal `=J39+J43` |  |
| 39 | Change in Ultimate for Past Service | Assets Remaining Coverage |  | subtotal `=SUM(D40:D42)` |  |
|  |  | Loss Recovery Component |  | subtotal `=SUM(F40:F42)` |  |
|  |  | Amounts Recoverable IC |  | subtotal `=SUM(H40:H42)` |  |
|  |  | Risk Adjustment |  | subtotal `=SUM(J40:J42)` |  |
| 40 | Paid in CY | Amounts Recoverable IC | + | `[RI PY Paid]` |  |
| 41 | Change in OS in CY | Amounts Recoverable IC | +/- | `[RI - PY O/S]` |  |
|  |  | Risk Adjustment | +/- | `[RI - PY RA (OS)]` |  |
| 42 | Change in IBNR in CY | Amounts Recoverable IC | +/- | `[RI - PY IBNR]` |  |
|  |  | Risk Adjustment | +/- | `[RI - PY RA (IBNR)]` |  |
| 43 | Change in Provision for risk of RI non-performance | Amounts Recoverable IC | +/- | **OVERRIDE** → `AD` (Change in Provision for risk of RI non-performance) | ⚠ override input |
| 44 | Other methodology diff | Amounts Recoverable IC | +/- | manual/0 |  |
|  |  | Risk Adjustment | +/- | manual/0 |  |
| 45 | Investment components | Assets Remaining Coverage |  | subtotal `=SUM(D46)` |  |
|  |  | Loss Recovery Component |  | subtotal `=SUM(F46)` |  |
|  |  | Amounts Recoverable IC |  | subtotal `=SUM(H46)` |  |
|  |  | Risk Adjustment |  | subtotal `=SUM(J46)` |  |
| 46 | Change in Profit Commission/ Sliding Scale commission | Amounts Recoverable IC | +/- | manual/0 |  |
| 47 | Reinsurance service result | Assets Remaining Coverage |  | subtotal `=D27-D21` |  |
|  |  | Loss Recovery Component |  | subtotal `=F27-F21` |  |
|  |  | Amounts Recoverable IC |  | subtotal `=H27-H21` |  |
|  |  | Risk Adjustment |  | subtotal `=J27-J21` |  |
| 48 | Reinsurance finance expenses/income | Assets Remaining Coverage |  | subtotal `=SUM(D49:D50)` |  |
|  |  | Loss Recovery Component |  | subtotal `=SUM(F49:F50)` |  |
|  |  | Amounts Recoverable IC |  | subtotal `=SUM(H49:H50)` |  |
|  |  | Risk Adjustment |  | subtotal `=SUM(J49:J50)` |  |
| 49 | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Reinsurance finance expenses/income - P&L | Assets Remaining Coverage | +/- | manual/0 |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Loss Recovery Component | +/- | **OVERRIDE** → `BL` (Reinsurance finance income/expense P&L) |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Amounts Recoverable IC | +/- | `[RI - Insurance Finance (Income)/Expense]` |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Risk Adjustment | +/- | manual/0 |  |
| 50 | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Reinsurance finance expenses/income - OCI | Assets Remaining Coverage | +/- | manual/0 |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Loss Recovery Component | +/- | manual/0 |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Amounts Recoverable IC | +/- | manual/0 |  |
|  | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Risk Adjustment | +/- | manual/0 |  |
| 51 | Effect of movements in exchange rates | Assets Remaining Coverage | +/- | manual/0 |  |
|  |  | Loss Recovery Component | +/- | manual/0 |  |
|  |  | Amounts Recoverable IC | +/- | manual/0 |  |
|  |  | Risk Adjustment | +/- | manual/0 |  |
| 52 | Other movements | Assets Remaining Coverage |  | subtotal `=SUM(D53:D54)` |  |
|  |  | Loss Recovery Component |  | subtotal `=SUM(F53:F54)` |  |
|  |  | Amounts Recoverable IC |  | subtotal `=SUM(H53:H54)` |  |
|  |  | Risk Adjustment |  | subtotal `=SUM(J53:J54)` |  |
| 53 | Item 1 (Specify) | Assets Remaining Coverage | +/- | manual/0 |  |
|  |  | Amounts Recoverable IC |  | **OVERRIDE** → `EZ` (RI Accrual Reserve (Specify)) |  |
| 54 | Item 2 (Specify) | Assets Remaining Coverage | +/- | manual/0 |  |
| 55 | Total changes in the statement of profit or loss and OCI | Assets Remaining Coverage |  | subtotal `=D52+D51+D48+D47` |  |
|  |  | Loss Recovery Component |  | subtotal `=F52+F51+F48+F47` |  |
|  |  | Amounts Recoverable IC |  | subtotal `=H52+H51+H48+H47` |  |
|  |  | Risk Adjustment |  | subtotal `=J52+J51+J48+J47` |  |
| 56 | **Cash flows** _(section)_ | | | | |
| 57 | Premium Paid | Assets Remaining Coverage | - | `[RI Premium Paid]` |  |
| 58 | Claims received | Amounts Recoverable IC | + | `[RI Claims received]` |  |
| 59 | Fixed Commission received | Assets Remaining Coverage | + | `[RI Fixed Commission received]` |  |
| 60 | Profit Commission/Sliding scale Commission received | Amounts Recoverable IC | + | manual/0 |  |
| 61 | Other Cash Flows | Assets Remaining Coverage | +/- | manual/0 |  |
|  |  | Amounts Recoverable IC | +/- | manual/0 |  |
| 62 | Total Cash Flows | Assets Remaining Coverage |  | subtotal `=SUM(D57:D61)` |  |
|  |  | Loss Recovery Component |  | subtotal `=SUM(F57:F61)` |  |
|  |  | Amounts Recoverable IC |  | subtotal `=SUM(H57:H61)` |  |
|  |  | Risk Adjustment |  | subtotal `=SUM(J57:J61)` |  |
| 63 | Reinsurance contract assets/(liabilities) as at 30/06 | Assets Remaining Coverage |  | subtotal `=D4+(D55-D62)` |  |
|  |  | Loss Recovery Component |  | subtotal `=F4+(F55-F62)` |  |
|  |  | Amounts Recoverable IC |  | subtotal `=H4+(H55-H62)` |  |
|  |  | Risk Adjustment |  | subtotal `=J4+(J55-J62)` |  |