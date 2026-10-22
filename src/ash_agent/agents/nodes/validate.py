from collections.abc import Awaitable, Callable

from ash_agent.agents.state import AgentState


def make_validate_node(success_on_attempt: int) -> Callable[[AgentState], Awaitable[dict]]:
    async def validate(state: AgentState) -> dict:
        attempt = state.get("attempt", 0)
        passed = success_on_attempt >= 1 and attempt >= success_on_attempt
        return {
            "test_results": {
                "passed": passed,
                "attempt": attempt,
                "summary": "mock tests passed" if passed else "mock tests failed",
            },
            "status": "validating",
        }

    return validate
