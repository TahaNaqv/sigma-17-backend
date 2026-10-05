# IFRS 17 LC roll-forward — client revision 2026-10-05

**Status:** implemented (working tree, uncommitted). **Source:** the client's
`sigma-17-desktop-app/Output Module 2/Module2_Final_Output-check.xlsx`
(sha256 `ecfc2adb064db28d9944c5042a3dc61e8173b261b3d674de17934f6f52826d0c`), a revision of
the signed-off `Module2_Final_Output.xlsx` (sha256 `1de8f8f0f4e0…`). Yellow cells mark
the requested changes.

Companion item from the same request — making Module 2 follow a monthly / quarterly /
annual Module 1 grain — is **not** in this change. It rests on a premise that does not
hold today (Module 1 booking is quarterly; see §7) and is parked pending the client's
reply (`docs/CLIENT_REPLY_LC_AND_GRANULARITY_2026-10-05.md`).

---

## 1. What the client asked for

| Sheet | Ask |
|---|---|
| IFRS Summary | CK:CR from Previous Period inputs; CS:CZ from the `LC` sheet; DA:DD calculated (`Gross_LC_New`, `Gross_LC_Change`, `RI_LC_New`, `RI_LC_Change`) |
| Gross | Loss Component column (F) relinked to the new columns |
| RI | Loss Recovery Component column (F) relinked; D6 and J20 corrected |
| LRC BOP-EOP Reconciliation | Loss Component + RI LRC block (H, J:N) |
| LIC BOP-EOP Reconciliation | RI LIC block (M:W) |

## 2. Method: separate the column shift from real changes

The client inserted 22 columns into IFRS Summary, which re-lettered every link after
`UCR_prev`. Every Gross/RI link in both files was translated to its **column header** and
compared by name. Once the shift is removed, the real changes are:

| Cell | Before | After | Nature |
|---|---|---|---|
| Gross F25 (opening LC) | flattened constant | `LC Discounted_CY_prev` | new link |
| Gross F43 (new onerous) | empty | `Gross_LC_New` | new link |
| Gross F45 (reversal) | empty | `Gross_LC_Change` | new link |
| RI D6 (premium payable BOP) | `RI Premium Paid` | `RI_Payable_prev` | **our bug** — a cash flow was used as a balance |
| RI F20 (LoRC BOP) | override `DX` | `Loss Recovery Component_prev` | override → computed |
| RI J20 (RA BOP) | `RI - RA (OS)_curr + …_prev` | `RI - RA (OS)_prev + …_prev` | **our bug** — closing RA in an opening balance |
| RI F34 (new LoRC) | override `BI` | `RI_LC_New` | override → computed |
| RI F36 (LoRC reversal) | override `BJ` | `RI_LC_Change` | override → computed |

Everything else is the shift alone. The IS and Gross_Note edits in the file were already
delivered on 2026-09-10/11 (`notes_schema.REVISIONS` R1–R4). `Sheet1` is a scratch pivot.

## 3. Decisions

**D1 — `LC_BOP` is a new, optional Previous Period sheet.** The Previous Period
workbook carried only `LIC_BOP` and `UPR-DAC_BOP`, so CK:CR had no source. `LC_BOP` has
exactly the columns of the allocate `LC` sheet — the prior run's `LC` sheet pasted as-is.
It is optional so every existing Previous Period workbook keeps working; without it, the
opening balances are zero and the job records a warning. It is also a dataset kind
(`previous_period_lc`), staged as the third sheet of `Previous_Period.xlsx` by the
existing `named_sheet` recipe. Duplicate (class, UWY) rows are rejected. Rows outside the
current period are ignored with a warning.

**D2 — Deviation LC-1: a cohort after the accounting period is "new".** The client wrote
`new = IF(UWY = AP, curr, 0)` and `change = IF(UWY < AP, curr − prev, 0)`. We implement
`new` for `UWY >= AP`. This is identical for every cohort up to the accounting period. It
additionally books cohorts written in advance (the reference book has 3: two 2025, one
2026) as new business, instead of leaving them as a permanent roll-forward break. The
client is asked to confirm.

**D3 — The `"-"` on the reversal rows is presentational.** Gross row 45 and RI row 36
carry `"-"`, but `*_LC_Change` is a signed `curr − prev`, so a release is already
negative. Applying the sign would turn a release into an increase. The client's own
static values agree (Gross F44 = −10,732,428.88). Both columns are in
`mapping.PRE_SIGNED_SOURCE_COLUMNS`, and the mapping validator enforces this.

**D4 — The RI reconciliation totals are signed as on the RI sheet.** The client's mock
sums RI UPR, payable and UCR plainly. We apply the RI sheet's signs (payable −,
unearned commission −, non-performance provision −), the same way the Gross LRC table
already signs DAC and receivables. Result: RI LRC equals the RI sheet's remaining-coverage
asset, and RI LIC equals amounts recoverable plus RA, for both opening and closing.
`test_reconciliation_ties_to_movement_balances` pins this.

**D5 — The client's EOP links are corrected, not copied.** In the LIC EOP block, R16, S16
and T16 point to `_prev` columns (AN, DO, DT); the EOP table uses `_curr`. Gross D29 is a
text cell (no `=`), so it did not follow the shift; our existing correct formula
`Rec_Provision_curr − Rec_Provision_prev` (amendment of 2026-09-10) stands.

**D6 — The reconciliation tables are not given the client's blank spacer columns** (I and
L). Headers are self-describing. The H "Loss Component" heading follows the client's
note ("Heading should be Loss component").

**D7 — The three retired override fields are deprecated, not removed.**
`ri_loss_recovery_new_onerous`, `ri_loss_recovery_reversal_amortization` and
`ri_methodology_diff_loss_recovery_bop` stay on `MovementOverrideRow`, so existing datasets
and job snapshots remain valid. The mapping no longer reads them. A non-zero value
produces a run warning that names what replaced it (`mapping.RETIRED_OVERRIDE_KEYS`). The
grid labels them "retired".

## 4. Implementation

| Area | Change |
|---|---|
| `module2_engine/engine.py` | `LC_MEASURES`, `lc_movement_columns()`, `_read_lc_bop()`, `build_lc_movement()` (pure); block assigned positionally into `ifrs_summary_df` between UPR openings and Expense-CF; `ProcessFrames.warnings`; `ri_lrc_components` / `ri_lic_components`; `create_lrc_reconciliation` / `create_lic_reconciliation` wrap the unchanged `create_lrc_table` / `create_lic_table` (still used by the sensitivity runner); `run_module2_process(warnings_out=)` |
| `scripts/gen_movement_mapping.py` | `CLIENT_AMENDMENTS` generalised to empty/const/override cells (`_cell_signature`, still asserted against the verbatim extract); 8 entries dated 2026-10-05 |
| `movement/mapping_source.json` | regenerated — exactly the 8 cells in §2 changed |
| `movement/mapping.py`, `compute.py` | pre-signed LC change columns; `RETIRED_OVERRIDE_KEYS`; engine warnings carried into `MovementResult.warnings` |
| `processing/tasks.py` | `input_meta.process_warnings` (process); `movement_warnings.warnings` (movement) — both additive |
| `processing/views.py` | optional `previous_period_lc_dataset_id` on process + movement; dataset path only, requires LIC + UPR |
| `datasets/` | kind `previous_period_lc`, `PreviousPeriodLcRow` (migration `0007`), serializer, columns, recipe, template (also added to the composite Previous Period template); adapter restores template header order (jsonb drops key order) |
| dashboard | kind registries, optional LC_BOP picker on Cash Flow Allocation + Movement Analysis, process "Input notes" card, movement warnings list, retired-override labels |

## 5. Verification

- **Additive-only proof against the frozen golden `m2_process_ref`:** 12 of 15 sheets are
  bit-identical. In IFRS Summary and both reconciliation sheets, every pre-existing column
  is unchanged and in its original position; only the new columns differ. The golden was
  then re-captured (goldens are git-ignored — re-capture locally with
  `manage.py capture_golden --only m2_process_ref --force`).
- **IFRS Summary header row is identical to the client's file:** 131 of 131 columns, in order.
- **End-to-end on the reference book with a synthetic `LC_BOP`:** Loss Component and
  Loss Recovery Component residuals are 0.0 on all 83 (class, UWY) pairs. The J20 fix also
  clears 40 pre-existing RI risk-adjustment breaks (182 → 142). The remaining 142 are
  identical before and after: the known empty cash-flow columns in the sample data.
- **Tests:** `module2_engine/tests/test_lc_rollforward.py` (20 tests: split rules,
  identity `prev + new + change = curr`, input contract, links, sign handling, retired
  overrides, reconciliation tie-out); API tests on both endpoints; materialisation of
  an `LC_BOP` dataset read back through the engine's reader; template tests; frontend
  `module2.test.ts`. Backend: all suites green except two pre-existing
  `test_dataset_e2e` failures (Module 1 summary / policy-UPR, which fail identically on a
  clean tree). Dashboard: vitest 358/358, vite build green, tsc error count unchanged.

## 6. Open with the client

1. Confirm deviation LC-1 (§3 D2).
2. Confirm the RI reconciliation signs (§3 D4).
3. Supply the prior period's `LC` sheet as `LC_BOP` for the first live run. Until then,
   the opening LC is zero and the full closing balance reads as movement.

## 7. Why the granularity request is parked

Module 1 booking is quarterly. Monthly and yearly exist only as the diagnostic Triangles
view (WP6; decision D4 in `CLIENT_REQUIREMENTS_DECISIONS.md`). Everything Module 2 reads —
UPR Run-Off, IBNR Summary, Allocation EP, LIC (OS) Summary — is quarterly, and the
client's `LIC_BOP` is keyed on quarterly accident periods. The Module 2 side is cheap
(`core.grain.PeriodGrain` already drives `calculate_discount_rates`, the payment pattern
and the run-off indexing). The upstream booking change and the period-key migration are
the real work, and they are a reversal of D4 that needs the client's answer first.
