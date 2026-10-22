from ash_agent.intelligence.chunking import CodeChunk
from ash_agent.intelligence.retriever import (
    BM25Index,
    build_retrieval_query,
    hybrid_search,
)


def chunk(identifier: str, path: str, content: str) -> CodeChunk:
    return CodeChunk(
        id=identifier,
        path=path,
        start_line=1,
        end_line=10,
        content=content,
        symbols=(),
        kind="function",
    )


CALC = chunk(
    "calc",
    "calculator.py",
    "def calculate_total(subtotal, discount, tax_rate):\n"
    "    discounted = subtotal - discount\n"
    "    return discounted * (1 + tax_rate)",
)
AUTH = chunk("auth", "auth.py", "def login(username, password): return token")
DOCS = chunk("docs", "README.md", "Project documentation and setup instructions")


class TestBM25:
    def test_ranks_lexical_match_first(self) -> None:
        index = BM25Index([CALC, AUTH, DOCS])

        results = index.search("tax discount calculate", limit=3)

        assert results
        assert results[0][0] == "calc"

    def test_empty_query_returns_nothing(self) -> None:
        assert BM25Index([CALC, AUTH]).search("", limit=3) == []


class TestHybridSearch:
    def test_fuses_dense_and_lexical_rankings(self) -> None:
        results = hybrid_search(
            [CALC, AUTH, DOCS],
            "tax discount",
            dense_ids=["auth", "calc"],
            top_k=3,
        )

        assert results[0].chunk.id == "calc"
        assert "dense" in results[0].sources
        assert "lexical" in results[0].sources

    def test_dense_only_result_is_kept(self) -> None:
        results = hybrid_search([CALC, AUTH], "unrelated", dense_ids=["auth"], top_k=2)

        assert [result.chunk.id for result in results] == ["auth"]
        assert results[0].sources == ("dense",)


class TestQueryBuilder:
    def test_combines_issue_root_cause_and_hints(self) -> None:
        query = build_retrieval_query(
            {
                "issue_title": "tax discount bug",
                "issue_body": "100 vs 99",
                "root_cause": "tax applied first",
                "suspected_files": ["calculator.py"],
            }
        )

        assert "tax discount bug" in query
        assert "tax applied first" in query
        assert "calculator.py" in query
