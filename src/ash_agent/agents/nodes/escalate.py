from ash_agent.agents.state import AgentState


async def escalate(state: AgentState) -> dict:
    if state.get("scope_error"):
        return {
            "status": "escalated",
            "last_error": f"could not scope the issue: {state['scope_error']}",
        }

    if not state.get("reproduction_confirmed", True):
        return {
            "status": "escalated",
            "last_error": "could not reproduce the reported failure in the sandbox",
        }

    attempts = state.get("attempt", 0)
    return {
        "status": "escalated",
        "last_error": f"repair budget exhausted after {attempts} attempt(s)",
    }
