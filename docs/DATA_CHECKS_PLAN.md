# Data checks — the client's valuation-report checklist

**Status:** implemented (working tree, uncommitted), 2026-10-09.
**Source:** client screenshots of their valuation report — Table A.6 (Data Checks Summary),
A.7 (Premiums Data Discrepancies Summary) and A.8 (Claims Data Discrepancies Summary), plus
the company's explanations ("Check 2.4 — endorsements issued after expiry … no adjustment").

## 1. What the client asked for

For every reserving run:
- **A checklist with a result per check:** general 1.1–1.4, premiums 2.1–2.12, claims 3.x.
- **A count of discrepant records and their total amount**, per failing check. Premium checks report NWP, claims checks report net paid claims, both in SAR '000.
- **The company's explanation** for each discrepancy.

## 2. Decisions

**D1 — Informational, never a gate.** In the client's report, discrepancies are explained,
not blocked ("not material, no adjustments were made"). The data checks never affect
`would_block`. The class-reconciliation gate (WP0 preflight) is unchanged and still blocks.

**D2 — When the checks run.**
- **Before submit:** in the existing `/api/module1/preflight/` response, as `data_checks`. The EOP is now sent, so the valuation-date checks can run.
- **On every reserving run:** after inputs are staged and before the gate. A blocked run still records its checks.
- **Never fails the run:** a failure inside the checks is recorded in place of the report.

**D3 — Record and amount basis.** These definitions are also written to the workbook's Basis sheet.
- **Premium record** = POLICYNUMBER + ENDORSEMENTNUMBER.
- **NWP** = gross − ceded, from the GROSS / RI rows.
- **Premium date checks read gross rows only.** On the reference book, 73% of RI rows have their own (cession) issue date, so checking them would report every cession as a policy defect.
- **Claims record** = CLAIMNUMBER, across the paid and outstanding files.
- **Net paid** = gross − RI, on the amount the engine consumes (recovery heads substituted, as in preflight).

**D4 — Exact rules.**
- **Valid date:** 1900–2100.
- **"After":** strictly after for policy periods (2.4, 2.5), and on-or-after for claim events (3.4, 3.5).
- **No double counting:** comparisons skip rows whose dates are invalid, because those rows are already counted by 2.1–2.3 / 3.1–3.3.
- **Duplicates (1.4):** rows identical in every column; each extra copy counts once. The provenance columns are ignored.
- **Format (1.2):** column names and type families (number / date / text), compared with the most recent earlier successful reserving run in the organisation.
- **Missing column:** a check whose column is absent reports **not run** with the reason, never a pass.

**D5 — Two artefacts.**
- **Evidence:** `Data_Checks.xlsx` in the run output. It holds the summary tables plus one sheet per failing check, listing every discrepant row with its source file and Excel row (capped at 100,000 rows per check). It is previewed in-app and retained or purged with the output.
- **Report:** `GET …/data-checks/export/`. It holds tables A.6 (with the explanations), A.7 and A.8, plus the Basis sheet. It is built on demand from the stored summary, so it is instant and still works after the output is purged.

**D6 — Explanations.**
- **Storage:** a `DataCheckExplanation` model, one row per (run, check), recording who wrote it and when. The run record itself stays immutable.
- **Editing:** writing requires `module1.run`. Empty text deletes the explanation.
- **Carry forward:** the previous run's explanations are offered, because a company's reasons recur between valuations.

**D7 — Premium datasets gain `endorsement_number` and `ifrs_class`** (optional, migration
`datasets/0008`). Without them, checks 2.12 and 2.11 cannot run on on-screen data. The
engine ignores both.

## 3. Implementation

| Area | Change |
|---|---|
| `processing/services/data_checks.py` | Pure checks engine: `CATALOGUE` (client IDs and wording), `run_data_checks`, `BASIS_TEXT` |
| `processing/services/data_checks_workbook.py` | Evidence and report workbooks (openpyxl, write-only) |
| `processing/services/data_checks_store.py` | Previous-run baseline (1.2), explanations, carry-forward |
| `processing/models.py` | `DataCheckExplanation` (migration `processing/0010`) |
| `processing/tasks.py` | Inputs read once with provenance and shared by checks and gate; `_run_data_checks` |
| `processing/views.py`, `urls.py` | `data_checks` in the preflight response; `GET /data-checks/`, `PUT /data-checks/<id>/explanation/`, `GET /data-checks/export/` |
| dashboard | `DataChecksPanel` (A.6/A.7/A.8, drill-down, explanations), `JobDataChecks`, review-step preview, result card, `OutputPreviewDialog.initialFile` |

## 4. Verification

- **Reference book (`benchmarks/fixtures/summary_ref`):** all 21 checks run in 0.35 s. The evidence workbook takes about 8 s, inside a minutes-long run.
- **Tests:** 24 unit tests (each rule, its basis, the not-run contract, the workbooks); 11 task/API tests (recorded on every run, cannot fail a run, 1.2 against the previous run, explanation validation, permissions, carry-forward, export, pre-run preview); 9 frontend tests.

## 5. Findings on the reference data (raised with the client)

| Check | Result | Note |
|---|---|---|
| 1.1 | 3 classes | The known class gaps (Health vs Health Insurance, D&O) |
| 1.4 | 9,277 duplicate rows | 7,640 premium, 1,637 claims paid — identical in every column |
| 2.4 | 53 records | Expiry before issue: the endorsement-after-expiry pattern the client describes |
| 2.12 | every record | ENDORSEMENTNUMBER is blank in the whole premium file |
| 3.5 | 1,650 claims | **In the OS file, LOSSDATE = As at − 2 days on every row.** The engine places outstanding claims by LOSSDATE, so if the live data is the same, outstanding claims all sit in the latest accident period |

## 6. Open with the client

1. The checklist rows we have not seen: **2.10 and 3.7–3.18** (A.8 reports 3.18). These are added as `CATALOGUE` entries.
2. The definition behind footnotes 1 and 2 on check 1.4 (duplicates).
3. Field mapping: effective date = POLICYSTARTDATE (or RiskStartDate?); reconciliation/modelling classes = RESERVINGCLASS, POLICYCLASS, IFRSCLASS; endorsement indicator = ENDORSEMENTNUMBER (blank in our files; is it the policy-number suffix instead?).
4. The OS LOSSDATE finding in §5.
