# Asset Intelligence Copilot

An Asset Intelligence Copilot for an oil field: ask a plain-English question about any
well and get a grounded answer with the records and documents it used, shown inline in
a generative chat UI (status card, table, line chart, bar chart, map, heatmap).

Built for the **FlytBase Hack Day (Hacktoberfest 2026)** challenge. See
[`WRITEUP.md`](WRITEUP.md) for the 2-page system design and results.

> **Design in one line:** a deterministic agent computes every number in DuckDB and
> cites its sources; the LLM layer is optional, cached, and never does arithmetic.

## Highlights

- **Grounded, cited answers.** Every reply carries the inspection / record / document
  IDs behind it, and every figure comes from SQL against DuckDB.
- **Two levels of memory.** Shared knowledge about a well is visible to everyone;
  "the wells we discussed" is private to that user. Corrections and well-type rules are
  stored at the right scope and applied *inside* the semantic views.
- **Honest about conflicts.** Contradictions between a claim and the data (or between
  users) are surfaced, not silently resolved.
- **Works with no model calls.** With no API key the copilot is fully functional, which
  keeps it alive on a rate-limited free tier and makes the whole thing testable offline.
- **Re-points to new data with one env var**, so it holds up on the released test set.

## Architecture

```
React + Vite UI  ──HTTP──►  FastAPI  ──►  orchestrator  ──►  tools  ──►  DuckDB (v_* views)
   (generative UI)                         (intent router)                 ▲
                                                │                          │ corrections
                                        memory store (assertions) ─────────┘
                                                ▲ optional, cached
                                          OpenRouter / Gemma
```

- `backend/app/ingest/` — ingests records, extracts well-type rules from the manuals,
  parses free-text repair reports into structured facts.
- `backend/app/db/schema.sql` — tables and the semantic views that apply corrections.
- `backend/app/tools/` — all analytics/knowledge SQL tools.
- `backend/app/memory/store.py` — shared + private memory, corrections, conflicts.
- `backend/app/agent/` — intent router, window/date parsing, optional LLM, orchestrator.
- `frontend/` — the chat UI and inline visual components (pure SVG, no chart deps).
- `eval/` — the deterministic evaluation harness.

## Run it

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# build the database from sample-data and run the API
python -m app.ingest.loader          # builds backend/data/copilot.duckdb
uvicorn app.main:app --reload --port 8077
```

No API key is required. To enable the optional LLM layer, copy `backend/.env.example` to
`backend/.env` and set `OPENROUTER_API_KEY`.

### Frontend

```bash
cd frontend
npm install
npm run dev            # dev server on :5173, proxies /api to :8077
```

Or build the UI and let the API serve it from one process:

```bash
cd frontend && npm run build
# then open http://127.0.0.1:8077/ — the FastAPI app serves frontend/dist
```

### Evaluate

```bash
python eval/run_eval.py                                   # 80/80 on sample-data
python eval/run_eval.py --data-dir /path/to/test-data     # re-point to new data
python eval/run_eval.py --only scenarios                  # just the 18 example questions
```

## Data and configuration

All configuration lives in `backend/app/config.py` and is overridable by env vars:

| Variable | Purpose |
| :--- | :--- |
| `COPILOT_DATA_DIR` | Dataset root (default `sample-data/`). Point this at the test dataset. |
| `COPILOT_DB_PATH` | Where the DuckDB file is built. |
| `COPILOT_TODAY` | The dataset's "today" (default `2026-10-16`). |
| `OPENROUTER_API_KEY` | Enables the optional LLM layer. Unset = fully deterministic. |
| `OPENROUTER_MODEL` | Defaults to a free Gemma model. |

## Challenge documents

| Document | What it is |
| :--- | :--- |
| [`01-problem-statement.md`](01-problem-statement.md) | The challenge, what to build, what to submit and how we judge |
| [`02-dataset-guide.md`](02-dataset-guide.md) | Every file and field in the sample dataset |
| [`03-evaluation-queries.md`](03-evaluation-queries.md) | Example questions with expected answers |
| [`04-prerequisites.md`](04-prerequisites.md) | What to set up before the event |

## Sample data

`sample-data/` holds a fictional oil field: 200 wells in 4 areas, with 4 years of history.
All data is synthetic and created for this challenge; manuals and procedures are
simplified, fictional documents, not real operating or legal guidance.

## License

[MIT](LICENSE)
