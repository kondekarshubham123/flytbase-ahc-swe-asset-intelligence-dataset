# Asset Intelligence Copilot — Write-up

_FlytBase Hack Day · Hacktoberfest 2026_

A chat copilot that answers operators' plain-English questions about 200 oil wells,
grounds every answer in the source records and documents, computes every number in SQL
(never in the model), and remembers what people tell it at the right scope.

**Repository:** `backend/` (FastAPI + DuckDB + Python), `frontend/` (React + Vite),
`eval/` (deterministic regression suite). **License:** MIT.

---

## Page 1 — System design

### The core decision: the LLM never does arithmetic

The free Gemma tier is rate-limited (~50 requests/day) and weak at multi-step tool
calling. So the copilot is a **deterministic agent with an optional thin LLM layer**.
The model is used in exactly two places, both optional and both cached: to re-parse a
question the keyword router is unsure about, and (available) to phrase an answer. It
never sees a number it could corrupt, because it never computes one. Every figure the
user reads is produced by a SQL query against DuckDB and carries the record IDs it came
from. With no API key at all the system is 100% functional — which is also what makes it
testable offline.

```
        user message
             │
   ┌─────────▼──────────┐   resolve well / area / user
   │  intent router     │   (memory-aware)
   │  keyword + regex   │◄──optional LLM re-parse (cached)
   └─────────┬──────────┘
             │  Intent{action, intent, window, asset_ids, area, visual}
   ┌─────────▼──────────┐
   │  orchestrator      │── feedback path ─► memory store (assertions)
   │  (no domain facts) │
   └─────────┬──────────┘
             │  tool call
   ┌─────────▼──────────┐   semantic views: corrections applied INSIDE the view
   │  tools → DuckDB    │   v_inspection_resolved, v_rule_effective,
   │  v_* views         │   v_prod_status, v_downtime, v_spills
   └─────────┬──────────┘
             │  ToolResult{data, evidence[], conflicts[], visual, notes[]}
   ┌─────────▼──────────┐
   │  answer composer   │──► prose + citations + generative-UI payload
   └────────────────────┘
```

### How the data is stored, indexed and retrieved

Ingest reads the dataset once into DuckDB (200 wells, ~39.6k inspections, 5.7k repairs,
1.5k report facts, 141 notes, 27 extracted rules, 42 document chunks). Free-text repair
reports are parsed into structured **report facts** (containment hours, compliance
matches) so quantities buried in prose become queryable. Manuals and SOPs are chunked
into `documents` for retrieval. Well-type rules are **extracted from the manuals at
ingest** rather than hard-coded — a regex over a fixed protocol vocabulary
(`T200.producing_delta_c`, `SR-01.notice_hours`, …) — so an unseen well type still
yields its thresholds.

The key storage idea is a set of **semantic views** that answer "what is true *now*":

- `v_inspection_resolved` — each inspection with every correction applied.
- `v_prod_status` — producing / not-producing per inspection, computed from the
  well-type temperature delta rule.
- `v_downtime` — downtime days, 7 per not-producing inspection.
- `v_spills` — only real spills, after corrections, with class and reportability.
- `v_rule_effective` — the effective rule value, with any user override layered on top.

Because corrections are applied *inside* the views, every tool — and therefore every
answer — inherits them automatically. There is no second code path that can disagree.

### The tools

The orchestrator holds no domain facts; it routes to tools that each return a
`ToolResult` with `data`, `evidence`, `conflicts` and a `visual` spec:

- **Analytics** (all SQL): `well_status`, `current_status`, `temp_series`, `downtime`,
  `downtime_detail`, `explain_downtime`, `spills`, `spills_heatmap`, `spills_map`,
  `spill_detail`, `repair_cost`, `rca_candidates`, `compliance_audit`, `containment_check`.
- **Knowledge**: `search_docs`, `get_rule`, `list_rules`.
- **Resolution**: robust well / area / user name resolution.

Combining numbers with documents and notes is explicit: a status answer stitches the
temperature reading (inspection), the well-type threshold (manual), and any human note
("shut in for planned rod-string replacement") into one reply, each with its ID.

### Two levels of memory

Everything people tell the copilot is written to an append-only **`assertions`** table
with a **scope key**: `inspection`, `well`, `well_type` or `global`. The original value
is always preserved in `original_value_text`.

- **Shared well knowledge** (scope `well`): "WELL-0007 is down for planned
  maintenance" is visible to *every* user asking about that well later.
- **Private per-user memory** (scope `user`): "the three wells we discussed" resolves
  through that user's own `session_focus`, excluding other users and the current session.
  A user with no prior conversation is told so rather than guessing.
- **Feedback / corrections**: a corrected raw reading (scope `inspection`) or a corrected
  analysis (e.g. a spill volume) is applied by the views for all users. A rule that
  applies to a whole well type is stored at scope `well_type` and read through
  `v_rule_effective`, so it changes only that type — verified by a test that a T350 rule
  leaves T200 wells untouched.

### Conflicts

Conflicts are detected by comparing a claim against the data, not by trusting either
side. A "well is running" claim against down data raises a conflict card showing both
sides and keeping the claim **unconfirmed**; a down / planned-shut-in claim is accepted
as shared knowledge (this is also how a *future* planned shut-in is recorded). A spill
correction that makes a spill reportable triggers an **SR-03 verification obligation**
(60 days, ≥4 samples, ≤1.0% TPH) as a follow-up. A vague correction with no value is
refused with a request for the corrected number, rather than blanking a field.

### Handling new data and new questions

- **New data:** one env var (`COPILOT_DATA_DIR`) re-points the whole system; a rebuild
  clears record *and* memory tables so a run on the test dataset starts clean. `build()`
  is idempotent and the schema is created from `schema.sql`.
- **New questions:** the router encodes *domain* vocabulary (spills, downtime, RCA,
  compliance, containment) rather than sample-question strings, and scales to any number
  of wells/areas. When it is unsure it can fall back to a guarded LLM re-parse, and there
  is a read-only tool layer over the `v_*` views as a hedge for unseen shapes.

### Models and cost

Gemma (free OpenRouter tier) for the two optional steps, cached in `llm_cache`. Normal
operation makes **zero** model calls, so the system is effectively free and immune to the
free-tier limit.

### What I would do next

Persist the LLM cache across rebuilds; add a small evaluation loop that scores free-text
answers with the model but keeps numbers deterministic; extend rule extraction to tables
inside the manuals; and add per-well timeline reconstruction so "what happened to
WELL-0001 in October" is one view.

---

## Page 2 — Results

### How the numbers were verified

Two deterministic suites run without any model call:

- `backend/tests/scenarios.py` — drives the copilot through all 18 example questions
  multi-turn and checks the key facts of each answer.
- `backend/tests/check_tools.py` — checks the tools directly against the expected
  answers in `03-evaluation-queries.md`.

`eval/run_eval.py` runs both: **80/80 checks pass** on the sample dataset, including the
memory and conflict scenarios (shared knowledge crosses users; private references do
not; corrections change later answers for everyone; conflicting claims are flagged).

### The 18 example questions — what worked

Every example question is answered with the expected value and citations. Highlights:

| # | Question (abridged) | Result |
| --- | --- | --- |
| Q1 | Is WELL-0001 producing now? | Not producing; latest INS-0001-0203 Δ3.2 °C; planned shut-in note; stale "active" flagged |
| Q2 | Which NE-B8 wells are down and why? | 4 wells (0164/0170/0172/0175) with per-well reasons |
| Q3 | 13-week temperature line | Line chart, non-producing points marked (8 & 15 Oct) |
| Q4 | WELL-0001 spills over 4 years | 6 valid spills, 15.1 bbl (correction applied) |
| Q5 | 2025 downtime | 21 days |
| Q6 | NE-B7 top-3 downtime | 0121 (49), 0138 (42), 0107 (42) |
| Q7 | NE-B8 spill map, 90 days | 4 pins |
| Q8 | 2026 spill heatmap | per-area/month totals (24:43, 7:37, 23:35, 8:26) |
| Q9 | WELL-0001 breaches, 12 months | SR-01, SR-02, SR-03, RC-04 |
| Q10 | SW-B24 late containment | 2 wells (0082, 0100) |

### What worked well

- **Corrections propagate correctly.** Correcting the 24 Sep WELL-0151 spill to 5.4 bbl
  immediately reclassifies it as reportable and raises the SR-01/SR-03 obligations —
  for every user afterwards.
- **Memory scoping is right.** Shared well knowledge crosses users; "the wells we
  discussed" stays private and declines when the user has no history.
- **Conflicts are honest.** A "running fine" claim against down data is shown as a
  conflict and kept unconfirmed, rather than silently overriding the record.

### What did not work / was iterated

- **Vague corrections.** Early on, "that inspection was wrong" with no corrected value
  blanked the temperature field. Fixed: the copilot now asks for the corrected value, and
  a "the well is producing" claim is routed to shared knowledge (conflict-checked)
  instead of a field edit.
- **Ambiguous volume vs temperature corrections.** A correction mentioning barrels could
  be misread as a temperature edit; now a barrel unit routes to the spill-volume analysis.
- **Two DuckDB connections in one process** saw stale snapshots during evaluation; the
  harness now uses a single connection per suite and rebuilds between them.

### Final questions

The same suites are re-run against the released test dataset with
`python eval/run_eval.py --data-dir <test-data>`, and the final questions are recorded
here (values below are produced by the tools, each with its cited records):

<!-- Final answers pasted here after the dataset and questions are released. -->

### Why this design should hold up

There is no per-question special-casing. Thresholds come from the manuals, downtime comes
from the inspection series, spills are corrected by user feedback, and every answer is a
SQL result with provenance. That is the part that transfers to unseen data and questions.
