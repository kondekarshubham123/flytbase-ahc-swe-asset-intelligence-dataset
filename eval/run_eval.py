#!/usr/bin/env python3
"""Deterministic evaluation harness for the Asset Intelligence Copilot.

This is the regression suite the judges can re-run. It runs the copilot
end-to-end against the shipped dataset (or any other dataset pointed at with
``--data-dir``) and checks the facts of every answer. Nothing here calls a
language model, so it is cheap, fast and reproducible.

Usage
-----
    # From the repository root, using the bundled sample data:
    python eval/run_eval.py

    # Against the test dataset released for the final evaluation:
    python eval/run_eval.py --data-dir /path/to/test-data --today 2026-10-16

    # Individual suites:
    python eval/run_eval.py --only scenarios
    python eval/run_eval.py --only tools

Exit code is 0 only when every check passes.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))


def configure_env(args: argparse.Namespace) -> None:
    """Point the app at the requested dataset *before* it is imported."""
    if args.data_dir:
        os.environ["COPILOT_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())
        # Keep the rebuilt database separate per dataset so a test run never
        # clobbers the sample-data database.
        if not args.db_path:
            os.environ["COPILOT_DB_PATH"] = str(
                BACKEND / "data" / f"copilot-{Path(args.data_dir).name}.duckdb"
            )
    if args.db_path:
        os.environ["COPILOT_DB_PATH"] = str(Path(args.db_path).expanduser().resolve())
    if args.today:
        os.environ["COPILOT_TODAY"] = args.today
    # The harness is intentionally LLM-free (works for any provider, including
    # local Ollama which needs no API key).
    os.environ.pop("OPENROUTER_API_KEY", None)
    os.environ["COPILOT_LLM_DISABLE"] = "1"


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the copilot evaluation suite.")
    ap.add_argument("--data-dir", help="Dataset root to evaluate against (default: sample-data).")
    ap.add_argument("--db-path", help="Where to build the DuckDB file.")
    ap.add_argument("--today", help="Override 'today', e.g. 2026-10-16.")
    ap.add_argument("--only", choices=["all", "scenarios", "tools"], default="all",
                    help="Run only one suite.")
    ap.add_argument("--quiet", action="store_true", help="Hide per-check output.")
    args = ap.parse_args()

    configure_env(args)

    # Imports happen after env is set on purpose.
    import app.config as config  # noqa: E402

    print("=" * 72)
    print("Asset Intelligence Copilot — evaluation")
    print("=" * 72)
    print(f"  data dir : {config.DATA_DIR}")
    print(f"  database : {config.DB_PATH}")
    print(f"  today    : {config.TODAY.date().isoformat()}")
    print(f"  llm      : {'on' if config.LLM_ENABLED else 'off (deterministic)'}")
    print()

    # Suite 1: end-to-end multi-turn scenarios (also rebuilds the database).
    scen_passed = scen_total = 0
    if args.only in ("all", "scenarios"):
        from tests import scenarios  # noqa: E402

        print("-" * 72)
        print("Scenario suite — the 18 example questions from 03-evaluation-queries.md")
        print("-" * 72)
        scen_passed, scen_total = scenarios.run(reset=True)
        print()

    # Suite 2: direct analytical tool checks against expected answers.
    tool_passed = tool_total = 0
    if args.only in ("all", "tools"):
        from tests import check_tools  # noqa: E402
        from app.ingest.loader import build, connect  # noqa: E402

        print("-" * 72)
        print("Tool suite — deterministic checks against expected answers")
        print("-" * 72)
        # Always rebuild first: the scenario suite deliberately writes
        # corrections into the database, so the raw-record tool checks must run
        # against a pristine dataset.
        con = connect()
        build(con=con, verbose=not args.quiet)
        con.close()
        tool_passed, tool_total = check_tools.main()
        print()

    passed = scen_passed + tool_passed
    total = scen_total + tool_total
    print("=" * 72)
    print(f"RESULT: {passed}/{total} checks passed")
    print("=" * 72)
    return 0 if total and passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
