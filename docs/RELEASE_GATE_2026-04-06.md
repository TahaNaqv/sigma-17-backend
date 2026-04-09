# Production Release Gate (2026-04-06)

## 1) Functional smoke pass

- Backend processing/API suites:
  - `poetry run python manage.py test processing.tests` -> pass
- Engine suites:
  - `poetry run pytest module1_engine/tests module2_engine/tests` -> pass

## 2) Quality gates

- Frontend:
  - `npm run lint` -> pass with warnings (no errors)
  - `npm run build` -> pass
- Backend:
  - tests above -> pass

## 3) Security and dependency scan

- Backend:
  - `pip-audit` -> no known vulnerabilities after upgrading `django` and `pyjwt`
- Frontend:
  - `npm audit --omit=dev --audit-level=high` -> one remaining high vulnerability in `xlsx` (no upstream fix)

## 4) Ops hardening and runbooks

- Environment-driven secure settings added:
  - secure cookies
  - HSTS
  - trusted CSRF origins
  - frame/referrer/content-type protections
- Operational docs delivered:
  - `PRODUCTION_RUNBOOK.md`
  - `SECURITY_CHECKLIST.md`
  - `PERFORMANCE_SLO.md`
  - `SECURITY_SCAN_REPORT.md`

## 5) Performance/capacity baseline

- Captured local baseline from critical API test suites (timings and memory), recorded in `PERFORMANCE_SLO.md`.

## Go / No-Go decision

- **Decision: GO, with one accepted risk**
- Accepted risk:
  - frontend `xlsx` vulnerability without upstream patch available
- Release condition:
  - track risk and prioritize migration from `xlsx` in next hardening sprint.
