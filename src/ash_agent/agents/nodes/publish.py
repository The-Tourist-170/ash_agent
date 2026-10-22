from ash_agent.agents.state import AgentState


async def publish(state: AgentState) -> dict:
    return {
        "status": "passed",
        "last_error": None,
    }
