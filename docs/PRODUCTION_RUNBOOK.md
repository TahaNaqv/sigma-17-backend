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
  - daily retention sweep counters (`retention.sweep_complete` log line: `examined`, `purged`, `errors`)

## 7) Chaining + retention operations

**Chaining lineage**
- `Module1Job.source_job` is a `PROTECT` self-FK. Attempting to delete a row
  that another job references will raise `ProtectedError`. This is the safe
  default; never use raw SQL to bypass it.
- To remove a job that has descendants, run **cascade purge** from Django
  admin: select the row(s), choose "Force purge job(s) and all descendants".
  This deletes the descendant subtree leaves-first, then the root, and removes
  every output ZIP along the way. The action records `retention.cascade_purge_complete`
  with `deleted_rows`, `purged_outputs`, `actor_id`.

**Daily output retention sweeper**
- Celery beat task `processing.tasks.purge_expired_outputs_task`. Schedule:
  03:15 UTC daily, batch size 500. Bounded to keep workers responsive.
- Selection criteria: `status=SUCCESS AND retention_until <= now AND legal_hold=False
  AND output_purged_at IS NULL AND output_zip != ''`.
- The sweeper deletes only the output ZIP file; it never deletes job rows.
  `output_purged_at` and an empty `output_artifacts` mark the row.

**Per-organization retention policy**
- Set `Organization.default_output_retention_days` in the org form on the
  dashboard or in Django admin. Blank = retain indefinitely.
- Existing jobs are not retroactively re-stamped; the policy applies to
  jobs that succeed AFTER the org setting takes effect.

**Legal hold**
- For regulated or audit-flagged jobs, set `legal_hold=True` on `Module1Job`
  (admin actions: "Set legal hold" / "Clear legal hold"). The sweeper skips
  these rows entirely.
- Cascade purge refuses to run if any node in the subtree has `legal_hold=True`
  unless explicitly passed `force=True`.

**One-shot backfill after the chaining migration**
- After applying `processing.0002_chaining_retention_lineage`, run:
  ```bash
  python manage.py backfill_output_artifacts
  ```
  This walks every successful job's output ZIP and stamps `output_artifacts`
  so the candidate-list endpoint and chaining filters work for historical
  rows. The command is idempotent, supports `--dry-run`, `--only-empty`,
  and `--batch-size`.

**Forensics for chained jobs**
- The job detail page in the dashboard shows a lineage badge ("From: Summary
  #3f9a · ...") linking to the source job.
- Django admin shows both `source_job` (link) and `derived_jobs` (list) on
  every job detail page.
- Task logs include structured `extra` fields: `job_id`, `job_type`,
  `org_id`, `user_id`, `source_job_id`, `source_artifact`.
