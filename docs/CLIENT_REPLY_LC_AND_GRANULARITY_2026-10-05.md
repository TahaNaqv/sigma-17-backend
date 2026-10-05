# Reply — Loss Component links done; a question before we change the time grain

Following your two notes: the link updates in `Module2_Final_Output-check.xlsx`, and the
request to run Module 2 monthly, quarterly or annually. The first is done, and we have
three small points for you to confirm. The second rests on something we need to correct
before we build anything, so we have a few questions for you.

---

## Part 1 — The link updates are implemented

All the cells you marked in yellow are now produced by the system:

* **IFRS Summary CK:DD.** The prior-period LC values (CK:CR), the current-period LC values
  (CS:CZ), and `Gross_LC_New`, `Gross_LC_Change`, `RI_LC_New` and `RI_LC_Change` (DA:DD).
  The column order matches your file exactly.
* **Gross.** The Loss Component column takes its opening balance, new losses and reversals
  from those columns.
* **RI.** The Loss Recovery Component column does the same, and D6 and J20 are corrected.
  Both were genuine errors on our side, and thank you for catching them. D6 used the
  period's RI premium paid where it needed the opening premium payable balance. J20 mixed
  the closing RA (OS) into an opening balance.
* **LRC and LIC BOP-EOP Reconciliation.** These now include the Loss Component, the RI LRC
  block with the Loss Recovery Component, and the RI LIC block.

On the reference data, the Loss Component and Loss Recovery Component now roll forward
exactly: opening + new + change = closing, with zero difference in every class and
underwriting year. The J20 correction also removed 40 risk-adjustment differences that the
RI sheet had been carrying.

### Where the prior-period LC values come from

The Previous Period file did not carry LC values, so there was nothing to fill CK:CR from.
It now accepts an optional third sheet, **`LC_BOP`**, which is simply **last period's `LC`
sheet, copied in as it is** (same columns). It can also be entered on screen as a dataset.
If it is not provided, opening LC balances are taken as zero and the run says so. For the
first live run, please include last period's `LC` sheet.

### Three points to confirm

1. **Underwriting years after the accounting period.** Your formula counts LC as "new"
   only when the UWY equals the accounting period. The data has three cohorts beyond it
   (two 2025, one 2026). Under that formula, any LC on those cohorts would show as neither
   new nor changed, and the roll-forward would not close. We count UWY greater than or
   equal to the accounting period as new. The result is identical for every year up to the
   accounting period. Please confirm.
2. **Signs in the RI reconciliation totals.** Your mock-up adds RI UPR, RI Payable and UCR
   straight across. We subtract the payable and the unearned commission, and in the LIC
   block the provision for non-performance, exactly as the RI movement sheet does. With
   that, "RI LRC" and "RI LIC" agree with the opening and closing balances on the RI sheet.
   Please confirm this is the intended presentation.
3. **Two references we did not copy.** In the LIC EOP block, cells R16, S16 and T16 point
   to the previous-period columns (discounting, RI claims receivable, RI provision). We
   have used the current-period columns, since that block is the closing position. Gross
   D29 is typed as text, so it did not move when you inserted the new columns. We kept
   the formula you corrected on 10 September (change in premium debtors' provision).

The three manual inputs that used to supply these RI lines (new onerous, reversal, and
methodology difference BOP) are no longer needed. Existing input files still load. If one
of those fields carries a value, the run ignores it and shows a note.

---

## Part 2 — Monthly / quarterly / annual: what exists today

Your note says the triangles and UPR in Module 1 were changed from quarterly to monthly or
annual. That is only partly the case, and the difference matters here.

**What changed.** The *Triangles* screen in Module 1 can display the paid and reported
triangles monthly, quarterly or yearly. It is a view for analysis and for checking
development.

**What did not change.** Everything Module 1 hands to Module 2 is still quarterly. That is
the UPR and the UPR Run-Off, the IBNR Summary (whose development factors drive the payment
pattern), the Allocation EP and the LIC (OS) Summary. This was the decision we agreed for
that work: show the other grains first, keep booking quarterly. One reason was that the
Previous Period `LIC_BOP` you supply is keyed on quarterly accident periods (2,144 rows).

So Module 2 cannot simply "follow" Module 1 today. There is no monthly or annual run-off,
pattern or IBNR for it to follow. The Module 2 calculations themselves are straightforward
to switch. Discounting, future cash flows, IBNR allocation and the payment pattern already
run on a common period setting. The real work is upstream: Module 1 producing its booking
outputs at the chosen grain, and the opening balances being supplied at that grain.

### Questions before we scope it

1. **Which grain do you need for booking: annual, monthly, or both?** Annual is much the
   simpler case, because quarters add up to years exactly. Monthly needs month-level
   development (about 84 development periods over seven years). On the thinner classes
   that makes the factors volatile, as the monthly triangles already show.
2. **One grain for the whole run, or a different grain per reserving class?** We would
   recommend one grain per run, chosen in Module 1 and carried automatically through to
   Module 2.
3. **The first run after a change of grain.** The opening balances (`LIC_BOP`) will
   still be quarterly. For annual we can add the quarters together. For monthly we would
   need either opening balances supplied at accident-month level, or your agreement on how
   to treat quarterly opening balances in that first run. Which do you prefer?
4. **Discount curve.** We propose to keep your annual-band Discount Rate input unchanged
   and convert it to the chosen grain: the monthly rate is (1 + annual)^(1/12) − 1, and
   the annual rate is used as it is. Is that right?
5. **Payment pattern overrides.** Patterns you have entered by hand are quarterly. They
   would need to be re-entered at the new grain, since we cannot safely split one quarter
   into three months. Is that acceptable?

Once we have your answers, we will send a short scope and timeline for this part.
