from ash_agent.agents.state import AgentState


async def escalate(state: AgentState) -> dict:
    attempts = state.get("attempt", 0)
    return {
        "status": "escalated",
        "last_error": f"repair budget exhausted after {attempts} attempt(s)",
    }
