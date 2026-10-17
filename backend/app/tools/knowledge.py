"""Retrieval over the manuals/SOPs plus the rule registry.

The corpus is small (one field's manuals and SOPs), so a transparent keyword
scorer is enough and keeps the system dependency-free. Every hit carries its
document id and section so answers can cite them.
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional

import duckdb

from .base import Evidence, ToolResult

STOP = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "be", "with", "as", "at", "by", "it", "this", "that", "from", "any", "all",
    "what", "which", "how", "when", "who", "why", "must", "should", "may",
}


def _tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP and len(t) > 1]


def search_docs(con: duckdb.DuckDBPyConnection, query: str, doc_id: Optional[str] = None,
                limit: int = 3) -> ToolResult:
    chunks = con.execute(
        "SELECT chunk_id, doc_id, title, section, text FROM documents"
        + (" WHERE doc_id = ?" if doc_id else ""),
        [doc_id] if doc_id else [],
    ).fetchall()
    q = _tokens(query)
    if not q:
        return ToolResult("search_docs", data={"hits": []})
    qset = set(q)

    # Document frequency for a light idf.
    df: Dict[str, int] = {}
    for _, _, _, _, text in chunks:
        for t in set(_tokens(text)):
            if t in qset:
                df[t] = df.get(t, 0) + 1
    n = max(len(chunks), 1)

    scored = []
    for chunk_id, did, title, section, text in chunks:
        toks = _tokens(text)
        if not toks:
            continue
        score = 0.0
        for t in q:
            tf = toks.count(t)
            if tf:
                score += (1 + math.log(tf)) * math.log(1 + n / (1 + df.get(t, 0)))
        if score > 0:
            scored.append((score, chunk_id, did, title, section, text))
    scored.sort(key=lambda x: x[0], reverse=True)

    hits = []
    res = ToolResult("search_docs", data={"query": query})
    for score, chunk_id, did, title, section, text in scored[:limit]:
        snippet = text.strip().split("\n\n")[0][:240]
        hits.append({"doc_id": did, "title": title, "section": section,
                     "score": round(score, 3), "snippet": snippet})
        res.add_evidence(Evidence(
            "document", did, f"{title} — {section}",
            detail=snippet, snippet=text[:600],
        ))
    res.data["hits"] = hits
    return res


def get_rule(con: duckdb.DuckDBPyConnection, scope_key: str, rule_key: str) -> ToolResult:
    rows = con.execute(
        """
        SELECT scope_key, rule_key, value_num, unit, source_doc, source_section,
               description, is_user_override
        FROM v_rule_effective WHERE scope_key = ? AND rule_key = ?
        """,
        [scope_key, rule_key],
    ).fetchall()
    res = ToolResult("get_rule", data={"scope_key": scope_key, "rule_key": rule_key, "rules": rows})
    for r in rows:
        res.add_evidence(Evidence(
            "document", r[4], f"{r[0]} {r[1]} = {r[2]} {r[3]}",
            detail=f"{r[6]} ({'user rule' if r[7] else r[4] + ' ' + (r[5] or '')})",
        ))
    return res


def list_rules(con: duckdb.DuckDBPyConnection, scope_key: Optional[str] = None) -> ToolResult:
    sql = "SELECT scope_key, rule_key, value_num, unit, source_doc, is_user_override FROM v_rule_effective"
    params: list = []
    if scope_key:
        sql += " WHERE scope_key = ?"
        params.append(scope_key)
    sql += " ORDER BY scope_key, rule_key"
    rows = con.execute(sql, params).fetchall()
    return ToolResult("list_rules", data={"rules": rows})
