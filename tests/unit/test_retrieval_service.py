from pathlib import Path

import pytest

from ash_agent.config import Settings
from ash_agent.intelligence.embeddings import HashingEmbedder
from ash_agent.services.repository_service import Checkout
from ash_agent.services.retrieval_service import RetrievalService


class FakeCloner:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def ensure_checkout(self, repository, commit_sha=None, ref=None) -> Checkout:
        return Checkout(
            path=self.root,
            repository=repository,
            commit_sha=commit_sha or "a" * 40,
        )


class FakeIndex:
    def __init__(self) -> None:
        self.chunks = []

    async def __aenter__(self) -> "FakeIndex":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def replace_commit(self, repository, commit_sha, chunks, vectors) -> None:
        self.chunks = list(chunks)

    async def search(self, repository, commit_sha, vector, limit) -> list[str]:
        return [chunk.id for chunk in self.chunks if "calculate_total" in chunk.content][:limit]


class FakeGraph:
    def __init__(self) -> None:
        self.indexed: list[tuple[str, str, int]] = []

    async def __aenter__(self) -> "FakeGraph":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def replace_commit(self, repository, commit_sha, parsed_files) -> None:
        self.indexed.append((repository, commit_sha, len(parsed_files)))


class BrokenEmbedder:
    dimension = 384

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding unavailable")


@pytest.fixture
def repository_root(tmp_path: Path) -> Path:
    (tmp_path / "calculator.py").write_text(
        "def apply_tax(subtotal, tax_rate):\n"
        "    return subtotal * (1 + tax_rate)\n\n"
        "def calculate_total(subtotal, discount, tax_rate):\n"
        "    return apply_tax(subtotal - discount, tax_rate)\n"
    )
    (tmp_path / "utils.py").write_text("def helper(value):\n    return value\n")
    (tmp_path / "README.md").write_text("pricing documentation\n")
    return tmp_path


def settings() -> Settings:
    return Settings(
        retrieval_enabled=True,
        embedding_provider="hash",
        embedding_dimension=384,
        retrieval_top_k=5,
        retrieval_chunk_lines=40,
        retrieval_chunk_overlap=5,
    )


def state() -> dict:
    return {
        "run_id": "retrieval-test",
        "repository": "The-Tourist-170/dummy_project",
        "ref": "main",
        "commit_sha": None,
        "issue_title": "calculate_total tax is computed before the discount",
        "issue_body": "Expected 99.0 but got 100.0.",
        "root_cause": "tax is applied to the undiscounted subtotal",
        "plan": "apply the discount before tax in calculate_total",
        "suspected_files": [],
    }


class TestRetrievalService:
    async def test_lexical_retrieval_finds_calculator(self, repository_root: Path) -> None:
        service = RetrievalService(
            settings(),
            cloner=FakeCloner(repository_root),
            embedder=HashingEmbedder(384),
        )

        outcome = await service.retrieve(state())

        assert outcome.files[0] == "calculator.py"
        assert outcome.backend == "lexical"
        assert outcome.chunks
        assert any("calculate_total" in chunk["content"] for chunk in outcome.chunks)
        assert all(chunk["score"] > 0 for chunk in outcome.chunks)

    async def test_hybrid_retrieval_uses_dense_and_lexical(self, repository_root: Path) -> None:
        index = FakeIndex()
        service = RetrievalService(
            settings(),
            cloner=FakeCloner(repository_root),
            embedder=HashingEmbedder(384),
            index_factory=lambda: index,
        )

        outcome = await service.retrieve(state())

        assert outcome.backend == "hybrid"
        assert outcome.files[0] == "calculator.py"
        assert any("dense" in chunk["sources"] for chunk in outcome.chunks)
        assert any("lexical" in chunk["sources"] for chunk in outcome.chunks)

    async def test_graph_indexing_is_reported(self, repository_root: Path) -> None:
        graph = FakeGraph()
        service = RetrievalService(
            settings(),
            cloner=FakeCloner(repository_root),
            embedder=HashingEmbedder(384),
            graph_factory=lambda: graph,
        )

        outcome = await service.retrieve(state())

        assert outcome.graph_indexed is True
        assert graph.indexed == [("The-Tourist-170/dummy_project", "a" * 40, 2)]

    async def test_embedding_failure_falls_back_to_lexical(self, repository_root: Path) -> None:
        index = FakeIndex()
        service = RetrievalService(
            settings(),
            cloner=FakeCloner(repository_root),
            embedder=BrokenEmbedder(),
            index_factory=lambda: index,
        )

        outcome = await service.retrieve(state())

        assert outcome.backend == "lexical"
        assert outcome.files[0] == "calculator.py"
        assert index.chunks == []
