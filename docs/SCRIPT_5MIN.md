# 5-MIN
00:00-00:55 Intro
...

# 5-Minute Video Script: Asset Intelligence Copilot

## Goal
Explain solution, performance, why best, scenario coverage, how it solves complex queries. Show mermaid + UI demo.

## Runtime: ~5 minutes

---

## 1) Intro (00:00 – 00:55) – Problem
**[On-screen: Slide 1 – Title + Tech stack]**

*Say:*
"Asset Intelligence Copilot is an evidence-grounded chat assistant for oil well operations. We have ~200 wells, weekly inspections, maintenance, regulatory events, notes, manuals and SOPs. Operators ask plain-English questions – we need correct answers with citations, not guesses."

*Show:* README/overview

---

## 2) Solution Overview (00:55 – 01:40) – Architecture
**[On-screen: Slide 2 – System diagram (Mermaid)]**

*Say:*
"Frontend is React + Vite with generative UI (card/table/line/bar/map/heatmap). Backend is FastAPI + DuckDB. The orchestrator routes by intent, tools run SQL against semantic views. LLM is optional, only for rare re-parse, and cached."

*Show:* Mermaid system flow

---

## 3) Why DuckDB (01:40 – 02:25) – Core Rationale
**[On-screen: Slide 3 – Why DuckDB]**

*Say:*
"DuckDB is in-process – no setup. All arithmetic is in SQL, never by LLM. Corrections live inside semantic views (v_prod_status, v_downtime, v_spills, v_inspection_resolved, v_rule_effective), so one change propagates everywhere. Domain rules (7-day downtime per not-producing, spill class/reportability, thresholds) live as SQL logic. Result: 80/80 deterministic checks pass."

*Show:* Schema/views summary

---

## 4) Memory, Conflicts, Corrections (02:25 – 03:10)
**[On-screen: Slide 4 – Memory/Conflicts]**

*Say:*
"Two-level memory: shared well knowledge visible to all; private per-user 'wells we discussed' via session_focus. Corrections applied at correct scope – inspection affects all; well-type override only for that type. When a claim contradicts data, we surface a conflict and keep it unconfirmed. Vague corrections ask for value."

*Show:* Evidence + conflict UI example

---

## 5) How Complex Queries Are Solved (03:10 – 04:00)
**[On-screen: Slide 5 – Query flow (Mermaid)]**

*Say:*
"Complexity is handled by composition. Parse intent+window+entities. Resolve wells/area/user. Route to tool. SQL on views fuses inspections, maintenance, notes, report_facts, regulatory, docs, rules. Emit evidence/conflicts/visual. Compose grounded prose with citations. Render SVG."

*Show:* Query execution Mermaid

---

## 6) Live Demo (04:00 – 04:30)
**[On-screen: App UI]**

*Show & Say:*
- Q1: "Is WELL-0001 producing now?" → status card with reasons & INS/CTX citations
- Q3: Line chart last 13 weeks (non-producing markers)
- Q7: Spills map last 90 days (volume-scaled pins)
- Q8: 2026 heatmap by area/month
- Correction: "WELL-0164 reading wrong, re-measured 41.0°C" → propagates

---

## 7) Wrap Up (04:30 – 05:00)
**[On-screen: Slide 6 – Takeaways]**

*Say:*
"Compute, don't guess. Logic centralized in views. Evidence-grounded – cites record IDs. Honest about conflicts; scoped memory. Fast, portable, no API key needed. 80/80 pass. Bottom line: computes, cites, shows."

*Show:* Final slide