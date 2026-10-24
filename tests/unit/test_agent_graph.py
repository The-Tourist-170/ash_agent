import pytest

from ash_agent.agents.graph import build_graph
from ash_agent.integrations.llm import Diagnosis
from ash_agent.intelligence.issue_scope import ScopeOutcome
from ash_agent.sandbox.executor import ReproductionOutcome, RuntimeOutcome
from ash_agent.services.retrieval_service import RetrievalOutcome


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
    async def diagnose(self, system_prompt: str, user_prompt: str) -> Diagnosis:
        return Diagnosis(
            root_cause="tax is applied before the discount",
            suspected_files=["src/dummy_project/core.py"],
            plan="apply discount before tax in calculate_total",
        )


class FakeRetrieval:
    async def retrieve(self, state) -> RetrievalOutcome:
        return RetrievalOutcome(
            repository="The-Tourist-170/dummy_project",
            commit_sha="c" * 40,
            checkout_path="/tmp/dummy-project",
            files=["calculator.py"],
            chunks=[
                {
                    "path": "calculator.py",
                    "start_line": 1,
                    "end_line": 4,
                    "content": "def calculate_total(): ...",
                    "symbols": ["calculate_total"],
                    "kind": "function",
                    "score": 0.03,
                    "sources": ["dense", "lexical"],
                }
            ],
            backend="hybrid",
            graph_indexed=True,
        )


class FailingReproduction:
    async def reproduce(self, state) -> ReproductionOutcome:
        return ReproductionOutcome(
            confirmed=False,
            logs="could not collect tests",
            test_results={"exit_code": 4, "errors": 1},
            backend="docker",
        )


class RuntimeReproduction:
    async def reproduce(self, state) -> ReproductionOutcome:
        return ReproductionOutcome(
            confirmed=False,
            logs="pytest passed",
            test_results={"exit_code": 0, "errors": 0},
            backend="docker",
        )

    async def reproduce_runtime(self, state) -> RuntimeOutcome:
        return RuntimeOutcome(
            confirmed=True,
            logs="NameError: message is not defined",
            results={"mode": "command", "confirmed": True},
            backend="docker",
        )


class RuntimeScope:
    async def determine(self, state) -> ScopeOutcome:
        return ScopeOutcome(
            target_tests=[],
            unrelated_tests=[],
            reasoning="runtime issue",
            scope_files=["scripts/smoke.py"],
            scope_symbols=[],
            validation_mode="command",
            runtime_commands=[["python", "scripts/smoke.py"]],
            runtime_description="runtime command: python scripts/smoke.py",
        )


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

    async def test_graph_uses_repository_retrieval(self) -> None:
        result = await build_graph(success_on_attempt=1, retrieval_service=FakeRetrieval()).ainvoke(
            base_state()
        )

        assert result["suspected_files"] == ["calculator.py"]
        assert result["commit_sha"] == "c" * 40
        assert result["repository_path"] == "/tmp/dummy-project"
        assert result["retrieval_backend"] == "hybrid"
        assert result["retrieved_chunks"][0]["path"] == "calculator.py"
        assert result["status"] == "passed"

    async def test_graph_escalates_when_reproduction_fails(self) -> None:
        result = await build_graph(reproduction_service=FailingReproduction()).ainvoke(base_state())

        assert result["reproduction_confirmed"] is False
        assert result["reproduction_results"]["exit_code"] == 4
        assert result["status"] == "escalated"
        assert "could not reproduce" in result["last_error"]

    async def test_graph_uses_runtime_validation_for_issue(self) -> None:
        result = await build_graph(
            success_on_attempt=1,
            reproduction_service=RuntimeReproduction(),
            scope_service=RuntimeScope(),
        ).ainvoke(
            base_state(
                source="issue",
                issue_number=11,
                issue_title="[BUG] Docker Compose smoke service crashes with NameError",
            )
        )

        assert result["validation_mode"] == "command"
        assert result["runtime_commands"] == [["python", "scripts/smoke.py"]]
        assert result["reproduction_confirmed"] is True
        assert result["status"] == "passed"
