# IFRS 17 Note Disclosure — corrections applied, for your confirmation

**Re:** `Module2_Final_Output.xlsx` — the four new sheets `Gross_Note`, `RI_Note`, `IS`, `BS`
**Status:** implemented and delivered · **Action requested:** confirm or correct the twelve
presentation assumptions listed in §2 · **Not blocking:** the disclosure is produced today;
each item below is reversible in one line if you read it differently.

---

## 1. What we implemented

The four sheets are produced automatically as part of the IFRS 17 Movement Analysis run,
from the same data as the `Gross` and `RI` sheets — no separate input, no manual step. They
appear as their own tabs at entity level, and the two note tables are also produced per
reserving class.

We verified your description that "all information in these sheets comes from Gross and RI":
every populated cell in the four sheets is a literal, a reference into `Gross`/`RI`, or a sum
within the notes. There is no other data source. We also confirmed that the `Gross` and `RI`
sheets themselves are **unchanged** from the version you sent previously — all 265 mapped
cells match — so this work did not reopen the agreed measurement mapping.

---

## 2. Corrections we applied to the template

Working through the four sheets against real data surfaced a number of cells that could not
have been intended as written. In each case we implemented what the label and the arithmetic
require, and recorded the change. **Please confirm each, or tell us to revert it.**

| # | Cell | What the file contains | What we implemented | Why |
|---|---|---|---|---|
| **1** | `Gross_Note` D26, E26 | The text `Gross!G67+Gross!G67` — no leading `=`, and the same cell twice, so Excel treats it as a label and the row reads zero | `=Gross!G67+Gross!G68` | The row is "Claims **and other directly attributable expenses** paid"; row 68 is that expense. It is also what makes the note's cash section equal `Gross` row 71 exactly |
| **2** | `Gross_Note` C27 | `=Gross!C69` only | `=Gross!C69+Gross!C70` | `Gross` row 40 ("Other Acquisition Cash Flows") and row 70 ("Other Cash Flows") are equal and opposite in your Total column (±49,658,854.57), so row 70 is the cash leg of the acquisition line. Without it the note drops 49.7m |
| **3** | `RI_Note` B25 | The text `RI!D57+RI!D59` — no leading `=` | `=RI!D57+RI!D59+RI!D61` | Same defect; row 61 added for the same completeness reason (nil today) |
| **4** | `RI_Note` D26 | `=RI!H58` only | `=RI!H58+RI!H60` | Completeness against `RI` row 62 (nil today) |
| **5** | `Gross_Note` D18, E18 | `=Gross!G45` / `=Gross!I45` — a **Loss Component** amortisation row | `=Gross!G47` / `=Gross!I47` | The label is "changes that relate to past service – adjustments to the LIC". With row 47, `Gross_Note` F20 equals `Gross` J31 exactly at 501,110,496.06. With row 45 it is overstated by 50,594,373.17 |
| **6** | `Gross_Note` C17 | `=Gross!E31` — the whole *Insurance service expenses* subtotal | `=Gross!E42` | Rows 16–19 of the note decompose row 31, so citing row 31 inside its own decomposition is circular. Identical today only because row 42 is the sole Loss Component contributor |
| **7** | `RI_Note` C17 | `=RI!F27` — the whole *Amounts Recoverable* subtotal | `=RI!F33` | Same as above, on the RI side |
| **8** | `Gross_Note` F32 | `=F12+F23+F29` — three blank spacer rows, so permanently zero | `=SUM(B32:E32)` | Every other Total cell in both notes sums its own row |
| **9** | `IS` C11, C20, C24 | *Expected credit loss*, *IFRS9 Adjustments*, *Zakat expense* have no value cell, although C13 and C27 sum over them | Explicit `0` | These are IFRS 9 / general-ledger items with no source in the Module 2 data. Excel already treats the blanks as zero; we made that explicit. **If you want these entered per run, tell us and we will add them as inputs** |
| **10** | `Gross_Note` B13:E13 and `IS` C5 | Insurance revenue carried through with the same sign as the `Gross` sheet | Revenue enters **negative** | See §3 — this is the one worth your attention |
| **11** | `RI_Note` header block | The column captions are the Gross ones ("Liability for remaining coverage") | The RI captions from your own `RI` sheet | Appears to be a copy-paste when the sheet was created |
| **12** | `BS` | Two lines only, no totals | Implemented exactly as given | We assume it is an IFRS 17 extract rather than a full balance sheet. Confirm if more is expected |

---

## 3. The one item we would most like confirmed: the sign of insurance revenue

In your file every revenue cell in the notes evaluates to zero, because the `Gross` sheet's
per-column cells point at row 1 of `IFRS Summary` — the header row — and so return text
rather than numbers. That means the intended sign of revenue is not observable anywhere in
the file. With real data flowing it becomes the largest number on the statement, so it has
to be decided rather than inherited.

**We implemented revenue as negative.** Three things point that way:

1. Your `IS` is expense-positive: C6 (*Insurance service expenses*) is positive, and C22 and
   C27 are labelled "loss" while positive. For `C8 = SUM(C5:C7)` to be an *Insurance service
   result*, revenue has to enter negative — otherwise the line adds positive revenue to
   positive expenses.
2. It matches the standard IFRS 17 note, where revenue releases the liability for remaining
   coverage and therefore reduces it.
3. It fits your own closing balance best: 3.9% against the alternative's 7.4%.

On our current data this produces an insurance service result of −41,531,601 and a net
result of −45,351,921 — i.e. a **profit**, shown negative in your loss-positive convention.
If your intended presentation is the opposite, this is a one-line change; please say so.

---

## 4. Two corrections to the `Gross` sheet itself

Implementing the notes required reading `Gross` rows that had been flattened to static
values in your file, losing their formulas. Two of our earlier reconstructions were wrong,
and the notes made that visible. These change figures in the `Gross` sheet you have already
seen, so they are flagged explicitly.

**(a) Missing subtotals.** Rows 38 (*Insurance Acquisition cash flows*), 42, 44 (*onerous
contracts*) and 57 (*Insurance finance expenses/income*) had no formula at all and rendered
as zero regardless of input. They now sum their children. On our data row 38 becomes
27,795,566.30 and row 57 becomes −3,553,928.39 — each equal to the corresponding source
column summed across all classes. Row 32 also now absorbs row 38, and the ULAE line sits
beside "Change in Ultimate" rather than inside it, both as your Total column requires.

**(b) The closing roll-forward.** Row 72 was a static in your file, so we had reconstructed
it from the RI equivalent. That was wrong twice over: the cash-flow sign is mirrored between
a liability and an asset, and — more importantly — row 64 (*Total changes in the statement of
profit or loss and OCI*) is a **profit-and-loss** aggregate and cannot be used in a balance
roll-forward, because it carries revenue with the opposite sign to the one a balance
requires.

Your `RI` sheet already encodes the correct treatment: `D63 = D4+(D55−D62)` reaches its
closing through `D47 = D27−D21`, which negates the allocation block. We have applied the
same logic to `Gross`. The result is that the note's closing balance and the movement
sheet's closing balance now agree **exactly**, where they previously differed by
946,797,798 at entity level.

---

## 5. What we would like back

1. **Confirmation of §3** — the sign of insurance revenue. This is the only item that changes
   the headline figures.
2. **Confirmation, or correction, of the twelve items in §2.** A simple "agreed" against the
   list is enough; we will record each as confirmed.
3. **§2 item 9** — whether *Expected credit loss*, *IFRS9 Adjustments* and *Zakat expense*
   should become per-run inputs rather than zero.
4. **§2 item 12** — whether `BS` is intended as the two-line extract.
5. **Optionally**, a corrected source template. Not required — our corrections are recorded
   and applied automatically — but it would let us retire the deviation list.

Until we hear back, every item in §2 is reported inside the application as a "presentation
assumption pending confirmation", so anyone reviewing the disclosure can see exactly where
our output departs from your template and why.
