# Sigma 17 Security Checklist

## Configuration baseline

- [ ] `DEBUG=False` in production.
- [ ] Strong `SECRET_KEY` set via environment variable.
- [ ] `ALLOWED_HOSTS` restricted to production domains.
- [ ] `CORS_ALLOW_ALL_ORIGINS=False` in production.
- [ ] `CORS_ALLOWED_ORIGINS` explicit allowlist.
- [ ] `CSRF_TRUSTED_ORIGINS` explicit allowlist.
- [ ] Secure cookie flags enabled (`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`).
- [ ] HSTS enabled with suitable duration (`SECURE_HSTS_SECONDS`).
- [ ] `X_FRAME_OPTIONS=DENY` unless framing is explicitly required.

## Application controls

- [ ] JWT auth validated for protected APIs.
- [ ] RBAC rules verified for module run/list/download/detail endpoints.
- [ ] Ownership checks enforced for job read/download paths.
- [ ] File uploads restricted to expected extensions and capped by size/count.
- [ ] Error responses avoid leaking stack traces or sensitive infrastructure details.

## Dependency and static checks

- [ ] Backend dependency scan completed (`poetry` environment).
- [ ] Frontend dependency scan completed (`npm audit`).
- [ ] Known vulnerabilities triaged and accepted-risk list documented.

## Operational controls

- [ ] Access to production secrets is restricted (least privilege).
- [ ] DB backups are encrypted and tested for restore.
- [ ] Logs avoid storing secrets, credentials, or personal data.
- [ ] Incident response contacts and escalation path are documented.
