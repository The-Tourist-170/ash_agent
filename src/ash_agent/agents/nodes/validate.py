from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from ash_agent.agents.nodes.patch import RepairBackend
from ash_agent.agents.state import AgentState

logger = logging.getLogger(__name__)


def make_validate_node(
    success_on_attempt: int,
    service: RepairBackend | None = None,
) -> Callable[[AgentState], Awaitable[dict]]:
    async def validate_with_service(state: AgentState) -> dict:
        attempt = state.get("attempt", 0)

        if state.get("patch_error"):
            message = str(state["patch_error"])
            return {
                "test_results": {
                    "passed": False,
                    "attempt": attempt,
                    "error": message[:2000],
                },
                "validation_logs": message[:20_000],
                "status": "validating",
            }

        if service is None:
            passed = success_on_attempt >= 1 and attempt >= success_on_attempt
            return {
                "test_results": {
                    "passed": passed,
                    "attempt": attempt,
                    "summary": "mock tests passed" if passed else "mock tests failed",
                },
                "status": "validating",
            }

        try:
            outcome = await service.validate(state)
        except Exception as exc:
            logger.warning("patch validation failed: %s", type(exc).__name__)
            message = f"{type(exc).__name__}: {exc}"
            return {
                "test_results": {
                    "passed": False,
                    "attempt": attempt,
                    "error": type(exc).__name__,
                },
                "validation_logs": message[:20_000],
                "status": "validating",
            }

        update = {
            "test_results": outcome.test_results,
            "validation_logs": outcome.logs[:20_000],
            "status": "validating",
        }
        if outcome.test_results.get("mode") == "command":
            update["runtime_validation"] = outcome.test_results
        return update

    return validate_with_service
