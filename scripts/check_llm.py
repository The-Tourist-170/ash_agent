#!/usr/bin/env python3
"""Check the configured OpenAI-compatible LLM with a sample diagnosis.

Usage:
    uv run python scripts/check_llm.py

Exit code 0 means the LLM returned a real diagnosis; 1 means it was not
configured or the call fell back to the deterministic mock.
"""

from __future__ import annotations

import asyncio
import json
import sys

from ash_agent.agents.nodes.diagnose import make_llm_diagnose_node
from ash_agent.config import get_settings
from ash_agent.integrations import build_llm_client

SAMPLE_STATE = {
    "run_id": "llm-check",
    "source": "issue",
    "repository": "The-Tourist-170/dummy_project",
    "ref": "main",
    "commit_sha": None,
    "issue_number": 1,
    "issue_title": "[BUG] Calculate tax after applying the discount",
    "issue_body": (
        "calculate_total(subtotal=100, discount=10, tax_rate=0.10) returns 100.0 but expected 99.0."
    ),
    "attempt": 0,
    "max_attempts": 3,
}


async def main() -> int:
    settings = get_settings()
    if not settings.llm_configured:
        print("LLM is not configured (set LLM_BASE_URL and LLM_MODEL in .env)")
        return 1

    print(f"endpoint: {settings.llm_base_url}")
    print(f"model:    {settings.llm_model}")
    print()

    client = build_llm_client(settings)
    result = await make_llm_diagnose_node(client)(SAMPLE_STATE)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if str(result.get("root_cause", "")).startswith("mock diagnosis"):
        print("\nwarning: LLM call failed; mock fallback was used", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
