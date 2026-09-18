# Reply — triangles, IBNR placement, and the quarter-end extract

Following your answers of 12 September. Three things below: one correction from us, one
consequence you should know about, and one request.

---

## On the zero-IBNR quarters — understood, and our earlier note overstated it

Thank you for explaining. We now understand the intent: IBNR is a balance-sheet position, the
book's IBNR was derived once (ELR on 2024-Q4: `0.6377 × 308,742,785 − 14,110,434 = 182,784,933`)
and booked in the latest accident quarter. That is a coherent approach, and our previous note —
which read the fifteen zero rows as something going wrong unnoticed — was wrong on that point.
We are glad it was deliberate.

Two narrower things still worth raising.

### 1. The recorded method does not describe what was done

Those fifteen rows carry `Selected Method = Reported BF`, with `Reported CDF = 1.0` and
`Implied LR` blank. In that state both terms of

> `Reported BF Ultimate = (1 − 1/Reported CDF) × EP × Implied LR + Reported Claims`

are zero, so the formula returns `Ultimate = Reported Claims`. The result matches your intent, but
the audit trail says a Bornhuetter-Ferguson calculation was performed when the intent was simply
"reported claims are ultimate for this quarter". An auditor reading the workbook cannot tell those
apart.

We are adding an explicit option for that intent — a **"Reported = Ultimate / no further
development"** selection — plus an on-screen note whenever a chosen method's inputs leave it
equal to an identity. Your numbers will not change; what changes is that the workbook will say
what you meant.

### 2. Where the IBNR sits does change your IFRS 17 figures

This one is not presentational. In Module 2 the accident period an amount occupies determines its
position in the run-off, and therefore its discounting:

* the accident period sets the cohort's **age**;
* the age selects which future quarters the cash flows are spread across;
* each future quarter carries its own cumulative discount factor;
* `Discounting Impact` is the difference between the discounted and undiscounted cash flows.

IBNR placed in the **latest** accident quarter is treated as the youngest cohort — with that
quarter's paid factor of 13.84, the model expects about **93% of it to be still unpaid** — and so
runs it off over the longest remaining pattern. That is the longest duration available, and
therefore the largest discount. The same IBNR attributed to the accident periods it actually
belongs to would run off sooner, discount less, and produce a **higher** LIC.

It also affects your LIC split by accident period, and next period's movement disclosure, which
joins prior-period figures on that same accident-period key.

We can measure this exactly for you — re-run the allocation with the IBNR attributed across
accident periods and difference the discounting impact — but we would need your Module 2 inputs
(discount curve and ULAE-RA) for the relevant run. We would rather measure it than estimate it;
say the word and we will.

---

## On quarter-end outstanding — yes is the answer that unlocks the most

This is the most valuable of your three answers. With outstanding at all sixteen quarter-ends
(31-03-2021 … 31-12-2024), in exactly the format and layout you already send:

* the **quarterly reported triangle becomes sound** — today, with only the four year-end
  valuations, outstanding lands on one development column in four, so the quarterly triangle
  alternates between "paid + case reserves" and "paid alone" and its factors measure where the
  valuation dates fall rather than how claims develop;
* the reported basis becomes usable at the grain you actually book on; and
* — most importantly — **it lets you test the assumption underneath your current IBNR position.**

That last point is worth spelling out. Booking zero IBNR for the mature quarters is a statement
that case reserves on those quarters are adequate: that reported claims are already the ultimate.
A quarterly reported triangle is precisely the instrument that tests it. If the reported
development factors come out at 1.0, your position is confirmed by your own data. If they come out
above 1.0, case reserves are developing and the mature quarters do carry IBNR. At present that
question cannot be answered at the quarterly grain at all.

Our software needs no change to take advantage of it: it detects which valuation dates an extract
carries and enables the reported basis at whatever grain those support.

---

## On Health Insurance — one-sided at the moment

Understood that no claims data has been uploaded. Please note the consequence: that class
currently carries 13,370 premium rows, which earn premium and flow into UPR and the LRC, while
contributing **no claims liability at all** — no outstanding, no IBNR, a zero loss ratio, and a
zero ultimate under any method. The balance sheet is one-sided for that class.

Our recommendation is to do one of two things, not neither: either supply the health claims so the
class reserves properly, or exclude it from the reserving run so it does not sit in the output
carrying premium alone. We can add the exclusion if that is the preferred route.
