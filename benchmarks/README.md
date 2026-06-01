# Engine benchmarks & golden fixtures

This directory holds the **data** for the performance scoreboard and the
bit-identical correctness net described in
[`docs/PERFORMANCE_OPTIMIZATION_PLAN.md`](../docs/PERFORMANCE_OPTIMIZATION_PLAN.md)
(Phase 0).

Real fixture inputs and frozen goldens are **git-ignored** (see `.gitignore`)
because they contain real/representative actuarial data. Only this README is
tracked. Code that drives them lives in `processing/benchmarks.py`,
`processing/golden.py`, and the `bench_engines` / `capture_golden` management
commands.

## Layout

```
benchmarks/
  fixtures/
    <fixture_name>/
      spec.json                 # describes the job & where its inputs are
      premium/        *.xlsx     # (example inputs — names come from spec.json)
      claims_paid/    *.xlsx
      claims_os/      *.xlsx
  goldens/
    <fixture_name>/             # produced by `manage.py capture_golden`
```

## `spec.json`

```json
{
  "job_type": "summary",
  "params": {
    "start": "01-01-2020",
    "end":   "31-12-2023",
    "bop":   "01-01-2023",
    "eop":   "31-12-2023"
  },
  "inputs": {
    "premium":      "premium",
    "claims_paid":  "claims_paid",
    "claims_os":    "claims_os"
  }
}
```

`job_type` ∈ `summary`, `policy_upr`, `update_reserve`, `m2_allocate`, `m2_process`.

Input roles per job type:

| job_type        | required `inputs` roles                              | key `params`                                  |
|-----------------|-----------------------------------------------------|-----------------------------------------------|
| `summary`       | `premium`, `claims_paid`, `claims_os` (folders)     | `start`, `end`, `bop`, `eop` (DD-MM-YYYY)     |
| `policy_upr`    | `premium` (folder)                                  | `bop`, `eop`                                  |
| `update_reserve`| `folder` (folder of reserve workbooks)              | —                                             |
| `m2_allocate`   | `combined_summary` (file)                           | —                                             |
| `m2_process`    | `combined_summary`, `previous_period`, `expense_cf` | `accounting_period`, `selected_ulr_rows`      |

Paths in `inputs` are relative to the fixture directory and may be a folder
(staged `.xlsx` files, mirroring how the Celery tasks present inputs) or a file.

## Workflow

1. Drop a fixture into `fixtures/<name>/` (inputs + `spec.json`). Use a small,
   a medium, and a large fixture per job type so we can see scaling.
2. Freeze goldens **on the current (pre-optimisation) code**:
   ```
   python manage.py capture_golden --all
   ```
3. Establish the baseline timing table (paste into the plan's appendix):
   ```
   python manage.py bench_engines --repeat 3
   ```
4. During/after each optimisation, prove correctness + measure:
   ```
   python manage.py bench_engines --check
   pytest module1_engine/tests/test_golden_engines.py
   ```

`SIGMA_PROFILE=1` also emits per-stage timing logs when running real jobs
through the Celery worker.
