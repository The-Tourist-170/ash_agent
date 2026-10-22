from ash_agent.agents.nodes.retrieve import make_retrieve_node
from ash_agent.services.retrieval_service import RetrievalOutcome


def base_state() -> dict:
    return {
        "run_id": "retrieve-node",
        "repository": "The-Tourist-170/dummy_project",
        "ref": "main",
        "commit_sha": None,
        "issue_title": "tax bug",
        "suspected_files": ["calculator.py"],
    }


class FakeRetrieval:
    def __init__(self, outcome: RetrievalOutcome | None = None, error: Exception | None = None):
        self.outcome = outcome
        self.error = error

    async def retrieve(self, state) -> RetrievalOutcome:
        if self.error is not None:
            raise self.error
        assert self.outcome is not None
        return self.outcome


class TestRetrieveNode:
    async def test_maps_retrieval_outcome_into_state(self) -> None:
        outcome = RetrievalOutcome(
            repository="The-Tourist-170/dummy_project",
            commit_sha="a" * 40,
            checkout_path="/tmp/checkout",
            files=["calculator.py"],
            chunks=[{"path": "calculator.py", "content": "def calculate_total(): ..."}],
            backend="hybrid",
            graph_indexed=True,
        )

        result = await make_retrieve_node(FakeRetrieval(outcome))(base_state())

        assert result["suspected_files"] == ["calculator.py"]
        assert result["commit_sha"] == "a" * 40
        assert result["repository_path"] == "/tmp/checkout"
        assert result["retrieval_backend"] == "hybrid"
        assert result["retrieved_chunks"] == outcome.chunks
        assert result["status"] == "retrieving"

    async def test_failure_preserves_existing_hints(self) -> None:
        result = await make_retrieve_node(FakeRetrieval(error=RuntimeError("clone failed")))(
            base_state()
        )

        assert result == {"status": "diagnosing"}
        assert base_state()["suspected_files"] == ["calculator.py"]

    async def test_no_service_uses_mock_path(self) -> None:
        result = await make_retrieve_node(None)(
            {
                "run_id": "no-service",
                "repository": "The-Tourist-170/dummy_project",
                "suspected_files": [],
            }
        )

        assert result["suspected_files"] == ["src/dummy_project/core.py"]

    async def test_empty_outcome_falls_back(self) -> None:
        outcome = RetrievalOutcome(
            repository="repo",
            commit_sha="a" * 40,
            checkout_path="/tmp/checkout",
            files=[],
            chunks=[],
            backend="lexical",
            graph_indexed=False,
        )

        result = await make_retrieve_node(FakeRetrieval(outcome))(base_state())

        assert result == {"status": "diagnosing"}
