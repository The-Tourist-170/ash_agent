from ash_agent.agents.state import AgentState


async def patch(state: AgentState) -> dict:
    attempt = state.get("attempt", 0) + 1
    return {
        "attempt": attempt,
        "patch_diff": f"--- mock patch attempt {attempt} ---\n+ fix\n- bug\n",
        "status": "validating",
    }
