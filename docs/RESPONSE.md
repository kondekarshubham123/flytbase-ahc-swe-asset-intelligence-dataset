# How Responses Are Computed

Every answer is grounded in data: numbers come from SQL, evidence is emitted by tools, and the orchestrator stitches text + visual + evidence + conflicts.

## 1) Parsing & routing

- **Intent** (`backend/app/agent/intent.py`): classifies action (`ask`, `correct_raw`, `correct_analysis`, `well_type_rule`, `share_knowledge`) and question intent (e.g. `status`, `current_status`, `downtime`, `temp_series`, `spills`, `spills_map`, `spills_heatmap`, `cost`, `rca`, `compliance`, `containment`, `rule_search`, `doc_search`, `reference_followup`). Uses keyword/regex rules; can be refined by cached LLM (`_llm_refine`) when uncertain.
- **Resolution** (`backend/app/tools/resolve.py`): extracts well IDs, area ID/name, user from text (handles names like "Northeast Block 8" → `NE-08`).
- **Window parsing** (`backend/app/agent/window.py`): parses relative dates ("last 90 days", "this summer", "last 13 weeks", "last 4 months", "last 12 months"), absolute dates, and single dates.
- **Private references**: `M.resolve_reference` looks up wells mentioned in *that user's* prior sessions (excluding current session). If none, the follow-up is declined.

## 2) Feedback paths (mutating memory, reading consequences)

- **Raw correction** (`correct_raw`): user corrects a raw reading for an inspection (e.g. wellhead temp). Written as `assertion` scope `inspection`, field `wellhead_temp_c`; `v_inspection_resolved` recomputes producing/delta. Status is re-fetched via `A.well_status` and returned with a card + evidence + any conflicts.
- **Analysis correction** (`correct_analysis`): e.g. spill volume corrected. Sets `spill_volume_bbl` at inspection scope; affects spill class/reportability. Compliance is re-audited (`A.compliance_audit`) to reflect new obligations (SR-01/SR-03) and consequences are explained.
- **Well-type rule** (`well_type_rule`): sets rule override at scope `well_type`; `v_rule_effective` makes it win for that type only (verified by isolation tests).
- **Share knowledge** (`share_knowledge`): records a shared claim about a well (scope `well`). If the claim contradicts current data (e.g. "running fine" vs down), a **conflict** is raised and the claim is stored as `unconfirmed`; otherwise it's accepted as `confirmed` shared knowledge. Shared knowledge is visible to all users later.

## 3) Question paths (read-only, compute from views)

All answers come from `backend/app/tools/analytics.py` (and `knowledge.py`). Examples:
- **Status**: `A.well_status` joins `v_prod_status` + reasons (open repairs + notes/context) → returns card visual + evidence (inspection, reasons).
- **Current status (area)**: `A.current_status` filters by area from `v_prod_status` (latest per well or as-of logic) → table.
- **Temp series**: `A.temp_series` pulls last N inspections' wellhead/ambient/producing from `v_prod_status` → line visual (non-producing points marked).
- **Downtime**: `A.downtime` sums `downtime_days` (7 per not-producing inspection) over window; `explain_downtime` lists contributing inspections/repairs/notes.
- **Spills**: `A.spills` from `v_spills` (only real spills, volume corrected). `spills_map` adds lat/lon → map visual. `spills_heatmap` aggregates by area×month for a year → heatmap.
- **Repair cost**: `A.repair_cost` aggregates `maintenance.cost_total` over window per well(s).
- **RCA candidates**: `A.rca_candidates` finds wells meeting RCA conditions (downtime>14d or ≥3 corrective repairs in 12mo window, per RC-03/RCA rules).
- **Compliance audit**: `A.compliance_audit` evaluates breaches/pending per protocol (uses `v_spills`, regulatory events, report facts, thresholds like SR-01 24h/2h-major, SR-02 4h, SR-03 60d/≥4 samples/≤1.0% TPH, RC-04 30d).
- **Containment check**: `A.containment_check` finds spills whose containment hours (from `report_facts`) exceed the limit (SR-02) → table.
- **Rules/docs**: `K.search_docs`, `K.get_rule`, `K.list_rules` search `documents`/`rules` (user overrides appear via `v_rule_effective`).

## 4) Answer composition

`backend/app/agent/orchestrator.py` builds the final reply:
- Sets `tool_used` from the tool result.
- Copies `evidence[]`, `conflicts[]`, `notes[]`, `data`, `visual` (optionally overrides visual if user asked for a specific kind).
- Writes prose: cites record IDs (`INS-…`, `MNT-…`, `CTX-…`, `RULE-…`), includes reasons, flags stale recorded status, states consequences (reportable, SR obligations), surfaces conflicts clearly.
- Records focus (which wells were touched) for private reference follow-ups.
- Persists user + assistant messages.

## 5) Visuals (generative UI)

The frontend (`frontend/src/components/Visual.tsx`) renders based on `visual.type`:
- **card**: well status (producing badge, Δ vs limit, last inspection, recorded status stale, reasons/inspector note)
- **table**: generic table from `data.rows` (curated columns)
- **line**: wellhead vs ambient over time; non-producing points marked
- **bar**: downtime days per asset
- **map**: lat/lon points (pin size by volume)
- **heatmap**: spills by area × month with totals

## 6) Evidence & conflicts

- **Evidence** (`backend/app/tools/base.py`): `kind` in {record,document,note}, `id`, `label`, `detail`, optional `path`/`snippet`. UI shows a collapsible "N sources" list.
- **Conflicts**: surfaced when claim contradicts data (status claim vs inspection) or when corrected values create contradictory interpretations. The reply explicitly states the conflict and keeps claims unconfirmed when appropriate.

## Example: status answer (WELL-0001)

- Tool: `well_status` → data includes `well`, `latest` (from `v_prod_status`), `producing`, `delta_c`, `reasons`, `inspector_note`.
- Visual: `card` with `asset_id` WELL-0001.
- Evidence: `INS-0001-0203`, `CTX-0001-003` (note), and rule reference (T200 producing_delta_c) appear in text.
- Text explains: not producing; Δ 3.2 °C < 15 °C; planned shut-in reason; recorded status "active" (out of date).

## Example: spills heatmap 2026

- Tool: `spills_heatmap(2026)` aggregates from `v_spills` → `cells` (area×month counts/sums) and `area_totals`.
- Visual: `heatmap` with x=month, y=area_name, value=spills.

## Example: correction propagation

1. User corrects WELL-0151 24 Sep spill volume to 5.4 bbl (analysis correction).
2. Assertion written at inspection scope; `v_spills` now returns volume 5.4, `reportable` true, class becomes reportable/major appropriately.
3. Compliance audit recomputes breaches/pending (SR-01/SR-03) and reply states obligations + evidence.
4. Later queries for spills/compliance see the corrected values for all users.
