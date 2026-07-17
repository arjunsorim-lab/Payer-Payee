# Provider LLM prediction service

The Provider LLM card combines a deterministic Python episode forecast with a concise Groq explanation. Groq never calculates the forecast: it explains the already-scored provider, payer, procedure, utilization, payment, and evidence facts.

The result opens in a claim-scoped modal. It separates `ACTUAL CLAIM FACTS` from `FORECAST FOR NEXT RELATED CLAIM`, shows a holdout backtest for adjudicated claims, and exposes outcome, denial and 30/60/90-day repeat probabilities, financial ranges, provider exposure, reconciliation, confidence, metric-specific sample details and prediction method. A dynamic provider money scenario map and ranked administrative actions use the same backend values. `EXACT MODEL OUTPUT` remains a PHI-free audit view.

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
- Financial ranges blend hierarchical peer rates with the member's earlier payer/CPT adjudication history. The submitted charge for the selected claim—not the total episode charge—is the amount basis. Matching starts at payer + billing provider + CPT + diagnosis family + place of service + units and falls back through seven documented levels only when the minimum sample is not met.
- Denial probability uses an empirical-Bayes blend of the peer denial rate and relevant earlier member adjudications.
- Repeat utilization at 30, 60, and 90 days uses mature historical recurrence observations for related diagnosis and procedure families, with peer evidence as the prior.
- Only claims earlier than the selected claim can become longitudinal inputs; later claims are excluded to prevent temporal leakage.
- Avoidable spend is shown only when repeated-service evidence and the configured minimum peer count are both present. It is always labelled “Potentially avoidable repeat-service spend.”
- Priority is an administrative review order from 0–100, not clinical acuity.
- Confidence combines local and external sample sizes, hierarchy specificity, fallback depth, required-field completeness, peer financial variance, historical MAE, interval coverage and outcome/recurrence calibration. It is calculated in Python and cannot be changed by Groq.
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
- `POST /api/provider-llm/chat` accepts only claim ID, episode ID, question and conversation ID. The backend rebuilds the structured case context and returns a grounded, claim-scoped answer without receiving raw CSV rows from the browser.

The prediction payload returns `actual_claim_facts`, `forecast`, `provider_financial_metrics`, `financial_reconciliation`, `backtest_against_actual`, `provider_money_scenario_map`, `prediction_basis`, `risk_drivers`, `recommended_actions`, `evidence_used`, `limitations`, and `exact_model_output`. Actual adjudication is never used as a prediction input; it is attached only after scoring for the backtest.

If Groq times out or returns invalid structured output after one repair attempt, the endpoint returns a labelled deterministic fallback instead of failing the page.
