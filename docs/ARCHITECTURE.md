# Architecture

## High-level system flow

```
User (React + Vite) ─HTTP─► FastAPI (backend/app/main.py)
                        │
                        ├─► Orchestrator (route + compose answer)
                        │    ├─ Intent router (deterministic + optional cached LLM refine)
                        │    ├─ Tools (SQL via DuckDB)
                        │    └─ Memory store (assertions + sessions/focus + cache)
                        │
                        ├─► DuckDB (schema + semantic v_* views)
                        └─► Serves built frontend/dist (single-process)
```

## Key components

- **Data model & semantic views** (`backend/app/db/schema.sql`): raw tables (users, areas, wells, inspections, maintenance, regulatory_events, notes, reports, report_facts, documents, rules, assertions) plus views where corrections are applied. `v_inspection_resolved`, `v_prod_status`, `v_downtime`, `v_spills`, `v_rule_effective` ensure "what's true now" is computed consistently.
- **Ingest** (`backend/app/ingest/loader.py`, `rules.py`, `reports.py`): loads fleet + per-well data, extracts well-type rules from manuals/SOPs into `rules`, chunks manuals into `documents`, parses repair report bodies into `report_facts`, and seeds `assertions` from correction notes. `build()` clears record + memory tables; it's idempotent.
- **Tools** (`backend/app/tools/*.py`): analytics (well_status, current_status, temp_series, downtime, spills, spills_map, spills_heatmap, spill_detail, repair_cost, rca_candidates, compliance_audit, containment_check, explain_downtime), knowledge (search_docs, get_rule, list_rules), resolve (wells/areas/users). Every tool returns `ToolResult` with `data`, `evidence[]`, `conflicts[]`, `visual{type,spec}`, `notes[]`.
- **Memory** (`backend/app/memory/store.py`): append-only `assertions` with scope (`inspection|well|well_type|global`) and scope_key; `sessions/messages/session_focus` for private per-user history; `llm_cache` for optional model calls. Corrections and well-type overrides are applied at the right scope.
- **Agent** (`backend/app/agent/*.py`): intent classification (keyword/regex) with optional cached LLM re-parse (`llm.py`), window/date parsing (`window.py`), orchestrator (`orchestrator.py`) that routes to tools and composes prose + citations + generative-UI payload.
- **API + UI** (`backend/app/main.py`, `frontend/src/*`): FastAPI exposes `/api/*`; serves `frontend/dist` when present for single-process. React/Vite chat renders card/table/line/bar/map/heatmap, evidence list, conflict cards.

## Data flow (request → answer)

1. User sends message → API stores user message in session.
2. Resolve wells/areas from text (names/IDs).
3. Classify intent (`ask` or feedback/correction/share/override). If uncertain and LLM enabled, refine (cached).
4. If feedback: write assertion(s) and answer with consequence (status re-evaluated, compliance recomputed, etc.).
5. If question: handle reference follow-up privately (uses that user's focus), then dispatch to the matching handler/tool.
6. Tools query DuckDB (via v_* views) → get `ToolResult`.
7. Orchestrator composes text + `visual` + `evidence` + `conflicts` + `focus`; saves assistant message; returns to UI.
8. UI renders inline visual (SVG) and citations.

## Key design choices

- **Determinism first**: all numbers from SQL; LLM only phrasing/re-parse when enabled, and everything cached. System runs fully without API key.
- **Semantic views**: corrections applied inside views → one source of truth.
- **Scope-based assertions**: prevents cross-bleed (T350 rule doesn't affect T200).
- **Evidence by construction**: tools emit evidence; citations are never hallucinated.
- **Graceful degradation**: LLM errors don't crash; vague corrections ask for a value.
- **Single-process deploy**: API serves built frontend for easy demos.

## File tree (relevant)

- `backend/app/config.py` — env, data dir, today, LLM config
- `backend/app/db/schema.sql` — tables + views
- `backend/app/ingest/loader.py` — orchestrates ingest + build/summary
- `backend/app/ingest/rules.py` — document chunking + rule extraction from manuals/SOPs
- `backend/app/ingest/reports.py` — parse repair report bodies into report_facts
- `backend/app/tools/analytics.py` — analytical SQL tools
- `backend/app/tools/knowledge.py` — document/rule retrieval
- `backend/app/tools/resolve.py` — entity resolution
- `backend/app/memory/store.py` — assertions + sessions + focus + cache
- `backend/app/agent/intent.py` — intent classification
- `backend/app/agent/window.py` — window/date parsing
- `backend/app/agent/llm.py` — cached OpenRouter client
- `backend/app/agent/orchestrator.py` — routing + composition
- `backend/app/main.py` — FastAPI app + API routes + static serving
- `frontend/src/*` — React/Vite UI + visuals
- `eval/run_eval.py` — deterministic harness
- `docs/*` — this documentation
