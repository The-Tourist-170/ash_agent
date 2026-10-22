from ash_agent.agents.state import AgentState


async def ingest(state: AgentState) -> dict:
    if not state.get("run_id") or not state.get("repository"):
        raise ValueError("agent state requires run_id and repository")
    return {
        "status": "diagnosing",
        "attempt": state.get("attempt", 0),
        "max_attempts": state.get("max_attempts", 3),
        "failure_logs": state.get("failure_logs", ""),
        "suspected_files": state.get("suspected_files", []),
        "test_results": state.get("test_results", {}),
    }
