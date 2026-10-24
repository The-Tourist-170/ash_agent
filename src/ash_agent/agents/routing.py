from typing import Literal

from ash_agent.agents.state import AgentState


def route_after_reproduce(state: AgentState) -> Literal["plan", "escalate"]:
    return "plan" if state.get("reproduction_confirmed") else "escalate"


def route_after_validation(
    state: AgentState,
) -> Literal["publish", "reflect", "escalate"]:
    if state.get("test_results", {}).get("passed"):
        return "publish"
    if state.get("attempt", 0) >= state.get("max_attempts", 3):
        return "escalate"
    return "reflect"
