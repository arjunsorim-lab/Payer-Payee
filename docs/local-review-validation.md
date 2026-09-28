# Local review validation

All identified backend failures have been repaired. Changes remain local and
have not been committed or pushed.

## Verification

- The complete backend run exercised 290 tests, including all selectable claims,
  all-member calculations, security, and 100,000-claim scale/concurrency tests.
  It passed 281 tests and exposed five additional workbook contract failures.
- All five repaired workbook checks subsequently passed together.
- Seven focused regression checks passed, including the newly added protection
  against scoring historical reference rows in the member audit endpoint.
- Shared login protection and existing security contracts passed 11 checks.
- Frontend: 38 tests passed. Lint and production build passed.
- Four optional checks were skipped: three require an available local Ollama
  service and one applies to the inactive legacy CSV mode.

Verification used a bounded 4,096-entry financial cache to avoid repeatedly
scoring the entire test workbook. The application default remains 512 entries;
the scale suite independently checks cache bounds and concurrent eviction.
The complete suite was followed by affected-test reruns after the final fixes.

## Correctness and organization

API tests isolate workstation authentication settings; the application's
authentication and role enforcement remain active outside the test fixture.
Tests read workbook counts and headers rather than relying on a previous file's
row counts. Member history includes reference evidence; financial summaries
score selectable claims only. Comparison cache identities use source content
when no workbook revision hash exists, avoiding reused Python object IDs.

Frontend modules now separate API/session access, routing, formatting, financial
narratives, claim evidence and financial breakdown presentation. Backend helpers
separate peer selection, rate comparisons, financial categories and chronological
outcome linkage. No fixed patient-name fallback or fixed-claim redirect is used.

## Production login protection

Set `LOGIN_RATE_LIMIT_REDIS_URL` to the shared Redis connection URL before
deploying. Every worker uses the same atomic, expiring login-attempt counter.
Production sign-in fails safely if that protection is missing or unavailable.
The Redis Lua behavior was tested with concurrent clients using a Redis emulator;
no deployed Redis instance or Render configuration was changed.
