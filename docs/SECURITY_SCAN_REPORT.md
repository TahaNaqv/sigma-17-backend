# Security Scan Report

Date: 2026-04-06

## Backend (`sigma-17-backend`)

- Tool: `pip-audit`
- Result after dependency upgrades: **no known vulnerabilities**
- Actions applied:
  - upgraded `django` to `6.0.4`
  - upgraded `pyjwt` to `2.12.1`

## Frontend (`sigma-17-dashboard`)

- Tool: `npm audit --omit=dev --audit-level=high`
- Result: 1 high vulnerability remains
  - package: `xlsx`
  - advisory: GHSA-4r6h-8v6p-xvw6 and GHSA-5pgg-2g8v-p4x9
  - fix status: **no upstream fix available**

## Risk decision

- Current posture:
  - backend vulnerabilities remediated
  - frontend residual risk limited to `xlsx` with no published fixed version
- Sign-off recommendation:
  - accept temporary risk for current release
  - prioritize migration away from `xlsx` to a maintained alternative in the next hardening sprint
