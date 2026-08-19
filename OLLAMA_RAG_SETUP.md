# Local Ollama + Workbook RAG Setup

The Provider Financial Prediction feature calculates every financial value in
Python. Ollama only explains the canonical result, and FAISS only retrieves
supporting evidence from the configured workbook.

## 1. Install and start Ollama on macOS

Install Ollama using the official macOS installer, then verify the client:

```bash
ollama --version
```

Start the local service only when the Ollama application is not already
running it:

```bash
ollama serve
```

Verify the server:

```bash
curl http://127.0.0.1:11434/api/tags
```

## 2. Install the local models

Model names are configurable; these are the defaults:

```bash
ollama pull gemma3
ollama pull embeddinggemma
```

Verify chat:

```bash
curl http://127.0.0.1:11434/api/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma3","messages":[{"role":"user","content":"Return OK"}],"stream":false}'
```

Verify embeddings:

```bash
curl http://127.0.0.1:11434/api/embed \
  -H "Content-Type: application/json" \
  -d '{"model":"embeddinggemma","input":"payment underpayment claim evidence"}'
```

## 3. Configure the backend

Copy `.env.example` and set the absolute workbook path:

```dotenv
SAVINGS_WORKBOOK_PATH=/absolute/path/to/EDI_834_837_20_members_ENRICHED.xlsx
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_CHAT_MODEL=gemma3
OLLAMA_EMBED_MODEL=embeddinggemma
OLLAMA_TIMEOUT_SECONDS=60
OLLAMA_MAX_PREDICT_TOKENS=160
RAG_TOP_K=8
RAG_VECTOR_WEIGHT=0.55
RAG_STRUCTURED_WEIGHT=0.45
RAG_INDEX_DIR=backend/.rag_index
RAG_VERSION=workbook-rag-v1
RAG_EMBED_BATCH_SIZE=64
RAG_AUTO_BUILD=true
ENABLE_RAG_ADMIN=false
```

When `LLM_PROVIDER=ollama`, the application never falls back to Groq.
Predictions remain available if Ollama is offline; only retrieval and
explanation become unavailable.

## 4. Build the workbook vector index

From the repository root:

```bash
python -m backend.workbook_rag build
```

The index is stored under:

```text
backend/.rag_index/<workbook_hash>/
```

It contains `index.faiss`, `metadata.json`, and `manifest.json`. It is rebuilt
when the workbook hash, embedding model, embedding dimension, or RAG version
changes.

## 5. Start the application

Backend:

```bash
python -m backend.app
```

Frontend:

```bash
npm run dev
```

Health:

```bash
curl http://127.0.0.1:4000/api/ai/health
```

For local development only, set `ENABLE_RAG_ADMIN=true` to enable:

```bash
curl -X POST http://127.0.0.1:4000/api/rag/rebuild
```

Retrospective prediction quality:

```bash
curl http://127.0.0.1:4000/api/predictions/validation
```
