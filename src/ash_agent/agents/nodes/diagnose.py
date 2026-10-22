from ash_agent.agents.state import AgentState


async def diagnose(state: AgentState) -> dict:
    if state.get("source") == "issue":
        title = state.get("issue_title") or "reported defect"
        subject = f"issue #{state.get('issue_number')}: {title}"
    else:
        subject = f"CI failure at {state.get('commit_sha') or 'unknown commit'}"
    return {
        "root_cause": f"mock diagnosis: {subject}",
        "status": "diagnosing",
    }
