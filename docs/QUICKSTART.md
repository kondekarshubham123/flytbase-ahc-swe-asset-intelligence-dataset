# Quickstart

## 1) Setup

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2) Ingest sample data

```bash
python -m app.ingest.loader
# or: backend/.venv/bin/python ../eval/run_eval.py --only tools  # also builds
```

This creates `backend/data/copilot.duckdb` and prints counts. Default `COPILOT_DATA_DIR` is `sample-data/`.

## 3) Run the full app (single process)

Build frontend and serve from API:

```bash
cd frontend && npm install && npm run build
cd ../backend && . .venv/bin/activate && uvicorn app.main:app --reload --port 8077
```

Open http://127.0.0.1:8077/. The API is under `/api/*`.

## 4) Dev mode (separate frontend)

API on 8077:
```bash
cd backend && . .venv/bin/activate && uvicorn app.main:app --reload --port 8077
```

Frontend on 5173 (proxies /api):
```bash
cd frontend && npm run dev
```

## 5) Test with sample questions

Use the UI or curl:

```bash
curl -s -X POST http://127.0.0.1:8077/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"U-201","session_id":"demo1","message":"Is WELL-0001 producing oil right now? Show me its status card."}' \
  | python3 -m json.tool | head -80
```

## 6) Deterministic evaluation

```bash
cd /path/to/repo
backend/.venv/bin/python eval/run_eval.py            # all 80/80
backend/.venv/bin/python eval/run_eval.py --only scenarios
backend/.venv/bin/python eval/run_eval.py --only tools
```

Re-point to test data (final 2 hours):

```bash
backend/.venv/bin/python eval/run_eval.py --data-dir /absolute/path/to/test-data --today 2026-10-16
```

## 7) LLM (optional)

Copy `.env.example` to `.env` and either set `OPENROUTER_API_KEY`, or set
`LLM_PROVIDER=ollama` to use a local Ollama model (e.g. `gemma3:4b`, no key needed).
Without either, the system is fully deterministic. Model calls are cached in `llm_cache`.

## Tips

- Use `python -m app.ingest.loader` to rebuild DB from current `COPILOT_DATA_DIR`.
- `eval/run_eval.py` rebuilds automatically between suites to avoid cross-contamination.
- "The wells we discussed" is private to the user (uses session focus).
- Conflicts (claim vs data) are surfaced with both sides.
- Corrections are applied inside views — they affect all later answers.
