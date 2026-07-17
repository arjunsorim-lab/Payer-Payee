# Provider LLM prediction service

The Provider LLM card combines a deterministic Python episode forecast with a concise Groq explanation. Groq never calculates the forecast: it explains the already-scored provider, payer, procedure, utilization, payment, and evidence facts.

The result UI separates `ACTUAL CLAIM FACTS` from the retrospective deterministic estimate. `PREDICTION SNAPSHOT` exposes outcome, denial and 30/60/90-day repeat probabilities, financial ranges, potentially avoidable spend availability, confidence, peer sample and method without requiring the user to interpret prose. `EXACT MODEL OUTPUT` is a PHI-free audit view of the numeric backend output.

## Configuration

Copy `.env.example` to `.env`. The existing provider settings are intentionally retained:

```dotenv
GROQ_API_KEY=your_backend_only_secret
GROQ_MODEL=openai/gpt-oss-20b
GROQ_CACHE_TTL_SECONDS=900
GROQ_TIMEOUT_SECONDS=15
PROVIDER_EPISODE_WINDOW_DAYS=90
PROVIDER_MIN_PEERS=5
```

Do not add the Groq key to frontend environment variables. Do not commit `.env`. The app does not use `OPENAI_API_KEY` or `OPENAI_MODEL` for this feature.

## Method

- Canonical claim records are validated before scoring.
- Valid claims are grouped into stable, non-PHI 90-day member and diagnosis-family episodes.
- Every valid claim is assigned to exactly one episode.
- Financial ranges use hierarchical matching and medians/interquartile ranges from earlier adjudicated peer episodes only.
- Denial probability uses a smoothed historical peer rate.
- Repeat utilization is reported at 30, 60, and 90 days.
- Avoidable spend is shown only when repeated-service evidence and the configured minimum peer count are both present. It is always labelled “Potentially avoidable repeat-service spend.”
- Priority is an administrative review order from 0–100, not clinical acuity.
- Confidence combines peer-claim sample size, hierarchy match specificity, required-field completeness and peer allowed-rate stability. It is calculated in Python and cannot be changed by Groq.
- Recommendations are deterministic and conditional: denial review requires a denial, repeat review requires repeated CPT evidence, and missing authorization/referral identifiers produce a verification instruction rather than an unsupported requirement claim.
- The LLM receives coded, de-identified facts and exact claim-evidence references. It cannot determine medical necessity.

## Commands

```bash
npm run backend
npm run frontend
npm run test:backend
npm run report:provider-llm
```

The batch command writes `output/provider_llm_batch_report.json`, including validation counts, assignment quality, aggregate forecast metrics, and a machine-readable record for every episode.

## API

- `GET /api/predictions/provider-case/<claim number>` returns the deterministic episode forecast and metadata.
- `POST /api/predictions/provider-case/<claim number>/llm` returns the strict Groq analysis. A successful response is cached by model, prompt version, and de-identified case content.

Both endpoints return `actual_claim_facts`, `forecast`, `prediction_basis`, `risk_drivers`, `recommended_actions`, `evidence_used`, `limitations`, and `exact_model_output`. The financial forecast is explicitly labelled `retrospective_current_claim` because the current submitted charge is its basis; actual adjudication remains separate.

If Groq times out or returns invalid structured output after one repair attempt, the endpoint returns a labelled deterministic fallback instead of failing the page.
