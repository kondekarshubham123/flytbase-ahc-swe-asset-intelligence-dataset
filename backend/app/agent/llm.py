"""Thin OpenRouter client with on-disk caching.

The copilot is designed to work with almost no model calls: the LLM is used only
to (a) re-parse a question the deterministic router is unsure about, and
(b) phrase an answer. Both are cached, so repeat runs (and the demo) cost nothing.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

import duckdb
import httpx

from .. import config


def available() -> bool:
    return config.LLM_ENABLED


def _cache_key(model: str, messages: List[dict]) -> str:
    raw = model + "|" + json.dumps(messages, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(con: Optional[duckdb.DuckDBPyConnection], key: str) -> Optional[str]:
    if con is None:
        return None
    row = con.execute("SELECT response FROM llm_cache WHERE cache_key=?", [key]).fetchone()
    return row[0] if row else None


def _cache_put(con: Optional[duckdb.DuckDBPyConnection], key: str, response: str) -> None:
    if con is None:
        return
    con.execute("DELETE FROM llm_cache WHERE cache_key=?", [key])
    con.execute("INSERT INTO llm_cache VALUES (?,?,?)",
                [key, response, config.TODAY.isoformat()])


def chat(messages: List[dict], con: Optional[duckdb.DuckDBPyConnection] = None,
         model: Optional[str] = None, temperature: float = 0.0,
         json_mode: bool = False) -> Optional[str]:
    """Return the assistant text, or None if the LLM is unavailable."""
    if not available():
        return None
    model = model or config.OPENROUTER_MODEL
    key = _cache_key(model, messages)
    cached = _cache_get(con, key)
    if cached is not None:
        return cached

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        resp = httpx.post(
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=config.LLM_TIMEOUT_S,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # network, rate limit, bad key — degrade gracefully
        print(f"[llm] unavailable ({exc}); falling back to the deterministic router")
        return None
    _cache_put(con, key, text)
    return text
