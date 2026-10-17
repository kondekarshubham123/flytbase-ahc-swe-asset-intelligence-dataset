"""Two-level memory, corrections and conflict handling.

One append-only `assertions` table serves all of it:
  * scope='inspection' -> a correction to a raw reading (keeps the original)
  * scope='well'       -> shared knowledge about one well, visible to everyone
  * scope='well_type'  -> a rule for every well of a type
Private memory is the per-user session/message/session_focus tables and never
crosses users.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import duckdb

from .. import config
from ..ingest.loader import new_id

PRODUCING_WORDS = ("running", "fine", "online", "on line", "producing", "active", "back up")
DOWN_WORDS = ("shut in", "shut-in", "shutin", "down", "offline", "off line", "stopped",
              "workover", "planned work", "maintenance")


def record_assertion(con: duckdb.DuckDBPyConnection, scope: str, scope_key: str, field: str,
                     value_text: str, author_user_id: Optional[str], reason: Optional[str],
                     status: str = "confirmed", source: str = "user",
                     original_value_text: Optional[str] = None,
                     valid_from: Optional[str] = None, valid_to: Optional[str] = None,
                     meta: Optional[dict] = None) -> str:
    aid = new_id("ASRT")
    con.execute(
        "INSERT INTO assertions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [aid, scope, scope_key, field, str(value_text), original_value_text,
         author_user_id, reason, status, source,
         datetime.utcnow().isoformat(), valid_from, valid_to,
         json.dumps(meta) if meta else None],
    )
    return aid


# ---------------------------------------------------------------------------
# Corrections to raw data (Q12, Q13)
# ---------------------------------------------------------------------------
def correct_raw_reading(con: duckdb.DuckDBPyConnection, user_id: str, inspection_id: str,
                        field: str, new_value: str, reason: str) -> dict:
    """Correct a raw inspection reading; the original is preserved.

    `field` is the semantic name used by the semantic layer; the original value is
    read from the corresponding raw column.
    """
    raw_col = {
        "wellhead_temp_c": "wellhead_temp_c",
        "ambient_temp_c": "ambient_temp_c",
        "spill_volume_bbl": "spill_est_volume_bbl",
        "spill_is_real": "spill_detected",
        "spill_area_pct": "spill_area_pct",
    }.get(field, field)
    row = con.execute(
        f"SELECT {raw_col} FROM inspections WHERE inspection_id = ?", [inspection_id]
    ).fetchone()
    original = row[0] if row else None
    record_assertion(con, "inspection", inspection_id, field, new_value, user_id, reason,
                     status="confirmed", source="user",
                     original_value_text=str(original) if original is not None else None)
    return {"inspection_id": inspection_id, "field": field, "original": original,
            "new_value": new_value}


def correct_analysis(con: duckdb.DuckDBPyConnection, user_id: str, inspection_id: str,
                     field: str, new_value: str, reason: str) -> dict:
    """Correct the AI's analysis (e.g. a spill volume) against an inspection."""
    return correct_raw_reading(con, user_id, inspection_id, field, new_value, reason)


def add_well_type_rule(con: duckdb.DuckDBPyConnection, user_id: str, well_type: str,
                       field: str, value: str, reason: str) -> str:
    """A rule that applies to every well of one type, and only that type (Q14)."""
    return record_assertion(con, "well_type", well_type, field, value, user_id, reason,
                            status="confirmed", source="user")


# ---------------------------------------------------------------------------
# Shared knowledge (Q15, Q16, Q17)
# ---------------------------------------------------------------------------
def _claim_direction(text: str) -> Optional[str]:
    t = text.lower()
    if any(w in t for w in DOWN_WORDS):
        return "down"
    if any(w in t for w in PRODUCING_WORDS):
        return "producing"
    return None


def current_producing(con: duckdb.DuckDBPyConnection, asset_id: str) -> Optional[bool]:
    row = con.execute(
        "SELECT producing FROM v_prod_status WHERE asset_id=? ORDER BY inspected_at DESC LIMIT 1",
        [asset_id],
    ).fetchone()
    return bool(row[0]) if row else None


def record_shared_knowledge(con: duckdb.DuckDBPyConnection, user_id: str, asset_id: str,
                            text: str, as_of: Optional[date] = None) -> dict:
    """Record something a user says about a well, for everyone (Q16/Q17).

    A claim that agrees with the data is stored as shared knowledge; a claim that
    contradicts the data is stored as *unconfirmed* and surfaced as a conflict
    rather than silently accepted.
    """
    as_of = as_of or config.TODAY.date()
    direction = _claim_direction(text)
    producing = current_producing(con, asset_id)
    status = "confirmed"
    conflict = None
    meta = {"direction": direction} if direction else None

    # Only a claim that a well is RUNNING while the data says it is down is a
    # conflict. A claim that a well is down/planned down is consistent with a
    # future or planned shut-in and is stored as shared knowledge.
    if direction == "producing" and producing is False:
        status = "unconfirmed"
        conflict = {
            "kind": "claim_vs_data",
            "asset_id": asset_id,
            "message": (f"{asset_id}: what {user_id} said contradicts the latest inspection."),
            "left": {"source": f"claim by {user_id}", "value": text},
            "right": {"source": "latest inspection (derived)", "value": "NOT producing"},
            "ask": "Please confirm.",
        }
        if meta is not None:
            meta["planned"] = False
    elif direction == "down":
        if meta is not None:
            meta["planned"] = True

    field = "status_claim"
    val_from = as_of.isoformat()
    assert_id = record_assertion(
        con, "well", asset_id, field, text, user_id, reason=text, status=status,
        source="user", valid_from=val_from, meta=meta,
    )
    return {"assertion_id": assert_id, "status": status, "conflict": conflict}


def shared_knowledge(con: duckdb.DuckDBPyConnection, asset_id: str,
                     as_of: Optional[date] = None) -> List[dict]:
    as_of = as_of or config.TODAY.date()
    rows = con.execute(
        """
        SELECT assertion_id, field, value_text, author_user_id, status, created_at
        FROM assertions
        WHERE scope = 'well' AND scope_key = ?
          AND (valid_to IS NULL OR CAST(valid_to AS DATE) >= ?)
        ORDER BY created_at DESC
        """,
        [asset_id, as_of],
    ).fetchall()
    return [
        {"assertion_id": r[0], "field": r[1], "text": r[2], "author": r[3],
         "status": r[4], "created_at": r[5]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Private, per-user conversation memory (Q11, Q16)
# ---------------------------------------------------------------------------
def note_focus(con: duckdb.DuckDBPyConnection, session_id: str, user_id: str,
               asset_ids: List[str], when: Optional[datetime] = None) -> None:
    when = when or datetime.utcnow()
    for a in asset_ids:
        con.execute("INSERT INTO session_focus VALUES (?,?,?,?)",
                    [session_id, user_id, a, when.isoformat()])


def resolve_reference(con: duckdb.DuckDBPyConnection, user_id: str, current_session_id: str
                      ) -> Dict[str, Any]:
    """Resolve 'the wells we discussed' for THIS user, excluding this session."""
    rows = con.execute(
        """
        SELECT session_id, asset_id, mentioned_at FROM session_focus
        WHERE user_id = ? AND session_id <> ?
        ORDER BY mentioned_at DESC
        """,
        [user_id, current_session_id],
    ).fetchall()
    if not rows:
        return {"found": False, "asset_ids": [], "session_id": None, "when": None}
    latest_session = rows[0][0]
    when = rows[0][2]
    seen: List[str] = []
    for sess, asset, _ in rows:
        if sess == latest_session and asset not in seen:
            seen.append(asset)
    return {"found": True, "asset_ids": seen, "session_id": latest_session, "when": when}


# ---------------------------------------------------------------------------
# Conversation persistence
# ---------------------------------------------------------------------------
def ensure_session(con: duckdb.DuckDBPyConnection, session_id: str, user_id: str,
                   title: Optional[str] = None) -> None:
    exists = con.execute("SELECT 1 FROM sessions WHERE session_id=?", [session_id]).fetchone()
    if not exists:
        con.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                    [session_id, user_id, title or "New chat", datetime.utcnow().isoformat()])


def add_message(con: duckdb.DuckDBPyConnection, session_id: str, user_id: str, role: str,
                content: str, meta: Optional[dict] = None) -> str:
    mid = new_id("MSG")
    con.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
                [mid, session_id, user_id, role, content, datetime.utcnow().isoformat(),
                 json.dumps(meta) if meta else None])
    return mid
