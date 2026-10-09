Hi,

The data checks from your valuation report are now part of every reserving run.

**What you get**

- **Before you run:** on the review step, the full checklist (Table A.6) appears alongside the existing input check. Discrepancies are for information only and never stop a run.
- **After each run:**
  - **Checks table:** the checklist with "No discrepancies identified" or "Discrepancies identified" for each check. Click any check to see how it was tested and the records involved.
  - **Amount tables:** the premiums table (number of records and total NWP, SAR '000) and the claims table (number of records and total net paid claims, SAR '000), as in Tables A.7 and A.8.
  - **Explanations:** a box for the company's explanation of each discrepancy. Each new run offers the previous run's explanation, so it doesn't need retyping.
  - **"Export report":** downloads Tables A.6–A.8 with the explanations, ready for the valuation report.
  - **Evidence workbook:** a Data_Checks workbook in the run output lists every discrepant record, with its source file and row number.

**How the amounts are measured**

- **Premium checks:**
  - A record is a policy transaction (policy number + endorsement number).
  - NWP = gross premium minus ceded premium.
  - Date checks use the policy's own rows, not the reinsurance cession rows, which carry their own dates.
- **Claims checks:**
  - A record is a claim.
  - Net paid = gross paid minus RI paid.

**What we need from you**

1. **The rest of the checklist.** Your table includes check 2.10 and claims checks 3.7–3.18 (Table A.8 reports a discrepancy under 3.18), but the page we received stops at 3.6. Please send the full list so we can add them with your numbering.
2. **Duplicates (check 1.4).** What do footnotes 1 and 2 say? We currently count a row as a duplicate when it is identical in every column to an earlier row.
3. **Please confirm which fields we should use:**
   - Effective date = POLICYSTARTDATE (rather than RiskStartDate)
   - Reconciliation and modelling classes = RESERVINGCLASS, POLICYCLASS and IFRSCLASS
   - Endorsement indicator = ENDORSEMENTNUMBER. This column is blank in all the premium files we have. If the endorsement is held elsewhere (for example the last part of the policy number, such as "/001"), please tell us where.

**Things the checks found in the sample data**

The checks found a few points in the data we have been testing with. Please check whether your current data shows the same:

- **Outstanding claims loss dates (most important).** In the outstanding claims file, the loss date on every row is exactly two days before the "As at" date. This looks like a system date rather than the real loss date. The reserving uses the loss date to place outstanding claims in accident periods. If your live file is the same, all outstanding claims would sit in the most recent period.
- **Duplicate rows:** 7,640 premium rows and 1,637 claims-paid rows are exact duplicates of other rows. If these are genuine separate transactions, there is nothing to do. If not, they are counted twice.

Best regards,
