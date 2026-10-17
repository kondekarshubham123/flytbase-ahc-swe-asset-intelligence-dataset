"""Resolve the names/ids people type in questions to real records.

Deliberately forgiving: "WELL 164", "well-0164", "Northeast Block 7" and "NE 7"
should all find the right thing, because the final questions are unseen.
"""
from __future__ import annotations

import re
from typing import List, Optional

import duckdb


def resolve_wells(con: duckdb.DuckDBPyConnection, text: str) -> List[str]:
    """Find every well id mentioned or implied in a piece of text."""
    found: List[str] = []
    for m in re.finditer(r"\bWELL[\s\-_]*0*(\d{1,4})\b", text, re.I):
        n = int(m.group(1))
        wid = f"WELL-{n:04d}"
        if wid not in found:
            found.append(wid)
    # Validate against the DB and keep order.
    if not found:
        return []
    placeholders = ",".join("?" for _ in found)
    valid = {
        r[0]
        for r in con.execute(
            f"SELECT asset_id FROM wells WHERE asset_id IN ({placeholders})", found
        ).fetchall()
    }
    return [w for w in found if w in valid]


def resolve_area(con: duckdb.DuckDBPyConnection, text: str) -> Optional[str]:
    """Map 'Northeast Block 7', 'NE Block 7', 'SW-23' … to an area_id."""
    rows = con.execute("SELECT area_id, name FROM areas").fetchall()

    # Direct id mention.
    for area_id, _ in rows:
        if area_id.lower() in text.lower():
            return area_id

    # Full name match.
    t = text.lower()
    for area_id, name in rows:
        if name and name.lower() in t:
            return area_id

    # Loose match: block number + a direction keyword.
    m = re.search(r"(northeast|northwest|southeast|southwest|north|south|east|west|ne|nw|se|sw)\D{0,12}?(?:block\s*)?(\d+)", t)
    if m:
        direction, number = m.group(1), m.group(2)
        synonyms = {
            "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
        }
        direction = synonyms.get(direction, direction)
        for area_id, name in rows:
            if not name:
                continue
            nl = name.lower()
            if direction in nl and re.search(rf"\b{number}\b", nl):
                return area_id
    return None


def resolve_user(con: duckdb.DuckDBPyConnection, text: str) -> Optional[str]:
    t = text.lower()
    for user_id, name in con.execute("SELECT user_id, name FROM users").fetchall():
        if name and name.lower() in t:
            return user_id
    return None


def resolve_area_name(con: duckdb.DuckDBPyConnection, area_id: str) -> str:
    row = con.execute("SELECT name FROM areas WHERE area_id = ?", [area_id]).fetchone()
    return row[0] if row and row[0] else area_id
