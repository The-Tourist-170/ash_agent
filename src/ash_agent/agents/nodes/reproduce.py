from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from ash_agent.agents.state import AgentState

if TYPE_CHECKING:
    from ash_agent.sandbox.executor import ReproductionOutcome

logger = logging.getLogger(__name__)


class ReproductionBackend(Protocol):
    async def reproduce(self, state: AgentState) -> ReproductionOutcome: ...


async def reproduce(state: AgentState) -> dict:
    return {
        "reproduction_confirmed": True,
        "status": "reproducing",
    }


def make_reproduce_node(service: ReproductionBackend | None):
    async def reproduce_with_service(state: AgentState) -> dict:
        if service is None:
            return await reproduce(state)

        try:
            outcome = await service.reproduce(state)
        except Exception as exc:
            logger.warning("sandbox reproduction failed: %s", type(exc).__name__)
            return {
                "reproduction_confirmed": False,
                "failure_logs": f"sandbox error: {type(exc).__name__}: {exc}"[:20_000],
                "reproduction_results": {
                    "backend": "docker",
                    "sandbox_error": type(exc).__name__,
                },
                "status": "reproducing",
            }

        return {
            "reproduction_confirmed": outcome.confirmed,
            "failure_logs": outcome.logs[:20_000],
            "reproduction_results": outcome.test_results,
            "status": "reproducing",
        }

    return reproduce_with_service
