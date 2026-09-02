# WP7 — Actuarial Visual System

> **Goal:** Make the app read like the Excel an actuary already trusts — factors visually
> distinct from money, exclusions unmistakable, deltas legible — as one system, in light and
> dark, and in print. And make its colours actually accessible, which today they are not.

Status: **implemented 2026-09-01** (see §10). Re-planned the same day after auditing the built frontend.
Requirement 8. Decisions: `CLIENT_REQUIREMENTS_DECISIONS.md` §3 D7.
Every number below is measured against the current `sigma-17-dashboard`; §9 lists the method.

---

## 0. Client requirement

> "over all view attractive), some colouring etc"

Deliberately not clarified with the client. This is a request they cannot specify, and asking
would stall it. D7 is to propose a concrete spec, build it, and show it. What follows is that
spec — but the audit below changed what it has to contain, because "some colouring" turns out
to be a live accessibility defect rather than a polish request.

---

## 1. What exists — audited, not assumed

The previous draft of this plan opened with *"the foundation is already good and is not being
replaced."* Measurement contradicts that in one specific, load-bearing way.

### 1.1 The architecture is genuinely good

`src/index.css` defines a complete semantic token set (`--primary`, `--success`, `--warning`,
`--destructive`, `--muted-foreground`, sidebar, glass, glow) for **both** themes, and
`tailwind.config.ts` uses class-based dark mode. It works: the whole application carries only
**6** `dark:` variants, because tokens flip themselves. `next-themes` defaults to **dark**, and
`ThemeToggle` in the header makes light one click away and persistent — so **both themes are
live**, dark for everyone by default and light for anyone who toggles.

That architecture is not being replaced. The values inside it are wrong.

### 1.2 Defect V1 — every semantic colour fails WCAG AA as text, in one theme or the other

Contrast of each token against its own theme's card, at the 4.5:1 threshold for body text:

| token | uses as text | light | dark | verdict |
|---|---:|---:|---:|---|
| `--primary` | **57** | **3.19** | 9.14 | fails light |
| `--destructive` | **51** | 4.80 | **3.94** | fails dark |
| `--warning` | **13** | **2.85** | 9.67 | fails light |
| `--success` | **4** | **2.89** | 9.15 | fails light |
| `--muted-foreground` | — | 4.72 | 5.25 | passes both |
| `--foreground` | — | 17.90 | 17.21 | passes both |

**125 usages across the application.** `--warning` and `--success` at 2.85 and 2.89 fail even
the 3:1 large-text threshold, so there is no font size at which they are compliant.

### 1.3 Defect V2 — the primary button's own label fails AA in light mode

`--primary-foreground` is white and `--primary` is `187 72% 40%`: **3.19:1**. The most-clicked
element in the product does not meet AA in the light theme. Same for `--success` (2.89) and
`--warning` (2.85) used as fills with white labels.

### 1.4 Defect V3 — every literal palette class in the codebase fails AA in one theme

**56 occurrences, 25 distinct classes, 14 files** bypass the tokens — and they are not
arbitrary: `emerald` where `--success` exists, `amber` where `--warning` exists, `red` where
`--destructive` exists. The same three semantics, expressed twice.

| class | on light card | on dark card |
|---|---:|---:|
| `text-amber-700` | 5.02 | **3.76** |
| `text-amber-800` | 7.09 | **2.66** |
| `text-amber-600` | **3.19** | 5.93 |
| `text-emerald-800` | 7.68 | **2.46** |
| `text-emerald-500` | **2.54** | 7.45 |
| `text-emerald-400` | **1.92** | 9.82 |
| `text-red-300` | **1.90** | 9.95 |

Every one of them fails in a theme the user can reach. Six carry no `dark:` variant at all, so
`border-emerald-300 bg-emerald-50 text-emerald-800` (MovementAnalysisPage `:505`, `:506`,
`:531`, `:532`) paints a near-white panel into the dark UI — internally readable at 7.29, but a
hole in the page.

### 1.5 Defect V4 — the output preview cannot format a triangle sheet

`processing/output_column_kinds.py` classifies **columns**, and does it well: on Reserve
Summary it correctly returns `ratio` for `Reported LR` / `Implied LR` / `ULR`, `factor` for
`Paid CDF` / `Reported CDF` / `CDF`, and `number` for WP5's `Large Paid` / `Large OS`.

But a triangle sheet's columns are development periods, and its **kind varies by row**:

```
Cumulative Triangle   3,463,357      money
Age-to-Age Factors    1.015748       factor
Factor Count          3              integer count
```

All three sit in the same column. The classifier returns `number` for every triangle column,
and `OutputPreviewDialog`'s `decimalColumns` heuristic then gives the column two decimals
because it contains a non-integer. Measured, on real output:

| stored value | what the preview shows | should be |
|---|---|---|
| `1.015748` (a factor) | **1.01** | 1.0157 |
| `8.150968` (a factor) | **8.15** | 8.1510 |
| `3` (Factor Count) | **3.00** | 3 |
| `3463357` (money) | 3,463,357.00 | 3,463,357 |

**An actuary cannot read their own development factors in the preview.** WP1 made this worse
by adding seven more factor rows and the `Factor Count` row. This defect is absent from the
previous draft and is the single most on-point item for "overall view".

### 1.6 What simply does not exist

| §2.7 claims | reality |
|---|---|
| print / board-pack stylesheet | **zero** `@media print` rules anywhere |
| sticky first column | `Table` supports a sticky **header** only; no first-column support |
| `tabular-nums` everywhere | 27 occurrences in 13 files, against 73 `font-mono` uses across 26 table-rendering surfaces |
| `prefers-reduced-motion` / `prefers-contrast` | one occurrence, in `src/App.css` — which is dead Vite boilerplate imported by nothing |

### 1.7 The drift the plan predicted has already started

`scales.ts` and `data-cell.tsx` were listed as new files. They were never built — but the
shading was, inline, as `shadeFor` inside `TriangleGrid.tsx` during WP5. One surface has a
private dialect of "shaded number" already. The draft's warning ("six surfaces will drift into
six dialects") is now a measurement, not a risk.

---

## 2. The spec

### 2.1 Fix the palette, and make the fix machine-checked

**Light mode** — one lightness change each; hue and saturation unchanged, so the brand survives:

| token | now | proposed | text ratio | white-label-on-fill |
|---|---|---|---:|---:|
| `--primary` | `187 72% 40%` | `187 72% 32%` | 3.19 → **4.74** | 3.19 → **4.74** |
| `--success` | `142 71% 40%` | `142 71% 31%` | 2.89 → **4.58** | 2.89 → **4.58** |
| `--warning` | `35 92% 45%` | `35 92% 34%` | 2.85 → **4.71** | 2.85 → **4.71** |

`--accent`, `--ring` and `--sidebar-primary` share the teal and move with it.

In light mode a single value serves both roles, because the card is white and the fill's label
is white — both ratios are measured against the same near-white and come out identical. **In
dark mode they point in opposite directions**, and one value cannot serve both:

| dark `--destructive` | text on card | near-white label on fill |
|---|---:|---:|
| `0 72% 51%` (now) | **3.94** ✗ | 4.58 ✓ |
| `0 72% 57%` | **4.54** ✓ | **3.97** ✗ |

So dark mode gets **one new token**, `--destructive-text: 0 72% 57%`, with `--destructive`
staying `51%` for fills. `--primary`, `--success` and `--warning` need no split in dark: they
are bright fills carrying a *dark* label, so both roles already pass (9.1–9.7 as text, 9.4–10.0
as fills).

**The rule this exposes, recorded so it is not rediscovered:** a colour used both as text and
as a fill needs two values in any theme where the page background and the fill's label sit on
opposite sides of the colour.

**`src/lib/palette.test.ts` is the centrepiece.** It parses `src/index.css`, computes every
(token × role × theme) contrast, and fails below AA. It also fails on any literal Tailwind
palette class reintroduced into `src/` outside an allowlist. That converts this entire class of
defect from "caught in review, sometimes" to "cannot merge".

### 2.2 Retire the 56 literal palette classes

Mechanical, semantics-preserving: `emerald → success`, `amber → warning`, `red → destructive`,
using the token that already exists. The four MovementAnalysisPage blocks become
`border-success/30 bg-success/10 text-success` and stop punching a white hole in the dark UI.
§2.1's test keeps them out.

### 2.3 Row-kind classification for triangle sheets (fixes V4)

Extend the existing conservative classifier rather than replacing it — it is proven and its
allowlist discipline is why it has never fabricated a misleading percentage.

```python
# processing/output_column_kinds.py
COUNT = "count"                       # new kind: integer, no decimals
def classify_rows(sheet_name, row_labels) -> list[str] | None
```

Returns `None` for sheets whose kind does not vary by row (every sheet except the two triangle
sheets), so nothing else changes. For a triangle sheet it matches on the **row label**:

| row label | kind |
|---|---|
| within the `Age-to-Age Factors` block, or `* LDF` / `* CDF` | `factor` |
| `Factor Count` | `count` |
| everything else (incremental, cumulative) | `number` |

The preview then resolves `row_kind ?? column_kind`, so a triangle renders factors at 4dp,
counts as integers, and money with separators — in the same column. `Selected LDF` seeded `=1`
reads as blank, which is correct: it is an unevaluated formula (F6).

### 2.4 One cell renderer

`src/components/ui/data-cell.tsx` takes `{ value, kind, shade, struck, strikeSource, delta }`
and is the single place cell presentation is decided. `TriangleGrid`'s inline `shadeFor` moves
into `src/lib/scales.ts` and both are then shared by `TriangleGrid`, `SensitivityMatrix`,
`LargeClaimsPanel`, `PaymentPatternEditor`, `OutputPreviewDialog`, `ReserveMethodTable`,
`UlrSelectionTable`. Without it, seven surfaces keep their own dialect.

### 2.5 Shading — two scales, chosen by meaning

| Block | Scale |
|---|---|
| Incremental / cumulative | **sequential**, low → high, per block |
| Age-to-age factors | **diverging, centred on 1.0** |
| Signed deltas (WP4) | **diverging, centred on 0**, sign always printed |

Centring the factor scale on 1.0 remains the highest-value single decision here: F3 shipped
factors of `0.125` and nobody saw it. Shading is computed **per block**, never per sheet — a
cumulative block and a factor block on one sheet have incomparable ranges. Intensity is capped
so that text on the most intense shade still clears AA in both themes, which §2.1's test
asserts.

### 2.6 Row hierarchy in the triangle sheet

```
Incremental / Cumulative / Age-to-Age    normal weight, shaded per §2.5
Simple / Weighted / Ex-Hi-Lo / Last-N /
  Median / Factor Count                  muted band, italic label — benchmarks
Selected LDF                             emphasised band, editable affordance
Selected CDF                             emphasised band, visibly derived
```

**Benchmarks are advice; Selected is the decision.** Today all thirteen benchmark rows look
identical to the two rows the engine actually consumes — and WP1 grew that block from four rows
to thirteen, so the distinction now matters more than when this was first written.

### 2.7 Exclusion treatment — already specified, now shipped in two places

| Source | Treatment |
|---|---|
| Factor struck out of an average (WP1) | strikethrough, muted, **dotted underline** |
| Cell containing an excluded large claim (WP5) | strikethrough, muted, **left accent bar** |
| Both | strikethrough, muted, accent bar + dotted underline |

Built during WP5 and WP1 and already consistent; WP7 moves it into `data-cell.tsx` so it stays
that way. Colour is never the carrier — these sheets print in mono.

### 2.8 Number discipline

Extending the existing rules, **not replacing them**. `src/lib/format.ts` documents a
deliberate product rule: thousands separators with **2 truncated — never rounded — decimals**,
so a displayed figure can never overstate the underlying number. The previous draft's "money:
no decimals, negatives in parentheses" would have silently changed every money figure in the
app and reversed that decision.

| Kind | Format |
|---|---|
| `number` (money) | unchanged: separators, 2 truncated decimals, `-` sign |
| `factor` | **4 decimals** (raised from the preview's current 3 — `1.015748` and `1.0157` are different selections) |
| `ratio` | percentage, 2 decimals (unchanged) |
| `count` | integer, separators, no decimals |
| accounting surfaces only | opt-in `accounting` style: negatives in parentheses, `—` for zero |

Parentheses-negatives and `—`-for-zero are actuarial report conventions and a large part of
what "looks professional" means to this audience — but they belong to **print and disclosure
surfaces**, opt-in, not to every input field in the product.

`font-variant-numeric: tabular-nums` on every numeric cell, via `data-cell`. Currently 27 uses
across 13 files against 26 table surfaces; routing cells through one renderer makes it
universal for free. It is the cheapest change with the largest perceived quality gain.

### 2.9 Layout

* Sticky first column **and** header on every triangle and matrix — `Table` gains a
  `stickyFirstColumn` prop beside the existing `sticky`.
* Wide tables scroll inside their own container; the page body never scrolls horizontally.
* Column-letter row retained in the preview — actuaries navigate by cell reference.
* Dense row height by default with a comfortable toggle. A triangle is read as a **shape**;
  generous padding destroys it.

### 2.10 Board-pack print

`src/styles/print.css`, imported once — none of this exists today. Landscape, repeated table
headers, no interactive chrome, shading preserved via `print-color-adjust: exact` with a
greyscale-safe fallback, and a run-metadata footer (job id, timestamp, policy and scenario
versions). The sensitivity matrix, large-claims summary and disclosure notes go into board
packs; they must survive `Ctrl+P`.

### 2.11 Motion and contrast preferences

Delete the dead `src/App.css`. Add real `prefers-reduced-motion` handling (the app uses
`animate-spin` 47 times, `animate-in`/`animate-out` 41 times) and `prefers-contrast: more`,
which flattens shading ramps to borders so the grid stays readable without fills.

---

## 3. Backend changes

| File | Change |
|---|---|
| `processing/output_column_kinds.py` | new `COUNT` kind; `classify_rows(sheet, row_labels)`; triangle-sheet row rules |
| `processing/views.py` | output-rows response carries `row_kinds` alongside `column_kinds` |
| `processing/tests/test_output_column_kinds.py` | row-kind cases, and that non-triangle sheets return `None` |

## 4. Frontend changes

| File | Change |
|---|---|
| `src/index.css` | corrected light values; `--destructive-text`; shading ramp tokens |
| `src/lib/palette.test.ts` | **new** — the machine-checked palette contract (§2.1) |
| `src/lib/scales.ts` | **new** — `sequentialScale`, `divergingScale`, AA-capped, theme-aware |
| `src/components/ui/data-cell.tsx` | **new** — the one cell renderer |
| `src/lib/format.ts` | `formatFactor` (4dp), `formatCount`, opt-in `accounting` style |
| `src/components/ui/table.tsx` | `stickyFirstColumn` |
| `src/styles/print.css` | **new** — board-pack rules |
| 14 files | literal palette classes → tokens (§2.2) |
| `TriangleGrid`, `SensitivityMatrix`, `LargeClaimsPanel`, `PaymentPatternEditor`, `OutputPreviewDialog`, `ReserveMethodTable`, `UlrSelectionTable` | adopt `data-cell` |
| `src/App.css` | deleted (dead) |

---

## 5. Tests

**`src/lib/palette.test.ts`** — parses `index.css`; every token × role × theme ≥ 4.5:1; no
literal Tailwind palette class outside the allowlist; shading ramps AA-safe at full intensity.

**`src/lib/scales.test.ts`** — diverging scale is neutral exactly at its centre; degenerate
range (`min == max`) returns neutral rather than dividing by zero; intensity is capped.

**`src/lib/format.test.ts`** — factor 4dp; count integer; money unchanged (a regression guard
on the truncation rule); accounting style only where opted in.

**`src/components/ui/data-cell.test.tsx`** — the two exclusion sources render distinguishably;
a struck cell carries an `aria-label` naming the reason; a struck cell is not also shaded.

**`processing/tests/test_output_column_kinds.py`** — a triangle sheet's factor rows classify as
`factor` and `Factor Count` as `count`; Reserve Summary is unaffected; an unknown row label
falls back to `number`.

**Visual regression** — the triangle grid, sensitivity matrix and large-claims panel snapshotted
light and dark at a fixed viewport, so shading changes are reviewed rather than discovered.

---

## 6. Sequencing — this is now a standalone pass

The previous draft threaded WP7 through WP1–WP6 and called it "not a phase". That was right
then and is wrong now: **WP1–WP6 are all built.** Delivering WP7 as one coherent pass is
strictly better — a single palette change, one cell renderer adopted across seven surfaces at
once, and one visual-regression baseline. Threading it now would mean seven separate reviews of
the same decision.

Order within the pass: palette + its test first (it gates everything and is the live defect),
then `scales.ts` + `data-cell.tsx`, then the row-kind fix, then adoption, then print.

---

## 7. Estimate

| | |
|---|---|
| palette correction + `palette.test.ts` + retiring 56 literal classes | 2d |
| `scales.ts` + `data-cell.tsx` + `format.ts` extensions | 2.5d |
| row-kind classification (backend + preview) | 1.5d |
| adoption across seven surfaces | 3d |
| sticky first column, dense/comfortable, motion & contrast prefs | 1d |
| print stylesheet | 1d |
| visual-regression harness | 1d |
| **Total** | **~12 days** |

Up from the draft's 10d: V1–V4 were not in it, and adoption is no longer amortised across other
work packages that have already shipped.

---

## 8. Risk

**The palette change is visible.** `--primary` moves from `187 72% 40%` to `187 72% 32%` — the
same teal, deeper. Nobody has approved a brand change, and it should be shown before it is
merged. The alternative is shipping a product whose primary button label fails AA in a theme
the user can reach in one click, which for an enterprise deliverable is not a real alternative.
Dark mode — the default, and what the client has actually seen — is **unchanged** except for
`--destructive-text`.

---

## 9. What changed after verification

Method: contrast computed with the WCAG 2.1 relative-luminance formula over the actual token
values in `src/index.css` and the actual Tailwind hex values used in `src/`; usage counted with
`grep` over `src/**/*.tsx`; preview formatting reproduced against `formatNumber`'s real
truncation logic; classifier output produced by calling `classify_columns` on real headers.

* **"The foundation is already good" was false.** Every semantic colour fails AA as text in one
  theme (V1, 125 usages), and the primary button's own label fails in light (V2). This is a
  live accessibility defect, not polish — and it is exactly what the client's "some colouring"
  points at.
* **The literal-class problem is universal, not stylistic.** All 25 distinct classes fail AA in
  a reachable theme (V3); six paint light-only panels into the dark default.
* **Fill and text need different values in dark mode** (§2.1). One token cannot serve both when
  the background and the label sit on opposite sides of the colour — found by trying the single
  change and measuring that it broke the other role.
* **The output preview cannot format a triangle** (V4) — factors render as `1.01`. Absent from
  the draft, and the most on-point defect for requirement 8. WP1 enlarged it.
* **Nothing in §2.7's print scope exists** — zero `@media print` rules, not a partial
  implementation.
* **`App.css` is dead** and contains the app's only `prefers-reduced-motion` block.
* **The predicted drift already happened**: `shadeFor` lives inline in `TriangleGrid.tsx`.
* **§2.5's money rule would have reversed a deliberate product decision** (truncate-never-round,
  2dp). Parentheses-negatives are now opt-in for report surfaces only.
* **WP7 is standalone now**, not threaded — WP1–WP6 all shipped.

---

## 10. Implementation status — built (2026-09-01)

Implemented and tested. What follows records where **building it found something the audit had
not**, and where the design changed as a result.

### 10.1 The palette is corrected and the correction is machine-checked

`src/lib/palette.test.ts` parses `index.css` and asserts every semantic colour, in each role,
in each theme. **21 assertions, all green.** It also rejects any literal Tailwind palette class
outside a two-file allowlist, so the 56 occurrences cannot come back.

Values changed: light `--primary` `40% -> 32%`, `--success` `40% -> 31%`, `--warning`
`45% -> 34%` (with `--accent`, `--ring`, `--sidebar-primary` and `--glow` following the teal),
and one new token `--destructive-text: 0 72% 57%` in dark. `text-<name>-text` is now the class
for coloured type; `<name>` stays the fill.

All 56 literal classes across 14 files were migrated to tokens. The four
`MovementAnalysisPage` blocks that painted `bg-emerald-50 / text-emerald-800` — a near-white
panel in the dark default — are now `bg-success/10 text-success-text` and follow the theme.

### 10.2 Defect V5 (new) — the factor strikethrough never rendered

Found the moment the two exclusion treatments moved into one renderer and a test asserted both
halves of the factor treatment:

```
"line-through text-muted-foreground underline decoration-dotted"
```

`line-through` and `underline` are **both `text-decoration-line` utilities**. tailwind-merge
keeps the last, and CSS would too. So the factor treatment shipped in WP1 and WP5 as a dotted
underline **with no strikethrough at all** — and its WP5 test passed throughout, because it
only asserted that the two sources differ, which they did.

Fixed with one arbitrary property, `[text-decoration-line:line-through_underline]`, at all
three sites (`data-cell`, `TriangleGrid`, `ReserveCdfEditor`), with a test that names the trap.
This is precisely the defect class §1.7 predicted from having a private dialect per surface.

### 10.3 The contrast cap turned out to be a rule about text, not about alpha

Measured over the real tokens, text at `--muted-foreground` on a tinted cell falls below AA at
**3%** tint in light and **9%** in dark, while `--foreground` survives to **58-90%**. So the
constraint is not "keep the ramp light" but:

> **A shaded cell must carry full-strength text, and a muted (struck-out) cell must not be
> shaded.**

`data-cell` enforces both — exclusion wins over shading, which makes the second half true for
free — and `scales.test.ts` pins the measurement from both sides, including the deliberately
failing muted case so the reason survives.

### 10.4 Row kinds: measured before and after

`classify_rows` returns `None` for every sheet except the two triangle sheets. On the real
reference output, `Motor Insurance Payment GROSS`:

| row | before | after |
|---|---|---|
| `Simple Avg LDF` | 9.05 | **9.0501** |
| `Weighted Avg LDF` | 4.79 | **4.7954** |
| `Factor Count` | 3.00 | **3** |
| `Accident Period` header | 0.00 | **0** |
| cumulative row | 3,463,357.00 | 3,463,357.00 |

Benchmark rows are matched by their `LDF` / `CDF` suffix rather than an allowlist, so the next
average basis is a factor without anyone remembering to register it — WP1 grew that block from
four rows to thirteen, and the failure mode is silent.

**Row kinds are classified over the whole sheet and then sliced to the page.** A row inherits
its kind from the block label above it, so classifying a page in isolation restarts that
inheritance at the page boundary and mislabels everything on page 2.

One extra case the audit missed: the repeated `Accident Period` header rows *inside* the
blocks hold the development numbers `0, 1, 2 …`. Inheriting the age-to-age block's kind would
have rendered a sheet's column headings as `0.0000, 1.0000, …`, so they classify as `count`.

### 10.5 Deltas use a different ramp from magnitudes

`SensitivityMatrix` had the second private dialect, and it used success/destructive rather than
the triangle's primary/destructive. That is not drift — it is meaning: a delta reads
favourable/adverse, a magnitude does not. `scales.ts` keeps both (`deltaShade` vs
`sequentialShade` / `factorShade`), and the sign is printed either way, so colour is never the
only carrier of direction.

### 10.6 Visual regression, honestly scoped

The plan asked for light/dark pixel snapshots. This repo has no browser tooling — no
Playwright, no Storybook — and adding it is an infrastructure decision, not a code one, so it
was **not** added unilaterally.

What ships instead is `src/components/visualSystem.test.tsx`: it snapshots the **class
structure** each grid produces in both themes, which catches every change flowing through
`data-cell` and `scales` — where the visual system actually lives — and runs in CI with no
browser. It also restates the two invariants explicitly, so a careless snapshot update cannot
quietly break them. Pixel-level regression remains open and needs Playwright.

### 10.7 Files

| | |
|---|---|
| `src/index.css` | corrected light values; `--*-text` roles; `--destructive-text` split in dark |
| `tailwind.config.ts` | `text` role on the four semantic colours |
| `src/lib/contrast.ts` | **new** — WCAG maths, shared by the palette test and the shading cap |
| `src/lib/palette.test.ts` | **new** — the machine-checked contract (21 assertions) |
| `src/lib/scales.ts` + `.test.ts` | **new** — sequential / diverging / delta ramps, AA-capped |
| `src/components/ui/data-cell.tsx` + `.test.tsx` | **new** — the one cell renderer |
| `src/lib/format.ts` | `formatFactor` (4dp), `formatIntegerCount`, opt-in `formatAccounting` |
| `src/components/ui/table.tsx` | `stickyFirstColumn`, adopted by 6 grids |
| `src/styles/print.css` | **new** — board pack, reduced motion, `prefers-contrast` |
| `src/components/PrintableSheet.tsx` + `.test.tsx` | **new** — printable region + run provenance |
| `processing/output_column_kinds.py` | `COUNT`; `classify_rows`; triangle row rules |
| `processing/output_preview.py` | serves `row_kinds`, classified whole-sheet then sliced |
| `src/components/TriangleGrid.tsx` | adopts `data-cell`; private `shadeFor` deleted |
| `src/components/SensitivityMatrix.tsx` | adopts `deltaShade`; private `shadeClass` deleted |
| 14 files | literal palette classes -> tokens |
| `src/App.css` | deleted (dead boilerplate holding the only reduced-motion block) |

### 10.8 Verification

* `vitest` — **272 passed** (29 files), including 21 palette-contract, 19 scale, 12 data-cell
  and 5 visual-regression assertions.
* `tsc` — unchanged **45**-error baseline. `vite build` clean; the emitted CSS was checked to
  contain the new token values, the `text-*-text` utilities, `@media print`,
  `prefers-reduced-motion` and `prefers-contrast`.
* `pytest module1_engine/tests module2_engine` — **280 passed**, 9 goldens green.
* `manage.py test processing datasets accounts tenants` — **297/299**; the two failures are
  `test_dataset_e2e`, which need a live Redis broker for `.delay()` and fail identically on
  this machine regardless of these changes.

**Not verified:** nothing here has been exercised against the running stack, and no one has
seen the corrected palette on screen. §8's risk stands — the light-mode brand teal is visibly
deeper, and that should be shown before it reaches the client. Dark mode, the default, is
unchanged apart from `--destructive-text`.
