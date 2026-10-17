"""FastAPI backend for the Asset Intelligence Copilot.

Thin HTTP layer over the orchestrator. The database is opened once and the schema
is built on first run if the file is missing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .agent import orchestrator as O
from .ingest.loader import build, connect, new_id, summary
from .memory import store as M

app = FastAPI(title="FlytBase Asset Intelligence Copilot", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_con = None


def con():
    global _con
    if _con is None:
        _con = connect()
        try:
            _con.execute("SELECT 1 FROM wells LIMIT 1")
        except Exception:
            build(con=_con, verbose=True)
    return _con


@app.on_event("startup")
def _startup() -> None:
    c = con()
    print("Copilot ready:", summary(c))


class ChatIn(BaseModel):
    user_id: str
    session_id: str
    message: str


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "today": config.TODAY.date().isoformat(),
        "data_dir": str(config.DATA_DIR),
        "llm_enabled": config.LLM_ENABLED,
        "model": config.LLM_MODEL if config.LLM_ENABLED else None,
        "provider": config.LLM_PROVIDER if config.LLM_ENABLED else None,
    }


@app.get("/api/bootstrap")
def bootstrap(user_id: Optional[str] = None) -> Dict[str, Any]:
    c = con()
    users = [
        {"user_id": r[0], "name": r[1], "role": r[2], "team_id": r[3]}
        for r in c.execute("SELECT user_id, name, role, team_id FROM users ORDER BY user_id").fetchall()
    ]
    areas = [
        {"area_id": r[0], "name": r[1]}
        for r in c.execute("SELECT area_id, name FROM areas ORDER BY area_id").fetchall()
    ]
    sessions = []
    if user_id:
        sessions = [
            {"session_id": r[0], "title": r[1], "created_at": r[2]}
            for r in c.execute(
                "SELECT session_id, title, created_at FROM sessions WHERE user_id=? "
                "ORDER BY created_at DESC LIMIT 30",
                [user_id],
            ).fetchall()
        ]
    return {
        "today": config.TODAY.date().isoformat(),
        "llm_enabled": config.LLM_ENABLED,
        "model": config.LLM_MODEL if config.LLM_ENABLED else None,
        "provider": config.LLM_PROVIDER if config.LLM_ENABLED else None,
        "stats": summary(c),
        "users": users,
        "areas": areas,
        "sessions": sessions,
    }


@app.post("/api/chat")
def chat(body: ChatIn) -> Dict[str, Any]:
    c = con()
    reply = O.handle(c, body.user_id, body.session_id, body.message)
    return reply.as_dict()


@app.get("/api/sessions")
def sessions(user_id: str) -> Dict[str, Any]:
    c = con()
    rows = c.execute(
        "SELECT session_id, title, created_at FROM sessions WHERE user_id=? "
        "ORDER BY created_at DESC LIMIT 50",
        [user_id],
    ).fetchall()
    return {"sessions": [
        {"session_id": r[0], "title": r[1], "created_at": r[2]} for r in rows
    ]}


@app.get("/api/sessions/{session_id}/messages")
def messages(session_id: str) -> Dict[str, Any]:
    c = con()
    rows = c.execute(
        "SELECT message_id, role, content, created_at, meta FROM messages "
        "WHERE session_id=? ORDER BY created_at",
        [session_id],
    ).fetchall()
    return {"messages": [
        {"message_id": r[0], "role": r[1], "content": r[2],
         "created_at": r[3], "meta": r[4]} for r in rows
    ]}


@app.get("/api/rules")
def rules(scope_key: Optional[str] = None) -> Dict[str, Any]:
    c = con()
    sql = ("SELECT scope_key, rule_key, value_num, unit, source_doc, is_user_override "
           "FROM v_rule_effective")
    params: list = []
    if scope_key:
        sql += " WHERE scope_key = ?"
        params.append(scope_key)
    sql += " ORDER BY scope_key, rule_key"
    return {"rules": [
        {"scope_key": r[0], "rule_key": r[1], "value": r[2], "unit": r[3],
         "source": r[4], "user_override": r[5]}
        for r in c.execute(sql, params).fetchall()
    ]}


@app.get("/api/assertions")
def assertions(scope: Optional[str] = None, scope_key: Optional[str] = None) -> Dict[str, Any]:
    c = con()
    sql = ("SELECT assertion_id, scope, scope_key, field, value_text, "
           "original_value_text, author_user_id, status, source, created_at, reason "
           "FROM assertions")
    where, params = [], []
    if scope:
        where.append("scope = ?")
        params.append(scope)
    if scope_key:
        where.append("scope_key = ?")
        params.append(scope_key)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT 200"
    return {"assertions": [
        {"assertion_id": r[0], "scope": r[1], "scope_key": r[2], "field": r[3],
         "value": r[4], "original_value": r[5], "author": r[6], "status": r[7],
         "source": r[8], "created_at": r[9], "reason": r[10]}
        for r in c.execute(sql, params).fetchall()
    ]}


@app.post("/api/sessions")
def new_session(user_id: str, title: Optional[str] = None) -> Dict[str, Any]:
    c = con()
    sid = new_id("SES")
    M.ensure_session(c, sid, user_id, title or "New chat")
    return {"session_id": sid, "user_id": user_id}


# --- Serve the built frontend (single-process demo) ------------------------
# `npm run build` in frontend/ produces frontend/dist; when it exists it is
# served here so the whole copilot runs from one process. During development the
# Vite dev server proxies /api to this app instead.
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="ui")

