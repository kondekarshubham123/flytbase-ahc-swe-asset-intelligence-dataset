"""Central configuration for the Asset Intelligence Copilot.

Everything that could change between the sample dataset and the test dataset lives
here so the system can be re-pointed with a single environment variable.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass

# --- Paths -----------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_DATA_DIR = REPO_ROOT / "sample-data"
DATA_DIR = Path(os.environ.get("COPILOT_DATA_DIR", DEFAULT_DATA_DIR))
DATA_VERSION = os.environ.get("COPILOT_DATA_VERSION", DATA_DIR.name)

DB_PATH = Path(os.environ.get("COPILOT_DB_PATH", BACKEND_DIR / "data" / "copilot.duckdb"))
CACHE_DIR = Path(os.environ.get("COPILOT_CACHE_DIR", BACKEND_DIR / "data" / "cache"))

# --- Time ------------------------------------------------------------------
# "Today" is fixed for the challenge dataset. The test dataset ships its own
# "today", so it can be overridden with COPILOT_TODAY.
TODAY = datetime.fromisoformat(
    os.environ.get("COPILOT_TODAY", "2026-10-16T00:00:00+00:00")
).astimezone(timezone.utc)
if TODAY.tzinfo is None:  # pragma: no cover
    TODAY = TODAY.replace(tzinfo=timezone.utc)

# --- LLM -------------------------------------------------------------------
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemma-4-31b-it:free")
# When no key is configured the system runs fully deterministically (keyword + SQL
# routing only). This is what keeps the copilot alive on a rate-limited free tier.
LLM_ENABLED = bool(OPENROUTER_API_KEY)
LLM_TIMEOUT_S = float(os.environ.get("COPILOT_LLM_TIMEOUT", "40"))

# --- Reporting units -------------------------------------------------------
DOWNTIME_DAYS_PER_INSPECTION = 7  # per the Dataset Guide
SPILL_REPORTABLE_BBL = 5.0

# Seeded challenge users (fleet/users.json covers these; kept as a fallback).
DEFAULT_USER_ID = os.environ.get("COPILOT_DEFAULT_USER", "U-201")
