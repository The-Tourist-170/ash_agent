from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from ash_agent.agents.state import AgentState
from ash_agent.config import Settings
from ash_agent.intelligence.embeddings import Embedder, build_embedder
from ash_agent.intelligence.repository_index import build_repository_index
from ash_agent.intelligence.retriever import (
    ScoredChunk,
    build_retrieval_query,
    hybrid_search,
)
from ash_agent.persistence.neo4j import Neo4jCodeGraph
from ash_agent.persistence.qdrant import QdrantCodeIndex
from ash_agent.services.repository_service import Checkout, RepositoryService

logger = logging.getLogger(__name__)

GraphFactory = Callable[[], AbstractAsyncContextManager[Neo4jCodeGraph]]
IndexFactory = Callable[[], AbstractAsyncContextManager[QdrantCodeIndex]]


@dataclass(frozen=True)
class RetrievalOutcome:
    repository: str
    commit_sha: str
    checkout_path: str
    files: list[str]
    chunks: list[dict]
    backend: str
    graph_indexed: bool


class RetrievalService:
    def __init__(
        self,
        settings: Settings,
        *,
        cloner: RepositoryService | None = None,
        embedder: Embedder | None = None,
        graph_factory: GraphFactory | None = None,
        index_factory: IndexFactory | None = None,
    ) -> None:
        self.settings = settings
        self.cloner = cloner or RepositoryService(
            workspace_dir=settings.workspace_dir,
            base_url=settings.github_base_url,
            token=settings.github_token.get_secret_value(),
            timeout_seconds=settings.git_clone_timeout_seconds,
        )
        self.embedder = embedder or build_embedder(settings)
        self.graph_factory = graph_factory
        self.index_factory = index_factory

    async def retrieve(self, state: AgentState) -> RetrievalOutcome:
        checkout = await self.cloner.ensure_checkout(
            repository=state["repository"],
            commit_sha=state.get("commit_sha"),
            ref=state.get("ref"),
        )
        logger.info(
            "checked out %s at %s into %s",
            checkout.repository,
            checkout.commit_sha,
            checkout.path,
        )

        index = build_repository_index(
            checkout.path,
            repository=checkout.repository,
            commit_sha=checkout.commit_sha,
            max_files=self.settings.retrieval_max_files,
            chunk_lines=self.settings.retrieval_chunk_lines,
            chunk_overlap=self.settings.retrieval_chunk_overlap,
        )
        logger.info(
            "indexed %s files into %s chunks",
            len(index.parsed_files),
            len(index.chunks),
        )

        graph_indexed = await self._index_graph(checkout, index.parsed_files)
        vectors = await self._embed_chunks(index.chunks)
        query = build_retrieval_query(state)
        dense_ids = await self._index_and_search_dense(
            checkout,
            index.chunks,
            vectors,
            query,
        )

        results = hybrid_search(
            index.chunks,
            query,
            dense_ids=dense_ids,
            top_k=self.settings.retrieval_top_k,
        )
        files = self._rank_files(results, index.chunks, state)
        backend = "hybrid" if dense_ids else "lexical"
        return RetrievalOutcome(
            repository=checkout.repository,
            commit_sha=checkout.commit_sha,
            checkout_path=str(checkout.path),
            files=files,
            chunks=[_chunk_payload(result) for result in results],
            backend=backend,
            graph_indexed=graph_indexed,
        )

    async def _index_graph(self, checkout: Checkout, parsed_files) -> bool:
        if self.graph_factory is None:
            return False
        try:
            async with self.graph_factory() as graph:
                await graph.replace_commit(checkout.repository, checkout.commit_sha, parsed_files)
            return True
        except Exception as exc:
            logger.warning("Neo4j indexing failed: %s", type(exc).__name__)
            return False

    async def _embed_chunks(self, chunks) -> list[list[float]]:
        if not chunks:
            return []
        try:
            return await self.embedder.embed([chunk.embedding_text for chunk in chunks])
        except Exception as exc:
            logger.warning("embedding failed: %s", type(exc).__name__)
            return []

    async def _index_and_search_dense(
        self,
        checkout: Checkout,
        chunks,
        vectors: list[list[float]],
        query: str,
    ) -> list[str]:
        if self.index_factory is None or not vectors or not query:
            return []
        try:
            async with self.index_factory() as index:
                await index.replace_commit(
                    checkout.repository, checkout.commit_sha, chunks, vectors
                )
                query_vectors = await self.embedder.embed([query])
                if not query_vectors:
                    return []
                return await index.search(
                    checkout.repository,
                    checkout.commit_sha,
                    query_vectors[0],
                    self.settings.retrieval_top_k * 2,
                )
        except Exception as exc:
            logger.warning("Qdrant indexing/search failed: %s", type(exc).__name__)
            return []

    def _rank_files(self, results: list[ScoredChunk], chunks, state: AgentState) -> list[str]:
        paths = {chunk.path for chunk in chunks}
        ordered: list[str] = []
        for path in state.get("suspected_files") or []:
            if path in paths and path not in ordered:
                ordered.append(path)
        for result in results:
            if result.chunk.path not in ordered:
                ordered.append(result.chunk.path)
        return ordered[: self.settings.retrieval_top_k]


def _chunk_payload(result: ScoredChunk) -> dict:
    chunk = result.chunk
    return {
        "path": chunk.path,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "content": chunk.content[:2000],
        "symbols": list(chunk.symbols),
        "kind": chunk.kind,
        "score": round(result.score, 4),
        "sources": list(result.sources),
    }


def build_retrieval_service(settings: Settings) -> RetrievalService | None:
    if not settings.retrieval_enabled:
        return None

    def graph_factory() -> AbstractAsyncContextManager[Neo4jCodeGraph]:
        return Neo4jCodeGraph(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password.get_secret_value(),
            database=settings.neo4j_database,
        )

    def index_factory() -> AbstractAsyncContextManager[QdrantCodeIndex]:
        return QdrantCodeIndex(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value(),
            collection=settings.qdrant_collection,
            dimension=settings.embedding_dimension,
        )

    return RetrievalService(
        settings,
        graph_factory=graph_factory,
        index_factory=index_factory,
    )
