"""Extract structured facts from free-text repair reports.

Some facts (containment time, the compliance-check table, root-cause actions) exist
*only* inside these reports. A pure RAG system cannot reliably answer Q9/Q10/Q13;
this pass makes those facts queryable columns.
"""
from __future__ import annotations

import re
from typing import Optional

import duckdb

CONTAIN_RE = re.compile(r"contained\s+(?:about\s+)?(\d+(?:\.\d+)?)\s*hours?", re.I)
COMPLIANCE_ROW_RE = re.compile(r"^\|\s*((?:SR|RC|WS)-\d+)\s*\|\s*\**([^|*]+?)\**\s*\|", re.I | re.M)
TPH_RE = re.compile(r"highest result(?: after clean-?up)? was\s*([\d.]+)\s*%\s*TPH", re.I)
SAMPLES_RE = re.compile(r"(\d+)\s+soil samples", re.I)
TOTAL_RE = re.compile(r"\|\s*\*\*Total\*\*\s*\|\s*\*\*([\d,]+)\*\*", re.I)
CLASS_RE = re.compile(
    r"\b(minor|moderate|reportable|major)\b[^.\n]{0,30}?\bspill\b"
    r"|\bspill\b[^.\n]{0,40}?\b(minor|moderate|reportable|major)\b",
    re.I,
)
RCA_RE = re.compile(r"action required", re.I)


def parse_body(body: str) -> dict:
    facts: dict = {
        "containment_hours": None, "containment_text": None, "spill_class": None,
        "sr01": None, "sr02": None, "sr03": None, "sr04": None,
        "rca_action_required": None, "final_tph_pct": None,
        "soil_sample_count": None, "reported_total_usd": None,
    }
    if not body:
        return facts

    m = CONTAIN_RE.search(body)
    if m:
        facts["containment_hours"] = float(m.group(1))
        line = body[max(0, m.start() - 40): m.end() + 40].replace("\n", " ").strip()
        facts["containment_text"] = line

    for proto, result in COMPLIANCE_ROW_RE.findall(body):
        key = proto.lower().replace("-", "")  # "SR-02" -> "sr02"
        if key in facts:
            facts[key] = result.strip().strip("*").strip()

    m = TPH_RE.search(body)
    if m:
        facts["final_tph_pct"] = float(m.group(1))
    m = SAMPLES_RE.search(body)
    if m:
        facts["soil_sample_count"] = int(m.group(1))
    m = TOTAL_RE.search(body)
    if m:
        facts["reported_total_usd"] = float(m.group(1).replace(",", ""))
    if RCA_RE.search(body):
        facts["rca_action_required"] = True

    m = CLASS_RE.search(body)
    if m:
        facts["spill_class"] = (m.group(1) or m.group(2)).lower()
    return facts


def parse_all(con: duckdb.DuckDBPyConnection) -> int:
    rows = con.execute(
        "SELECT report_file, asset_id, maintenance_id, body FROM reports"
    ).fetchall()
    n = 0
    for report_file, asset_id, maintenance_id, body in rows:
        f = parse_body(body or "")
        con.execute(
            "INSERT INTO report_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                report_file, asset_id, maintenance_id, f["containment_hours"],
                f["containment_text"], f["spill_class"], f["sr01"], f["sr02"],
                f["sr03"], f["sr04"], f["rca_action_required"], f["final_tph_pct"],
                f["soil_sample_count"], f["reported_total_usd"],
            ],
        )
        n += 1
    return n
