"""Route a parsed Intent to the right tools and compose a grounded answer.

The orchestrator holds no domain facts of its own: it calls deterministic tools,
then turns their structured output into (a) prose with citations, (b) generative-UI
payloads, (c) conflicts. This is where "combine numbers with documents and notes"
happens.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import duckdb

from .. import config
from ..ingest.loader import new_id
from ..memory import store as M
from ..tools import analytics as A
from ..tools import knowledge as K
from ..tools.base import ToolResult, fmt_num, iso
from ..tools.resolve import resolve_area, resolve_area_name, resolve_wells
from . import intent as I
from . import llm
from . import window as W


@dataclass
class Reply:
    text: str
    visual: Optional[dict] = None
    evidence: List[dict] = field(default_factory=list)
    conflicts: List[dict] = field(default_factory=list)
    intent: dict = field(default_factory=dict)
    tool_used: str = ""
    notes: List[str] = field(default_factory=list)
    focus: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "text": self.text, "visual": self.visual, "evidence": self.evidence,
            "conflicts": self.conflicts, "intent": self.intent, "tool": self.tool_used,
            "notes": self.notes, "focus": self.focus, "data": self.data,
        }


def _money(x: Any) -> str:
    try:
        return f"USD {int(round(float(x))):,}"
    except (TypeError, ValueError):
        return "USD 0"


def _name(con: duckdb.DuckDBPyConnection, user_id: Optional[str]) -> str:
    if not user_id:
        return "unknown"
    row = con.execute("SELECT name FROM users WHERE user_id=?", [user_id]).fetchone()
    return row[0] if row and row[0] else user_id


def _receipt(res: ToolResult, reply: Reply, visual_override: Optional[str]) -> None:
    reply.evidence = [e.as_dict() for e in res.evidence]
    reply.conflicts = res.conflicts
    reply.notes = res.notes
    reply.visual = res.visual
    reply.data = res.data
    if visual_override:
        reply.visual = {"type": visual_override, "spec": (res.visual or {}).get("spec", {})}
    reply.tool_used = res.tool


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
_LLM_INTENTS = (
    "status", "current_status", "downtime", "temp_series", "spills", "spills_map",
    "spills_heatmap", "cost", "rca", "compliance", "containment", "rule_search",
    "doc_search",
)


def _llm_refine(con, message: str, it: "I.Intent", as_of: date) -> "I.Intent":
    """Ask the model to pick an intent when the deterministic router is unsure.

    The model only chooses from a fixed vocabulary; it never supplies numbers.
    Any failure leaves the deterministic intent untouched.
    """
    system = (
        "You route questions about an oil field to one query. Reply with JSON only: "
        '{"intent": "<one of: ' + ", ".join(_LLM_INTENTS) + '>", '
        '"visual": "<card|table|line|bar|map|heatmap|null>"}. '
        "Choose 'current_status' for 'which wells are not producing / are down', "
        "'status' for a single well's up/down state, 'downtime' for days offline, "
        "'temp_series' for temperature over time, 'spills' for spill counts/volumes, "
        "'spills_map' for a map of spills, 'spills_heatmap' for spills by area/month, "
        "'cost' for repair cost, 'rca' for root-cause analysis needs, "
        "'compliance' for broken rules, 'containment' for containment time, "
        "'rule_search'/'doc_search' for procedures and thresholds."
    )
    text = llm.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": message}],
        con=con, temperature=0.0, json_mode=True,
    )
    if not text:
        return it
    try:
        import json as _json
        data = _json.loads(text)
    except Exception:
        return it
    intent = str(data.get("intent") or "").strip()
    if intent in _LLM_INTENTS:
        it.intent = intent
        it.confidence = 0.6
        it.source = "llm"
    visual = data.get("visual")
    if not it.visual and visual in ("card", "table", "line", "bar", "map", "heatmap"):
        it.visual = visual
    return it


def handle(con: duckdb.DuckDBPyConnection, user_id: str, session_id: str, message: str,
           as_of: Optional[date] = None) -> Reply:
    as_of = as_of or config.TODAY.date()
    M.ensure_session(con, session_id, user_id)
    M.add_message(con, session_id, user_id, "user", message)

    asset_ids = resolve_wells(con, message)
    area = resolve_area(con, message)

    # Optionally let the LLM refine only when the deterministic router is unsure.
    it = I.classify(message, today=as_of, asset_ids=asset_ids, area=area)
    if llm.available() and it.intent in ("unknown",) and it.action == "ask":
        it = _llm_refine(con, message, it, as_of)

    if it.action != "ask":
        reply = _handle_feedback(con, user_id, session_id, message, it, asset_ids, as_of)
    else:
        reply = _handle_question(con, user_id, session_id, message, it, asset_ids, area, as_of)

    reply.intent = it.as_dict()
    M.add_message(con, session_id, user_id, "assistant", reply.text,
                  meta={"tool": reply.tool_used})
    return reply


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------
def _handle_question(con, user_id, session_id, message, it: I.Intent,
                     asset_ids, area, as_of) -> Reply:
    # Resolve "the wells we discussed" strictly per user.
    if it.intent == "reference_followup":
        ref = M.resolve_reference(con, user_id, session_id)
        if not ref["found"]:
            return Reply(
                text=("I don't have an earlier conversation with you about any wells, so I "
                      "can't tell which wells you mean. Name a well or a question first."),
                tool_used="memory", intent=it.as_dict(),
            )
        asset_ids = ref["asset_ids"]
        # The follow-up is usually a cost/status question.
        if any(w in message.lower() for w in ("cost", "spend", "spent", "paid")):
            it.intent = "cost"
        else:
            it.intent = "status"

    handler = {
        "status": _q_status,
        "current_status": _q_current_status,
        "downtime": _q_downtime,
        "temp_series": _q_temp_series,
        "spills": _q_spills,
        "spills_map": _q_spills_map,
        "spills_heatmap": _q_spills_heatmap,
        "cost": _q_cost,
        "rca": _q_rca,
        "compliance": _q_compliance,
        "containment": _q_containment,
        "rule_search": _q_rule,
        "doc_search": _q_docs,
    }.get(it.intent, _q_unknown)

    reply = handler(con, user_id, session_id, message, it, asset_ids, area, as_of)

    # Remember which wells this user touched (for later "the wells we discussed").
    focus = reply.focus or asset_ids
    if focus:
        M.note_focus(con, session_id, user_id, focus)
    return reply


def _q_status(con, user_id, session_id, message, it, asset_ids, area, as_of):
    if not asset_ids:
        return _q_unknown(con, user_id, session_id, message, it, asset_ids, area, as_of)
    aid = asset_ids[0]
    res = A.well_status(con, aid, as_of=as_of)
    reply = Reply(text="", intent=it.as_dict())
    _receipt(res, reply, it.visual)

    d = res.data
    latest = d["latest"]
    if latest is None:
        reply.text = f"I have no inspection on or before {as_of} for {aid}."
        return reply

    delta = d["delta_c"]
    threshold = latest["threshold_c"]
    wtype = d["well"]["well_type"]
    insp = latest["inspection_id"]
    insdate = iso(latest["inspected_at"])
    who = _name(con, latest["inspector_user_id"])
    method = latest["method"]

    if d["producing"] is False:
        head = (f"No, **{aid}** is not producing oil. Latest inspection **{insp}** on {insdate} "
                f"({method}, by {who}) measured the wellhead {delta} °C warmer than the air — "
                f"below the {wtype} limit of {fmt_num(threshold)} °C.")
    elif d["producing"] is True:
        head = (f"Yes, **{aid}** is producing oil. Latest inspection **{insp}** on {insdate} "
                f"({method}, by {who}) measured the wellhead {delta} °C warmer than the air — "
                f"at or above the {wtype} limit of {fmt_num(threshold)} °C.")
    else:
        head = f"I found inspection {insp} for {aid} but could not compute a status."

    parts = [head]
    # If the reading was corrected, say who corrected it and mention the original.
    if latest.get("wh_corrected_by"):
        parts.append(f"This is based on a corrected reading (corrected by "
                     f"{_name(con, latest['wh_corrected_by'])}); the original value is kept on record.")
    if d["producing"] is False:
        if d["reasons"]:
            reason_bits = []
            for r in d["reasons"][:2]:
                reason_bits.append(f"{r['text']} ({r['id']})")
            parts.append("Reason: " + " ".join(reason_bits) + ".")
        else:
            note = d.get("inspector_note")
            extra = f" The inspector noted \"{note}\"." if note else ""
            parts.append("**No recorded reason** explains the stop." + extra)
        if d.get("recorded_status_stale"):
            parts.append(f"The recorded status still says \"{d['recorded_status']}\", which is out of date.")

    if reply.conflicts:
        for c in reply.conflicts:
            parts.append(f"⚠️ {c['message']} {c.get('ask','')}")

    reply.text = " ".join(parts)
    return reply


def _q_current_status(con, user_id, session_id, message, it, asset_ids, area, as_of):
    res = A.current_status(con, area_id=area, asset_ids=asset_ids or None, as_of=as_of)
    reply = Reply(text="", intent=it.as_dict())
    _receipt(res, reply, it.visual)
    rows = res.data["rows"]
    down = [r for r in rows if not r["producing"]]
    reply.focus = [r["asset_id"] for r in down] or [r["asset_id"] for r in rows]
    scope = resolve_area_name(con, area) if area else "the selected wells"

    if it.visual is None:
        reply.visual = {"type": "table", "spec": {"rows": down}}

    if not down:
        reply.text = f"All wells I checked in {scope} are producing oil right now (latest inspection for each)."
        return reply

    parts = [f"**{len(down)} well(s)** in {scope} are not producing oil right now:"]
    for r in down:
        reason = "; ".join(f"{x['text']} ({x['id']})" for x in r["reasons"]) or None
        if reason:
            rr = f"Reason: {reason}."
        else:
            note = r.get("inspector_note")
            rr = "**No recorded reason**" + (f" (inspector noted \"{note}\")." if note else ".")
        stale = " — recorded status still \"active\" (out of date)" if (
            r["recorded_status"] == "active") else ""
        parts.append(f"- **{r['asset_id']}** ({r['well_type']}): Δ {r['delta_c']} °C{stale}. {rr}")
    parts.append("Where nothing explains the stop, the possible cause should be checked on site.")
    reply.text = "\n".join(parts)
    return reply


def _q_downtime(con, user_id, session_id, message, it, asset_ids, area, as_of):
    w = it.window
    res = A.downtime(con, asset_ids=asset_ids or None, area_id=area,
                     start=w.get("start"), end=w.get("end"), top=it.top)
    reply = Reply(text="", intent=it.as_dict())
    _receipt(res, reply, it.visual)
    rows = res.data["rows"]
    label = w.get("label") or "the period"
    reply.focus = [r["asset_id"] for r in rows]

    # "Is any downtime planned?" — planned downtime lives in shared knowledge, not
    # in the inspections that have already happened.
    if "planned" in message.lower() and asset_ids:
        planned = [k for k in M.shared_knowledge(con, asset_ids[0], as_of)
                   if k["status"] == "confirmed" and any(
                       x in (k["text"] or "").lower() for x in ("shut in", "shut-in", "planned", "workover"))]
        if planned:
            k = planned[0]
            reply.text = (f"Yes — {k['text']} This is shared knowledge recorded by "
                          f"{_name(con, k['author'])} ({k['assertion_id']}).")
            reply.evidence = [{"kind": "note", "id": k["assertion_id"],
                               "label": f"Shared note from {_name(con, k['author'])}",
                               "detail": k["text"]}]
            reply.tool_used = "memory:shared"
            return reply

    if len(rows) == 1 and rows[0]["asset_id"] in (asset_ids or []):
        r = rows[0]
        parts = [f"**{r['asset_id']}** was down **{fmt_num(r['downtime_days'])} days** in {label}, "
                 f"from {r['down_inspections']} weekly inspection(s) that found it not producing "
                 f"(each counts as 7 days)."]
        detail = A.explain_downtime(con, r["asset_id"], w.get("start"), w.get("end"))
        for d in detail:
            cause = d.get("cause") or {}
            cause_txt = (f" Cause: {cause.get('issue')} ({cause.get('maintenance_id')})."
                         if cause else "")
            parts.append(f"- **{iso(d['inspected_at'])}:** wellhead {d['wellhead_temp_c']} °C vs air "
                         f"{d['ambient_temp_c']} °C (Δ {d['delta_c']} °C, below the {fmt_num(d['threshold_c'])} °C limit)."
                         + cause_txt)
        reply.text = "\n".join(parts)
        # Downtime over time → line chart.
        reply.visual = {"type": "table", "spec": {"rows": detail}} if it.visual is None else reply.visual
        return reply

    if not rows:
        reply.text = f"No downtime was recorded in {label} for the wells I checked."
        return reply

    parts = [f"Downtime in {label} (from weekly inspections; each not-producing inspection = 7 days):"]
    for r in rows:
        parts.append(f"- **{r['asset_id']}** ({r['area_name']}): **{fmt_num(r['downtime_days'])} days**")
    reply.text = "\n".join(parts)
    return reply


def _q_temp_series(con, user_id, session_id, message, it, asset_ids, area, as_of):
    if not asset_ids:
        return _q_unknown(con, user_id, session_id, message, it, asset_ids, area, as_of)
    aid = asset_ids[0]
    w = it.window
    res = A.temp_series(con, aid, start=w.get("start"), end=w.get("end"))
    reply = Reply(text="", intent=it.as_dict())
    _receipt(res, reply, it.visual)
    rows = res.data["rows"]
    if not rows:
        reply.text = f"No inspections found for {aid} in that window."
        return reply
    nonprod = [iso(r["inspected_at"]) for r in rows if not r["producing"]]
    span = f"{iso(rows[0]['inspected_at'])} to {iso(rows[-1]['inspected_at'])}"
    txt = (f"**{aid}** wellhead temperature against air temperature: {len(rows)} readings ({span}).")
    if nonprod:
        txt += f" Not producing oil on: {', '.join(nonprod)}."
    else:
        txt += " The well was producing at every reading."
    reply.text = txt
    if it.visual is None:
        reply.visual = res.visual
    return reply


def _q_spills(con, user_id, session_id, message, it, asset_ids, area, as_of):
    t = message.lower()
    reportable_only = any(p in t for p in ("needed a report", "report to the regulator",
                                           "reportable", "written notice"))
    # Single-spill investigation: "was the <date> spill at WELL-xxxx real oil?"
    if asset_ids and it.window.get("single") and any(p in t for p in ("real", "actually", "was the")):
        return _q_spill_detail(con, message, it, asset_ids, as_of)

    w = it.window
    res = A.spills(con, asset_ids=asset_ids or None, area_id=area,
                   start=w.get("start"), end=w.get("end"), reportable_only=reportable_only)
    reply = Reply(text="", intent=it.as_dict())
    _receipt(res, reply, it.visual)
    rows = res.data["rows"]
    scope = (" " + resolve_area_name(con, area)) if area else ""
    reply.focus = [r["asset_id"] for r in rows]

    if not rows:
        reply.text = f"I found no" + (" reportable" if reportable_only else "") + f" spills in{scope}."
        return reply

    if asset_ids and len(asset_ids) == 1:
        r0 = asset_ids[0]
        raw = res.data["raw_spill_detections"]
        corrected = raw - res.data["count"]
        txt = (f"**{r0}** had **{res.data['count']} spill(s)**, totalling **{res.data['total_bbl']} barrels**"
               f" in {w.get('label') or 'the period'}.")
        if corrected:
            txt += (f" There were {raw} raw detections; {corrected} was corrected as not a spill and does not count.")
        reply.text = txt
    else:
        txt = (f"**{res.data['count']}** " + ("reportable " if reportable_only else "") +
               f"spill(s) in{scope} in {w.get('label') or 'the period'}, "
               f"totalling **{res.data['total_bbl']} barrels**.")
        if reportable_only:
            txt += " The reporting threshold is 5 bbl (SR-01)."
            missing = []
            for r in rows:
                filed = con.execute(
                    "SELECT 1 FROM regulatory_events WHERE asset_id=? AND "
                    "related_inspection_id=? AND event_type='spill_report_filed'",
                    [r["asset_id"], r["inspection_id"]],
                ).fetchone()
                if not filed:
                    missing.append(f"{r['asset_id']} ({r['volume_bbl']} bbl, {iso(r['inspected_at'])})")
            if missing:
                txt += (" No written notice is on record for " + "; ".join(missing)
                        + " — flagged as overdue (possible SR-01 breach).")
        reply.text = txt
    return reply


def _q_spill_detail(con, message, it, asset_ids, as_of):
    aid = asset_ids[0]
    res = A.spill_detail(con, aid, inspected_on=it.window.get("single"))
    reply = Reply(text="", intent=it.as_dict())
    _receipt(res, reply, None)
    if "spill" not in res.data:
        reply.text = f"I found no spill detection for {aid} on that date."
        return reply
    s = res.data["spill"]
    if res.data["corrected"]:
        reply.text = (f"**No** — the detection {s['inspection_id']} ({iso(s['inspected_at'])}) was "
                      f"corrected: it is not a crude spill.")
        return reply
    notes = res.data["notes"]
    if notes:
        reply.text = (f"**Yes** — {aid} had a {s['spill_est_volume_bbl']} bbl spill detected at "
                      f"{s['inspection_id']} on {iso(s['inspected_at'])}. Notes disagree, so both are shown: "
                      + " ".join(f"({n['note_id']}) {n['text']}" for n in notes)
                      + " Neither note is a formal correction, so the detection stands.")
    else:
        reply.text = (f"**Yes** — {aid} had a {s['spill_est_volume_bbl']} bbl spill detected at "
                      f"{s['inspection_id']} on {iso(s['inspected_at'])}.")
    return reply


def _q_spills_map(con, user_id, session_id, message, it, asset_ids, area, as_of):
    w = it.window
    res = A.spills_map(con, area_id=area, asset_ids=asset_ids or None,
                       start=w.get("start"), end=w.get("end"))
    reply = Reply(text="", intent=it.as_dict())
    _receipt(res, reply, "map")
    rows = res.data["rows"]
    reply.focus = [r["asset_id"] for r in rows]
    scope = (" " + resolve_area_name(con, area)) if area else ""
    if not rows:
        reply.text = f"No real spills found in{scope} in {w.get('label') or 'the period'}."
        return reply
    wells = ", ".join(sorted({r["asset_id"] for r in rows}))
    reply.text = (f"**{len(rows)}** spill(s) across {len({r['asset_id'] for r in rows})} well(s) in"
                  f"{scope} in {w.get('label') or 'the period'}: {wells}.")
    return reply


def _q_spills_heatmap(con, user_id, session_id, message, it, asset_ids, area, as_of):
    year = (it.window.get("start") or as_of).year
    res = A.spills_heatmap(con, year)
    reply = Reply(text="", intent=it.as_dict())
    _receipt(res, reply, "heatmap")
    totals = {r["area_name"]: r["total"] for r in res.data["area_totals"]}
    if totals:
        top = max(totals, key=totals.get)
        reply.text = (f"Spills in {year} by area and month. The most affected area is **{top}** "
                      f"with {totals[top]} spills.")
    else:
        reply.text = f"No spills recorded in {year}."
    return reply


def _q_cost(con, user_id, session_id, message, it, asset_ids, area, as_of):
    if not asset_ids:
        return _q_unknown(con, user_id, session_id, message, it, asset_ids, area, as_of)
    w = it.window
    res = A.repair_cost(con, asset_ids, start=w.get("start"), end=w.get("end"))
    reply = Reply(text="", intent=it.as_dict())
    _receipt(res, reply, it.visual)
    rows = res.data["rows"]
    label = w.get("label") or "the period"
    reply.focus = [r["asset_id"] for r in rows]
    if not rows:
        reply.text = f"No repair costs recorded for {', '.join(asset_ids)} in {label}."
        return reply
    bits = " · ".join(f"{r['asset_id']}: {_money(r['cost_usd'])}" for r in rows)
    reply.text = f"Repair costs in {label} — {bits}."
    return reply


def _q_rca(con, user_id, session_id, message, it, asset_ids, area, as_of):
    equip = it.equipment or "pump_jack"
    res = A.rca_candidates(con, equipment_kind=equip, area_id=area,
                           asset_ids=asset_ids or None, months=it.months or 12, as_of=as_of)
    reply = Reply(text="", intent=it.as_dict())
    _receipt(res, reply, it.visual)
    flagged = res.data["flagged"]
    reply.focus = [r["asset_id"] for r in flagged]
    if not flagged:
        reply.text = f"No wells currently meet the root-cause threshold for {equip.replace('_', ' ')}."
        return reply
    names = ", ".join(f"{r['asset_id']} ({r['repairs']} repairs)" for r in flagged)
    user_rule = any(r["threshold_source"] == "user rule" for r in flagged)
    if user_rule:
        reply.text = (f"Following the user's rule for those well types, **{names}** need a root-cause "
                      f"analysis on their {equip.replace('_', ' ')}.")
    else:
        reply.text = (f"**{names}** need a root-cause analysis on their {equip.replace('_', ' ')} "
                      f"(3 or more corrective repairs in the last 12 months, per the well-type manual).")
    return reply


def _q_compliance(con, user_id, session_id, message, it, asset_ids, area, as_of):
    if not asset_ids:
        return _q_unknown(con, user_id, session_id, message, it, asset_ids, area, as_of)
    aid = asset_ids[0]
    res = A.compliance_audit(con, aid, months=it.months or 12, as_of=as_of)
    reply = Reply(text="", intent=it.as_dict())
    _receipt(res, reply, it.visual)
    b = res.data["breaches"]
    if not b:
        reply.text = f"I found no rule breaches for {aid} in the last {it.months or 12} months."
        return reply
    fines = sum(x.get("fine_usd") or 0 for x in b)
    parts = [f"**{aid}** has **{len(b)}** breach(es) in the last {it.months or 12} months:"]
    for x in b:
        parts.append(f"- **{x['protocol']}:** {x['detail']}")
    if fines:
        parts.append(f"Fines involved: {_money(fines)}.")
    if res.data["pending"]:
        parts.append("Still pending (deadlines not yet passed or work under way):")
        for p in res.data["pending"][:4]:
            parts.append(f"- {p['protocol']}: {p['detail']}")
    reply.text = "\n".join(parts)
    return reply


def _q_containment(con, user_id, session_id, message, it, asset_ids, area, as_of):
    w = it.window
    res = A.containment_check(con, area_id=area, asset_ids=asset_ids or None,
                              start=w.get("start"), end=w.get("end"))
    reply = Reply(text="", intent=it.as_dict())
    _receipt(res, reply, it.visual)
    rows = res.data["rows"]
    limit = fmt_num(res.data["limit_hours"])
    scope = (" " + resolve_area_name(con, area)) if area else ""
    if not rows:
        reply.text = f"No spills in{scope} exceeded the {limit}-hour containment limit in {w.get('label') or 'the period'}."
        return reply
    parts = [f"**{len(rows)}** spill(s) in{scope} were not contained within the SOP limit of {limit} hours "
             f"(SR-02). The containment time is recorded only in the repair reports:"]
    for r in rows:
        parts.append(f"- **{r['asset_id']}:** contained after about {fmt_num(r['containment_hours'])} hours "
                     f"(report {r['report_file']}, inspection {r['inspection_id']}).")
    reply.text = "\n".join(parts)
    return reply


def _q_rule(con, user_id, session_id, message, it, asset_ids, area, as_of):
    # Rules are usually asked about in the context of a well or well type.
    scope = None
    if asset_ids:
        row = con.execute("SELECT well_type FROM wells WHERE asset_id=?", [asset_ids[0]]).fetchone()
        scope = row[0] if row else None
    res = K.search_docs(con, message, limit=3)
    reply = Reply(text="", intent=it.as_dict())
    _receipt(res, reply, None)
    if res.data["hits"]:
        h = res.data["hits"][0]
        reply.text = f"From **{h['title']} — {h['section']}**: {h['snippet']}"
    else:
        reply.text = "I could not find that rule in the manuals or SOPs."
    return reply


def _q_docs(con, user_id, session_id, message, it, asset_ids, area, as_of):
    return _q_rule(con, user_id, session_id, message, it, asset_ids, area, as_of)


def _q_unknown(con, user_id, session_id, message, it, asset_ids, area, as_of):
    res = K.search_docs(con, message, limit=3)
    reply = Reply(text="", intent=it.as_dict())
    _receipt(res, reply, None)
    if res.data["hits"]:
        h = res.data["hits"][0]
        reply.text = (f"I'm not certain what you're asking. Related guidance — **{h['title']} "
                      f"({h['section']})**: {h['snippet']}\n\nTry naming a well (e.g. WELL-0001), "
                      f"an area, or a period.")
    else:
        reply.text = ("I couldn't map that to the data. Try naming a well (e.g. WELL-0001), an area, "
                      "or a period such as 'last 90 days'.")
    reply.tool_used = "fallback"
    return reply


# ---------------------------------------------------------------------------
# Feedback: corrections, rules and shared knowledge  (Q12-Q17)
# ---------------------------------------------------------------------------
def _find_inspection(con, asset_id: str, when: Optional[date]) -> Optional[str]:
    if when:
        row = con.execute(
            "SELECT inspection_id FROM inspections WHERE asset_id=? "
            "ORDER BY ABS(CAST(inspected_at AS DATE) - CAST(? AS DATE)) LIMIT 1",
            [asset_id, when],
        ).fetchone()
    else:
        row = con.execute(
            "SELECT inspection_id FROM inspections WHERE asset_id=? ORDER BY inspected_at DESC LIMIT 1",
            [asset_id],
        ).fetchone()
    return row[0] if row else None


def _handle_feedback(con, user_id, session_id, message, it, asset_ids, as_of) -> Reply:
    reply = Reply(text="", intent=it.as_dict())
    who = _name(con, user_id)

    if it.action == "well_type_rule":
        if not it.well_type or not it.field or not it.value:
            reply.text = ("I can record that, but please state the well type, the rule and the new value "
                          "(for example: 'for all T350 wells, RCA after 2 repairs').")
            return reply
        M.add_well_type_rule(con, user_id, it.well_type, it.field, it.value, message)
        reply.tool_used = "memory:well_type_rule"
        reply.text = (f"Recorded. For **all {it.well_type} wells**, `{it.field}` is now "
                      f"**{it.value}** (was {fmt_num(_rule_default(con, it.well_type, it.field))}), "
                      f"based on {who}'s note. This applies only to {it.well_type} wells.")
        # Immediately answer the follow-up if the same message contains a question.
        return reply

    if it.action in ("correct_raw", "correct_analysis"):
        if not it.value:
            reply.text = ("I can correct that, but please give me the corrected value "
                          "(for example: 'the 15 Oct reading for WELL-0164 was wrong — the crew "
                          "re-measured the wellhead at 41.0 °C').")
            return reply
        if not asset_ids:
            reply.text = "Which well is this correction about?"
            return reply
        aid = asset_ids[0]
        when = it.window.get("single") if it.window else None
        insp = _find_inspection(con, aid, when)
        if not insp:
            reply.text = f"I couldn't find an inspection for {aid} to correct."
            return reply
        field = "spill_volume_bbl" if it.action == "correct_analysis" else "wellhead_temp_c"
        info = M.correct_raw_reading(con, user_id, insp, field, it.value, message)
        reply.tool_used = "memory:correction"
        bits = [f"Recorded a correction to **{insp}** ({aid}): `{field}` is now **{it.value}** "
                f"(original **{info['original']}** kept), corrected by {who}."]
        if it.action == "correct_analysis":
            # Re-run compliance so the consequence (e.g. a now-reportable spill) is reflected.
            comp = A.compliance_audit(con, aid, months=12, as_of=as_of)
            reply.evidence = [e.as_dict() for e in comp.evidence]
            newb = [b for b in comp.data["breaches"] if b["protocol"] in ("SR-01", "SR-03")]
            newp = [p for p in comp.data["pending"] if p["protocol"] in ("SR-01", "SR-03")]
            detail = A.spill_detail(con, aid, inspected_on=when)
            v = float(it.value)
            if v >= config.SPILL_REPORTABLE_BBL:
                bits.append(f"At {v} bbl the spill is **reportable** under the Spill Response SOP "
                            f"(threshold {config.SPILL_REPORTABLE_BBL} bbl).")
                for b in newb:
                    bits.append(f"- {b['protocol']}: {b['detail']}")
                for p in newp:
                    bits.append(f"- {p['protocol']}: {p['detail']}")
                bits.append("Any earlier classification based on the old estimate no longer holds.")
            if detail.conflicts:
                reply.conflicts = detail.conflicts
        else:
            status = A.well_status(con, aid, as_of=as_of)
            reply.visual = status.visual
            reply.data = status.data
            reply.evidence = [e.as_dict() for e in status.evidence]
            d = status.data
            if d["producing"]:
                bits.append(f"With the corrected reading the well is now **producing oil** "
                            f"(Δ {d['delta_c']} °C, above the {d['latest']['threshold_c']} °C limit).")
            else:
                bits.append(f"With the corrected reading the well is still **not producing** "
                            f"(Δ {d['delta_c']} °C).")
            if status.conflicts:
                reply.conflicts = status.conflicts
                for c in status.conflicts:
                    bits.append(f"⚠️ {c['message']} {c.get('ask','')}")
        reply.text = " ".join(bits)
        return reply

    if it.action == "share_knowledge":
        if not asset_ids:
            reply.text = "Which well is this about?"
            return reply
        aid = asset_ids[0]
        out = M.record_shared_knowledge(con, user_id, aid, message, as_of)
        reply.tool_used = "memory:shared"
        if out["status"] == "confirmed":
            reply.text = (f"Noted for everyone: {message.strip()} I've stored this as shared knowledge "
                          f"about **{aid}** (from {who}).")
        else:
            c = out["conflict"]
            reply.conflicts = [c]
            reply.text = (f"{c['message']} The latest inspection shows the well is "
                          f"**{'producing' if c['right']['value'] == 'producing' else 'NOT producing'}** "
                          f"oil, which does not match what you said. I've kept your statement on record "
                          f"as **unconfirmed**. {c.get('ask','')}")
        return reply

    reply.text = "I'm not sure what to do with that."
    return reply


def _rule_default(con, well_type: str, rule_key: str):
    row = con.execute(
        "SELECT value_num FROM rules WHERE scope_key=? AND rule_key=? LIMIT 1",
        [well_type, rule_key],
    ).fetchone()
    return row[0] if row else None
