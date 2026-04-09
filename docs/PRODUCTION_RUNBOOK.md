# Sigma 17 Production Runbook

This runbook is the operational checklist for backend production deploys.

## 1) Pre-deploy checklist

- Confirm latest code is in release branch.
- Confirm `.env` values are present and validated:
  - `DEBUG=False`
  - `SECRET_KEY` is strong and unique
  - `ALLOWED_HOSTS` contains production domains only
  - `CORS_ALLOW_ALL_ORIGINS=False`
  - `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` are explicit
  - production security flags enabled (`SECURE_*`, cookies secure)
- Confirm PostgreSQL, Redis, and Celery worker are healthy.
- Confirm backup snapshot was taken before deploy.

## 2) Deployment steps

1. Pull release version to server.
2. Install dependencies.
3. Run migrations:
   - `python manage.py migrate`
4. Seed RBAC:
   - `python manage.py seed_rbac`
5. Restart app process and Celery worker.
6. Verify health endpoint:
   - `GET /api/health/`

## 3) Post-deploy verification

- Run authentication smoke (token issue + `/api/auth/me/`).
- Module 1 smoke:
  - submit summary job
  - poll status
  - download output
- Module 2 smoke:
  - allocate -> ULR -> process -> download
- Verify processing history endpoint:
  - `GET /api/processing/jobs/`

## 4) Rollback playbook

If critical issues are detected:

1. Stop new traffic (or place app in maintenance mode).
2. Roll back to previous known-good release artifact.
3. Restart app and Celery worker with previous release.
4. Re-run health and smoke checks.
5. If schema migration introduced incompatibility, execute rollback migration only if tested; otherwise restore DB snapshot.

## 5) Backups and restore

Minimum policy:

- Daily full PostgreSQL backup.
- Additional backup before every production deploy.
- Retention: at least 14 days.
- Quarterly restore drill in staging from a production backup snapshot.

Record each restore drill result with timestamp and operator.

## 6) Monitoring and alerting baseline

- Alert on:
  - backend process down
  - celery worker down
  - redis unavailable
  - DB connectivity failures
  - sustained 5xx rate spike
- Track:
  - job failures by module/job_type
  - queue depth / queue lag
  - API latency p95 for job list/detail/status endpoints
