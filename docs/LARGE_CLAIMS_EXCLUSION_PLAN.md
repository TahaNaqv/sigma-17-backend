# WP5 — Large Claims Summary & Triangle Exclusion

> **Goal:** Surface the largest paid and outstanding claims in the data, let the actuary strike them
> out, and rebuild the triangles and factors without them — with the excluded exposure accounted for
> explicitly rather than silently dropped.

Status: planned (2026-08-21). Decisions: `docs/CLIENT_REQUIREMENTS_DECISIONS.md` §3 D5.
Requirement 6. Depends on WP0 and WP1 (reuses `TriangleGrid`).

---

## 0. Client requirement

> "Summary of high/top 10 claims paid and OS in the data, and a functionality if we remove those in
> our experience, triangles should be adjusted through a strikethrough feature"

## 1. How it works today

### 1.1 The claim identifier is discarded at the door

`import_data` (`module1_engine/engine.py`) reads a fixed column list:

```python
needed_columns = ['AMOUNTPAID','AMOUNTRECOVERED','ISSUEDATE','LOSSDATE','PAYMENTDATE',
                  'RESERVINGCLASS','POLICYCLASS','RI_TREATY_TYPE','HEADOFDAMAGE']
```

`CLAIMNUMBER` **is present in the source files** — verified in
`benchmarks/fixtures/summary_ref/` — but is not in the list, so it never reaches a DataFrame.
`ClaimsPaidRow` / `ClaimsOSRow` have no claim-number field either. `REPORTEDDATE` is likewise present
and discarded.

Claim identity therefore does not exist anywhere in the system today. Everything downstream is an
aggregate.

### 1.2 The data grain (measured, not assumed)

| File | Grain | Evidence |
|---|---|---|
| Claims paid | `(claim × head of damage × treaty type × transaction)` | 6,580 rows / 1,645 claims; 3,290 single-transaction slices and 1,645 two-transaction slices |
| Claims OS | `(claim × as-at × treaty type)` | 6,272 rows / 1,685 claims; exactly 2 rows per `(claim, as-at)` |

`CLAIMNUMBER` quality: zero nulls in either file, 836 claims present in both, and **no claim spans
more than one reserving class**. Safe as a key.

**Concentration: the top 10 claims are 29.9% of total paid.** Exclusion will move factors materially;
this is a high-impact, high-scrutiny feature.

## 2. Design

### 2.1 Ranking must be slice-scoped, never a naive sum

A naive `groupby(CLAIMNUMBER).sum()` over `AMOUNTPAID` **adds GROSS to RI and nets Payment against
Salvage** — a meaningless ordering. The ranking is therefore defined over an explicit slice:

```
rank_paid(slice) = SUM(engine "Amount") over rows matching the slice, grouped by CLAIMNUMBER
                   default slice: RI_TREATY_TYPE == "GROSS" AND HEADOFDAMAGE == "Payment"

rank_os(slice)   = SUM over rows at the LATEST "As at" per claim, grouped by CLAIMNUMBER
                   default slice: RI_TREATY_TYPE == "GROSS"
```

OS **must** use the latest as-at. Summing across as-at snapshots multiply-counts one reserve — with
up to 8 snapshots per claim in the reference data, by up to 8x.

Ranking uses the engine's derived `Amount` column (which already handles the Motor/recovery
substitution), not raw `AMOUNTPAID`, so the report agrees with the triangles it is used to adjust.

**No assumption is made about rows per claim.** The reference file's uniform 4 rows per claim is an
artefact of that extract; aggregation is by explicit key and the tests assert behaviour under
1, 2 and n transactions per slice.

### 2.2 Selection: top-N and threshold

| Mode | Parameter | Note |
|---|---|---|
| `top_n` | N, default 10 | What the client asked for |
| `threshold` | amount | More defensible period-over-period; a fixed count silently changes the exclusion basis as the book grows |

Both are scoped per `(RESERVINGCLASS)` by default, optionally book-wide. Per-class is the default
because factor selection is per class.

### 2.3 Treatment modes

| Mode | Effect | Default |
|---|---|---|
| `exclude_from_ldf_only` | Excluded claims removed from the **age-to-age factor** calculation only; they remain in the cumulative triangle, the Reserve Summary and the reserve base | **yes** |
| `exclude_and_add_back` | Attritional triangle developed without them; their actual paid + OS re-added at Reserve Summary level as a `Large Loss` line, plus an optional user-entered large-loss IBNR loading | |
| `exclude_entirely` | Removed from every calculation | no — warned |

`exclude_from_ldf_only` is the default because it changes the *factors* without changing the booked
reserve, which is the least-intrusive and most common actuarial treatment. `exclude_entirely`
understates the ultimate and the UI says so at the point of selection, not in a footnote.

The three modes differ only in **where the filter is applied**, which keeps the implementation small:

```
exclude_from_ldf_only  -> filter applied when building the a2a input only
exclude_and_add_back   -> filter applied to the triangle; excluded totals surfaced as a separate line
exclude_entirely       -> filter applied at import, before any aggregation
```

### 2.4 Strikethrough is one component, two meanings

WP1 builds `TriangleGrid` with per-cell strikethrough for **factor** exclusion. WP5 extends the same
component with **claim** exclusion, rendered as strikethrough on the affected triangle cells with a
tooltip naming the contributing claims. One component, one visual language, two exclusion sources —
and the UI distinguishes them by colour treatment so an actuary can see *why* a cell is struck.

### 2.5 Audit

An exclusion is a material judgement. Persisted to `input_meta["large_claims"]` and snapshotted:

```json
{
  "mode": "exclude_from_ldf_only",
  "selection": {"kind": "top_n", "n": 10, "per_class": true},
  "slice": {"treaty": "GROSS", "head_of_damage": "Payment"},
  "excluded": [{"claim_number": "SIL/D/C003/0000001213/0317/001",
                "reserving_class": "Banker's Blanket",
                "paid": 7665800.0, "os": 0.0, "manual": false}],
  "manual_additions": [], "manual_removals": [],
  "actor": "user@example.com", "applied_at": "2026-08-21T10:14:03Z"
}
```

`manual` distinguishes claims the rule selected from claims the actuary added or removed by hand —
the part a reviewer will ask about.

## 3. Backend changes

| File | Change |
|---|---|
| `module1_engine/engine.py` | `import_data` reads `CLAIMNUMBER` and `REPORTEDDATE`; `run_generate_summary` accepts `excluded_claims` + `mode`; filter applied at the point dictated by mode |
| `module1_engine/large_claims.py` | **new** — `rank_paid`, `rank_os`, `select_claims`, `LargeClaimReport` |
| `datasets/models.py` | `claim_number`, `reported_date` on `ClaimsPaidRow` / `ClaimsOSRow` + migration |
| `datasets/services/columns.py` | map entries; `claim_number` required, `reported_date` optional |
| `datasets/services/templates.py` | `COLUMN_META` entries |
| `processing/services/large_claims.py` | **new** — build the report from staged frames or a source job |
| `processing/views.py` | `Module1LargeClaimsView` (report), `excluded_claims` on the summary job view |
| `processing/tasks.py` | thread exclusions through; persist the audit block |

**Backfill note.** `claim_number` is `blank=True` on existing rows. Datasets imported before WP5 have
no claim numbers; the large-claims report reports that plainly rather than showing an empty top-10.

## 4. Frontend changes

| File | Change |
|---|---|
| `src/components/LargeClaimsPanel.tsx` | **new** — ranked table (claim, class, paid, OS, % of class total, cumulative %), select-all-top-N, threshold input, manual add/remove, mode selector |
| `src/components/TriangleGrid.tsx` | extend for claim-driven strikethrough with source attribution |
| `src/pages/SummaryGeneratorPage.tsx` | optional large-claims step before generate |
| `src/api/module1.ts` | `LargeClaimReportDto`, `ExcludedClaims` |
| `src/state/wizards/summary.ts` | persist selection, mode and manual edits |
| `src/data/fileSchemas.ts` | `CLAIMNUMBER` required, `REPORTEDDATE` optional on both claims schemas |

The panel shows **cumulative % of class paid** beside each claim. "These 10 claims are 29.9% of paid"
is the number that tells an actuary whether exclusion is warranted; the individual amounts do not.

## 5. Bit-identity and goldens

* No exclusions supplied → **bit-identical**. Reading two extra columns does not change any
  aggregate; asserted explicitly, because widening `needed_columns` touches a hot path.
* New goldens from the reference fixture for each of the three modes with the default top-10.

## 6. Tests

**`module1_engine/tests/test_large_claims.py`** (new)
* ranking excludes RI and Salvage rows under the default slice
* a claim with 1, 2 and n transactions in a slice aggregates correctly — the grain assumption is
  explicitly not relied upon
* OS ranking uses the latest as-at; a claim whose reserve falls between snapshots is ranked on the
  latest, not the maximum
* ranking uses the engine `Amount` column, matching Motor/recovery substitution
* `top_n` per class vs book-wide produce different sets on a crafted fixture
* `threshold` and `top_n` agree when the threshold is set to the Nth value
* `exclude_from_ldf_only` changes a2a factors but leaves the cumulative triangle and Reserve Summary
  `Paid Claims` unchanged
* `exclude_entirely` changes both
* `exclude_and_add_back` — attritional ultimate plus the large-loss line reconciles to the
  unexcluded reserve base within tolerance
* excluding every claim in a class → empty triangle handled, warned, not a crash
* an excluded claim number absent from the data → warning, not error

**`processing/tests/test_large_claims_api.py`** (new)
* report endpoint works from a source job and from staged uploads
* datasets without claim numbers produce an explicit "not available" response, not an empty list
* audit block persisted with actor and manual/rule provenance

**`datasets/tests/test_dataset_api.py`**
* claim-number round-trip through import, template and engine adapter

## 7. Edge cases

* **Same claim large in paid and in OS** → one entry, both columns populated; never double-listed.
* **Claim spanning reserving classes** — not present in reference data but not guaranteed. Excluded
  per (claim, class) pair so a cross-class claim is handled per class rather than being globally
  dropped.
* **Claim excluded from LDF but with the only observation in a development column** → that column
  loses its only factor; the column renders blank and WP1's "no valid cells" path applies.
* **Negative claim totals** (recoveries exceeding payments) — rankable and shown; "largest" is by
  absolute magnitude with the sign displayed.
* **Threshold above every claim** → empty selection, stated plainly.
* **Motor recovery substitution** — the engine substitutes `AMOUNTRECOVERED` for `AMOUNTPAID` on
  Motor recovery heads; the ranking uses the post-substitution `Amount`, so the report can legitimately
  differ from a naive spreadsheet sort of `AMOUNTPAID`. Documented in the panel.

## 8. Estimate

Backend 5d (plumbing 1.5d, ranking 1.5d, three modes 2d), frontend 4d, tests 3d, goldens 1d.
**~13 days.**
