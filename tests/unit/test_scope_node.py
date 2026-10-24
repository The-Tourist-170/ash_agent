from ash_agent.agents.nodes.scope import make_scope_node
from ash_agent.agents.routing import route_after_scope
from ash_agent.intelligence.issue_scope import ScopeOutcome


def state() -> dict:
    return {
        "run_id": "scope-run",
        "source": "issue",
        "reproduction_results": {
            "failed_tests": [
                "tests/test_calculator.py::test_addition_returns_sum",
                "tests/test_calculator.py::test_subtraction_returns_difference",
            ]
        },
    }


class FakeScope:
    def __init__(self, outcome: ScopeOutcome | None = None, error: Exception | None = None):
        self.outcome = outcome
        self.error = error

    async def determine(self, state) -> ScopeOutcome:
        if self.error:
            raise self.error
        assert self.outcome is not None
        return self.outcome


class TestScopeNode:
    async def test_maps_target_and_unrelated_tests(self) -> None:
        result = await make_scope_node(
            FakeScope(
                ScopeOutcome(
                    target_tests=["tests/test_calculator.py::test_addition_returns_sum"],
                    unrelated_tests=[
                        "tests/test_calculator.py::test_subtraction_returns_difference"
                    ],
                    reasoning="addition issue",
                )
            )
        )(state())

        assert result["target_tests"] == ["tests/test_calculator.py::test_addition_returns_sum"]
        assert result["unrelated_failed_tests"] == [
            "tests/test_calculator.py::test_subtraction_returns_difference"
        ]
        assert result["scope_error"] is None
        assert result["status"] == "patching"

    async def test_empty_scope_sets_error(self) -> None:
        result = await make_scope_node(
            FakeScope(ScopeOutcome([], ["tests/test_x.py::test_x"], "no match"))
        )(state())

        assert result["target_tests"] == []
        assert result["scope_error"] == "could not map the issue to a failing test"

    async def test_scope_failure_sets_error(self) -> None:
        result = await make_scope_node(FakeScope(error=RuntimeError("llm down")))(state())

        assert result["target_tests"] == []
        assert "RuntimeError" in result["scope_error"]

    async def test_default_scope_uses_all_failed_tests(self) -> None:
        result = await make_scope_node(None)(state())

        assert len(result["target_tests"]) == 2
        assert result["scope_error"] is None


class TestScopeRouting:
    def test_targets_go_to_plan(self) -> None:
        assert route_after_scope({"target_tests": ["test_x"], "scope_error": None}) == "plan"

    def test_scope_error_goes_to_escalate(self) -> None:
        assert route_after_scope({"scope_error": "no match"}) == "escalate"
