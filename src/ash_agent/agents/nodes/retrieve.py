from ash_agent.agents.state import AgentState


async def retrieve(state: AgentState) -> dict:
    if state.get("suspected_files"):
        return {"status": "diagnosing"}

    repository_name = state.get("repository", "repository").split("/")[-1]
    return {
        "suspected_files": [f"src/{repository_name}/core.py"],
        "status": "diagnosing",
    }
