import pytest

from ash_agent.agents.graph import build_graph


def base_state(**overrides) -> dict:
    state = {
        "run_id": "test-run",
        "source": "workflow_run",
        "repository": "The-Tourist-170/dummy_project",
        "ref": "main",
        "commit_sha": "a" * 40,
        "issue_number": None,
        "issue_title": None,
        "issue_body": None,
        "attempt": 0,
        "max_attempts": 3,
    }
    state.update(overrides)
    return state


class FakeLLM:
    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        return {
            "root_cause": "tax is applied before the discount",
            "suspected_files": ["src/dummy_project/core.py"],
            "plan": "apply discount before tax in calculate_total",
        }


class TestAgentGraph:
    async def test_passes_on_first_attempt(self) -> None:
        result = await build_graph(success_on_attempt=1).ainvoke(base_state())

        assert result["status"] == "passed"
        assert result["attempt"] == 1
        assert result["root_cause"].startswith("mock diagnosis")
        assert result["reproduction_confirmed"] is True
        assert result["test_results"]["passed"] is True
        assert result["last_error"] is None

    async def test_retries_until_validation_passes(self) -> None:
        result = await build_graph(success_on_attempt=2).ainvoke(base_state())

        assert result["status"] == "passed"
        assert result["attempt"] == 2
        assert result["test_results"]["attempt"] == 2

    async def test_escalates_when_retry_budget_is_exhausted(self) -> None:
        result = await build_graph(success_on_attempt=99).ainvoke(base_state(max_attempts=3))

        assert result["status"] == "escalated"
        assert result["attempt"] == 3
        assert "budget exhausted" in result["last_error"]

    async def test_respects_custom_budget(self) -> None:
        result = await build_graph(success_on_attempt=99).ainvoke(base_state(max_attempts=2))

        assert result["status"] == "escalated"
        assert result["attempt"] == 2

    async def test_issue_source_is_diagnosed_from_issue_metadata(self) -> None:
        result = await build_graph(success_on_attempt=1).ainvoke(
            base_state(
                source="issue",
                commit_sha=None,
                issue_number=7,
                issue_title="[BUG] Calculate tax",
            )
        )

        assert "issue #7" in result["root_cause"]
        assert result["suspected_files"] == ["src/dummy_project/core.py"]

    async def test_ingest_rejects_incomplete_state(self) -> None:
        with pytest.raises(ValueError, match="run_id and repository"):
            await build_graph().ainvoke({"run_id": "only-id"})

    async def test_graph_uses_llm_diagnosis_and_plan(self) -> None:
        result = await build_graph(success_on_attempt=1, llm_client=FakeLLM()).ainvoke(base_state())

        assert result["root_cause"] == "tax is applied before the discount"
        assert result["suspected_files"] == ["src/dummy_project/core.py"]
        assert result["plan"] == "apply discount before tax in calculate_total"
        assert result["status"] == "passed"
