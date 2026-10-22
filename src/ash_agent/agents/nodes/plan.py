from ash_agent.agents.state import AgentState


async def plan(state: AgentState) -> dict:
    if state.get("plan"):
        return {"status": "patching"}

    suspected = state.get("suspected_files") or ["the suspected module"]
    return {
        "plan": f"mock plan: inspect {suspected[0]} and correct the faulty logic",
        "status": "patching",
    }
