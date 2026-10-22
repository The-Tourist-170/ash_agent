from ash_agent.agents.state import AgentState


async def reproduce(state: AgentState) -> dict:
    return {
        "reproduction_confirmed": True,
        "status": "reproducing",
    }
