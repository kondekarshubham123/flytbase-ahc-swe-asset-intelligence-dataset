"""Ingest the challenge dataset into DuckDB.

Path-driven and idempotent: re-running drops and reloads every table, so the test
dataset (released in the final 2 hours) is loaded by pointing COPILOT_DATA_DIR at it
and re-running — no code changes.

Deliberately schema-driven and free of well IDs so it generalises to new wells and
new well types.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Iterable, List, Optional

import duckdb

from .. import config
from . import reports as reports_mod
from . import rules as rules_mod

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

# Tables that are rebuilt on every ingest.
RECORD_TABLES = [
    "users", "teams", "areas", "wells", "equipment", "inspections", "maintenance",
    "regulatory_events", "notes", "reports", "report_facts", "documents", "rules",
    "assertions",
]
# Conversation memory survives a data reload.
MEMORY_TABLES = ["sessions", "messages", "session_focus", "llm_cache"]


def connect(path: Optional[Path] = None) -> duckdb.DuckDBPyConnection:
    p = Path(path or config.DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(p))


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA_PATH.read_text())


def _clear(con: duckdb.DuckDBPyConnection, tables: Iterable[str]) -> None:
    for t in tables:
        con.execute(f"DELETE FROM {t}")


def _read_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    out = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Fleet-wide reference data
# ---------------------------------------------------------------------------
def load_fleet(con: duckdb.DuckDBPyConnection, fleet: Path) -> None:
    users = _read_json(fleet / "users.json", {"users": []})["users"]
    for u in users:
        con.execute(
            "INSERT INTO users VALUES (?,?,?,?)",
            [u["user_id"], u.get("name"), u.get("role"), u.get("team_id")],
        )

    areas_doc = _read_json(fleet / "areas.json", {"areas": [], "teams": []})
    for t in areas_doc.get("teams", []):
        con.execute(
            "INSERT INTO teams VALUES (?,?,?)",
            [t["team_id"], t.get("name"), t.get("lead_user_id")],
        )
    for a in areas_doc.get("areas", []):
        c = a.get("center") or {}
        con.execute(
            "INSERT INTO areas VALUES (?,?,?,?,?)",
            [a["area_id"], a.get("name"), a.get("team_id"),
             c.get("lat"), c.get("lon")],
        )


# ---------------------------------------------------------------------------
# Per-well records
# ---------------------------------------------------------------------------
def load_well(con: duckdb.DuckDBPyConnection, wdir: Path) -> None:
    meta = _read_json(wdir / "metadata.json")
    if not meta:
        return
    loc = meta.get("location") or {}
    li = meta.get("last_inspection") or {}
    con.execute(
        "INSERT INTO wells VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            meta["asset_id"], meta.get("name"), meta.get("asset_category"),
            meta.get("well_type"), meta.get("area_id"), meta.get("team_id"),
            meta.get("in_charge_user_id"), loc.get("lat"), loc.get("lon"),
            meta.get("installed_on"), meta.get("recorded_status"),
            li.get("inspection_id"), li.get("inspected_at"), li.get("inspector_user_id"),
        ],
    )
    for e in meta.get("equipment", []):
        con.execute(
            "INSERT INTO equipment VALUES (?,?,?,?)",
            [e["equipment_id"], meta["asset_id"], e.get("kind"), e.get("model")],
        )

    for i in _read_jsonl(wdir / "inspections.jsonl"):
        con.execute(
            "INSERT INTO inspections VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                i["inspection_id"], i["asset_id"], i.get("inspected_at"),
                i.get("inspector_user_id"), i.get("method"),
                i.get("ambient_temp_c"), i.get("wellhead_temp_c"),
                i.get("spill_detected"), i.get("spill_area_pct"),
                i.get("spill_est_volume_bbl"),
                json.dumps(i.get("evidence_images") or []), i.get("notes") or "",
            ],
        )

    for m in _read_jsonl(wdir / "maintenance.jsonl"):
        cost = m.get("cost_usd") or {}
        con.execute(
            "INSERT INTO maintenance VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                m["maintenance_id"], m["asset_id"], m.get("started_at"),
                m.get("completed_at"), m.get("maintenance_type"),
                m.get("equipment_kind"), m.get("issue"), m.get("action_taken"),
                m.get("technician_user_id"), m.get("contractor"),
                m.get("downtime_hours"), cost.get("labor"), cost.get("parts"),
                cost.get("total"), m.get("triggered_by_inspection_id"),
                m.get("report_file"),
            ],
        )
        # Free-text report body, if the record points at one.
        rf = m.get("report_file")
        if rf:
            body = (wdir / rf).read_text() if (wdir / rf).exists() else ""
            con.execute(
                "INSERT INTO reports VALUES (?,?,?,?)",
                [rf, m["asset_id"], m["maintenance_id"], body],
            )

    for e in _read_jsonl(wdir / "regulatory-events.jsonl"):
        con.execute(
            "INSERT INTO regulatory_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                e["event_id"], e["asset_id"], e.get("date"), e.get("agency"),
                e.get("event_type"), e.get("protocol_id"),
                e.get("related_inspection_id"), e.get("hours_to_report"),
                e.get("detail"), e.get("fine_usd"), e.get("status"),
            ],
        )

    for n in _read_jsonl(wdir / "context.jsonl"):
        con.execute(
            "INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?)",
            [
                n["note_id"], n["asset_id"], n.get("created_at"),
                n.get("author_user_id"), n.get("note_type"),
                n.get("inspection_id"), n.get("text"),
                n.get("valid_from"), n.get("valid_to"),
            ],
        )


def seed_correction_assertions(con: duckdb.DuckDBPyConnection) -> int:
    """Turn seeded `correction` notes into inspection-level overrides.

    Every correction note in the dataset invalidates a spill detection
    ("not a spill" / "produced water" / "wet soil after rain" / "shadow").
    The original detection is preserved in `original_value_text`.
    """
    rows = con.execute(
        """
        SELECT n.note_id, n.inspection_id, n.author_user_id, n.text,
               n.created_at, i.spill_detected, i.spill_est_volume_bbl
        FROM notes n
        LEFT JOIN inspections i ON i.inspection_id = n.inspection_id
        WHERE n.note_type = 'correction' AND n.inspection_id IS NOT NULL
        """
    ).fetchall()
    n = 0
    for note_id, insp_id, author, text, created, spill, vol in rows:
        con.execute(
            "INSERT INTO assertions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                f"ASRT-{note_id}", "inspection", insp_id, "spill_is_real", "false",
                str(vol) if vol is not None else None, author,
                f"seed correction note {note_id}: {text}", "confirmed", "seed_note",
                created or config.TODAY.isoformat(), None, None,
                json.dumps({"note_id": note_id}),
            ],
        )
        n += 1
    return n


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def build(data_dir: Optional[Path] = None, con: Optional[duckdb.DuckDBPyConnection] = None,
          verbose: bool = True) -> dict:
    data_dir = Path(data_dir or config.DATA_DIR)
    own = con is None
    con = con or connect()
    ensure_schema(con)
    _clear(con, RECORD_TABLES)
    # A rebuild is a clean slate: conversations and runtime corrections from a
    # previous run/dataset must not leak into the new one.
    _clear(con, MEMORY_TABLES)

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    load_fleet(con, data_dir / "fleet")
    log(f"  fleet loaded from {data_dir/'fleet'}")

    assets = sorted((data_dir / "assets").glob("WELL-*"))
    for wdir in assets:
        load_well(con, wdir)
    log(f"  {len(assets)} wells loaded")

    corrections = seed_correction_assertions(con)
    log(f"  {corrections} seeded correction assertions")

    doc_chunks = rules_mod.load_documents(con, data_dir / "fleet")
    log(f"  {doc_chunks} document chunks")

    rule_rows = rules_mod.build_rules(con, data_dir / "fleet")
    log(f"  {rule_rows} rules extracted")

    fact_rows = reports_mod.parse_all(con)
    log(f"  {fact_rows} report fact rows parsed")

    stats = summary(con)
    if own:
        con.close()
    return stats


def summary(con: duckdb.DuckDBPyConnection) -> dict:
    def c(sql: str) -> int:
        return con.execute(sql).fetchone()[0]

    return {
        "wells": c("SELECT count(*) FROM wells"),
        "inspections": c("SELECT count(*) FROM inspections"),
        "maintenance": c("SELECT count(*) FROM maintenance"),
        "regulatory_events": c("SELECT count(*) FROM regulatory_events"),
        "notes": c("SELECT count(*) FROM notes"),
        "reports": c("SELECT count(*) FROM reports"),
        "report_facts": c("SELECT count(*) FROM report_facts"),
        "rules": c("SELECT count(*) FROM rules"),
        "documents": c("SELECT count(*) FROM documents"),
        "assertions": c("SELECT count(*) FROM assertions"),
        "valid_spills": c("SELECT count(*) FROM v_spills"),
    }


def new_id(prefix: str = "ID") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


if __name__ == "__main__":
    print("Building copilot database from", config.DATA_DIR)
    s = build()
    for k, v in s.items():
        print(f"  {k:20s} {v}")
