from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from ash_agent.agents.state import AgentState

if TYPE_CHECKING:
    from ash_agent.services.retrieval_service import RetrievalOutcome

logger = logging.getLogger(__name__)


class RetrievalBackend(Protocol):
    async def retrieve(self, state: AgentState) -> RetrievalOutcome: ...


async def retrieve(state: AgentState) -> dict:
    return _fallback(state)


def make_retrieve_node(service: RetrievalBackend | None):
    async def retrieve_with_service(state: AgentState) -> dict:
        if service is None:
            return _fallback(state)
        try:
            outcome = await service.retrieve(state)
        except Exception as exc:
            logger.warning("retrieval failed, using fallback: %s", type(exc).__name__)
            return _fallback(state)

        if not outcome.chunks and not outcome.files:
            logger.warning("retrieval returned no context, using fallback")
            return _fallback(state)

        return {
            "suspected_files": outcome.files,
            "retrieved_chunks": outcome.chunks,
            "repository_path": outcome.checkout_path,
            "commit_sha": outcome.commit_sha,
            "retrieval_backend": outcome.backend,
            "status": "retrieving",
        }

    return retrieve_with_service


def _fallback(state: AgentState) -> dict:
    if state.get("suspected_files"):
        return {"status": "diagnosing"}

    repository_name = state.get("repository", "repository").split("/")[-1]
    return {
        "suspected_files": [f"src/{repository_name}/core.py"],
        "status": "diagnosing",
    }
