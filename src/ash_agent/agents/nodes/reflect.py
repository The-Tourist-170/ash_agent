from ash_agent.agents.state import AgentState


async def reflect(state: AgentState) -> dict:
    attempt = state.get("attempt", 0)
    return {
        "last_error": f"validation failed on attempt {attempt}",
        "status": "patching",
    }
