# WP7 — Actuarial Visual System

> **Goal:** Make the outputs read like the Excel an actuary already trusts — triangles that show
> their shape at a glance, factors that are visually distinguishable from money, exclusions that are
> unmistakable, and deltas that are legible — as one system, in light and dark, and in print.

Status: planned (2026-08-21). Decisions: `docs/CLIENT_REQUIREMENTS_DECISIONS.md` §3 D7.
Requirement 8. Threaded through WP1-WP6 rather than delivered as a block.

---

## 0. Client requirement

> "over all view attractive), some colouring etc"

Deliberately not clarified with the client. This is a request they cannot specify and asking would
stall it; the decision (D7) is to propose a concrete spec, build it, and show it. What follows is
that spec.

## 1. What exists

The foundation is already good and is not being replaced:

* Tailwind + shadcn/ui with semantic HSL tokens (`--primary`, `--success`, `--warning`, …) and a
  class-based dark mode (`tailwind.config.ts`)
* `glass-card`, sticky table headers, `TablePagination`, `StatusBadge`
* An Excel-style output preview (`OutputPreviewDialog`) with column-letter and column-name frozen
  header rows
* A conservative, exact-match column classifier (`processing/output_column_kinds.py`) already
  distinguishing `ratio` / `factor` / `number` — the hard semantic problem is solved

WP7 is applied colour and typography on top of that, plus three new primitives. It is not a redesign.

## 2. The spec

### 2.1 Triangle shading — two scales, chosen by meaning

| Block | Scale | Rationale |
|---|---|---|
| Incremental / cumulative | **sequential**, low → high within the block | Reading magnitude; the developed region should be visibly distinct from the undeveloped one |
| Age-to-age factors | **diverging, centred on 1.0** | A factor below 1 means negative development. The current defect (F3) produced factors of 0.125 and nobody saw it. A diverging scale makes that impossible to miss |

Centring the factor scale on 1.0 is the single highest-value decision in this plan: it converts a
class of silent numerical error into something visible at a glance.

Shading is computed per block, not per sheet — a cumulative triangle and a factor block on the same
sheet have incomparable ranges. Intensity is capped so text contrast never drops below WCAG AA
against either theme's background.

### 2.2 Row hierarchy in the triangle sheet

```
Incremental / Cumulative / Age-to-Age   normal weight, shaded per §2.1
Simple / Weighted / Ex-Hi-Lo / Last-N   muted band, italic label — benchmarks
Selected LDF                            emphasised band, editable affordance
Selected CDF                            emphasised band, derived — visibly read-only
```

The distinction that must survive: **benchmarks are advice, Selected is the decision.** Today all
average rows look identical to the row the engine actually consumes.

### 2.3 Exclusion treatment (WP1 + WP5)

| Source | Treatment |
|---|---|
| Factor excluded by the user (WP1) | strikethrough, muted, dotted underline |
| Cell affected by an excluded claim (WP5) | strikethrough, muted, **left accent bar** |
| Both | strikethrough, muted, accent bar + dotted underline |

Two exclusion sources with different consequences must not look identical. Hovering names the reason
and, for claims, the claim numbers.

### 2.4 Signed deltas (WP4)

Diverging red/green centred on zero, with the **sign always printed**. Colour is never the only
signal — a red cell and a green cell must remain distinguishable in greyscale and to a
red-green-colour-blind reader, so magnitude shading is paired with an explicit `+` / `−`.

### 2.5 Number discipline

Extending the existing classifier rather than replacing it:

| Kind | Format |
|---|---|
| `number` (money) | thousands separators, no decimals, **negatives in parentheses**, right-aligned, tabular figures |
| `factor` | 4 decimals, right-aligned, tabular figures |
| `ratio` | percentage, 1 decimal |
| Zero | rendered `—` in triangles so an empty cell and a genuine zero are distinguishable |
| Null | blank |

Negatives in parentheses and `—` for zero are actuarial-report conventions; using them is a large
part of what "looks professional" means to this audience.

`font-variant-numeric: tabular-nums` on every numeric cell so columns align — the cheapest change
with the largest perceived quality gain.

### 2.6 Layout

* Sticky first column **and** header row on every triangle and matrix (currently header only)
* Wide tables scroll inside their own container; the page body never scrolls horizontally
* Column-letter row retained in the output preview — actuaries navigate by cell reference
* Dense row height by default with a comfortable toggle; triangles are read as a shape, and
  generous padding destroys the shape

### 2.7 Board-pack print / PDF

A print stylesheet for the sensitivity matrix, the large-claims summary and the disclosure notes:
landscape, repeated headers, no interactive chrome, shading preserved via print-colour-adjust with a
greyscale fallback, run metadata footer (job id, timestamp, policy and scenario versions). These are
artefacts that go into a board pack; they must survive `Ctrl+P`.

### 2.8 Charts

All charts follow the `dataviz` skill conventions — the tornado chart (WP4), pattern sparklines
(WP3) and any development-curve overlay draw from one palette shared with the table shading, so the
application reads as a single system rather than a set of separately-styled screens.

## 3. Implementation

New primitives, each used by several work packages:

| File | Purpose |
|---|---|
| `src/lib/scales.ts` | `sequentialScale(min,max)`, `divergingScale(center,min,max)`, theme-aware, AA-contrast-capped |
| `src/components/ui/data-cell.tsx` | one cell renderer taking `{value, kind, shade, struck, strikeSource, delta}` — the single place cell presentation is decided |
| `src/lib/format.ts` | extend with parentheses-negatives, `—` for zero, tabular-figure class |
| `src/styles/print.css` | board-pack print rules |
| `tailwind.config.ts` | shading ramp tokens for both themes |

Then applied: `TriangleGrid` (WP1), `LargeClaimsPanel` (WP5), `SensitivityMatrix` (WP4),
`PaymentPatternEditor` (WP3), `OutputPreviewDialog`, `ReserveMethodTable`, `UlrSelectionTable`.

**`data-cell.tsx` is the load-bearing piece.** Without one renderer, six surfaces will drift into six
dialects of "shaded number", which is precisely the state the client is reacting to.

## 4. Accessibility and theme

* WCAG AA contrast for all text on shaded cells, verified at maximum shading intensity in both themes
* Colour never the sole carrier of meaning: strikethrough carries exclusion, sign carries direction,
  italics carry "benchmark"
* Full keyboard operation for cell exclusion toggles, with a visible focus ring on shaded cells
* Shading respects `prefers-reduced-motion` for transitions and `prefers-contrast` by flattening ramps

## 5. Tests

**`src/lib/scales.test.ts`**
* diverging scale returns the neutral colour at exactly the centre
* contrast ratio ≥ 4.5:1 for foreground on the most intense shade, both themes
* a degenerate range (`min == max`) returns neutral, does not divide by zero

**`src/lib/format.test.ts`**
* negative money renders in parentheses; zero renders `—`; null renders blank
* factor precision is 4dp; ratio 1dp

**`src/components/ui/data-cell.test.tsx`**
* struck cell exposes `aria-label` naming the exclusion reason
* both exclusion sources render distinguishable treatments

**Visual regression** — snapshot the triangle grid, sensitivity matrix and large-claims panel in
light and dark at a fixed viewport, so shading changes are reviewed deliberately rather than
discovered.

## 6. Sequencing

WP7 is not a phase. It ships with its consumers:

| Delivered with | Pieces |
|---|---|
| WP1 | `scales.ts`, `data-cell.tsx`, `format.ts`, triangle shading, row hierarchy, factor strikethrough |
| WP3 | pattern sparklines |
| WP4 | delta shading, tornado chart, print stylesheet |
| WP5 | claim-exclusion treatment |
| WP6 | grain-aware headers |

Only the print stylesheet and the visual-regression harness are standalone.

## 7. Estimate

Primitives 3d (front-loaded with WP1), applied styling ~0.5-1d per consuming surface (~4d total),
print stylesheet 1d, accessibility audit 1d, visual regression harness 1d. **~10 days**, distributed
across the other work packages rather than taken as a block.
