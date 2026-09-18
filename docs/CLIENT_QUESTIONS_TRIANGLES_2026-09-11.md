# Triangles, reported basis and earned premium — answers, one urgent finding, three asks

In reply to: *"i don't understand this diagnostic only? can we use these in our calculation or its
for show only? — To use these triangles, we must have their reported paid also i.e. paid claims
triangle and reported claims triangle, along with their earned premium thats also monthly,
quarterly, annually"*

Since you sent us the current `Claim OS Full.xlsx` and a produced reserve workbook, most of what
we would have asked is now answered from your own data. What remains is three questions, one of
which is urgent. Full analysis: `docs/TRIANGLE_REPORTED_AND_EP_PLAN.md`.

---

## 1. "Diagnostic only" — our wording was wrong, and we are changing it

It never meant the triangles are decorative. **They are the reserving calculation.** The quarterly
paid triangle and the quarterly reported triangle each carry a Selected LDF row; those become the
`Paid CDF` and `Reported CDF` columns in Reserve Summary, and those drive all five ultimate
methods — Paid CL, Reported CL, ELR, Paid BF, Reported BF — and therefore the IBNR. Earned premium
is already there too, as the `EP` column, driving ELR and both BF methods.

What the label meant was narrower: the *Triangles screen* writes nothing to a workbook, and the
**monthly and yearly** views cannot be booked — booking is quarterly throughout, because the
IFRS 17 comparatives are keyed on quarterly accident periods, and because monthly development
factors cannot be multiplied up into quarterly ones (doing so is wrong by over 400% on your data).
Monthly and yearly are there to inform the factor you select; quarterly is the one that books.

**So all three things you asked for already exist and are already in the calculation, at
quarterly.** What is genuinely missing is that the Triangles *screen* shows only the paid triangle
and no earned premium. We are adding both, at all three grains.

## 2. What your data can support, grain by grain

Your OS extract holds **four valuation dates, all year-ends** (31-12-2021 … 31-12-2024). Since an
outstanding balance is only known at a valuation date, that frequency — not our software — sets
what a reported triangle can do:

| | Paid triangle | Reported triangle | Earned premium |
|---|---|---|---|
| **Yearly** | ✅ | ✅ fully supported | ✅ |
| **Quarterly** *(booking basis)* | ✅ | ⚠️ **see below** | ✅ |
| **Monthly** | ✅ | ❌ not derivable | ✅ |

The yearly reported triangle is sound — it is the 4 × 4, 100%-populated view in your screenshot.

**The quarterly one is not, and this matters because quarterly is what books.** With only annual
valuations, outstanding lands on one column in four. For `Fire / Payment / GROSS`, **40 of 136
cells (29%)** carry any outstanding; the other 71% contain paid claims only. The triangle
therefore alternates between "paid + case reserves" and "paid alone", so its development factors
measure *where your valuation dates fall* rather than how incurred develops. Any chain-ladder or
BF factor selected from it is not measuring development.

Two good things we can confirm from the same file: it is a proper **full open-claim inventory**
(each valuation carries claims back to accident year 1999, not just the current year), and your
case estimates **are** revised — of 3,961 claims present at more than one valuation, 1,483 change
their outstanding. Both were open questions; neither needs asking now.

## 3. One finding that needs your attention now

`Reported BF` is the method selected on **447 of 520 booked rows**, and since 8 September it is
effectively the only method in use. Taking one of your own booked workbooks,
`Fire Payment GROSS 2024-12.xlsx`:

| Accident period | Paid CDF | Reported CDF | Implied LR | Method | IBNR |
|---|---:|---:|---:|---|---:|
| 2021-Q1 … 2023-Q4 | 1.0000 → 1.0515 | **1.0000** | — | Reported BF | **0** |
| 2024-Q1 | 1.2489 | **1.0000** | — | Reported BF | **0** |
| 2024-Q2 | 1.5151 | **1.0000** | — | Reported BF | **0** |
| 2024-Q3 | 2.5803 | **1.0000** | — | Reported BF | **0** |
| 2024-Q4 | 13.8442 | 1.0000 | 0.6377 | ELR | 182,784,933 |

**Fifteen of the sixteen accident quarters book exactly zero IBNR.** The reason is that both
inputs to the Reported BF formula are unset:

> `Reported BF Ultimate = (1 − 1/Reported CDF) × EP × Implied LR + Reported Claims`

The Selected LDF row on the Reported Triangle was left at its placeholder of 1.0, making
`Reported CDF = 1` and the first bracket zero; and `Implied LR` is blank on those fifteen rows,
making the middle term zero as well. The formula therefore collapses to
`Ultimate = Reported Claims`, i.e. no IBNR, regardless of what the triangle contains.

The comparison that needs no assumptions is the last two quarters: **your own paid basis says
2024-Q3 needs a factor of 2.58 and 2024-Q4 needs 13.84; the reported basis you booked on says
1.0000 for both.** Both cannot be right.

We are not going to put a number on the shortfall: projecting at the reported triangle's own
volume-weighted factors gives a figure in the billions for this one class, but those factors
(10.6 and 139.9 at the two newest quarters) are the usual chain-ladder instability at immature
cohorts — exactly what BF is designed to damp. The size of the correct reserve is your actuary's
selection. What is not a matter of judgement is that zero is wrong for fifteen quarters.

Where the fault lies: we checked the whole chain and the triangle and the arithmetic are correct
everywhere — the workbook's own benchmark factors (`28.40, 5.52, 1.38, …`) reproduce exactly in
both our engine and the web app. What was missing is a **guard**. The product allowed a
placeholder row of 1.0s and a blank Implied LR to be applied and booked with no warning that the
method had been reduced to an identity. We are adding that guard, and it ships before any of the
new features.

---

## 4. What we have decided, so you do not have to

* **The reported basis will be offered at yearly grain only**, until the extract supports more.
  That is what your four annual valuations can carry, and it is sound there.
* **At quarterly — the booking grain — the reported basis will be blocked**, with the reason
  shown. A sawtooth triangle cannot produce a development factor, and we would rather refuse than
  let one be selected.
* **Our recommendation for quarterly booking in the meantime: use Paid CL / Paid BF.** Your paid
  triangle is healthy at every grain, its factors are real (1.00 → 13.84 across maturities), and
  it needs no change to your data. Use the yearly reported triangle alongside it as a cross-check.
* **Earned premium** will be added to the Triangles screen at all three grains, on the same
  definition the booking already uses. One limitation we cannot engineer away: premium is not
  currently retained after a run, so EP will appear on runs made *after* that change ships, not
  retrospectively.
* We will also flag any selection that produces a zero or negative IBNR, and any Implied LR left
  blank on a method that needs one.

---

## 5. What we still need from you

Only three things, and none of them blocks the work above.

**A — Can your claims system produce outstanding at every quarter-end?**
Sixteen quarter-end valuations (31-03-2021 … 31-12-2024), in exactly the format and layout you
already send, would make the quarterly reported triangle sound and let you keep using the reported
basis at the grain you book on. Month-ends would extend it to monthly. If this is not possible,
tell us and we will proceed with yearly-only as described above — we just need to know which.

**B — The fifteen zero-IBNR quarters: which reporting periods have they fed, and do you want them
revisited?** We can identify every affected run precisely, so this is a scoping question rather
than an investigation. The decision to restate is yours.

**C — 'Health Insurance' has 13,370 premium rows and no claims at all.**
We checked the claims extract: there is no health, medical or hospital class in it under any
column — it carries exactly four classes (Motor, Misc, Marine, Fire). So this is not a naming
mismatch on our side. Is it a class with genuinely no claims experience yet, or are health claims
held in a separate system that is not being sent to us? As it stands the class earns premium at a
zero loss ratio, which understates any ELR or BF ultimate derived from it.
