from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ash_agent.agents.state import AgentState
from ash_agent.intelligence.patch_scope import audit_patch_scope, scope_files_for_state

if TYPE_CHECKING:
    from ash_agent.services.pull_request_service import PullRequestResult

logger = logging.getLogger(__name__)


class PublishBackend(Protocol):
    async def publish(self, state: AgentState) -> PullRequestResult: ...


async def publish(state: AgentState) -> dict:
    return {
        "status": "passed",
        "last_error": None,
    }


def make_publish_node(service: PublishBackend | None):
    async def publish_with_service(state: AgentState) -> dict:
        root = state.get("repository_path")
        edits = state.get("patch_edits") or []
        if root and edits:
            violations = audit_patch_scope(
                edits,
                Path(root),
                scope_files_for_state(state),
                [str(symbol) for symbol in state.get("scope_symbols") or []],
            )
            if violations:
                logger.warning("publish blocked by scope guard: %s", "; ".join(violations))
                return {
                    "status": "escalated",
                    "last_error": ("patch scope violation: " + "; ".join(violations))[:2000],
                    "publish_status": "blocked_scope",
                }

        if service is None:
            return {
                "status": "passed",
                "last_error": None,
                "publish_status": "skipped",
            }

        try:
            result = await service.publish(state)
        except Exception as exc:
            logger.warning("pull request publishing failed: %s", type(exc).__name__)
            return {
                "status": "passed",
                "last_error": None,
                "pr_error": f"{type(exc).__name__}: {exc}"[:2000],
                "publish_status": "failed",
            }

        return {
            "status": "passed",
            "last_error": None,
            "pr_url": result.url,
            "pr_number": result.number,
            "pr_branch": result.branch,
            "publish_status": "published",
        }

    return publish_with_service
