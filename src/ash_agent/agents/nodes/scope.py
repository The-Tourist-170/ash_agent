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
    return {
        "target_tests": [str(test) for test in failed],
        "unrelated_failed_tests": [],
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
                "scope_error": f"{type(exc).__name__}: {exc}"[:2000],
                "scope_reasoning": "issue scoping failed",
                "status": "patching",
            }

        update = {
            "target_tests": outcome.target_tests,
            "unrelated_failed_tests": outcome.unrelated_tests,
            "scope_reasoning": outcome.reasoning,
            "scope_error": None,
            "status": "patching",
        }
        if not outcome.target_tests:
            update["scope_error"] = "could not map the issue to a failing test"
        return update

    return scope_with_service
