from ash_agent.intelligence.issue_scope import IssueScopeService, build_scope_service


class FakeLLM:
    def __init__(self, target_tests: list[str] | None = None, error: Exception | None = None):
        self.target_tests = target_tests or []
        self.error = error

    async def complete_structured(self, schema, system_prompt, user_prompt):
        if self.error:
            raise self.error
        return schema(target_tests=self.target_tests, reasoning="test scope")


def state(
    source: str = "issue",
    failed: list[str] | None = None,
    title: str = "[BUG] Addition subtracts instead of adding",
) -> dict:
    return {
        "source": source,
        "issue_title": title,
        "issue_body": "add(2, 3) returns -1",
        "root_cause": "add uses subtraction",
        "reproduction_results": {
            "failed_tests": failed
            or [
                "tests/test_calculator.py::test_addition_returns_sum",
                "tests/test_calculator.py::test_subtraction_returns_difference",
                "tests/test_calculator.py::test_multiplication_returns_product",
                "tests/test_calculator.py::test_division_preserves_fractional_result",
            ]
        },
        "retrieved_chunks": [],
    }


class TestIssueScopeService:
    async def test_llm_selects_only_issue_related_test(self) -> None:
        llm = FakeLLM(["tests/test_calculator.py::test_addition_returns_sum"])
        outcome = await IssueScopeService(llm).determine(state())

        assert outcome.target_tests == ["tests/test_calculator.py::test_addition_returns_sum"]
        assert "tests/test_calculator.py::test_subtraction_returns_difference" in (
            outcome.unrelated_tests
        )
        assert outcome.reasoning == "test scope"

    async def test_lexical_fallback_selects_addition(self) -> None:
        llm = FakeLLM(error=RuntimeError("llm down"))
        outcome = await IssueScopeService(llm).determine(state())

        assert outcome.target_tests == ["tests/test_calculator.py::test_addition_returns_sum"]

    async def test_workflow_run_scopes_all_failures(self) -> None:
        outcome = await IssueScopeService(FakeLLM()).determine(
            state(source="workflow_run", title="CI failure")
        )

        assert len(outcome.target_tests) == 4
        assert outcome.unrelated_tests == []

    async def test_unmappable_issue_returns_no_target(self) -> None:
        failed = [
            "tests/test_calculator.py::test_division_by_zero_is_reported",
            "tests/test_calculator.py::test_binary_operation_requires_two_operands",
        ]
        outcome = await IssueScopeService(None).determine(
            state(title="[BUG] something entirely unrelated", failed=failed)
        )

        assert outcome.target_tests == []
        assert outcome.unrelated_tests == failed

    async def test_build_scope_service_requires_llm(self) -> None:
        assert build_scope_service(None) is None
