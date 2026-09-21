# Deployment guide

This document covers running PayerPayee outside local development. The
application is claims-utilisation and financial decision support. It is not
clinical advice, does not determine medical necessity, and must never present a
recommendation, a model estimate or synthetic demonstration data as recorded
care.

## 1. Local versus production mode

| Mode | How it is selected | Access rule | Write rule |
| --- | --- | --- | --- |
| Local demo | Default when neither `APP_ENV=production` nor `RENDER` is set and `REQUIRE_AUTH` is not `true` | Loopback addresses (`127.0.0.1`, `::1`) only | Allowed for the `local-demo` reviewer identity |
| Production | `APP_ENV=production`, or `RENDER` is present, or `REQUIRE_AUTH=true` | Sign-in required for every `/api/*` route except `/api/session` and `/api/login` | Requires a signed-in `reviewer` or `admin` plus a matching CSRF token |

A remote request that reaches a service in local demo mode is rejected with
`403` ("Local demo access only"). This is deliberate: an unconfigured service
must never expose claims to an untrusted network.

## 2. Required production credentials

Set these in the hosting environment (Render: *Environment* tab). Never commit
them, and never store plaintext passwords.

| Variable | Required | Purpose |
| --- | --- | --- |
| `APP_ENV` | Yes (`production`) | Forces authentication regardless of `REQUIRE_AUTH` |
| `REVIEW_SESSION_SECRET` | Yes | At least 32 random characters; signs the session cookie |
| `REVIEW_USERS_JSON` | Yes | JSON map of username to `{ "password_hash": "<werkzeug hash>", "role": "viewer\|reviewer\|admin" }` |
| `CORS_ORIGIN` | Yes | Comma-separated exact browser origins; no wildcards |
| `REVIEW_DB_PATH` | Recommended | Private SQLite path for review decisions and audit events |
| `SAVINGS_WORKBOOK_PATH` | Yes (workbook mode) | The authoritative claims workbook |
| `MONGODB_URI` | Only if MongoDB endpoints are used | Use a hosted MongoDB; never `localhost` on a remote host |

Generate a password hash:

```bash
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('change-me'))"
```

Generate a session secret:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### Failing safely when credentials are missing

If authentication is required but credentials are not configured
(`REVIEW_USERS_JSON` empty or the session secret shorter than 32 characters),
every protected route answers `503` with a clear message instead of falling back
to anonymous access:

```
Secure access is not configured. Set review users and a session secret on the server.
```

There are no built-in default production accounts. If no user is configured,
no one can read claims.

## 3. Roles

| Role | Read claims | Submit review decisions | Rebuild retrieval index | Read audit log |
| --- | --- | --- | --- | --- |
| `viewer` | Yes | No | No | No |
| `reviewer` | Yes | Yes | No | No (own-review history only) |
| `admin` | Yes | Yes | Yes | Yes |

Write endpoints are rejected for `viewer` with `403`. Review endpoints require
`reviewer` or `admin`. `/api/review-audit` and `/api/rag/rebuild` require
`admin`.

## 4. Security controls

| Control | Implementation |
| --- | --- |
| Authentication | Cookie session; `401` without a session when auth is required |
| CSRF | Non-`GET` browser requests must send `X-CSRF-Token` matching the token issued by `/api/session` and `/api/login` |
| Cookies | `HttpOnly`, `SameSite=Strict`, `Secure` in production, 8-hour lifetime |
| Rate-limited login | 5 failed attempts per IP per 15 minutes, then `429` |
| Audit logging | Every API access, login result and review change is written to the private audit table with actor, event, route pattern, status and detail |
| CORS | Explicit origin allow-list from `CORS_ORIGIN`; requests with a disallowed `Origin` are rejected before routing |
| Security headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `Cache-Control: no-store` on API responses |
| Request size limit | `MAX_CONTENT_LENGTH` (1 MiB) rejects oversized bodies with `413` |

The audit log stores route patterns and status codes, not member identifiers,
query strings or request bodies.

## 5. Scale configuration

Paging is enforced on collection endpoints (`page`, `limit`, capped per route),
and every in-process memoisation cache is bounded so a large population cannot
grow memory without limit.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CACHE_MAX_ENTRIES` | `512` | Default bound for workbook-scoped caches |
| `FINANCIAL_CACHE_MAX_ENTRIES` | `CACHE_MAX_ENTRIES` | Canonical financial result cache |
| `PAYER_COHORT_CACHE_MAX_ENTRIES` | `CACHE_MAX_ENTRIES` | Payer cohort episode cache |
| `PAYER_MEMBER_CACHE_MAX_ENTRIES` | `128` | Per-member payer summary cache |
| `AVOIDABLE_CACHE_MAX_ENTRIES` | `CACHE_MAX_ENTRIES` | Avoidable-spend forecast cache |
| `LLM_CACHE_MAX_ENTRIES` / `LLM_CHAT_CACHE_MAX_ENTRIES` | `128` / `256` | Explanation and chat caches |
| `RAG_CACHE_MAX_ENTRIES` | `8` | Workbook retrieval index cache |

Caches are also invalidated when the workbook hash changes.

## 6. Deploy steps

```bash
# 1. Install and build
pip install -r backend/requirements.txt
npm ci
npm run build

# 2. Verify before starting
npm run test:backend
npm run test:frontend
npm run lint

# 3. Start
gunicorn backend.app:app --preload --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120
```

`render.yaml` performs the install and build steps and starts gunicorn. It marks
the secrets as `sync: false` so they must be provided explicitly in the Render
dashboard.

### Post-deploy verification

```bash
BASE=https://<your-service>
curl -s "$BASE/health"                                   # service and source health
curl -s -o /dev/null -w '%{http_code}\n' "$BASE/api/claims"   # expect 401/503, never 200
curl -s "$BASE/api/session"                              # expect authenticated=false, mode=secure
```

An unauthenticated `200` from a claims route means authentication is not
enforced; stop and fix the configuration before proceeding.

## 7. Evidence-integrity rules in production

- Claim values are labeled `recorded_claim_fact`; generated demonstration rows
  are labeled `synthetic_demonstration`.
- Intervention output is labeled `recommendation` and is never recorded care.
- Reviewer decisions are labeled `reviewer_observation` and remain unverified.
- Predicted and estimated amounts are labeled `model_estimate`.
- Savings are labeled `verified_savings` only when post-intervention claims
  evidence supports them; claims correlation alone never verifies a saving.
- A causal clinical effect is never claimed from claims correlation.

## 8. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `503` on every `/api/*` route | Auth required but users/secret missing | Set `REVIEW_USERS_JSON` and a 32+ character `REVIEW_SESSION_SECRET` |
| `403` "Local demo access only" | Remote request to a service in local demo mode | Set `APP_ENV=production` and configure users |
| `403` "Refresh your session before saving" | Missing or stale CSRF token | Reload the page to re-issue a session token |
| `429` on sign-in | Rate limit reached | Wait 15 minutes |
| `413` on a POST | Body over 1 MiB | Reduce the payload |
| `409` "This review changed. Reload before saving." | Stale review edit | Reload the member and re-apply the decision |
