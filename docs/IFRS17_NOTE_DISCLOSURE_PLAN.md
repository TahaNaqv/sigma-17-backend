# IFRS 17 Note Disclosure Layer — `Gross_Note` / `RI_Note` / `IS` / `BS`

**Status:** Proposed (post-discovery, pre-implementation) · **Scope:** `sigma-17-backend` (engine +
job), `sigma-17-dashboard` (copy + generated schema mirror only) · **Source artifact:**
`sigma-17-desktop-app/Output Module 2/Module2_Final_Output.xlsx`
(sha256 `1de8f8f0f4e066cae0e96f80fe7279a587571bfc2d595791b2817f070438e3f0`, 21 sheets, 2026-07-31).

Companion to [`IFRS17_MOVEMENT_PLAN.md`](./IFRS17_MOVEMENT_PLAN.md) — this plan is **strictly
additive** to it. Nothing in the existing Gross/RI projection is redesigned.

---

## 0. Provenance — what actually changed in the client file

The mapping we already treat as authoritative was encoded from this same workbook at sha256
`16f9656004…` (`mapping_source.json :: _meta.source_sha256`). The new file hashes differently,
so the first question is whether the client altered `Gross`/`RI` as well as adding sheets.

**Verified: they did not.** Every `(line, bucket)` cell recorded in `client_source_extract.json`
was re-read from the new workbook and compared against the recorded formula / `IFRS Summary`
column reference / constant:

```
checked=265  mismatches=0        # Gross: 4 buckets × 67 lines, RI: 4 buckets × 63 lines
```

**Consequence:** `schema_source.json`, `mapping_source.json` and `client_source_extract.json`
remain valid untouched. This work adds a *presentation layer* on top of them and does not reopen
the actuarial mapping sign-off.

The delta is exactly four new worksheets, inserted between `IFRS Summary` and `Gross`:

| Sheet | Rows × Cols | Title cell | Depends on |
|---|---|---|---|
| `Gross_Note` | 34 × 6 | `12.2.1.1 Insurance contracts` | `Gross` only |
| `RI_Note` | 33 × 6 | `12.2.2.1 Reinsurance contracts` | `RI` only |
| `IS` | 28 × 3 | `Income Statement` | `Gross_Note`, `RI_Note`, `Gross`, `RI` |
| `BS` | 5 × 3 | `Balance Sheet` | `Gross_Note`, `RI_Note` |

---

## 1. What the client is asking for (requirement, restated)

> *"All information in these sheets is coming from Gross and RI sheet and then being reflected in
> a different format in these 4 new sheets."*

Restated precisely, and confirmed cell-by-cell:

1. **No new measures.** There is not a single reference to `IFRS Summary`, `Movement Analysis`, `LC`
   or any raw frame in the four sheets. Every populated cell is one of: a literal `0`, a literal
   `'-'`, a reference to a `Gross`/`RI` cell, or a `SUM`/arithmetic over cells *within* the note
   sheets. This is a **re-presentation**, not a computation.
2. **A statutory-note shape.** `Gross_Note` / `RI_Note` collapse the ~67-line SAMA roll-forward into
   the ~12-line IFRS 17 note format (opening → P&L → cash flows → closing), keeping the same four
   measurement buckets plus Total.
3. **A two-statement summary.** `IS` and `BS` are the financial-statement extracts driven off those
   notes.
4. **Live linkage.** "Linked with previous and with each other" = the dependency chain
   `Gross`/`RI` → `Gross_Note`/`RI_Note` → `IS`/`BS`. Regenerating the movement run must
   regenerate all four.

**Implication for us:** the four sheets are a pure function of the per-`(class, UWY)` `SheetResult`
values `movement/compute.py` already produces. No engine, mapping, or dataset changes are required
to source them. The work is a new declarative note schema + a renderer + controls.

---

## 2. Complete anatomy of the four sheets

Layout for both notes: `A` = label, `B..E` = the four measurement buckets, `F` = Total.
Merged header block `B2:F2` = *"As at Val Date"*; `B3:C3` = *Liability for remaining coverage*;
`D3:E3` = *Liability for incurred claims*; `B4:B5` = *Excluding loss component*; `C4` = *Loss
component*; `D4` = *Estimates of present value of FCF*; `E4:E7` = *Risk Adjustment for
non-financial risk*. Number format is Excel accounting
(`_(* #,##0_);_(* \(#,##0\);_(* "-"??_);_(@_)`), font 10pt, gridlines hidden, `A` width 35.8.

### 2.1 `Gross_Note` — every populated row

| Row | Label | B (LRC excl LC) | C (Loss Component) | D (LIC excl RA) | E (RA) | F (Total) |
|---:|---|---|---|---|---|---|
| 9 | Insurance contract liabilities – opening | `Gross!C6` | `Gross!E6` | `Gross!G6` | `Gross!I6` | `SUM(B9:E9)` |
| 10 | Insurance contract assets – opening | 0 | 0 | 0 | 0 | `SUM(B10:E10)` |
| 11 | **Opening balance – net** | `SUM(B9:B10)` | … | … | … | `SUM(F9:F10)` |
| 13 | Insurance revenue | `Gross!C26` | `Gross!E26` | `Gross!G26` | `Gross!I26` | `SUM(B13:E13)` |
| 16 | Incurred claims and other directly attributable expenses | 0 | 0 | `Gross!G32` | `Gross!I32` | `SUM(B16:E16)` |
| 17 | Onerous contracts recognized / Reversal | 0 | **`Gross!E31`** ⚠D4 | 0 | 0 | `SUM(B17:E17)` |
| 18 | Changes that relate to past service – adjustments to the LIC | 0 | 0 | **`Gross!G45`** ⚠D3 | **`Gross!I45`** ⚠D3 | `SUM(B18:E18)` |
| 19 | Insurance acquisition cashflows amortisation | `Gross!C38` | 0 | 0 | 0 | `SUM(B19:E19)` |
| 20 | **Insurance service expenses** | `SUM(B16:B19)` | … | … | … | `SUM(F16:F19)` |
| 21 | Finance expense from insurance contracts | `Gross!C57` | `Gross!E57` | `Gross!G57` | `Gross!I57` | `SUM(B21:E21)` |
| 22 | **Total changes in the statement of income** | `B13+B20+B21` | … | … | … | `F13+F20+F21` |
| 25 | Premiums received | `Gross!C66` | 0 | 0 | 0 | `SUM(B25:E25)` |
| 26 | Claims and other directly attributable expenses paid | 0 | 0 | **text `Gross!G67+Gross!G67`** ⚠D1 | **text `Gross!I67+Gross!I67`** ⚠D1 | `SUM(B26:E26)` |
| 27 | Insurance acquisition cashflows paid | `Gross!C69` | 0 | *(blank)* | 0 | `SUM(B27:E27)` |
| 28 | **Total cash inflows (outflows)** | `SUM(B25:B27)` | … | … | … | `SUM(F25:F27)` |
| 31 | Insurance contract liabilities – closing | `B11+B22+B28` | … | … | … | `F11+F22+F28` |
| 32 | Insurance contract assets – closing | 0 | 0 | 0 | 0 | **`F12+F23+F29`** ⚠D5 |
| 33 | **Closing balance – net** | `SUM(B31:B32)` | … | … | … | `SUM(F31:F32)` |

### 2.2 `RI_Note` — every populated row

| Row | Label | B (Assets RC) | C (Loss Recovery) | D (Amounts Recoverable IC) | E (RA) | F (Total) |
|---:|---|---|---|---|---|---|
| 9 | Reinsurance contract assets – opening | `RI!D4` | `RI!F4` | `RI!H4` | `RI!J4` | `SUM(B9:E9)` |
| 10 | Reinsurance contract liabilities – opening | `'-'` | `'-'` | `'-'` | `'-'` | `'-'` |
| 11 | **Opening balance – net** | `SUM(B9:B10)` | … | … | … | `SUM(F9:F10)` |
| 13 | Allocation of reinsurance premium | `RI!D21` | `RI!F21` | `RI!H21` | `RI!J21` | `SUM(B13:E13)` |
| 16 | Claims recovered and other directly attributable expenses | 0 | 0 | `RI!H28` | `RI!J28` | `SUM(B16:E16)` |
| 17 | Loss-recovery on onerous underlying contracts | 0 | **`RI!F27`** ⚠D4 | 0 | 0 | `SUM(B17:E17)` |
| 18 | Changes that relate to past service – FCF for incurred claims recovery | 0 | 0 | `RI!H38` | `RI!J38` | `SUM(B18:E18)` |
| 19 | **Amounts recoverable from reinsurers – net** | `SUM(B16:B18)` | … | … | … | `SUM(F16:F18)` |
| 21 | Finance expenses from reinsurance contracts | `RI!D48` | `RI!F48` | `RI!H48` | `RI!J48` | `SUM(B21:E21)` |
| 22 | **Total changes in the statement of income** | `B13+B19+B21` | … | … | … | `F13+F19+F21` |
| 25 | Premiums ceded and acquisition cashflows paid | **text `RI!D57+RI!D59`** ⚠D2 | 0 | 0 | 0 | `SUM(B25:E25)` |
| 26 | Recoveries from reinsurance | 0 | 0 | `RI!H58` | 0 | `SUM(B26:E26)` |
| 27 | **Total cash inflows (outflows)** | `SUM(B25:B26)` | … | … | … | `SUM(F25:F26)` |
| 30 | Reinsurance contract assets – closing | `B11+B22+B27` | … | … | … | `F11+F22+F27` |
| 31 | Reinsurance contract liabilities – closing | `'-'` | `'-'` | `'-'` | `'-'` | `'-'` |
| 32 | **Closing balance – net** | `SUM(B30:B31)` | … | … | … | `SUM(F30:F31)` |

### 2.3 `IS` — Income Statement (single `Total` column, C)

| Row | Label | C |
|---:|---|---|
| 5 | Insurance revenue | `Gross_Note!F13` ⚠D7 (sign) |
| 6 | Insurance service expenses | `Gross_Note!F20` |
| 7 | Net expenses from reinsurance contracts | `RI_Note!F22` |
| 8 | **Insurance service result** | `SUM(C5:C7)` |
| 10 | Interest income | `0` |
| 11 | Expected credit loss on financial assets | *(no value cell)* ⚠D6 |
| 12 | Net losses on financial assets measured at FVTPL | `0` |
| 13 | **Net investment loss** | `SUM(C10:C12)` |
| 14 | Net finance expenses from insurance contracts | `Gross!J57` |
| 15 | Net finance income from reinsurance contracts | `RI!K48` |
| 16 | Other Items | `0` |
| 17 | **Net insurance and investment result** | `SUM(C14:C16)+SUM(C10:C12)+C8` |
| 19 | Other operating expenses | `0` |
| 20 | IFRS9 Adjustments | *(no value cell)* ⚠D6 |
| 21 | Other income | `0` |
| 22 | **Total loss for the year … before zakat and income tax** | `C17+C19+C21` |
| 24 | Zakat expense | *(no value cell)* ⚠D6 |
| 25 | Income tax | `'-'` |
| 27 | **NET LOSS FOR THE YEAR ATTRIBUTABLE TO THE SHAREHOLDERS** | `C22+C24` |

Rows 5 and 7 carry a green fill (`FF92D050`) in the client file — their convention for
"linked/checked" cells. Note `IS!C14`/`C15` bypass the notes and read `Gross`/`RI` Total columns
directly; they are numerically identical to `Gross_Note!F21` / `RI_Note!F21`.

### 2.4 `BS` — Balance Sheet

| Row | Label | C |
|---:|---|---|
| 4 | Insurance contract liabilities | `Gross_Note!F33` |
| 5 | Reinsurance contract assets | `RI_Note!F32` |

Two lines only — see ⚠D8.

---

## 3. Structural arithmetic verified against the client's `Total` column

The `Gross` per-bucket columns `C/E/G/I` in the client file are largely broken references (they
point at `'IFRS Summary'!<col>1`, i.e. the **header row**, so they cache as text such as
`'GWP'`). Column `J` is the only column carrying real numbers. Every subtotal hypothesis was
therefore tested against `J`:

| Relation | Result |
|---|---|
| `J6 = SUM(J7:J25)` | ✅ 526,143,684.46 |
| `J26 = SUM(J27:J30)` | ✅ 508,233,604.91 |
| `J31 = J32+J42+J47+J53+J54` | ✅ 501,110,496.06 |
| `J32 = SUM(J33:J37)` | ❌ off by 107,844,353.35 |
| **`J32 = SUM(J33:J37)+J38`** | ✅ 548,201,472.24 |
| **`J38 = SUM(J39:J41)`** | ✅ 107,844,353.35 |
| **`J42 = J43+J44`** | ✅ 3,503,397.00 |
| **`J44 = SUM(J45:J46)`** | ✅ −10,732,428.88 |
| `J48 = SUM(J49:J52)` | ❌ off by 12,162,829.48 (= the ULAE line, row 52) |
| **`J48 = SUM(J49:J51)`** | ✅ −38,431,543.69 |
| **`J47 = J48+J52`** | ✅ −50,594,373.17 |
| **`J57 = SUM(J58:J59)`** | ✅ 7,853,583.02 |
| `J61 = SUM(J62:J63)` | ✅ 0 |
| `J64 = J61+J60+J57+J56` | ✅ −493,256,913.04 |
| `J71 = SUM(J66:J70)` | ⚠ structure holds (−106,292,137.16); the file caches 0 because the per-bucket cash-flow cells are broken |
| `J72 = J6+J64−J71` | ⚠ does not hold — row 72 is an **independent** closing balance from the client's own model, not a roll-forward |

The row 47/48 split is independently confirmed by the per-bucket statics:
`G47 − G48 = −44,046,641.70 − (−31,883,812.22) = −12,162,829.48` = exactly the ULAE line.

The row-72 finding matches — and validates — the existing `compute.py` design, which already
models `closing_independent` vs `closing_rollforward` with a reported `residual`.

### 3.1 The decisive tie-out

With `Gross_Note` row 18 pointing at the row it should (row 47, *Past Service: Changes to
liabilities for incurred claims*) rather than row 45:

```
Gross_Note!F20  =  F16          + F17           + F18            + F19
                =  440,357,118.88 + 3,503,397.00 + (−50,594,373.17) + 107,844,353.35
                =  501,110,496.06
Gross!J31       =  J32 + J42 + J47 + J53 + J54
                =  548,201,472.24 + 3,503,397.00 + (−50,594,373.17) + 0 + 0
                =  501,110,496.06                                        ✅ exact
```

As shipped (pointing at row 45), `Gross_Note!F20` = 551,704,869.24 — overstated by exactly
50,594,373.17. This is the single strongest piece of evidence that ⚠D3 is a typo in the client's
template and not an intentional presentation choice, and it becomes **tie-out control C1** below.

---

## 4. Defects in the client's file — each resolved from the file itself

Every defect below is resolved from internal evidence in the supplied workbook. None requires a
client answer to proceed; §11 records the derivation and the confidence for each, and §8 keeps the
deviation ledger so any of them can be reversed in one line if the client says otherwise.

| # | Where | Defect | Resolution | Basis |
|---|---|---|---|---|
| **D1** | `Gross_Note!D26`, `E26` | Cell contains the *literal text* `Gross!G67+Gross!G67` — no leading `=`, and the same cell twice | `=Gross!G67+Gross!G68` | Row label reads "Claims **and other directly attributable expenses** paid"; and it is what makes the note's cash section reconstruct `Gross!J71` exactly (§4.1) |
| **D2** | `RI_Note!B25` | Literal text `RI!D57+RI!D59` — no leading `=` | `=RI!D57+RI!D59(+RI!D61)` | Same completeness tie-out on the RI side (§4.1) |
| **D3** | `Gross_Note!D18`, `E18` | Points at `Gross!G45`/`I45` = *Reversal/amortization of losses following an assumed pattern* — a **Loss Component** row — while the label reads *"Changes that relate to past service – adjustments to the LIC"* | `=Gross!G47`/`I47` | §3.1 — repointing makes `F20` equal `Gross!J31` to the cent |
| **D4** | `Gross_Note!C17`, `RI_Note!C17` | Point at the whole *Insurance service expenses* / *Amounts Recoverable* subtotal (`Gross!E31`, `RI!F27`) rather than the onerous-contract line (`Gross!E42`, `RI!F33`) | `=Gross!E42` / `=RI!F33` | The cited subtotal **contains** the line the note is decomposing — rows 16–19 are a partition of row 31, so citing row 31 inside it is circular. Numerically identical today only because row 42 is the sole LC contributor |
| **D5** | `Gross_Note!F32` | `=F12+F23+F29` — all three are blank spacer rows, so the cell is permanently 0 and inconsistent with `B32:E32` | `=SUM(B32:E32)` | Every other Total cell in both notes is the `SUM` of its own row |
| **D6** | `IS!C11`, `C20`, `C24` | *Expected credit loss*, *IFRS9 Adjustments*, *Zakat expense* have no value cell, yet `C27 = C22+C24` consumes `C24` | Explicit `0` | These are IFRS 9 / general-ledger items with no source anywhere in the Module 2 pipeline; Excel already coerces the blanks to 0, so this is the file's own effective behaviour made explicit |
| **D7** | `Gross_Note!B13:E13` / `IS!C5` | **Sign convention** — revenue enters the note and the `IS` unflipped | Revenue enters **negative**: note row 13 = **−**`Gross!C26…I26` | Three independent lines of evidence converge — see §11 Q1 |
| **D8** | `BS` | Two lines only — no assets/equity, no totals | Implement exactly as given | It is an IFRS 17 *extract*, not a full balance sheet; both lines are the closing balances of the two notes. Anything more would be invented |

None of D1–D6 are visible in the client's own workbook, because the `Gross`/`RI` per-bucket cells
they depend on are broken references caching header text. They surface the moment real numbers
flow through — i.e. in our output.

### 4.1 The cash-flow completeness tie-out (basis for D1/D2)

The note's cash-flow section must reproduce the movement sheet's total cash flows. With D1 applied,
and *Other Cash Flows* folded into the acquisition line, it does so exactly:

```
Gross_Note  row 25  = Gross!C66                     Premiums received
            row 26  = Gross!G67 + Gross!G68         Claims + directly attributable expenses paid
            row 27  = Gross!C69 + Gross!C70         Acquisition cash flows paid
            row 28  = Σ rows 25:27  ==  Gross!J71 = SUM(J66:J70) = −106,292,137.16   ✅
```

Folding `Gross!C70` (*Other Cash Flows*) into the acquisition line is not a guess: row 40
(*Other Acquisition Cash Flows*) and row 70 (*Other Cash Flows*) are **exactly equal and opposite**
in the client's own Total column — `+49,658,854.56684359` and `−49,658,854.56684359` — so row 70 is
the cash-flow leg of the acquisition line. Without folding it in, the note silently drops 49.7m of
cash flow. The RI side is the mirror: rows 25–26 extended with `RI!D61` and `RI!H60` reproduce
`RI!K62`; both are tier-M lines (0 until an override fills them), so this is numerically a no-op
today and structurally correct forever.

---

## 5. Defects this exposes in **our** current implementation

Independent of the note layer, `movement/workbook.py :: _FLATTENED_GROSS` is incomplete. The
`Gross` sheet was flattened to statics in the source workbook, so rows that lost their formulas
were reconstructed by hand; four were missed and two are wrong. Confirmed empirically — with a
synthetic frame where row 39 (Commission) = 7.0 and row 58 (Finance P&L) = 5.0, the renderer emits:

```
row 31 Insurance service expenses                 0.0    ← should include 7.0
row 32 Incurred claims and other expenses         0.0
row 38 Insurance Acquisition cash flows …         0.0    ← should be 7.0
row 42 Future Service: Losses on onerous …        0.0
row 44 Reversal of losses on existing onerous …   0.0
row 57 Insurance finance expenses/income          0.0    ← should be 5.0
```

| # | Line | Current | Correct (verified §3) | Impact |
|---|---|---|---|---|
| **E1** | row 38 *Insurance Acquisition cash flows…* | no formula → **0** | `=SUM(C39:C41)` | Acquisition amortisation missing from the Gross sheet entirely; `Gross_Note` row 19 would be 0 |
| **E2** | row 57 *Insurance finance expenses/income* | no formula → **0** | `=SUM(C58:C59)` | Finance expense missing; `Gross_Note` row 21 and `IS!C14` would be 0 |
| **E3** | rows 42, 44 *Future Service / Reversal* | no formula → **0** | `=C43+C44`, `=SUM(C45:C46)` | Onerous-contract movement missing; `Gross_Note` row 17 would be 0 |
| **E4** | row 32 *Incurred claims and other expenses* | `=SUM(C33:C37)` | `=SUM(C33:C37)+C38` | LRC bucket understated by the acquisition amount (row 31 consumes 32, not 38 — the client's own row-31 formula omits 38) |
| **E5** | rows 47/48 *Past Service / Change in Ultimate* | `47 = C48+C53`, `48 = SUM(C49:C52)` | `47 = C48+C52`, `48 = SUM(C49:C51)` | Two bugs: ULAE (row 52) sits inside "Change in Ultimate" instead of beside it, **and** row 53 is double-counted (row 31 already adds it). Row 53 carries the routed reconciliation residual, so this is live, not theoretical |
| **E6** | — | `scripts/extract_client_disclosure.py` referenced by `client_source_extract.json :: _meta.generated_by` does not exist in the repo | — | Provenance not reproducible; add it (§6, Phase 0) |
| **E7** | Gross closing roll-forward | `72 = C6+C64−C71` and `compute.py :: closing_rollforward = opening + pnl − cf` | `72 = C6+C64+C71` for **Gross** (RI unchanged) | See §5.1 — the Gross and RI sign columns are mirror images (liability vs asset), so the RI formula cannot be transplanted to Gross. Materially inflates every Gross residual today |

**E1–E5 must be fixed before the note layer is meaningful** — the notes read exactly these rows.
They also change existing `IFRS17_Movement_Analysis.xlsx` output, so they need their own
regression note and actuarial sign-off. They do **not** touch `run_module2_process`, so
`Module2_Final_Output.xlsx` stays bit-identical and the golden net is unaffected.

### 5.1 E7 — the Gross roll-forward subtracts cash flows that are already signed

Row 72 was flattened to statics in the client's file, so it carries **no formula**; our
`_FLATTENED_GROSS[72] = "=C6+C64-C71"` was reconstructed from the RI parallel
(`RI!D63 = D4+(D55−D62)`). That transplant is invalid, because the two sheets sign cash flows in
mirror image:

| | RI (an **asset**) | Gross (a **liability**) |
|---|---|---|
| Premium | *paid*, sign `-` → negative; paying premium **increases** the asset → subtracting is correct | *received*, sign `+` → positive; premium received **increases** the liability → subtracting is **wrong** |
| Claims | *received*, sign `+` → positive; **decreases** the asset → subtracting is correct | *paid*, sign `-` → negative; **decreases** the liability → subtracting is **wrong** |

Tested against the client's own independent closing balance (`J72 = 404,874,855.43`), using their
Total column throughout:

| Hypothesis | Closing | Gap |
|---|---:|---:|
| **(a) IFRS 17 liability roll-forward** — `open + (−rev + exp + fin) + cash` | 420,582,021.47 | **15,707,166.04 (3.9%)** |
| (b) P&L-signed changes, cash added — `open + (rev − exp + fin) + cash` | 434,828,239.17 | 29,953,383.74 (7.4%) |
| (d) liability roll-forward, cash subtracted | 633,166,295.79 | 228,291,440.36 (56.4%) |
| (c) **current implementation** — P&L-signed, cash subtracted | 647,412,513.49 | 242,537,658.06 (59.9%) |

The residual gap under (a) is 3.9% on a 526m opening — well inside the noise, since the client's
Total column comes from a *different run* than the workbook's own `IFRS Summary` (their `J27` GWP is
548.9m against 473.4m in the data sheet). The current implementation is the worst of the four.

Decisive corroboration comes from the newly supplied sheets themselves: `Gross_Note!F31` is
`= F11 + F22 + F28` — opening **plus** changes **plus** cash flows. The client has now, in their own
hand, written down the Gross roll-forward convention that their older sheet left as a static.

### 5.1b E8 — the roll-forward and the sheet disagree on the sign of expenses

**Found during Phase 1 implementation**, by a test asserting that the workbook's rendered
closing (row 72) and `compute.closing_rollforward` agree. They do not, and the gap is exact:

```
rendered row 72        = opening + (revenue − expenses) + cash     ← via row 64 → row 56
compute closing_rf     = opening + (revenue + expenses) + cash     ← Σ P&L input lines
divergence             = 2 × insurance service expenses
```

Measured on a fixture with revenue 90, expenses 7, cash 40: rendered 323, computed 337,
divergence 14 = 2 × 7. Isolating each block shows cash and revenue agree exactly across
both paths; **only the expense block diverges**. `compute` sums the P&L *input* lines with
their mapped signs, so expenses enter positive; the sheet reaches its closing through row
56 (`= revenue − expenses`), so the same expenses enter negative.

This **predates E7** — it is a P&L-sign question, not a cash-flow one — and it is
independent of it: the divergence is identical before and after the E7 change. It is also
the same family of defect as D7/Q1: neither path implements the IFRS 17 liability movement
(`−revenue + expenses`), which §5.1's fit table identifies as the correct form.

**Deferred out of Phase 1, resolved after Phase 5** — see §5.3. It was pinned in the
meantime by a test asserting the divergence equalled exactly twice the expense block, so
the gap had a measured size and could not drift while it waited.

**Related finding:** the reconciliation control is computed entirely from `opening`,
`pnl_total` and `cf_total` — never from the rendered subtotals. That is why the Phase 1
before/after run showed the rendered sheet changing materially while `breaches` and
`max_abs_residual` stayed *identical*. It also means **the recon control could never have
caught E1–E3**: an aggregate row rendering a hard 0 is invisible to it. The note controls
(§6.5) close that gap — they compare the rendered numbers, which is exactly what the
reconciliation cannot see.

### 5.3 E8 resolved — a P&L total is not a balance movement

Phase 5's C3 control turned E8 from a documented curiosity into the largest number in the
report: an entity closing gap of **−946,797,797.91**, exactly −2 × the note's revenue. That
forced the analysis.

**The defect, precisely.** Both closing paths were wrong, in opposite directions:

```
rendered row 72   = opening + row 64 + cash      where row 64 carries row 56 = revenue − expenses
closing_rollforward = opening + Σ P&L inputs + cash   where revenue and expenses are both positive
correct (balance)  = opening + (−revenue + expenses + finance + fx + other) + cash
```

Row 64 is *"Total changes in the statement of profit or loss and OCI"* — a **P&L
aggregate**. A balance roll-forward must not consume it. Insurance revenue *releases* the
LRC, so it reduces the liability; row 64 adds it.

**This includes a Phase 1 error of mine.** §5.1's fit table already identified the balance
form (a) as the best fit at 3.9% against the P&L-signed form (b) at 7.4% — and E7 then
shipped `=C6+C64+C71`, which *is* form (b). I scoped E7 as "the cash sign" and did not
notice that row 72 inherits its P&L sign from row 64. The fit table was right; the
implementation didn't follow it.

**The decisive structural evidence** is the client's own RI sheet — the one whose formulas
they never flattened. `D63 = D4+(D55−D62)` reaches its closing through `D47 = D27−D21`,
which **negates the allocation block**. They encoded the correct treatment where they kept
their formulas, and it was lost on Gross precisely because Gross was flattened to statics.

**The fix.** `schema.ROLLFORWARD_NEGATED_BLOCK` declares, per sheet, the subtotal whose
input block enters the balance roll-forward negated — `insurance_revenue` for Gross,
`amounts_allocated_to_reinsurance` for RI. `compute` applies it to `pnl_total`; row 72
becomes `=C6+(C31-C26+C57+C60+C61)+C71`. Note this affects **both** sheets: RI's
`closing_rollforward` was wrong by twice the allocation, even though RI's *rendered*
closing was right.

**Measured on the real dataset:**

| | Before E8 | After E8 |
|---|---:|---:|
| Reconciliation `max_abs_residual` | 133,714,206.09 | **123,371,660.41** (−7.7%) |
| Reconciliation breaches | 210/664 | 210/664 |
| Note controls | 0/960 | 0/960 |
| **C3 entity closing gap (Gross)** | **−946,797,797.91** | **0.00** |

The residual fall is real but modest, and the breach *count* is unchanged — those 210 are
dominated by the ~⅓ of lines that are manual and default to 0, which E8 does not touch. The
decisive result is C3: two independently implemented paths — the sheet's formula evaluation
and `compute`'s line summation — now produce **identical** closings across all 96 views of
real data, where they previously disagreed by nearly a billion.

### 5.2 Evidence grading — E7 is not in the same class as E1–E5

These findings are **not** equally proven, and the plan must not treat them as if they were.

| Grade | Findings | Nature of the evidence |
|---|---|---|
| **Proven** | E1, E2, E3, E4, E5, D3, D5, and the §4.1 cash tie-out | Exact arithmetic against the client's Total column — residual `0.00`. Not a judgement call |
| **Structurally proven** | D1, D2, D4, D8, Q5 | No arithmetic possible (the cells are text, or the data is 0), but only one reading is internally consistent — the alternatives are circular or drop data |
| **Inferred, high confidence** | **E7**, **D7/Q1** | Converging structural + comparative-fit + standard-form arguments. **No exact tie-out exists in the supplied data** |

E7 and D7 therefore carry an explicit empirical gate before they are considered settled.

**The gate.** With real, populated cash-flow data, hypothesis (a) predicts something measurable:
`closing_rollforward` should move *toward* `closing_independent` (which is computed from actual EOP
balances, entirely independently), so **Gross per-bucket residuals must fall** — materially, and on
the aggregate across all pairs, not just on cherry-picked cohorts. If they do not fall, the
inference is wrong and E7 is reverted; the ledger entry stays `assumed` until this test passes.

**Honest limitation: that test cannot be run today.** The expense-cash-flow columns are empty in
*both* available datasets — the client's `IFRS Summary` sheet (`Premium Received`, `Claims Paid`,
`Insurance Acquisition Cash flows`, `Other Cash Flows`, `RI Premium Paid/Claims received/Fixed
Commission received`, `Directly Attributable Expenses` all have `n=0` numeric values) and the
desktop sample `Input Format-Module 2/Expense-CF.xlsx`, which is a blank-template header row. With
zero cash flows, `cf_total == 0` and E7 is a **no-op** — its correction is invisible on every dataset
we currently hold.

There is a strong prior that E7 is real: an unexplained large aggregate residual across the 83 pairs
was already observed and attributed at the time to "cash-flow sign conventions". E7 is exactly that
defect. But *attributed* is not *demonstrated*.

**Consequence for sequencing.** E1–E5 ship on their proven arithmetic. E7 ships behind the same
code change but is **validated on the client's first run with populated cash-flow data**, and that
run is a named exit criterion (Phase 1b), not an afterthought. Until then E7 changes nothing
observable, which is precisely why it is safe to land it early — and why it must not be reported as
verified.

---

## 6. Design

### 6.1 Where the code goes

```
module2_engine/movement/
  notes_schema.py     ★ NEW  declarative note schema (lines × columns × refs), pure stdlib
  notes.py            ★ NEW  build_notes(view) -> NoteResult; pure fn over SheetResult row values
  workbook.py           MOD  fix _FLATTENED_GROSS (E1–E5); render the 4 note tables
  compute.py            MOD  extend reconciliation_report with note tie-out controls C1–C3
scripts/
  extract_client_disclosure.py  ★ NEW  (E6) reproducible provenance
  gen_movement_schema.py        MOD  emit the note schema into schema.ts as well
```

### 6.2 Schema-as-code, consistent with the existing layer

The note schema follows the same pattern as `schema.py`: a committed JSON (`notes_source.json`,
extracted from the client file) plus a curated override layer carrying the D1–D7 resolutions —
each override annotated with its defect id so the deviation from the client's file is
self-documenting and auditable.

```python
@dataclass(frozen=True)
class NoteRef:
    sheet: str          # "Gross" | "RI" | ""    ("" = literal / intra-note)
    line_id: str        # resolved from the source row via schema.Line.row
    factor: float = 1.0 # −1.0 for the revenue sign flip (D7)

@dataclass(frozen=True)
class NoteLine:
    id: str
    row: int            # provenance: original row in the client's note sheet
    label: str
    kind: str           # opening | input | subtotal | closing | section | spacer
    columns: dict[str, NoteRef | float | str | Formula]   # bucket -> source
    total: str          # "sum" | explicit expression over note rows
```

Refs are stored as **line ids, not row numbers**, resolved once at load time against
`SCHEMA.sheets[...]`. A ref to a non-existent line fails `validate_notes_schema()` at import, so a
future SAMA renumbering cannot silently produce zeros — the failure mode we just found in E1–E3.

### 6.3 Computation

`build_notes(view)` consumes the **already-resolved** `_row_values(sheet, sres)` map that
`workbook.py` computes for `Gross`/`RI`. That guarantees, by construction, that the note lines
equal the movement lines they cite — the notes cannot drift from the sheet they present.

Every note line is a linear combination of `Gross`/`RI` line values, so the notes are **additive
across `(class, UWY)` pairs** exactly as `sum_sheet_results` already assumes. Entity, class and
cohort note tables therefore each tie to the sum of their parts, with no special-casing.

Sign handling is confined to the schema, never to the renderer: the only transformation any note
line applies is the `NoteRef.factor`, which is `-1.0` on exactly one line (revenue, §11 Q1) and
`1.0` everywhere else. Cash-flow rows carry the movement sheet's own liability-impact signs and are
**added**, matching both the client's `F31 = F11+F22+F28` and the E7 correction — so after Phase 1
the note closing and the movement closing use one consistent convention across the workbook.

### 6.4 Rendering

- **Static values, never formulas.** `processing/output_preview.py` loads with
  `data_only=True`, and openpyxl does not evaluate formulas — a formula-bearing cell previews as
  blank. The existing renderer already writes evaluated values; the notes must do the same. (The
  cross-sheet linkage the client describes is preserved *semantically* — regenerating the job
  regenerates every dependent sheet.)
- **Tabs and order.** Entity grain gets four dedicated tabs named exactly as the client's:
  `Gross_Note`, `RI_Note`, `IS`, `BS`, emitted **immediately after `Entity Total` and before the
  per-class sheets** — the client's own ordering puts the notes ahead of the detail. Per-class and
  per-cohort notes are stacked beneath the existing Gross/RI tables on each class sheet, preserving
  today's one-sheet-per-class model. Tab names still go through `_safe_sheet_name`, so a reserving
  class literally named `IS` cannot collide.
- **Degenerate inputs — defined, not incidental.** Three cases exist today and must each have a
  stated behaviour rather than whatever falls out:
  - `render_sama_workbook(levels=…)` **without `"entity"`** — `IS`/`BS` are entity-level by
    decision (§11 Q5), so they are simply not emitted; the note tabs are skipped, not faked from a
    partial roll-up.
  - **A view with no `RI` sheet** — `_render_view` already skips a `None` sheet; `RI_Note` follows,
    and `IS!C7` / `BS!C5` render `0`, never a crash.
  - **Empty result (no pairs)** — the existing fallback writes a single `Movement Analysis`
    placeholder sheet; the note layer contributes nothing and must not raise.
- **Header.** `B2` *"As at Val Date"* is replaced with the job's `reporting_date`
  (already plumbed through `render_sama_workbook(reporting_date=…)`), falling back to the literal
  when absent.
- **Styling.** Accounting number format
  `_(* #,##0_);_(* \(#,##0\);_(* "-"??_);_(@_)`, merged bucket headers `B3:C3` / `D3:E3`,
  medium/thick rules on subtotal and closing rows, `'-'` for the structurally-absent RI liability
  rows — matching the client's presentation.
- **JSON companion.** `build_json_companion` gains a `"notes"` block per view
  (`{gross_note, ri_note, is, bs}`), each an ordered line list with values — so API and any future
  UI table consume structured data, never a cracked workbook.

### 6.5 New tie-out controls (extends `reconciliation_report`)

| # | Control | Assertion |
|---|---|---|
| **C1** | Note ↔ movement service expenses | `Gross_Note.insurance_service_expenses.Total == Gross.row31.Total` (§3.1) |
| **C2** | Note ↔ movement revenue / finance | `Gross_Note.revenue == ±Gross.row26`; `Gross_Note.finance == Gross.row57`; RI equivalents |
| **C3** | Note closing coherence | note closing (`opening + changes + cash flows`) vs `Gross.closing_rollforward`; the gap is reported per bucket, **not** silently absorbed — the note omits several movement lines (FX, other movements, investment components), so a gap is expected and must be *visible* |
| **C4** | IS ↔ BS linkage | `BS!C4 == Gross_Note.closing_net.Total`, `BS!C5 == RI_Note.closing_net.Total`, `IS!C8 == C5+C6+C7` |

**Tolerance.** "Ties exactly" means within the existing `DEFAULT_TOL_ABS = 1.0` / `DEFAULT_TOL_REL =
1e-4` envelope already used by `reconciliation_report`, **not** float equality — these are sums of
83 pairs of IEEE-754 doubles and `==` would flap. C1/C2/C4 are equality controls at that tolerance;
C3 is a *reported gap*, never a pass/fail.

**Single source of truth for `IS`.** The client's `IS!C14`/`C15` read `Gross!J57` / `RI!K48`
directly rather than the note's own finance row. Numerically identical, but we source **from the
notes** (`Gross_Note.finance`, `RI_Note.finance`) so that every `IS` figure has exactly one upstream
path. C2 asserts the two agree, which keeps the client's cross-check meaningful instead of
discarding it.

These join the existing per-bucket roll-forward residual in `input_meta.movement_warnings`, so the
UI's reconciliation banner surfaces them with no frontend change beyond copy.

### 6.6 Frontend impact — smaller than it looks

`MovementAnalysisPage.tsx` renders results through the **generic** `OutputPreviewDialog`
(file list → sheet list → paged rows). New worksheets appear automatically in preview and in the
downloaded zip. `src/features/movement/schema.ts` is generated but **currently imported nowhere** —
it is a prepared artifact for a future in-app table. So:

- **Required:** none for functionality.
- **Recommended:** regenerate `schema.ts` with the note schema (keeps the CI drift check honest);
  extend the reconciliation banner to render C1–C4; update the page's description copy to mention
  the note/IS/BS sheets.

---

## 7. Implementation phases

| Phase | Work | Exit criteria |
|---|---|---|
| **0 — Provenance** | Add `scripts/extract_client_disclosure.py` (E6), covering **all six** sheets — `Gross`/`RI` *and* the four note sheets, so `notes_source.json` is as reproducible as the mapping is; re-extract and assert the committed `client_source_extract.json` is byte-identical; record the new file sha256 in `_meta` | Extraction reproducible from the new workbook; 265/265 re-verified in CI; `notes_source.json` regenerable from the client file, not hand-authored |
| **1 — Fix the Gross subtotals + roll-forward (E1–E5, E7)** | Correct `_FLATTENED_GROSS`; fix the Gross closing sign in `_FLATTENED_GROSS[72]` **and** `compute.py :: closing_rollforward` (Gross adds cash, RI subtracts); move the reconstructed formulas out of `workbook.py` into `schema.py` beside the other curated overrides so they are covered by `validate_schema()` | Unit test per row against the §3 verified arithmetic; §5.1 hypothesis table pinned as a test; before/after diff of a real movement run attached to the actuarial sign-off ticket; expect Gross residuals to **fall** materially |
| **2 — Note schema** | `notes_source.json` + `notes_schema.py` + `validate_notes_schema()`; D1–D8 resolutions encoded as annotated overrides | Schema validates at import; every ref resolves to a real `Gross`/`RI` line id; deviation ledger (§8) complete |
| **3 — Note computation** | `notes.py :: build_notes`; additive aggregation across grains | Golden-vector test: entity note == Σ class notes == Σ cohort notes; C1 ties exactly |
| **4 — Rendering** | 4 entity tabs + stacked per-class tables; accounting formats, merges, rules, `'-'` cells, reporting-date header | Workbook opens; `openpyxl(data_only=True)` returns numbers (not blanks) for every value cell — the preview contract |
| **5 — Controls** | C1–C4 in `reconciliation_report`; surfaced in `input_meta.movement_warnings` | Controls fire on a deliberately broken fixture |
| **6 — API + JSON** | `"notes"` block in `build_json_companion` | `test_movement_api` asserts the block's shape |
| **7 — Frontend** | **Required:** make the new `movement_warnings.notes` key optional in `MovementWarnings` and render the banner from `recon` alone when absent (§12.2) — otherwise opening a historical job breaks. **Then:** regenerate `schema.ts`, extend the banner with C1–C4, update page copy | `npm run build` clean; tsc error count not increased vs baseline (~37 pre-existing); a pre-change job fixture renders without error |
| **8 — Docs + sign-off** | Client information memo (D1–D8, E7 with evidence), updated `IFRS17_MOVEMENT_SOURCE_RECONCILIATION.md`, updated `IFRS17_MOVEMENT_PLAN.md` cross-ref | Deviations recorded in the mapping `_meta`; any client acknowledgement flips `assumed` → `client_confirmed` |

Plus one phase that is **not** code and cannot be scheduled by us:

| **1b — E7/D7 empirical validation** | On the client's first run carrying populated cash-flow data, capture the before/after `reconciliation_report` and confirm Gross aggregate residuals fall (§5.2) | Residuals fall materially → ledger flips to `verified`. They don't → **revert E7**, reopen §5.1. Blocking for *sign-off*, not for *shipping* — E7 is a no-op until such data exists |

Phases 0–1 are independently shippable and valuable on their own — they fix live defects in the
existing disclosure. No phase is blocked on client input.

### 7.1 Effort and sequencing

Phases 0–1 are ~1 focused day and carry the regression risk (they change existing output).
Phases 2–4 are the bulk of the new code, ~2–3 days, and carry almost no risk because they only add
sheets. Phases 5–8 are ~1 day. The critical path is Phase 1's actuarial review of the before/after
diff, which is calendar time, not engineering time — start it first.

---

## 8. Ambiguity registry (deviation ledger)

Every place our output intentionally differs from a literal reading of the client's file is
recorded in `notes_source.json` under `_meta.deviations`, keyed by defect id, with: the client's
literal cell, our resolution, the evidence, and a `status` of `assumed` | `client_confirmed`.
`build_json_companion` emits any `assumed` deviations into the job's warnings so the UI can show
*"N presentation assumptions pending client confirmation"* on the sign-off banner. Nothing silently
diverges from the signed source.

---

## 9. Testing strategy

- **Schema** — `validate_notes_schema()`: unique ids, every `NoteRef` resolves, every declared
  column is a real bucket, no cycles in intra-note references.
- **Arithmetic** — the §3 relations pinned as explicit unit tests against the client's `Total`
  column figures, so a future template revision that changes them fails loudly.
- **Tie-out** — C1 asserted at tolerance (`Gross_Note.service_expenses == Gross.row31`) on a golden
  vector built from the desktop reference dataset (`MOTOR COMPULSORY + OTHERS`/2023,
  `PROPERTY`/2022, `GROUP MEDICAL`/2024 — per movement plan §3a).
- **Additivity** — entity note == Σ class notes == Σ cohort notes, per bucket, per line.
- **Degenerate inputs** — the three cases in §6.4 (`levels` without `entity`, a view with no `RI`
  sheet, an empty result) each get a test. These are the paths that turn a disclosure bug into a
  500 on a Friday afternoon.

**Coverage limitation, stated plainly.** The golden vectors above exercise the opening build-up,
the P&L block and the closing — but **not the cash-flow section**, because every expense-cash-flow
column is empty in both available datasets (§5.2). So §4.1's completeness tie-out and the E7
correction are covered by *synthetic* fixtures with hand-set cash values, which prove the
arithmetic but not the mapping to real data. The first client run with populated cash flows is a
required validation step (Phase 1b), and until it passes, the cash-flow section of this disclosure
is tested but not *proven*. That gap should be visible to whoever signs off, not buried in a test
suite that reports green.
- **Regression** — `run_module2_process` output remains bit-identical (golden net) — the note layer
  touches only the movement path.
- **Preview contract** — round-trip the generated workbook through
  `output_preview.read_sheet_page` and assert the note tabs return numeric cells.
- **API/E2E** — movement job produces `IFRS17_Movement_Analysis.xlsx` with the four tabs, the JSON
  companion carries the `notes` block, and the warnings payload carries C1–C4.

---

## 10. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| D7 (revenue sign) resolved wrongly | **High** — flips the sign of the headline `IS` result | Three converging lines of evidence (§11 Q1); implemented as a one-line `factor: -1.0` in the schema; controls C1/C4 make the consequence explicit either way; recorded in the deviation ledger so the client's actuary sees it stated, not buried |
| E1–E5, E7 change existing movement output | Medium | Separate phases, separate sign-off, before/after diff attached; process output provably untouched (golden net) |
| **E7 cannot be empirically validated with the data we hold** — every cash-flow column is empty (§5.2) | Medium | E7 is a provable **no-op** on zero cash flows, so landing it early is safe; Phase 1b makes validation on the client's first populated run an explicit, named gate rather than an assumption that quietly ages into fact |
| Historical movement jobs break the Result card once the warnings payload grows | Medium | `movement_warnings.notes` typed optional; banner falls back to `recon` alone; pre-change fixture test (§12.2) |
| Client "fixes" their template later, invalidating our overrides | Medium | Deviation ledger (§8) + Phase 0 reproducible extraction: re-running the extractor shows exactly which deviations became unnecessary |
| Note-vs-movement closing gap (C3) read as a bug | Low | Reported as an explicit, labelled reconciling item, never absorbed into a plug |
| Preview shows blanks | Low | Static-values rule (§6.4) enforced by the round-trip test |

---

## 11. Resolutions — decided from the file, nothing blocked

Every question this discovery raised is answered from internal evidence in the supplied workbook.
Implementation proceeds on these decisions; §8's ledger records each as `assumed` until the client
acknowledges, and every one is a one-line change to reverse.

### Q1 (D7) — Insurance revenue enters **negative**. Confidence: high.

Three independent lines of evidence converge:

1. **`IS` internal logic.** `C6` (*Insurance service expenses*) is `+551.7m` and `C22`/`C27` are
   labelled *"Total **loss**"* / *"NET **LOSS**"* while positive — so the statement is
   expense-positive. `C8 = SUM(C5:C7)` is labelled *Insurance service result*; a result that
   **added** positive revenue to positive expenses would be meaningless. Revenue must be negative
   for `C8` to be a result at all.
2. **IFRS 17 disclosure form.** The standard note (IFRS 17 ¶100) rolls the liability forward as
   *opening → insurance revenue (releases LRC, negative) → service expenses (positive) → finance
   (positive) → cash flows → closing*. `Gross_Note` reproduces that shape line for line.
3. **Numerical fit.** Against the client's own closing balance, the revenue-negative form fits at
   3.9%; the revenue-positive form at 7.4% and the current implementation at 59.9% (§5.1 table).

Encoded as `factor: -1.0` on the row-13 `NoteRef`, so reversing it is a single edit.

### Q2 (D1, D2) — Confirmed by the cash-flow completeness tie-out. Confidence: high.

`Gross_Note` row 26 = `Gross!G67 + Gross!G68`, row 27 = `Gross!C69 + Gross!C70`; the three cash rows
then reconstruct `Gross!J71` exactly (§4.1). The row-40/row-70 equal-and-opposite pair
(±49,658,854.56684359) independently confirms the *Other Cash Flows* fold-in. RI mirrors it.

### Q3 (D6) — Hard `0`. Confidence: high.

*Expected credit loss*, *IFRS9 Adjustments* and *Zakat expense* have no source anywhere in the
Module 2 pipeline — they are IFRS 9 / general-ledger items, not IFRS 17 measurement. Excel already
coerces the client's blank cells to 0 inside `C27 = C22+C24`, so rendering an explicit 0 *is* the
file's behaviour, made auditable. They are **not** routed through the movement-override dataset:
that dataset is keyed `(class, UWY)`, and these are entity-level GL figures — forcing them into a
class×cohort grain would invent an allocation nobody asked for. If the client later wants them
entered, the correct surface is an entity-level job input, added then.

### Q4 (D8) — Implement the two lines exactly as given. Confidence: high.

`BS` is an IFRS 17 *extract* — the closing balances of the two notes, the only two balance-sheet
captions IFRS 17 produces. A fuller balance sheet would require GL data the engine does not hold.
Additive later if asked; nothing is lost by shipping the extract.

### Q5 — Notes at every grain; `IS`/`BS` at entity grain only. Confidence: high.

`Gross_Note`/`RI_Note` are re-presentations of `Gross`/`RI`, which we already produce per entity,
class and cohort — and every note line is a linear combination of movement lines, so all three
grains tie to the sum of their parts for free. `IS` and `BS` are **financial statements**: they are
entity-level by definition, and a per-class `IS` would imply allocating GL items (interest income,
zakat, IFRS 9) that have no class dimension. Entity only.

### Q6 (D3, D4, D5) — All three repointings confirmed. Confidence: high (D3, D5), high (D4).

- **D3** — the §3.1 tie-out is exact to the cent (`Gross_Note!F20 == Gross!J31 == 501,110,496.06`).
  A coincidence at that precision is not plausible.
- **D4** — rows 16–19 of the note are a *partition* of `Gross` row 31; citing row 31 from inside its
  own decomposition is circular. Row 42 is the correct member of that partition.
- **D5** — every other Total cell in both notes is `SUM` of its own row.

### For client information (non-blocking)

A short memo should accompany delivery, listing D1–D8 and E7 with the evidence above, so their
actuary can acknowledge the corrections and — if they wish — fix the source template. Delivery does
not wait on it: the ledger (§8) already surfaces every deviation on the sign-off banner, and each is
reversible in one line.

---

## 11b. Phase 0–1 — delivered

**Phase 0 (provenance, E6).** `scripts/extract_client_disclosure.py` added. Default mode
re-extracts from the client workbook and diffs against the committed artifact, exiting
non-zero on drift — the mode CI should run. Result on first execution:

```
OK — 127 lines, 265 bucket cells reproduce the committed extract
```

Regeneration is byte-identical to the committed file except `_meta.source_sha256`, which
now records the current workbook (`1de8f8f0…`, superseding `16f96560…`). Re-running
`gen_movement_mapping.py` propagated that one line to `mapping_source.json` and changed
**nothing else** — coverage identical (Gross D:33 Δ:4 O:0 M:36 structural:17). The signed
mapping is therefore provably unaffected by the client's new file.

**Phase 1 (E1–E5, E7).** Reconstructed formulas moved from `workbook.py` into
`schema.RECONSTRUCTED_FORMULAS`, each annotated with its verification, and now covered by
`validate_schema()` — which rejects a formula on an input line, a reference to a
non-existent row, and self-reference. `CLOSING_CASHFLOW_SIGN` added; `compute.py` and the
rendered row 72 both consume it.

Before/after on the real desktop dataset (83 pairs, entity Total column):

| Row | Line | Before | After | Δ |
|---:|---|---:|---:|---:|
| 38 | Insurance Acquisition cash flows | 0.00 | 27,795,566.30 | **+27,795,566.30** |
| 57 | Insurance finance expenses/income | 0.00 | −3,553,928.39 | **−3,553,928.39** |
| 32 | Incurred claims and other expenses | 239,198,206.61 | 266,993,772.91 | +27,795,566.29 |
| 31 | Insurance service expenses | 338,261,103.61 | 366,056,669.90 | +27,795,566.29 |
| 48 | Change in Ultimate for Past Service | 99,062,896.99 | 99,086,497.46 | +23,600.46 |
| 47 | Past Service: Changes to LIC | 99,062,896.99 | 99,062,896.99 | 0.00 |
| 64 | Total changes in P&L and OCI | 135,137,795.35 | 103,788,300.66 | −31,349,494.69 |
| 72 | Closing | 657,130,185.15 | 625,780,690.46 | −31,349,494.69 |

Two independent cross-checks confirm the newly surfaced figures are the right ones:
row 38 equals the `Commission Expense` column of `IFRS Summary` summed over all pairs
(27,795,566.30) **exactly**, and row 57 equals the
`GROSS - Insurance Finance (Income)/Expense` column summed (−3,553,928.39) **exactly**.
Row 47 is unchanged while row 48 moves by the ULAE line — precisely the predicted
signature of E5: the total is preserved, only the internal split is corrected.

E7 changed nothing on this dataset, exactly as §5.2 predicted — the cash-flow columns are
empty, so `cf_total` is 0 and the sign is unobservable. Its validation stands open as
Phase 1b.

**Phase 2 (note schema).** `extract_client_disclosure.py` extended to the four note
sheets, producing `notes_source.json` — 63 lines, 187 source cells, extracted **verbatim
including the defects**: the three unparsed text cells keep their `literal_text`, D5's
`=F12+F23+F29` is recorded as a sum over rows 12/23/29, and IS rows 11/20/24 are recorded
as sections because the client has no value cell there.

`notes_schema.py` resolves that source into typed `NoteSheet` / `NoteLine` /
`ColumnSource`, converting every Excel row reference into a **line id** at load time, then
applies `DEVIATIONS` — a 12-entry ledger where each correction carries its defect id, the
client's literal cell, our resolution, the evidence, and a `status` (all `assumed`).
`validate_notes_schema()` checks unique ids, that every movement term resolves to a real
line and a declared bucket, that note references resolve, that sums reference real lines,
that `row_total` covers declared columns, that no intra-note sum cycles, and that every
deviation targets a real line.

Two structural tie-outs are asserted rather than trusted: the note's cash block must cover
**exactly** the movement sheet's cash-flow rows (which is what forces D1's `G67+G68` and
the `Other Cash Flows` fold-in), and no `client_literal` may survive into the resolved
schema — an unparsed client cell without a deviation fails the build.

One finding worth carrying into Phase 3–4: several line ids exist on **both** movement
sheets — `other_cash_flows` is Gross r70 *and* RI r61; `past_service_changes_to_…` is
Gross r47 *and* RI r38. Any lookup keyed on line id alone silently resolves to the wrong
row. `MovementTerm` carries `sheet`, so the schema is safe, but the renderer must key on
`(sheet, line)` too. A test helper that got this wrong is exactly how it surfaced.

**Phase 3 (note computation).** A layering fix came first: the subtotal evaluator lived in
`workbook.py`, but `notes.py` needs the same numbers and `workbook.py` must import
`notes.py` to render them — a cycle. `row_values` and its helpers moved into `compute.py`
(numbers belong with computation, not presentation), with `_row_values` kept as an alias
so existing call sites and tests are untouched. `compute.line_totals` was added: the same
resolution keyed by **line id** with `Total` appended, which is how the notes address
movement lines.

`notes.py :: build_notes(view)` evaluates all four tables from a view's `SheetResult`s —
pure, no frames, no I/O. `"-"` cells evaluate to `None` and render as a dash while
contributing 0 to any sum containing them, matching Excel's treatment of the client's
literal `-`.

Verified on the real desktop dataset at entity grain (83 pairs, 12 classes) — every
control from §6.5 holds:

| Control | Note | Movement |
|---|---:|---:|
| C1 service expenses == `Gross` r31 | 366,056,669.90 | 366,056,669.90 |
| C2a revenue == −`Gross` r26 | −473,398,898.95 | −473,398,898.95 |
| C2b finance == `Gross` r57 | −3,553,928.39 | −3,553,928.39 |
| C2d RI recoverable == `RI` r27 | 15,996,778.94 | 15,996,778.94 |
| C4a `BS` liabilities == note closing | 411,096,232.36 | 411,096,232.36 |
| C4b `BS` reinsurance == note closing | 141,403,419.50 | 141,403,419.50 |
| C4c `IS` service result == C5+C6+C7 | −41,531,601.11 | −41,531,601.11 |
| Additivity: entity == Σ 12 classes | 411,096,232.36 | 411,096,232.36 |

C1 is the tie-out §3.1 predicted, now holding on live data rather than on the client's
Total column. The cash controls (C2c/C2e) pass trivially at 0 — the cash-flow columns are
empty in this dataset, the same limitation §5.2 records.

The resulting `IS` reads coherently: revenue −473,398,898.95 (equal to the `GWP` column
summed over all pairs), expenses 366,056,669.90 (the Phase 1 corrected figure), service
result −41,531,601.11, and a net result of −45,351,920.74 — a negative "NET LOSS", i.e. a
profit, which is what the D7 sign resolution implies for this data.

**Phase 4 (rendering).** The four note tables render as **static values** into dedicated
tabs at the entity grain — `Entity Total`, then `Gross_Note`, `RI_Note`, `IS`, `BS`, then
the per-class detail, matching the client's own ordering. Class and cohort sheets stack the
two note tables beneath their movement tables; `IS`/`BS` stay entity-only per §11 Q5. The
`As at Val Date` placeholder is replaced by the job's reporting date, `-` cells render as a
dash, and the client's accounting number format is used throughout.

The column-group headers are taken from the client, with one correction: `RI_Note`'s header
block in their file is a copy-paste of the Gross captions ("Liability for remaining
coverage"), so the RI wording comes from their own `RI` movement sheet instead.

`build_json_companion` gains a `notes` block per view, `notes_schema_version`, and a
top-level `deviations` list carrying every `assumed` entry from the ledger — the data
Phase 5 will surface on the sign-off banner.

Verified end-to-end on the real desktop dataset: the workbook renders with all four tabs,
and every note cell reads back through `openpyxl(data_only=True)` as a **number**, which is
the preview contract. The rendered `IS` reproduces Phase 3's computed figures exactly.

**Measured performance** (correcting §12.4's estimate, which was optimistic): render goes
from 5.3s to 8.3s (+3.0s) and the workbook from 483 KB to 676 KB, against a job whose
process-intermediates stage dominates total runtime. JSON companion adds 1.0s. Acceptable,
but it is a real cost rather than the "no measurable impact" originally claimed.

**Phase 5 (controls).** `notes.note_controls(view)` evaluates C1/C2a–f and C4a–c against
the movement sheet at runtime, with the same two-sided tolerance the roll-forward
reconciliation uses. `notes_report(result)` aggregates them across grains and lands in
`input_meta.movement_warnings["notes"]` — an **additive** key, so jobs produced before this
shipped still parse (§12.2).

`notes_report` takes a `MovementResult`, symmetric with `reconciliation_report`, so callers
and their test seams have one collaborator to patch rather than two. That shape came out of
a real failure: the first version took pre-aggregated views, and
`test_movement_task_feeds_inherited_bytes_to_engine` — which mocks the engine wholesale —
broke because the aggregation ran against a stub before the mock could intercept.

On the real dataset: **960 controls across 96 views, 0 breaches.**

**C3 is now measuring E8.** The entity closing gap is **−946,797,797.91**, which is exactly
−2 × the note's revenue (−473,398,898.95). That is the E8 divergence (§5.1b) — the note
negates revenue while `closing_rollforward` adds it — surfacing at full scale rather than
on a synthetic fixture. C3 is reported, never absorbed, so it stays visible until E8 is
resolved; once it is, this number should collapse to the genuinely omitted lines (FX, other
movements, investment components). It is the strongest argument yet for taking E8 next.

**Phase 7 (frontend).** `MovementWarnings` gains an optional `notes?: MovementNoteControls`,
and the Result card derives `noteControls` from it defensively — a job completed before this
shipped renders from `reconciliation` alone. `src/api/movementWarnings.test.ts` pins that
back-compatibility at both type and runtime level: a non-optional `notes` would fail to
compile against the historical payload in the first test, which is the regression being
guarded. The banner reports the control count, any breaches, the C3 closing gap (labelled as
reported-not-an-error), and the count of assumed deviations. Page copy now names the four
sheets. tsc 0 errors, 72 frontend tests pass, `vite build` clean.

**Deviation from this plan:** §6.6 recommended regenerating `schema.ts` with the note schema.
Not done, deliberately. `MOVEMENT_SCHEMA` is already generated and imported nowhere; adding
a second unused mirror would be more dead code, not a stronger drift guard. The existing
`test_schema_ts_mirror_is_in_sync` still passes — the movement schema is unchanged. When an
in-app note table is actually built, generate the mirror then.

**Phase 8 (client memo).** `docs/IFRS17_NOTE_CLIENT_MEMO.md` — the twelve corrections with
evidence, the revenue-sign question stated as the one item worth their attention, and the two
`Gross` sheet corrections flagged explicitly because they change figures the client has
already seen.

**Post-build audit — three gaps found and closed.** Asking "is this actually finished?"
turned up three things this plan specified and the implementation had not delivered:

1. **Nothing ran the extractor.** Phase 0's exit criterion was "265/265 re-verified in CI",
   but the script existed with no test invoking it — a hand-edit to either extract, or a
   client file that no longer said what we recorded, would have passed CI silently. Closed
   by `test_client_extract_provenance.py`, which runs the verifier, guards the cell counts
   against a silently truncated extraction, and checks the recorded sha still matches the
   workbook on disk. Skips when the client workbook isn't checked out.
2. **`SCHEMA_VERSION` was never bumped**, despite E1–E5, E7 and E8 all changing reported
   figures — a consumer keyed on it could not distinguish a corrected artifact from an
   earlier one. Rather than edit the extraction artifact (whose version describes the SAMA
   template, which did not change), the version is now composite:
   `TEMPLATE_VERSION + CURATED_REVISION` → `2026.06+r2`, with the revision documenting
   exactly which corrections it covers.
3. **The note tabs carried no version stamp**, though §12.1 said both versions go into the
   workbook. They now carry `schema v… · notes v…` plus the pending-assumption count.

A fourth surfaced while fixing (2): `gen_movement_schema.py` emitted `SCHEMA.version`, not
`SCHEMA_VERSION`, so the frontend mirror would have advertised `2026.06` while the workbook
and JSON said `2026.06+r2` — two different things both called "schema version". The
generator now emits the composite, and the drift test covers it.

Tests: 95 engine tests pass, including a new
`test_movement_reconstructed_formulas.py` that pins every reconstructed relation against
the client's Total column. The full Django suite runs 182 tests with 2 failures, both in
`processing.tests.test_dataset_e2e` (a Module 1 summary-dataset path); both reproduce on a
clean `HEAD` with these changes stashed, so they are pre-existing and unrelated.

---

## 12. Versioning, compatibility and rollout

The analysis above is only half of production-readiness. These are the operational commitments.

### 12.1 Schema versioning

`SCHEMA_VERSION` is currently `"2026.06"` and is stamped into the workbook header, the JSON
companion and (via `gen_movement_schema.py`) `schema.ts`, where CI fails on drift. Adding the note
layer **bumps it** — a consumer must be able to tell a note-bearing artifact from a pre-note one by
version alone, not by probing for a key. The note schema carries its **own** `NOTES_SCHEMA_VERSION`
so a note-only revision does not invalidate movement-schema-keyed caches, and both are emitted into
the JSON companion and the workbook meta row.

### 12.2 Backward compatibility

- **JSON companion** — the `"notes"` block is *additive*. Every artifact produced before this ships
  lacks it; consumers must treat it as optional. Re-rendering old jobs is not in scope (outputs are
  immutable artifacts under retention).
- **`input_meta.movement_warnings`** — C1–C4 land under a new `"notes"` key alongside the existing
  `reconciliation` block. The frontend `MovementWarnings` / `MovementReconciliation` types in
  `src/api/module2.ts` must mark it optional, or the Result card breaks on any historical job the
  user opens. This is the one frontend change that is **required**, not cosmetic — §6.6's "no
  functional work" applies to rendering the workbook, not to typing the warnings payload.
- **Old jobs in the UI** — the reconciliation banner must render from `recon` alone when the notes
  block is absent. Test with a fixture built from a pre-change job.

### 12.3 Rollout and rollback

- **Phases 2–8 are purely additive** — new sheets, new JSON key. Rollback is reverting the commit;
  no data migration, no artifact rewrite.
- **Phase 1 (E1–E5, E7) changes numbers in an existing client-visible deliverable.** It ships as its
  own commit, with the before/after workbook diff attached to the sign-off ticket, so it can be
  reverted independently of the note layer. The note layer *depends* on it (the notes read those
  rows), so the revert order is notes-then-fixes, and that ordering is recorded in the commit
  messages.
- **No feature flag.** A flag here would mean shipping two disclosure conventions simultaneously and
  letting a runtime toggle decide which numbers a regulator sees — worse than a clean revert. The
  deviation ledger provides the auditability a flag would pretend to.
- **Deploy note:** `openpyxl` only. Do **not** introduce `xlsxwriter` — it is an optional dependency
  absent from `poetry.lock` and from production, and adding it would flip the global `WRITE_ENGINE`
  and put the bit-identical process goldens at risk.

### 12.4 Performance

**Measured after Phase 4** (superseding the estimate this section originally carried):
rendering goes from **5.3s to 8.3s (+3.0s)** and the workbook from **483 KB to 676 KB** on
the real 83-pair dataset; the JSON companion adds 1.0s. The cost is real — the note layer
re-resolves both movement sheets per view — but small against a job whose
process-intermediates stage dominates, and nothing in the bit-identical process path is
touched. No benchmark gate added; the existing movement job benchmark stays the guard. If
this ever matters, the fix is memoising `line_totals` per view rather than per note.

### 12.5 Dependency: sign-off has no workflow yet

C1–C4 are described as "gating sign-off", but the `module2.signoff` permission and approval action
were deferred and **do not exist**. Today the controls surface as a banner a user can read and
ignore. That is a real limitation of the disclosure pipeline, not of this plan — but it should be
stated in the same breath as "gates sign-off", and it belongs on the roadmap before this disclosure
is relied on for statutory reporting.
