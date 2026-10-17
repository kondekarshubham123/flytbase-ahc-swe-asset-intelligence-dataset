# Ingestion

Ingest reads the dataset from `COPILOT_DATA_DIR` (default `sample-data/`) into DuckDB and produces a consistent, queryable state.

## Quick build

```bash
cd backend
. .venv/bin/activate
python -m app.ingest.loader        # builds backend/data/copilot.duckdb (or COPILOT_DB_PATH)
```

Or from repo root:

```bash
backend/.venv/bin/python eval/run_eval.py --data-dir sample-data
```

## Where data comes from

- `fleet/` — `users.json`, `areas.json`, `teams` implied; `well-types/*.md`, `sops/*.md` (manuals/SOPs)
- `assets/WELL-*/` — `metadata.json`, `inspections.jsonl`, `maintenance.jsonl`, `regulatory-events.jsonl`, `context.jsonl`, `maintenance-reports/*.txt`

## Loader steps

1. **Connect & ensure schema** (`loader.connect`, `loader.ensure_schema`): create tables/views from `backend/app/db/schema.sql`.
2. **Clear** (`loader._clear`): delete from record tables and from memory tables (sessions/messages/focus/cache) — rebuild is a clean slate.
3. **Load fleet** (`loader.load_fleet`): insert `users`, `teams` (from areas.json), `areas`.
4. **Load each well** (`loader.load_well`): insert `wells`, `equipment`, `inspections`, `maintenance`, also write `reports` entries for maintenance that reference a report file (reads the text body). Insert `regulatory_events`, `notes` (context.jsonl).
5. **Seed corrections** (`loader.seed_correction_assertions`): convert `correction` notes attached to inspections into `assertions` (scope `inspection`, field `spill_is_real` set to `false`, preserving original volume/detection in `original_value_text`). These become effective in `v_inspection_resolved`.
6. **Load documents & rules** (`ingest.rules.load_documents`, `ingest.rules.build_rules`): chunk well-type manuals and SOPs into `documents`; extract well-type numeric rules (regex over a fixed protocol vocabulary) into `rules`.
7. **Parse report facts** (`ingest.reports.parse_all`): scan all report bodies and extract structured facts (containment hours, SR/SOP markers, RCA flags, samples, TPH, reported USD) into `report_facts`.
8. **Summarize**: return counts (wells, inspections, maintenance, regulatory_events, notes, reports, report_facts, rules, documents, assertions, valid_spills).

## Notes about scoping & overrides

- Well-type user overrides go into `assertions` (scope `well_type`, field = rule_key) and win in `v_rule_effective` (user override prio > base).
- Inspection corrections win in `v_inspection_resolved` (latest confirmed by `created_at` per (scope_key,field)).
- Seeded correction notes are treated as confirmed inspection overrides.
- Memory (sessions/messages/focus/llm_cache) is cleared on rebuild — so each rebuild starts from a fresh runtime state. The "today" date is fixed (default `2026-10-16`) and can be overridden via `COPILOT_TODAY`.

## Re-pointing to test data

Set `COPILOT_DATA_DIR` to the new dataset root. The eval harness uses a separate DB per dataset name unless you set `COPILOT_DB_PATH` explicitly:

```bash
backend/.venv/bin/python eval/run_eval.py --data-dir /path/to/test-data --today 2026-10-16
```

## Environment variables

| Var | Purpose |
|---|---|
| `COPILOT_DATA_DIR` | Dataset root (default `sample-data/`) |
| `COPILOT_DB_PATH` | DuckDB path (default `backend/data/copilot.duckdb`) |
| `COPILOT_TODAY` | ISO datetime for "today" (default `2026-10-16T00:00:00+00:00`) |
| `LLM_PROVIDER` | `openrouter` (default) or `ollama` |
| `OPENROUTER_API_KEY` | Optional; enables LLM layer on OpenRouter (otherwise deterministic) |
| `OPENROUTER_MODEL` | Model slug (default `google/gemma-4-31b-it:free`) |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | Local Ollama endpoint (default `http://localhost:11434/v1`) and model (default `gemma3:4b`) |
| `COPILOT_LLM_TIMEOUT` | Timeout seconds (default `40`) |
| `COPILOT_CACHE_DIR` | Cache dir (default `backend/data/cache`) |
