# Sigma 17 Backend

Django REST API backend for the Sigma 17 actuarial calculation engine. Provides authentication (JWT), RBAC (users, roles, permissions), and core APIs for the web application.

## Requirements

- **Python** 3.12+
- **PostgreSQL** 16+ (or use Docker)
- **Poetry** (recommended) or pip

## Project Structure

```
sigma-17-backend/
├── config/           # Django project settings (includes Celery app)
├── accounts/         # Auth, users, roles, permissions (RBAC)
├── core/             # Core views (health, registration)
├── processing/       # Module 1 background jobs (Celery), ZIP downloads
├── module1_engine/   # Headless Excel pipeline; `uw_patch.py` applies UW/ULAE/Discount to Combined_Summary
├── manage.py
├── pyproject.toml    # Dependencies (Poetry)
├── .env.example      # Environment template
├── Dockerfile
└── docker-compose.yml
```

## Setup

### 1. Clone and enter the project

```bash
cd sigma-17-backend
```

### 2. Create and activate a virtual environment

**With Poetry (recommended):**

```bash
poetry install
poetry shell
```

**With venv + pip:**

```bash
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# or: .venv\Scripts\activate   # Windows

pip install -e .
```

### 3. Environment configuration

Copy the example env file and edit as needed:

```bash
cp .env.example .env
```

Edit `.env` with your settings. Default PostgreSQL connection:

```env
DATABASE_URL=postgres://postgres:postgres@localhost:5432/sigma17
```

### 4. PostgreSQL

**Option A: Run PostgreSQL via Docker**

```bash
docker compose up -d db
```

**Option B: Use a local PostgreSQL instance**

Ensure a database named `sigma17` exists:

```bash
createdb sigma17
```

### 5. Run migrations

```bash
python manage.py migrate
```

### 6. Seed RBAC (optional but recommended)

Creates default permissions and roles (Super Admin, Admin, Actuary, Analyst, Viewer):

```bash
python manage.py seed_rbac
# To force-update existing roles:
python manage.py seed_rbac --force
```

### 7. Create a superuser (optional)

```bash
python manage.py createsuperuser
```

### 8. Run the development server

```bash
python manage.py runserver
```

API base URL: **http://127.0.0.1:8000**

---

## Run with Docker Compose

Starts PostgreSQL, **Redis**, the Django app, and a **Celery worker** (required for Module 1 jobs):

```bash
docker compose up --build
```

- **API:** http://localhost:8000
- **PostgreSQL:** localhost:5432
- **Redis:** localhost:6379
- **Celery:** worker container `sigma17-celery` (processes `processing.tasks`)

---

## Module 1 (Celery + Redis)

Long-running Excel jobs are queued to Celery. With Docker Compose, `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` default to `redis://redis:6379/0`.

**Local development (without Docker for workers):**

1. Start Redis (e.g. `docker run -p 6379:6379 redis:7-alpine`).
2. Set in `.env`: `CELERY_BROKER_URL=redis://localhost:6379/0` and `CELERY_RESULT_BACKEND=redis://localhost:6379/0`.
3. Run Django: `python manage.py runserver`
4. In another terminal: `celery -A config worker -l info`

Upload limits (optional env): `MODULE1_MAX_UPLOAD_FILES`, `MODULE1_MAX_UPLOAD_MB`.

To run only the database:

```bash
docker compose up -d db
```

Then run migrations and the server locally:

```bash
python manage.py wait_for_db
python manage.py migrate
python manage.py runserver
```

---

## Commands Reference

| Command | Description |
|---------|-------------|
| `python manage.py runserver` | Start development server (port 8000) |
| `python manage.py migrate` | Apply database migrations |
| `python manage.py makemigrations` | Create migrations after model changes |
| `python manage.py createsuperuser` | Create admin user |
| `python manage.py seed_rbac` | Seed permissions and roles |
| `python manage.py seed_rbac --force` | Force-update role permissions |
| `python manage.py wait_for_db` | Wait until PostgreSQL is ready |
| `python manage.py shell` | Django shell |
| `python manage.py test` | Run tests (includes `processing.tests` for API tests) |
| `pytest` | Run `module1_engine` unit tests (from repo root / backend) |

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health/` | Health check |
| `POST` | `/api/auth/token/` | Obtain JWT (login) |
| `POST` | `/api/auth/token/refresh/` | Refresh JWT |
| `GET` | `/api/auth/me/` | Current user (authenticated) |
| `GET` | `/api/auth/profile/` | User profile (authenticated) |
| `POST` | `/api/auth/change-password/` | Change password (authenticated) |
| `POST` | `/api/register/` | Register new user |
| `GET` | `/api/users/` | List users (admin) |
| `POST` | `/api/users/` | Create user (admin) |
| `GET` | `/api/users/{id}/` | User detail (admin) |
| `PUT` | `/api/users/{id}/` | Update user (admin) |
| `DELETE` | `/api/users/{id}/` | Delete user (admin) |
| `GET` | `/api/roles/` | List roles |
| `GET` | `/api/permissions/` | List permissions |
| `GET` | `/admin/` | Django admin |
| `GET` | `/api/processing/source-candidates/?artifact=Combined_Summary.xlsx` | List jobs eligible to chain (`module1.run` or `module2.run`) |
| `GET` | `/api/module1/jobs/` | List Module 1 jobs (`runhistory.view`) |
| `POST` | `/api/module1/jobs/summary/` | Start summary job — multipart (`module1.run`) |
| `POST` | `/api/module1/jobs/policy-upr/` | Start policy UPR job (`module1.run`) |
| `POST` | `/api/module1/jobs/update-reserve/` | Start update-reserve job (`module1.run`) |
| `GET` | `/api/module1/jobs/{uuid}/` | Job status (`module1.run` or `outputs.download` or `runhistory.view`; own jobs) |
| `GET` | `/api/module1/jobs/{uuid}/download/` | ZIP download when successful (same permissions) |
| `GET` | `/api/module1/jobs/{uuid}/output/files/` | List previewable output files in ZIP (`.xlsx`) |
| `GET` | `/api/module1/jobs/{uuid}/output/sheets/?file=...` | List sheets + dimensions for one workbook in ZIP |
| `GET` | `/api/module1/jobs/{uuid}/output/rows/?file=...&sheet=...&page=1&page_size=50` | Paginated sheet rows for in-app preview |
| `POST` | `/api/module1/combined-summary/uw-preview/` | Multipart `combined_summary` — returns JSON templates for Exp Ratio, ULAE-RA, Discount rows (`module1.run`) |
| `POST` | `/api/module1/jobs/uw-parameters/` | Multipart `combined_summary` + form field `payload` (JSON string with `exp_ratio`, `ulae_ra`, `discount`) — Celery job; ZIP contains updated `Combined_Summary.xlsx` (`module1.run`) |
| `GET` | `/api/module2/jobs/` | List Module 2 jobs (`runhistory.view`) |
| `POST` | `/api/module2/jobs/allocate/` | Start Module 2 allocate job with multipart `combined_summary` (`module2.run`) |
| `GET` | `/api/module2/jobs/{uuid}/ulr/` | Get ULR rows after allocate success (`module2.run`) |
| `POST` | `/api/module2/jobs/process/` | Start Module 2 process job with multipart `previous_period`, `expense_cf`, `allocate_job_id`, `accounting_period`, and JSON `selected_ulr` (`module2.run`) |
| `GET` | `/api/module2/jobs/{uuid}/` | Module 2 job status/details (same read model as Module 1 + ownership) |
| `GET` | `/api/module2/jobs/{uuid}/download/` | Module 2 ZIP download when successful (same read model as Module 1 + ownership) |

**`payload` JSON shape (snake_case):**

- `exp_ratio`: `[{ "reserving_class", "uwy", "exp_ratio", "ri_percent" }]`
- `ulae_ra`: `[{ "reserving_class", "gross_ri": "GROSS" \| "RI", "ulae_percent", "ra_percent" }]`
- `discount`: `[{ "time_period", "cy_discount", "py_discount" }]`

---

## Chaining (Module 1 → Module 1, Module 1 → Module 2)

Any endpoint that consumes `Combined_Summary.xlsx` accepts **either** a fresh
upload or a `source_job_id` pointing at a previous successful job whose output
ZIP contains that artifact. The chaining is a single primitive implemented in
`processing/services/source_resolver.py` and applied uniformly across:

| Consumer endpoint                                | Upload field                        | Chain field                                      | Rule              |
|--------------------------------------------------|-------------------------------------|--------------------------------------------------|-------------------|
| `POST /api/module2/jobs/allocate/`               | `combined_summary`                  | `source_job_id`                                  | exactly one       |
| `POST /api/module1/jobs/uw-parameters/`          | `combined_summary`                  | `source_job_id`                                  | exactly one       |
| `POST /api/module1/combined-summary/uw-preview/` | `combined_summary`                  | `source_job_id`                                  | exactly one       |
| `POST /api/module1/jobs/update-reserve/`         | `combined_summary`                  | `source_job_id`                                  | at most one       |
| `POST /api/module1/jobs/summary/`                | `existing_combined_summary`         | `existing_combined_summary_source_id`            | at most one       |
| `POST /api/module2/jobs/process/`                | —                                   | `allocate_job_id` (chains via same primitive)    | required          |

Eligible **source** job types are `summary`, `uw_parameters`, `update_reserve`,
`module2_allocate` — any successful job whose denormalized `output_artifacts`
field includes `Combined_Summary.xlsx`. Sources are org-scoped and owner-scoped
for non-superusers; uniform 400 errors avoid info leaks. Lineage is enforced at
the database via `Module1Job.source_job` (`ForeignKey('self', on_delete=PROTECT)`).

`GET /api/processing/source-candidates/?artifact=Combined_Summary.xlsx&job_type=&page=&page_size=`
powers the picker UIs in the dashboard; it returns successful, non-purged jobs
of the producer types listed above.

## Output retention

`Organization.default_output_retention_days` configures when successful jobs'
output ZIPs become eligible for the daily cleanup sweep. Per-job overrides:

- `Module1Job.legal_hold = True` — sweeper skips the row indefinitely (admin action).
- `Module1Job.output_purged_at` — set when the ZIP has been deleted; the row stays.

The sweep runs as `processing.tasks.purge_expired_outputs_task` (Celery beat,
03:15 UTC daily, batch size 500). Cascade purge of a source job + its
descendants is exposed as an admin action on `Module1Job` and as
`processing.tasks.cascade_purge_task`.

After applying the new migration on an existing deployment, run:

```bash
python manage.py backfill_output_artifacts
```

to populate `output_artifacts` for previously successful jobs (idempotent;
takes `--batch-size`, `--only-empty`, `--dry-run` flags).

## Module 2 payloads

### Allocate request

- `combined_summary`: `Combined_Summary.xlsx` (multipart file)

### ULR rows response

- `rows`: list of objects with fields used by dashboard ULR table:
  - `reserving_class`, `uwy`
  - `gwp`, `upr`, `gep`, `paid_claims`, `os`, `ibnr`
  - `incurred_claims`, `ultimate_claims`
  - `paid_lr`, `inc_lr`, `ult_lr`
  - `commission_expense`, `comm_ratio`, `exp_ratio`, `ri_percent`, `ra_percent`
  - `selected_ulr`, `combined_ratio`

### Process request

- `allocate_job_id`: UUID of successful allocate job
- `accounting_period`: year (integer string, e.g. `2024`)
- `selected_ulr`: JSON array string:
  - `[{ "reserving_class": "...", "uwy": "...", "selected_ulr": 0.73 }]`
- `previous_period`: workbook containing `LIC_BOP` and `UPR-DAC_BOP`
- `expense_cf`: workbook containing `Expense-CF`

### Module 2 output workbook sheets

- Allocate stage includes:
  - `MainSheet`, `FutureCF`, `Discounted CF CY`, `Discounted CF PY`
  - `Payment Pattern`, `Run-off`, `Loss Ratio`, `LC`, `CY-PY Discount`, `UPR-DAC_eop`
- Process stage adds:
  - `Movement Analysis`, `IFRS Summary`, `LRC BOP-EOP Reconciliation`, `LIC BOP-EOP Reconciliation`

---

## Authentication

Uses **JWT** (Simple JWT). Include the access token in requests:

```
Authorization: Bearer <access_token>
```

- **Access token lifetime:** 60 minutes  
- **Refresh token lifetime:** 7 days  

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgres://postgres:postgres@localhost:5432/sigma17` | PostgreSQL connection URL |
| `DB_NAME` | `sigma17` | Database name |
| `DB_USER` | `postgres` | Database user |
| `DB_PASSWORD` | `postgres` | Database password |
| `DB_HOST` | `localhost` | Database host |
| `DB_PORT` | `5432` | Database port |
| `SECRET_KEY` | (insecure default) | Django secret key; **set in production** |
| `DEBUG` | `True` | Debug mode; **False in production** |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,0.0.0.0` | Allowed hosts (comma-separated) |
| `CORS_ALLOW_ALL_ORIGINS` | `True` (when DEBUG) | Allow all CORS origins; restrict in production |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Celery broker (Redis) |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | Celery result backend |
| `MODULE1_MAX_UPLOAD_FILES` | `50` | Max uploaded files per job |
| `MODULE1_MAX_UPLOAD_MB` | `200` | Max total upload size per job |
| `MODULE1_OUTPUT_PREVIEW_DEFAULT_PAGE_SIZE` | `50` | Default sheet preview page size |
| `MODULE1_OUTPUT_PREVIEW_MAX_PAGE_SIZE` | `200` | Max allowed `page_size` for output preview |
| `MODULE1_OUTPUT_PREVIEW_MAX_CELLS` | `20000` | Max cells returned in one preview response |

`MODULE1_OUTPUT_PREVIEW_MAX_PAGE_SIZE` limits requested rows per page, while `MODULE1_OUTPUT_PREVIEW_MAX_CELLS` limits total cells returned in a single preview response.

---

## Development Notes

- The backend expects the **sigma-17-dashboard** frontend to run (e.g. on port 5173). CORS is configured for cross-origin requests when `DEBUG=True`.
- For production, use **gunicorn**:  
  `gunicorn config.wsgi:application --bind 0.0.0.0:8000`
- Set `SECRET_KEY`, `DEBUG=False`, and restrict `ALLOWED_HOSTS` and CORS in production.

## Module 2 release checklist

- Run migrations: `python manage.py migrate`
- Ensure Celery worker + Redis are running
- Seed RBAC: `python manage.py seed_rbac`
- Verify Module 1 flows remain green
- Run Module 2 end-to-end: Allocate -> ULR edit/skip -> Process -> Download

## Production operations docs

- Runbook: `PRODUCTION_RUNBOOK.md`
- Security checklist: `SECURITY_CHECKLIST.md`
- Performance/SLO notes: `PERFORMANCE_SLO.md`

### Module 1 release runbook

- Ensure `python manage.py migrate` has been applied (including `Module1Job` job type updates).
- Ensure Redis and a Celery worker are running; Module 1 jobs are asynchronous and will not complete without workers.
- Seed RBAC (`python manage.py seed_rbac`) and verify users have `module1.run` and/or `runhistory.view`/`outputs.download` as intended.
- Keep `MODULE1_OUTPUT_PREVIEW_MAX_PAGE_SIZE` and `MODULE1_OUTPUT_PREVIEW_MAX_CELLS` tuned for your workload; very large sheets may require lower limits.
