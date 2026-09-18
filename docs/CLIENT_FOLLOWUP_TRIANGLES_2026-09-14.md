# Follow-up — what is built, what we settled, and the one thing we need

> **Superseded in part by the client's reply of 15 September.** Route B below is
> withdrawn — `LIC (OS) Summary` is on an underwriting-year basis that clubs
> pre-2018 into UW 2018, so it cannot source a triangle that discards by experience
> start date. Health Insurance is decided (excluded) and the empty ULAE % is
> intentional. See `CLIENT_REPLY_TRIANGLES_2026-09-15.md`.

Further to our note of 12 September. The triangle work is complete and tested. Two of the three
questions we were going to ask you we have since answered ourselves; one decision remains yours,
and one file would unlock the last of the functionality.

---

## 1. What is now built

| | Paid triangle | Reported triangle | Earned premium |
|---|---|---|---|
| **Monthly** | ✅ | needs month-end valuations | ✅ |
| **Quarterly** *(booking basis)* | ✅ | needs quarter-end valuations | ✅ |
| **Yearly** | ✅ | ✅ available now | ✅ |

The Triangles screen now carries a **Paid / Reported** selector, a **head of damage** filter — so a
view corresponds to exactly one reserve workbook and can be checked against a booked figure — and
an **earned premium** panel with the paid and reported loss ratios it makes possible.

Two things it will not do, deliberately:

* It will not show a reported triangle your data cannot support. Ask for one and it explains what
  your extract holds, at which dates, and what would change the answer.
* Where a cell has no valuation behind it, it is left **empty rather than filled with paid claims
  alone**. This is the important one: filling those cells is what produces a triangle whose
  development factors measure where your valuation dates fall rather than how claims develop.

The earned premium is the same definition the booking already uses — written premium less the
movement in unearned premium reserve, on your own UPR method — so it reconciles to the `EP` column
in the reserve workbooks **to the cent**. We hold that as a standing test.

## 2. What we need: outstanding at each quarter end — two ways to give it

You confirmed on 12 September that your system can produce this. It is the only thing standing
between you and a reported triangle at the grain you book on. **Either** of these works:

**Route A — the claims extract, valued quarterly.** `Claim OS Full.xlsx` as you already send it,
but with sixteen `As at` dates (31-03-2021 … 31-12-2024) instead of the four year-ends it carries
now, listing every open claim at each. This is the fuller option: it supports the triangle at
reserving class **and head of damage**, matching your workbooks exactly.

**Route B — sheets you already produce.** Your `Combined_Summary.xlsx` contains a
**`LIC (OS) Summary`** sheet: outstanding by reserving class, UWY, accident period and GROSS/RI at
one valuation date. The copy we hold is dated 31/12/2023 and carries 56 accident periods back to
1999-Q1. That sheet *is* one diagonal of a reported triangle, in exactly the right form. If you
have kept the `Combined_Summary.xlsx` from each quarterly run, sending those files gives us the
same result **with no change to your extract process at all**.

Route B's one limitation: that sheet has no head-of-damage split, so triangles built from it would
be per reserving class and treaty only. If you want them to line up with your per-workbook slicing,
Route A is the one to take.

Nothing needs installing or deploying either way — the system reads the valuation dates it is
given and enables the quarterly view accordingly.

As a reminder of why it matters beyond the feature: booking zero IBNR on your mature quarters is a
statement that case reserves on those quarters are adequate. A quarterly reported triangle is the
instrument that tests that statement against your own data.

## 3. Two things we found and have already handled

**Recovery amounts that never reach a triangle.** Our claims reader substitutes
`AMOUNTRECOVERED` for `AMOUNTPAID` only where `POLICYCLASS` is exactly `Motor`. In the 2016-2017
sample set you gave us, **no row spells it that way at all** — so the substitution never fires
anywhere in that book, and **12 recovery rows worth 1,743,541 contribute nothing to any
triangle**. (1,645 rows match the pattern structurally, but all except those twelve carry a
recovered amount of zero and lose nothing.) Your current extract does spell it `Motor`, so motor
recoveries are being picked up today — but the behaviour turns on a spelling, and nothing
normalises that column the way reserving class is normalised.

We have not changed the arithmetic, because whether recoveries should reduce paid claims is your
actuary's decision and it moves booked numbers. What we have done is make it **impossible to miss**:
preflight now reports, before every run, how many recovery rows carry money that the substitution
will not reach, what they are worth, and which policy classes they sit in. If that number is ever
non-zero on a run of yours, you will see it on screen.

One observation for your actuary while deciding: the substitution is motor-only by design, so
recoveries on Health, Miscellaneous, Engineering and Fire are never substituted regardless of
spelling.

**Earned premium outside the booking window.** We settled this one ourselves. The triangle screen
shows earned premium across the whole experience period, because that is the axis the triangle is
drawn on — a loss ratio needs premium for the same period as its claims. The panel names its own
window on screen so it can never be confused with the workbook's column, which covers the booking
period only.

Checking that turned up something worth telling you. Where a run's experience period is wider than
its booking period, the reserve workbook's `EP` column is **zero** for the accident periods
outside it. Zero earned premium makes the expected-loss term of a Bornhuetter-Ferguson zero, so
those rows return reported claims unchanged and book no IBNR — with the loss ratio and the
development factor both looking perfectly healthy. It is the same silent zero we wrote to you
about on 12 September, reached by a third route. The product now states it explicitly on any row
where it happens.

## 4. The one decision still with you

**Health Insurance.** Supply the claims, or tell us to exclude the class from the run. As it
stands it earns premium into the LRC while contributing no claims liability at all, and we would
rather not leave that one-sided. We can add the exclusion in about two days once you decide.

## 5. The IBNR placement measurement — we no longer need anything from you

You explained that IBNR is booked as a position in the latest accident quarter for simplicity. We
accept that. The one consequence worth repeating is that Module 2 uses an amount's accident period
to decide how far into the future its cash flows run, and therefore how much it is discounted — so
the placement changes your LIC and the accident-period split of your movement disclosure.

We previously said we would need your Module 2 inputs to measure that. We have since found them in
the material you already gave us — the `Discount Rate` and `ULAE-RA` sheets inside
`Combined_Summary.xlsx`. So we can measure it whenever you want it; just say the word.

Two gaps in that sheet you may want to look at regardless: **`ULAE %` is empty for every class**,
and **`RA %` is present only for GROSS and missing entirely for Health Insurance**. Both feed the
LIC directly.
