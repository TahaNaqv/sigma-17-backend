# Sigma 17 Performance and Capacity Notes

## Suggested SLO targets

- Job status polling endpoint p95: <= 500 ms
- Processing history endpoint p95: <= 700 ms
- Module 1/2 job queue-to-start latency p95: <= 30 s
- Celery task success rate: >= 99%

## Capacity baseline to measure

- Large workbook upload processing time (Module 1 and Module 2):
  - sample set: small/medium/large files
  - record total wall-clock from submit to success
- Concurrent polling behavior:
  - multiple active users polling job status every 2-3 seconds
  - monitor backend p95 and error rate
- Worker throughput:
  - jobs completed per hour by type
  - queue lag under normal and burst load

## Guardrails and limits

- `MODULE1_MAX_UPLOAD_FILES` and `MODULE1_MAX_UPLOAD_MB` protect upload volume.
- Preview limits:
  - `MODULE1_OUTPUT_PREVIEW_MAX_PAGE_SIZE`
  - `MODULE1_OUTPUT_PREVIEW_MAX_CELLS`

For production, document accepted maximum workbook dimensions and expected completion windows per module.

## Current baseline evidence (local CI-like run)

- `processing.tests.test_module2_api`:
  - 12 tests in ~25.0s
  - command elapsed ~29.9s, max RSS ~128 MB
- `processing.tests.test_output_preview_api`:
  - 13 tests in ~16.7s
  - command elapsed ~20.8s, max RSS ~128 MB

These are not a substitute for staging load tests, but provide a repeatable baseline before release.
