"""Deterministic analytical tools.

Every number the copilot ever reports is computed here, in SQL, against the
semantic layer — never by a language model. The tools return structured data plus
the evidence records that justify it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import duckdb

from .base import Evidence, ToolResult, fmt_num, iso
from .resolve import resolve_area_name

REASON_KEYWORDS = (
    "shut in", "shut-in", "shutin", "planned", "workover", "down for",
    "stopped", "not producing", "maintenance", "offline", "off line",
)


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: Optional[list] = None) -> List[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


# ---------------------------------------------------------------------------
# 1. Well status  (Q1, Q12, Q15)
# ---------------------------------------------------------------------------
def _open_repairs(con: duckdb.DuckDBPyConnection, asset_id: str, as_of: date) -> List[dict]:
    return _rows(
        con,
        """
        SELECT maintenance_id, started_at, completed_at, equipment_kind, issue,
               action_taken, report_file
        FROM maintenance
        WHERE asset_id = ?
          AND maintenance_type = 'corrective'
          AND CAST(started_at AS DATE) <= ?
          AND (completed_at IS NULL OR CAST(completed_at AS DATE) > ?)
        ORDER BY started_at DESC
        """,
        [asset_id, as_of, as_of],
    )


def _context_reasons(con: duckdb.DuckDBPyConnection, asset_id: str, as_of: date) -> List[dict]:
    notes = _rows(
        con,
        """
        SELECT note_id, text, note_type, author_user_id, valid_from, valid_to, created_at
        FROM notes
        WHERE asset_id = ?
          AND note_type IN ('operator_feedback', 'operator_note')
          AND (valid_from IS NULL OR CAST(valid_from AS DATE) <= ?)
          AND (valid_to IS NULL OR CAST(valid_to AS DATE) >= ?)
        ORDER BY created_at DESC
        """,
        [asset_id, as_of, as_of],
    )
    out = []
    for n in notes:
        t = (n["text"] or "").lower()
        if any(k in t for k in REASON_KEYWORDS):
            out.append(n)
    return out


def well_status(con: duckdb.DuckDBPyConnection, asset_id: str,
                as_of: Optional[date] = None, user_id: Optional[str] = None) -> ToolResult:
    as_of = as_of or date.today()
    well = con.execute("SELECT * FROM wells WHERE asset_id = ?", [asset_id]).fetchone()
    if not well:
        return ToolResult("well_status", notes=[f"{asset_id} not found"])
    cols = [d[0] for d in con.execute("SELECT * FROM wells WHERE asset_id = ?", [asset_id]).description]
    w = dict(zip(cols, well))

    latest = _rows(
        con,
        """
        SELECT * FROM v_prod_status
        WHERE asset_id = ? AND CAST(inspected_at AS DATE) <= ?
        ORDER BY inspected_at DESC LIMIT 1
        """,
        [asset_id, as_of],
    )
    latest = latest[0] if latest else None
    res = ToolResult("well_status", data={"well": w, "latest": latest})

    producing = bool(latest["producing"]) if latest else None
    delta = None
    if latest and latest["wellhead_temp_c"] is not None and latest["ambient_temp_c"] is not None:
        delta = round(latest["wellhead_temp_c"] - latest["ambient_temp_c"], 1)

    reasons: List[dict] = []
    reasons += _open_repairs(con, asset_id, as_of)
    reasons += _context_reasons(con, asset_id, as_of)

    inspector_note = (latest or {}).get("notes") or ""
    conflicts: List[dict] = []

    # A correction that flips the reading, while the raw inspection note disagrees.
    if latest and latest.get("wh_corrected_by") and producing is True:
        if any(k in inspector_note.lower() for k in ("not moving", "stopped", "no movement", "idle")):
            conflicts.append({
                "kind": "correction_vs_note",
                "message": ("The corrected wellhead reading says the well is producing, but the "
                            "inspector's note on the same inspection disagrees."),
                "left": {"source": f"correction by {latest['wh_corrected_by']}",
                         "value": f"{latest['wellhead_temp_c']} °C wellhead"},
                "right": {"source": f"inspection {latest['inspection_id']} note",
                          "value": inspector_note},
                "ask": "Please confirm which is correct.",
            })

    # Evidence
    if latest:
        res.add_evidence(Evidence(
            "record", latest["inspection_id"],
            f"Inspection {latest['inspection_id']} on {iso(latest['inspected_at'])}",
            detail=(f"wellhead {latest['wellhead_temp_c']} °C vs air {latest['ambient_temp_c']} °C "
                    f"(Δ {delta} °C; {w['well_type']} limit {latest['threshold_c']} °C)"),
        ))
        res.add_evidence(Evidence(
            "record", f"RULE-{w['well_type']}-producing_delta_c",
            f"{w['well_type']} production temperature rule",
            detail=f"≥ {latest['threshold_c']} °C above ambient counts as producing",
            path=f"{w['well_type']} manual",
        ))
    for r in reasons:
        if r.get("maintenance_id"):
            res.add_evidence(Evidence(
                "record", r["maintenance_id"],
                f"Repair {r['maintenance_id']} ({r['equipment_kind']})",
                detail=f"{r['issue']} · started {iso(r['started_at'])} · open",
                path=r.get("report_file"),
            ))
        else:
            res.add_evidence(Evidence(
                "note", r["note_id"], f"Context note {r['note_id']}",
                detail=r["text"], path=None,
            ))

    # Card data for the UI
    res.data["producing"] = producing
    res.data["delta_c"] = delta
    res.data["recorded_status"] = w.get("recorded_status")
    res.data["recorded_status_stale"] = (
        w.get("recorded_status") == "active" and producing is False
    )
    res.data["reasons"] = [
        {"type": "repair" if r.get("maintenance_id") else "note",
         "id": r.get("maintenance_id") or r.get("note_id"),
         "text": (r.get("issue") if r.get("maintenance_id") else r.get("text"))}
        for r in reasons
    ]
    res.data["inspector_note"] = inspector_note
    res.conflicts = conflicts
    res.visual = {"type": "card", "spec": {"asset_id": asset_id}}
    return res


# ---------------------------------------------------------------------------
# 2. Downtime  (Q5, Q6)
# ---------------------------------------------------------------------------
def downtime(con: duckdb.DuckDBPyConnection, asset_ids: Optional[List[str]] = None,
             area_id: Optional[str] = None, start: Optional[date] = None,
             end: Optional[date] = None, top: Optional[int] = None) -> ToolResult:
    where = ["1=1"]
    params: list = []
    if asset_ids:
        where.append(f"d.asset_id IN ({','.join('?' for _ in asset_ids)})")
        params += asset_ids
    if area_id:
        where.append("d.area_id = ?")
        params.append(area_id)
    if start:
        where.append("CAST(d.inspected_at AS DATE) >= ?")
        params.append(start)
    if end:
        where.append("CAST(d.inspected_at AS DATE) <= ?")
        params.append(end)

    sql = f"""
        SELECT d.asset_id, w.name AS well_name, d.area_id, a.name AS area_name,
               d.well_type,
               SUM(d.downtime_days) AS downtime_days,
               COUNT(*) FILTER (WHERE d.downtime_days > 0) AS down_inspections
        FROM v_downtime d
        JOIN wells w USING (asset_id)
        LEFT JOIN areas a ON a.area_id = d.area_id
        WHERE {' AND '.join(where)}
        GROUP BY d.asset_id, w.name, d.area_id, a.name, d.well_type
        HAVING SUM(d.downtime_days) > 0
        ORDER BY downtime_days DESC
    """
    rows = _rows(con, sql, params)
    if top:
        rows = rows[:top]

    res = ToolResult("downtime", data={"rows": rows, "start": iso(start), "end": iso(end)})
    for r in rows:
        res.add_evidence(Evidence(
            "record", r["asset_id"],
            f"{r['asset_id']} — {fmt_num(r['downtime_days'])} days down",
            detail=f"{r['down_inspections']} weekly inspection(s) found it not producing (×7 days)",
        ))
    if len(rows) == 1:
        res.visual = {"type": "line", "spec": {}}
    elif len(rows) > 1:
        res.visual = {"type": "bar", "spec": {
            "x": "asset_id", "y": "downtime_days", "title": "Downtime (days)"}}
    else:
        res.visual = {"type": "table", "spec": {"columns": ["asset_id", "downtime_days"], "rows": rows}}
    return res


def current_status(con: duckdb.DuckDBPyConnection, area_id: Optional[str] = None,
                   asset_ids: Optional[List[str]] = None, as_of: Optional[date] = None) -> ToolResult:
    """Every well's latest status (Q2): which wells are not producing right now."""
    as_of = as_of or date.today()
    where = ["1=1"]
    params: list = []
    if area_id:
        where.append("l.area_id = ?")
        params.append(area_id)
    if asset_ids:
        where.append(f"l.asset_id IN ({','.join('?' for _ in asset_ids)})")
        params += asset_ids
    rows = _rows(
        con,
        f"""
        WITH latest AS (
            SELECT v.*, row_number() OVER (PARTITION BY v.asset_id ORDER BY v.inspected_at DESC) rn
            FROM v_prod_status v
            WHERE CAST(v.inspected_at AS DATE) <= ?
        )
        SELECT l.inspection_id, l.asset_id, w.name AS well_name, l.area_id, a.name AS area_name,
               l.well_type, l.inspected_at, l.wellhead_temp_c, l.ambient_temp_c,
               ROUND(l.delta_c, 1) AS delta_c, l.threshold_c, l.producing,
               w.recorded_status, l.notes AS inspector_note, l.wh_corrected_by
        FROM latest l
        JOIN wells w USING (asset_id)
        LEFT JOIN areas a ON a.area_id = l.area_id
        WHERE l.rn = 1 AND {' AND '.join(where)}
        ORDER BY l.asset_id
        """,
        [as_of] + params,
    )
    for r in rows:
        r["reasons"] = []
        if not r["producing"]:
            for rep in _open_repairs(con, r["asset_id"], as_of):
                r["reasons"].append({"type": "repair", "id": rep["maintenance_id"],
                                     "text": rep["issue"]})
            for note in _context_reasons(con, r["asset_id"], as_of):
                r["reasons"].append({"type": "note", "id": note["note_id"],
                                     "text": note["text"]})
    res = ToolResult("current_status", data={"rows": rows, "as_of": iso(as_of)})
    for r in rows:
        res.add_evidence(Evidence(
            "record", r["inspection_id"],
            f"{r['asset_id']} latest inspection {r['inspection_id']}",
            detail=f"Δ {r['delta_c']} °C vs {r['well_type']} limit {r['threshold_c']} °C "
                   f"→ {'producing' if r['producing'] else 'NOT producing'}",
        ))
    res.visual = {"type": "table", "spec": {"rows": rows}}
    return res


# ---------------------------------------------------------------------------
# Well locations  ("give me the locations of all wells in <area>")
# ---------------------------------------------------------------------------
def well_locations(con: duckdb.DuckDBPyConnection, area_id: Optional[str] = None,
                   asset_ids: Optional[List[str]] = None) -> ToolResult:
    """Coordinates for wells, optionally scoped to an area or a set of wells."""
    where = ["w.lat IS NOT NULL", "w.lon IS NOT NULL"]
    params: list = []
    if area_id:
        where.append("w.area_id = ?")
        params.append(area_id)
    if asset_ids:
        where.append(f"w.asset_id IN ({','.join('?' for _ in asset_ids)})")
        params += asset_ids

    rows = _rows(
        con,
        f"""
        SELECT w.asset_id, w.name AS well_name, w.area_id, a.name AS area_name,
               a.center_lat, a.center_lon, w.well_type, w.team_id,
               ROUND(w.lat, 5) AS lat, ROUND(w.lon, 5) AS lon, w.recorded_status
        FROM wells w
        LEFT JOIN areas a ON a.area_id = w.area_id
        WHERE {' AND '.join(where)}
        ORDER BY w.area_id, w.asset_id
        """,
        params,
    )
    res = ToolResult("well_locations", data={"rows": rows, "count": len(rows),
                                             "area_id": area_id})
    res.visual = {"type": "map", "spec": {"points": [
        {"asset_id": r["asset_id"], "lat": r["lat"], "lon": r["lon"],
         "area_name": r["area_name"], "well_type": r["well_type"]}
        for r in rows
    ]}}
    for r in rows:
        res.add_evidence(Evidence(
            "record", r["asset_id"],
            f"{r['asset_id']} location",
            detail=f"{r['lat']}, {r['lon']} · {r['area_name'] or 'unassigned'} · "
                   f"{r['well_type']}",
        ))
    return res


def temp_series(con: duckdb.DuckDBPyConnection, asset_id: str,
                start: Optional[date] = None, end: Optional[date] = None) -> ToolResult:
    where = ["asset_id = ?"]
    params: list = [asset_id]
    if start:
        where.append("CAST(inspected_at AS DATE) >= ?")
        params.append(start)
    if end:
        where.append("CAST(inspected_at AS DATE) <= ?")
        params.append(end)
    rows = _rows(
        con,
        f"""
        SELECT inspection_id, inspected_at, wellhead_temp_c, ambient_temp_c,
               ROUND(delta_c,1) AS delta_c, threshold_c, producing
        FROM v_prod_status WHERE {' AND '.join(where)} ORDER BY inspected_at
        """,
        params,
    )
    res = ToolResult("temp_series", data={"rows": rows, "asset_id": asset_id})
    for r in rows:
        res.add_evidence(Evidence(
            "record", r["inspection_id"],
            f"{r['inspection_id']} on {iso(r['inspected_at'])}",
            detail=f"wellhead {r['wellhead_temp_c']} °C / air {r['ambient_temp_c']} °C "
                   f"({'producing' if r['producing'] else 'NOT producing'})",
        ))
    res.visual = {"type": "line", "spec": {"asset_id": asset_id,
                                           "series": ["wellhead_temp_c", "ambient_temp_c"]}}
    return res


def downtime_detail(con: duckdb.DuckDBPyConnection, asset_id: str,
                    start: Optional[date] = None, end: Optional[date] = None) -> List[dict]:
    """The individual downtime inspections, for a single-well explanation."""
    where = ["asset_id = ?", "downtime_days > 0"]
    params: list = [asset_id]
    if start:
        where.append("CAST(inspected_at AS DATE) >= ?")
        params.append(start)
    if end:
        where.append("CAST(inspected_at AS DATE) <= ?")
        params.append(end)
    return _rows(
        con,
        f"SELECT inspection_id, inspected_at, wellhead_temp_c, ambient_temp_c, delta_c, threshold_c "
        f"FROM v_downtime WHERE {' AND '.join(where)} ORDER BY inspected_at",
        params,
    )


# ---------------------------------------------------------------------------
# 3. Spills  (Q4, Q7, Q8)
# ---------------------------------------------------------------------------
def spills(con: duckdb.DuckDBPyConnection, asset_ids: Optional[List[str]] = None,
           area_id: Optional[str] = None, start: Optional[date] = None,
           end: Optional[date] = None, include_points: bool = True,
           reportable_only: bool = False) -> ToolResult:
    where = ["1=1"]
    params: list = []
    if asset_ids:
        where.append(f"s.asset_id IN ({','.join('?' for _ in asset_ids)})")
        params += asset_ids
    if area_id:
        where.append("s.area_id = ?")
        params.append(area_id)
    if start:
        where.append("CAST(s.inspected_at AS DATE) >= ?")
        params.append(start)
    if end:
        where.append("CAST(s.inspected_at AS DATE) <= ?")
        params.append(end)
    if reportable_only:
        where.append("s.reportable IS TRUE")

    rows = _rows(
        con,
        f"""
        SELECT s.inspection_id, s.asset_id, w.name AS well_name, s.area_id,
               a.name AS area_name, s.well_type, s.inspected_at,
               s.volume_bbl, s.spill_class, s.reportable, s.lat, s.lon,
               s.spill_corrected_by
        FROM v_spills s
        JOIN wells w USING (asset_id)
        LEFT JOIN areas a ON a.area_id = s.area_id
        WHERE {' AND '.join(where)}
        ORDER BY s.inspected_at DESC
        """,
        params,
    )
    total = sum((r["volume_bbl"] or 0) for r in rows)
    # Raw detections before corrections (to explain "7 detections, 1 corrected").
    raw_where = ["i.spill_detected IS TRUE"]
    raw_params: list = []
    if asset_ids:
        raw_where.append(f"i.asset_id IN ({','.join('?' for _ in asset_ids)})")
        raw_params += asset_ids
    if area_id:
        raw_where.append("w.area_id = ?")
        raw_params.append(area_id)
    if start:
        raw_where.append("CAST(i.inspected_at AS DATE) >= ?")
        raw_params.append(start)
    if end:
        raw_where.append("CAST(i.inspected_at AS DATE) <= ?")
        raw_params.append(end)
    raw = con.execute(
        f"SELECT COUNT(*) FROM inspections i JOIN wells w USING (asset_id) "
        f"WHERE {' AND '.join(raw_where)}",
        raw_params,
    ).fetchone()[0]
    res = ToolResult("spills", data={
        "rows": rows, "count": len(rows), "total_bbl": round(total, 3),
        "raw_spill_detections": raw,
        "start": iso(start), "end": iso(end),
    })
    for r in rows:
        res.add_evidence(Evidence(
            "record", r["inspection_id"],
            f"Spill at {r['asset_id']} on {iso(r['inspected_at'])}",
            detail=f"{r['volume_bbl']} bbl, {r['spill_class']}"
                   + (f" (volume corrected by {r['spill_corrected_by']})" if r["spill_corrected_by"] else ""),
        ))
    res.visual = {"type": "table", "spec": {"rows": rows}}
    return res


def spills_heatmap(con: duckdb.DuckDBPyConnection, year: int) -> ToolResult:
    rows = _rows(
        con,
        """
        SELECT a.name AS area_name, MONTH(s.inspected_at) AS month, COUNT(*) AS spills
        FROM v_spills s
        JOIN areas a ON a.area_id = s.area_id
        WHERE YEAR(s.inspected_at) = ?
        GROUP BY a.name, MONTH(s.inspected_at)
        ORDER BY a.name, month
        """,
        [year],
    )
    area_totals = _rows(
        con,
        """
        SELECT a.name AS area_name, COUNT(*) AS total
        FROM v_spills s JOIN areas a ON a.area_id = s.area_id
        WHERE YEAR(s.inspected_at) = ? GROUP BY a.name ORDER BY a.name
        """,
        [year],
    )
    res = ToolResult("spills", data={
        "cells": rows, "area_totals": area_totals, "year": year})
    res.add_evidence(Evidence("record", "v_spills", f"All valid spills in {year}",
                              detail="corrected detections excluded"))
    res.visual = {"type": "heatmap", "spec": {"x": "month", "y": "area_name", "value": "spills",
                                              "year": year}}
    return res


def spills_map(con: duckdb.DuckDBPyConnection, area_id: Optional[str] = None,
               asset_ids: Optional[List[str]] = None,
               start: Optional[date] = None, end: Optional[date] = None) -> ToolResult:
    res = spills(con, asset_ids=asset_ids, area_id=area_id, start=start, end=end)
    res.visual = {"type": "map", "spec": {"points": [
        {"asset_id": r["asset_id"], "lat": r["lat"], "lon": r["lon"],
         "date": iso(r["inspected_at"]), "bbl": r["volume_bbl"]}
        for r in res.data["rows"]
    ]}}
    return res


# ---------------------------------------------------------------------------
# 4. Repair cost  (Q11)
# ---------------------------------------------------------------------------
def repair_cost(con: duckdb.DuckDBPyConnection, asset_ids: List[str],
                start: Optional[date] = None, end: Optional[date] = None,
                equipment_kind: Optional[str] = None) -> ToolResult:
    where = [f"m.asset_id IN ({','.join('?' for _ in asset_ids)})"]
    params: list = list(asset_ids)
    if start:
        where.append("CAST(m.started_at AS DATE) >= ?")
        params.append(start)
    if end:
        where.append("CAST(m.started_at AS DATE) <= ?")
        params.append(end)
    if equipment_kind:
        where.append("m.equipment_kind = ?")
        params.append(equipment_kind)

    rows = _rows(
        con,
        f"""
        SELECT m.asset_id, w.name AS well_name, SUM(m.cost_total) AS cost_usd,
               COUNT(*) AS jobs
        FROM maintenance m JOIN wells w USING (asset_id)
        WHERE {' AND '.join(where)}
        GROUP BY m.asset_id, w.name
        ORDER BY cost_usd DESC
        """,
        params,
    )
    res = ToolResult("repair_cost", data={"rows": rows, "start": iso(start), "end": iso(end)})
    for r in rows:
        jobs = _rows(con, "SELECT maintenance_id, cost_total, started_at, equipment_kind "
                          "FROM maintenance WHERE asset_id=? ORDER BY started_at",
                     [r["asset_id"]])
        res.add_evidence(Evidence("record", f"cost:{r['asset_id']}",
                                  f"{r['asset_id']} repair cost {fmt_num(r['cost_usd'])} USD",
                                  detail=f"{r['jobs']} jobs in window"))
    res.visual = {"type": "table", "spec": {"rows": rows}}
    return res


# ---------------------------------------------------------------------------
# 5. Root-cause candidates  (Q14)
# ---------------------------------------------------------------------------
def rca_candidates(con: duckdb.DuckDBPyConnection, equipment_kind: str = "pump_jack",
                   area_id: Optional[str] = None, asset_ids: Optional[List[str]] = None,
                   months: int = 12, as_of: Optional[date] = None) -> ToolResult:
    as_of = as_of or date.today()
    since = as_of - timedelta(days=int(months * 30.44))
    where = ["m.maintenance_type = 'corrective'", "m.equipment_kind = ?"]
    params: list = [equipment_kind]
    if area_id:
        where.append("w.area_id = ?")
        params.append(area_id)
    if asset_ids:
        where.append(f"m.asset_id IN ({','.join('?' for _ in asset_ids)})")
        params += asset_ids
    where.append("CAST(m.started_at AS DATE) >= ?")
    params.append(since)

    rows = _rows(
        con,
        f"""
        SELECT m.asset_id, w.well_type, w.area_id, COUNT(*) AS repairs,
               SUM(m.cost_total) AS cost_usd
        FROM maintenance m JOIN wells w USING (asset_id)
        WHERE {' AND '.join(where)}
        GROUP BY m.asset_id, w.well_type, w.area_id
        ORDER BY repairs DESC
        """,
        params,
    )
    # Apply the effective per-well-type RCA threshold (manual value or a user rule).
    for r in rows:
        r["threshold"] = _rule_for_well_type(con, r["well_type"], "rca_corrective_count", 3.0)
        r["needs_rca"] = r["repairs"] >= r["threshold"]
        r["threshold_source"] = ("user rule" if _rule_is_user_override(
            con, r["well_type"], "rca_corrective_count") else f"{r['well_type']} manual §10")

    flagged = [r for r in rows if r["needs_rca"]]
    res = ToolResult("rca_candidates", data={
        "rows": rows, "flagged": flagged, "equipment_kind": equipment_kind,
        "months": months, "as_of": iso(as_of),
    })
    for r in flagged:
        res.add_evidence(Evidence(
            "record", r["asset_id"],
            f"{r['asset_id']} ({r['well_type']}): {r['repairs']} corrective {equipment_kind} repairs",
            detail=f"threshold {fmt_num(r['threshold'])} ({r['threshold_source']})",
        ))
    res.visual = {"type": "table", "spec": {"rows": flagged}}
    return res


# ---------------------------------------------------------------------------
# 6. Compliance audit  (Q9, Q13)
# ---------------------------------------------------------------------------
def _rule(con: duckdb.DuckDBPyConnection, scope_key: str, rule_key: str,
          default: float) -> float:
    row = con.execute(
        """
        SELECT value_num FROM v_rule_effective
        WHERE rule_key = ? AND (scope_key = ? OR scope_key = '*')
        ORDER BY is_user_override DESC, CASE WHEN scope_key = ? THEN 1 ELSE 0 END DESC
        LIMIT 1
        """,
        [rule_key, scope_key, scope_key],
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else default


def _rule_for_well_type(con, well_type: str, rule_key: str, default: float) -> float:
    return _rule(con, well_type, rule_key, default)


def _rule_is_user_override(con, scope_key: str, rule_key: str) -> bool:
    row = con.execute(
        "SELECT is_user_override FROM v_rule_effective WHERE rule_key=? AND scope_key=? LIMIT 1",
        [rule_key, scope_key],
    ).fetchone()
    return bool(row and row[0])


def compliance_audit(con: duckdb.DuckDBPyConnection, asset_id: str,
                     months: int = 12, as_of: Optional[date] = None) -> ToolResult:
    as_of = as_of or date.today()
    since = as_of - timedelta(days=int(months * 30.44))
    breaches: List[dict] = []
    pending: List[dict] = []
    met: List[dict] = []
    evidence: List[Evidence] = []

    sr01_limit = _rule(con, "SR-01", "report_deadline_hours", 24.0)
    sr02_limit = _rule(con, "SR-02", "containment_deadline_hours", 4.0)
    sr03_days = _rule(con, "SR-03", "verify_deadline_days", 60.0)
    rc04_days = _rule(con, "RC-04", "nov_response_days", 30.0)
    rc02 = "Annual flowline integrity test"

    # --- Spill-driven protocols -----------------------------------------
    spill_rows = _rows(
        con,
        "SELECT * FROM v_spills WHERE asset_id = ? AND CAST(inspected_at AS DATE) >= ? "
        "ORDER BY inspected_at",
        [asset_id, since],
    )
    for s in spill_rows:
        insp = s["inspection_id"]
        disc = _as_date(s["inspected_at"])
        reportable = bool(s["reportable"])
        evidence.append(Evidence("record", insp, f"Spill {insp} ({s['volume_bbl']} bbl)",
                                 detail=f"{s['spill_class']} on {iso(disc)}"))

        if reportable:
            # SR-01 — written notice within the limit.
            filed = _rows(
                con,
                "SELECT event_id, hours_to_report, date FROM regulatory_events "
                "WHERE asset_id=? AND related_inspection_id=? AND event_type='spill_report_filed'",
                [asset_id, insp],
            )
            if not filed:
                breaches.append({"protocol": "SR-01", "status": "breach",
                                 "detail": f"No spill report recorded for the {s['volume_bbl']} bbl "
                                           f"spill on {iso(disc)} (limit {fmt_num(sr01_limit)} h).",
                                 "evidence_id": insp})
            else:
                hrs = filed[0]["hours_to_report"]
                if hrs is not None and hrs > sr01_limit:
                    breaches.append({"protocol": "SR-01", "status": "breach",
                                     "detail": f"Reported {fmt_num(hrs)} h after discovery "
                                               f"(limit {fmt_num(sr01_limit)} h).",
                                     "evidence_id": filed[0]["event_id"]})
                    evidence.append(Evidence("record", filed[0]["event_id"],
                                             f"Spill report {filed[0]['event_id']}",
                                             detail=f"{fmt_num(hrs)} h after discovery"))
                else:
                    met.append({"protocol": "SR-01", "detail": "Report filed within limit"})

            # SR-03 — remediation verified within the limit. Verification is closed
            # only by a passing (closed) site inspection from the agency.
            due = disc + timedelta(days=int(sr03_days))
            verif = _rows(
                con,
                "SELECT event_id, date FROM regulatory_events WHERE asset_id=? AND "
                "event_type='site_inspection' AND status='closed' AND date >= ? "
                "ORDER BY date LIMIT 1",
                [asset_id, disc],
            )
            open_insp = _rows(
                con,
                "SELECT event_id, date FROM regulatory_events WHERE asset_id=? AND "
                "event_type='site_inspection' AND status='open' AND date >= ? "
                "ORDER BY date LIMIT 1",
                [asset_id, disc],
            )
            if not verif:
                ref = open_insp[0]["event_id"] if open_insp else insp
                if as_of > due:
                    breaches.append({"protocol": "SR-03", "status": "breach",
                                     "detail": f"Clean-up verification was due {iso(due)} and is still open.",
                                     "evidence_id": ref})
                    if open_insp:
                        evidence.append(Evidence("record", open_insp[0]["event_id"],
                                                 f"Agency site inspection {open_insp[0]['event_id']}",
                                                 detail=f"status open, not a passing verification"))
                else:
                    pending.append({"protocol": "SR-03",
                                    "detail": f"Verification due {iso(due)} (not yet passed)."})
            else:
                met.append({"protocol": "SR-03", "detail": "Verification recorded"})

        # SR-02 — containment within the limit, from the repair report.
        rep = _rows(
            con,
            """
            SELECT m.maintenance_id, m.report_file, f.containment_hours
            FROM maintenance m LEFT JOIN report_facts f ON f.report_file = m.report_file
            WHERE m.asset_id = ? AND m.triggered_by_inspection_id = ?
            """,
            [asset_id, insp],
        )
        for rr in rep:
            if rr["containment_hours"] is not None:
                if rr["containment_hours"] > sr02_limit:
                    breaches.append({"protocol": "SR-02", "status": "breach",
                                     "detail": f"Contained after about {fmt_num(rr['containment_hours'])} h "
                                               f"(limit {fmt_num(sr02_limit)} h).",
                                     "evidence_id": rr["report_file"]})
                    evidence.append(Evidence("record", rr["report_file"],
                                             f"Repair report {rr['report_file']}",
                                             detail=f"containment {fmt_num(rr['containment_hours'])} h"))
                else:
                    met.append({"protocol": "SR-02", "detail": "Contained within limit"})

    # --- Notices of violation (RC-04) -----------------------------------
    notices = _rows(
        con,
        "SELECT event_id, date, detail, fine_usd, status FROM regulatory_events "
        "WHERE asset_id=? AND event_type='notice_of_violation' AND date >= ? ORDER BY date",
        [asset_id, since],
    )
    for n in notices:
        ndate = _as_date(n["date"])
        due = (ndate + timedelta(days=int(rc04_days))) if ndate else None
        resp = _rows(
            con,
            "SELECT event_id FROM regulatory_events WHERE asset_id=? AND "
            "event_type='nov_response_filed' AND date >= ? ORDER BY date LIMIT 1",
            [asset_id, ndate] if ndate else [asset_id, since],
        )
        if not resp:
            detail = (f"Notice {n['event_id']} needed a response by {iso(due)}; none is recorded."
                      if due and as_of > due else
                      f"Notice {n['event_id']} response due {iso(due)}.")
            entry = {"protocol": "RC-04", "detail": detail,
                     "evidence_id": n["event_id"], "fine_usd": n["fine_usd"],
                     "open": n["status"] == "open"}
            (breaches if (due and as_of > due) else pending).append(entry)
            evidence.append(Evidence("record", n["event_id"],
                                     f"Notice of violation {n['event_id']}",
                                     detail=f"{n['detail']} · fine {fmt_num(n['fine_usd'])} USD · {n['status']}"))
        else:
            met.append({"protocol": "RC-04", "detail": "Notice answered"})

    # --- Flowline integrity test (RC-02) --------------------------------
    years = sorted({d.year for d in [since, as_of]})
    flow_ok = True
    for y in range(years[0], as_of.year + 1):
        row = con.execute(
            "SELECT COUNT(*) FROM maintenance WHERE asset_id=? AND "
            "LOWER(issue) LIKE '%flowline%integrity%' AND YEAR(CAST(started_at AS DATE))=?",
            [asset_id, y],
        ).fetchone()
        if not row[0]:
            flow_ok = False
    if flow_ok:
        met.append({"protocol": "RC-02", "detail": "Annual flowline integrity test recorded"})
    else:
        pending.append({"protocol": "RC-02", "detail": "Annual flowline integrity test not found for a year in window"})

    # --- Root-cause analysis (T200 §10) ---------------------------------
    for r in _rows(
        con,
        """
        SELECT m.equipment_kind, COUNT(*) AS repairs
        FROM maintenance m JOIN wells w USING (asset_id)
        WHERE m.asset_id=? AND m.maintenance_type='corrective'
          AND CAST(m.started_at AS DATE) >= ?
        GROUP BY m.equipment_kind
        """,
        [asset_id, since],
    ):
        wtype = con.execute("SELECT well_type FROM wells WHERE asset_id=?", [asset_id]).fetchone()[0]
        thr = _rule(con, wtype, "rca_corrective_count", 3.0)
        if r["repairs"] >= thr:
            acts = _rows(
                con,
                "SELECT report_file FROM report_facts WHERE asset_id=? AND rca_action_required IS TRUE",
                [asset_id],
            )
            mark = " (a report marks it 'Action required')" if acts else ""
            pending.append({"protocol": f"{wtype} §10 RCA",
                            "detail": f"{r['repairs']} corrective {r['equipment_kind']} repairs "
                                      f"(threshold {fmt_num(thr)}); root-cause analysis{mark}."})

    res = ToolResult("compliance", data={
        "breaches": breaches, "pending": pending, "met": met,
        "months": months, "as_of": iso(as_of), "asset_id": asset_id,
    })
    res.evidence = evidence
    for b in breaches:
        res.notes.append(f"{b['protocol']}: {b['detail']}")
    res.visual = {"type": "table", "spec": {"rows": breaches}}
    fw = con.execute("SELECT issue FROM maintenance WHERE asset_id=? AND maintenance_type='corrective' "
                     "ORDER BY started_at DESC LIMIT 5", [asset_id]).fetchall()
    return res


# ---------------------------------------------------------------------------
# 7. Containment check across an area  (Q10)
# ---------------------------------------------------------------------------
def containment_check(con: duckdb.DuckDBPyConnection, area_id: Optional[str] = None,
                      asset_ids: Optional[List[str]] = None,
                      start: Optional[date] = None, end: Optional[date] = None) -> ToolResult:
    where = ["1=1"]
    params: list = []
    if area_id:
        where.append("s.area_id = ?")
        params.append(area_id)
    if asset_ids:
        where.append(f"s.asset_id IN ({','.join('?' for _ in asset_ids)})")
        params += asset_ids
    if start:
        where.append("CAST(s.inspected_at AS DATE) >= ?")
        params.append(start)
    if end:
        where.append("CAST(s.inspected_at AS DATE) <= ?")
        params.append(end)

    limit = _rule(con, "SR-02", "containment_deadline_hours", 4.0)
    rows = _rows(
        con,
        f"""
        SELECT s.inspection_id, s.asset_id, s.inspected_at, s.volume_bbl, s.spill_class,
               m.maintenance_id, m.report_file, f.containment_hours
        FROM v_spills s
        LEFT JOIN maintenance m ON m.triggered_by_inspection_id = s.inspection_id
        LEFT JOIN report_facts f ON f.report_file = m.report_file
        WHERE {' AND '.join(where)}
        ORDER BY s.inspected_at
        """,
        params,
    )
    late = []
    for r in rows:
        ch = r["containment_hours"]
        if ch is None:
            continue
        r["limit_hours"] = limit
        r["late"] = ch > limit
        if r["late"]:
            late.append(r)
    res = ToolResult("containment_check", data={"rows": late, "limit_hours": limit})
    for r in late:
        res.add_evidence(Evidence(
            "record", r["report_file"] or r["maintenance_id"],
            f"{r['asset_id']} containment {fmt_num(r['containment_hours'])} h (limit {fmt_num(limit)} h)",
            detail=f"spill {r['inspection_id']} on {iso(r['inspected_at'])} · report {r.get('report_file')}",
        ))
    res.visual = {"type": "table", "spec": {"rows": late}}
    return res


# ---------------------------------------------------------------------------
# 8. Single-spill investigation  (Q18)
# ---------------------------------------------------------------------------
def spill_detail(con: duckdb.DuckDBPyConnection, asset_id: str,
                 inspected_on: Optional[date] = None) -> ToolResult:
    """Everything about one spill: the detection, the notes about it, the repair."""
    where = ["i.asset_id = ?", "i.spill_detected IS TRUE"]
    params: list = [asset_id]
    if inspected_on:
        where.append("CAST(i.inspected_at AS DATE) = ?")
        params.append(inspected_on)
    rows = _rows(
        con,
        f"""
        SELECT i.inspection_id, i.inspected_at, i.wellhead_temp_c, i.ambient_temp_c,
               i.spill_est_volume_bbl, i.spill_area_pct, i.notes,
               (SELECT COUNT(*) FROM assertions a
                 WHERE a.scope='inspection' AND a.scope_key=i.inspection_id
                   AND a.field='spill_is_real' AND a.status='confirmed') AS is_corrected
        FROM inspections i
        WHERE {' AND '.join(where)}
        ORDER BY i.inspected_at DESC LIMIT 1
        """,
        params,
    )
    if not rows:
        return ToolResult("spill_detail", notes=[f"No spill found for {asset_id}"])
    s = rows[0]
    insp = s["inspection_id"]

    related_notes = _rows(
        con,
        "SELECT note_id, note_type, author_user_id, inspection_id, text, created_at FROM notes "
        "WHERE asset_id=? AND (inspection_id=? OR CAST(created_at AS DATE) <= CAST(? AS DATE) + INTERVAL 30 DAY) "
        "ORDER BY created_at",
        [asset_id, insp, s["inspected_at"]],
    )
    related_notes = [n for n in related_notes
                     if insp in (n["text"] or "") or n["inspection_id"] == insp]

    repair = _rows(
        con,
        "SELECT maintenance_id, issue, action_taken, report_file FROM maintenance "
        "WHERE asset_id=? AND triggered_by_inspection_id=?",
        [asset_id, insp],
    )

    res = ToolResult("spill_detail", data={
        "spill": s, "notes": related_notes, "repairs": repair,
        "corrected": bool(s["is_corrected"]),
    })
    res.add_evidence(Evidence("record", insp, f"Spill detection {insp} on {iso(s['inspected_at'])}",
                              detail=f"{s['spill_est_volume_bbl']} bbl, {s['spill_area_pct']}% of pad"
                                     + (f" — note: {s['notes']}" if s['notes'] else "")))
    for n in related_notes:
        res.add_evidence(Evidence("note", n["note_id"], f"Note {n['note_id']} ({n['note_type']})",
                                  detail=n["text"]))
    for r in repair:
        res.add_evidence(Evidence("record", r["maintenance_id"], f"Repair {r['maintenance_id']}",
                                  detail=r["action_taken"], path=r.get("report_file")))
    if not res.conflicts and len(related_notes) >= 2:
        res.conflicts.append({
            "kind": "notes_disagree",
            "message": "Two context notes disagree about this spill; neither is a formal correction.",
            "left": {"source": related_notes[0]["note_id"], "value": related_notes[0]["text"]},
            "right": {"source": related_notes[-1]["note_id"], "value": related_notes[-1]["text"]},
            "ask": "The detection stands until a formal correction is recorded.",
        })
    res.visual = {"type": "card", "spec": {"asset_id": asset_id, "kind": "spill"}}
    return res


# ---------------------------------------------------------------------------
# 9. Explain downtime intervals  (Q5)
# ---------------------------------------------------------------------------
def explain_downtime(con: duckdb.DuckDBPyConnection, asset_id: str,
                     start: Optional[date] = None, end: Optional[date] = None) -> List[dict]:
    """Each non-producing inspection in a window, with the most likely cause."""
    detail = downtime_detail(con, asset_id, start, end)
    repairs = con.execute(
        "SELECT maintenance_id, started_at, equipment_kind, issue, action_taken "
        "FROM maintenance WHERE asset_id=? AND maintenance_type='corrective' "
        "ORDER BY started_at",
        [asset_id],
    ).fetchall()
    out = []
    for d in detail:
        when = _as_date(d["inspected_at"])
        cause = None
        best = None
        for mid, started, kind, issue, action in repairs:
            sd = _as_date(started)
            if sd is None:
                continue
            gap = abs((sd - when).days)
            if gap <= 14 and (best is None or gap < best[0]):
                best = (gap, {"maintenance_id": mid, "started_at": iso(sd),
                              "equipment_kind": kind, "issue": issue, "action": action})
        if best:
            cause = best[1]
        d = dict(d)
        d["cause"] = cause
        out.append(d)
    return out
