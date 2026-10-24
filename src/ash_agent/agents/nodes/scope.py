from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from ash_agent.agents.state import AgentState

if TYPE_CHECKING:
    from ash_agent.intelligence.issue_scope import ScopeOutcome

logger = logging.getLogger(__name__)


class ScopeBackend(Protocol):
    async def determine(self, state: AgentState) -> ScopeOutcome: ...


async def scope(state: AgentState) -> dict:
    failed = (state.get("reproduction_results") or {}).get("failed_tests") or []
    suspected = [str(path) for path in state.get("suspected_files") or []]
    return {
        "target_tests": [str(test) for test in failed],
        "unrelated_failed_tests": [],
        "scope_files": [path for path in suspected if not path.startswith("tests/")],
        "scope_symbols": [],
        "validation_mode": "pytest",
        "runtime_commands": [],
        "runtime_expected_exit": 0,
        "runtime_expected_output": None,
        "runtime_description": "",
        "scope_reasoning": "mock scope: all failing tests",
        "scope_error": None,
        "status": "patching",
    }


def make_scope_node(service: ScopeBackend | None):
    async def scope_with_service(state: AgentState) -> dict:
        if service is None:
            return await scope(state)

        try:
            outcome = await service.determine(state)
        except Exception as exc:
            logger.warning("issue scoping failed: %s", type(exc).__name__)
            return {
                "target_tests": [],
                "unrelated_failed_tests": [],
                "scope_files": [],
                "scope_symbols": [],
                "validation_mode": "pytest",
                "runtime_commands": [],
                "runtime_expected_exit": 0,
                "runtime_expected_output": None,
                "runtime_description": "",
                "scope_error": f"{type(exc).__name__}: {exc}"[:2000],
                "scope_reasoning": "issue scoping failed",
                "status": "patching",
            }

        update = {
            "target_tests": outcome.target_tests,
            "unrelated_failed_tests": outcome.unrelated_tests,
            "scope_files": outcome.scope_files,
            "scope_symbols": outcome.scope_symbols,
            "validation_mode": outcome.validation_mode,
            "runtime_commands": outcome.runtime_commands,
            "runtime_expected_exit": outcome.runtime_expected_exit,
            "runtime_expected_output": outcome.runtime_expected_output,
            "runtime_description": outcome.runtime_description,
            "scope_reasoning": outcome.reasoning,
            "scope_error": None,
            "status": "patching",
        }
        if not outcome.target_tests and outcome.validation_mode != "command":
            update["scope_error"] = "could not map the issue to a failing test"
        return update

    return scope_with_service
