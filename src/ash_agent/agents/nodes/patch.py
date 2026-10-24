from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from ash_agent.agents.state import AgentState

if TYPE_CHECKING:
    from ash_agent.services.repair_service import PatchOutcome, ValidationOutcome

logger = logging.getLogger(__name__)


class RepairBackend(Protocol):
    async def generate_patch(self, state: AgentState) -> PatchOutcome: ...

    async def validate(self, state: AgentState) -> ValidationOutcome: ...


async def patch(state: AgentState) -> dict:
    attempt = state.get("attempt", 0) + 1
    return {
        "attempt": attempt,
        "patch_diff": f"--- mock patch attempt {attempt} ---\n+ fix\n- bug\n",
        "status": "validating",
    }


def make_patch_node(service: RepairBackend | None):
    async def patch_with_service(state: AgentState) -> dict:
        attempt = state.get("attempt", 0) + 1
        if service is None:
            return await patch(state)

        try:
            outcome = await service.generate_patch(state)
        except Exception as exc:
            logger.warning("patch generation failed: %s", exc)
            message = f"{type(exc).__name__}: {exc}"
            return {
                "attempt": attempt,
                "patch_error": message[:4000],
                "last_error": message[:2000],
                "test_results": {
                    "passed": False,
                    "attempt": attempt,
                    "error": type(exc).__name__,
                },
                "status": "validating",
            }

        return {
            "attempt": attempt,
            "patch_diff": outcome.diff,
            "patch_summary": outcome.summary,
            "patch_edits": outcome.edits,
            "changed_files": outcome.changed_files,
            "patched_files": outcome.files,
            "patch_workspace": outcome.workspace_path,
            "patch_error": None,
            "last_error": None,
            "status": "validating",
        }

    return patch_with_service
