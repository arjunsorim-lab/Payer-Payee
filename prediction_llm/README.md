# Prediction LLM — local claims analytics

A local, evidence-grounded claims analytics service for the MongoDB `payer_payee.837_claims` collection.

## What it does

- Summarises a member's available claim history.
- Analyses ICD-10 and CPT codes together.
- Compares charge, allowed, paid, and patient-responsibility amounts.
- Finds similar earlier claims using an explicit, inspectable score.
- Detects repeated CPT activity and adjacent claims within zero to three days.
- Compares financial values with earlier claims sharing the same CPT and ICD-10 codes.
- Shows the underlying claim records and Claim IDs for every finding.
- Separates stored facts, calculated patterns, and insufficient evidence.
- Uses the local Ollama `gemma3:1b` model only to explain deterministic evidence.

The service does not give clinical advice, diagnose, recommend treatment or medication, infer why care occurred, or assess medical necessity.

## Run

```sh
./start_prediction_llm.sh
open http://127.0.0.1:8765/
```

Stop it with:

```sh
./stop_prediction_llm.sh
```

## API

- `GET /health`
- `GET /api/members`
- `GET /api/members/{member_id}/claims`
- `GET /api/members/{member_id}/summary?use_llm=true`
- `GET /api/claims/{claim_id}/analysis?use_llm=true`
- `POST /api/ask` with `member_id` or `claim_id` and `question`

All services bind to `127.0.0.1`; no claims are sent to a hosted LLM.
