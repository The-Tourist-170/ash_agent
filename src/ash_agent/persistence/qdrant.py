from __future__ import annotations

from collections.abc import Iterable

from qdrant_client import AsyncQdrantClient, models

from ash_agent.intelligence.chunking import CodeChunk


class QdrantCodeIndex:
    def __init__(
        self,
        *,
        url: str,
        api_key: str = "",
        collection: str = "code_chunks",
        dimension: int = 384,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.url = url
        self.api_key = api_key
        self.collection = collection
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds
        self._client: AsyncQdrantClient | None = None

    async def __aenter__(self) -> QdrantCodeIndex:
        self._client = AsyncQdrantClient(
            url=self.url,
            api_key=self.api_key or None,
            timeout=int(self.timeout_seconds),
            check_compatibility=False,
        )
        await self.ensure_collection()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def ensure_collection(self) -> None:
        client = self._client_or_raise()
        if await client.collection_exists(self.collection):
            info = await client.get_collection(self.collection)
            size = info.config.params.vectors.size
            if size != self.dimension:
                raise RuntimeError(
                    f"Qdrant collection {self.collection!r} has dimension {size}, "
                    f"expected {self.dimension}"
                )
            return

        await client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(
                size=self.dimension,
                distance=models.Distance.COSINE,
            ),
        )
        for field in ("repository", "commit_sha", "path"):
            await client.create_payload_index(
                collection_name=self.collection,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    async def replace_commit(
        self,
        repository: str,
        commit_sha: str,
        chunks: list[CodeChunk],
        vectors: list[list[float]],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")

        client = self._client_or_raise()
        point_filter = _filter(repository, commit_sha)
        await client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(filter=point_filter),
            wait=True,
        )

        points = [
            models.PointStruct(
                id=chunk.id,
                vector=vector,
                payload={
                    "repository": repository,
                    "commit_sha": commit_sha,
                    "path": chunk.path,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "content": chunk.content,
                    "symbols": list(chunk.symbols),
                    "kind": chunk.kind,
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=False)
            if vector
        ]
        for batch in _batches(points, 128):
            await client.upsert(
                collection_name=self.collection,
                points=batch,
                wait=True,
            )

    async def search(
        self,
        repository: str,
        commit_sha: str,
        vector: list[float],
        limit: int,
    ) -> list[str]:
        client = self._client_or_raise()
        response = await client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=_filter(repository, commit_sha),
            limit=limit,
            with_payload=False,
        )
        return [str(point.id) for point in response.points]

    async def count(self, repository: str, commit_sha: str) -> int:
        client = self._client_or_raise()
        result = await client.count(
            collection_name=self.collection,
            count_filter=_filter(repository, commit_sha),
            exact=True,
        )
        return result.count

    def _client_or_raise(self) -> AsyncQdrantClient:
        if self._client is None:
            raise RuntimeError("Qdrant client is not open")
        return self._client


def _filter(repository: str, commit_sha: str) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(key="repository", match=models.MatchValue(value=repository)),
            models.FieldCondition(key="commit_sha", match=models.MatchValue(value=commit_sha)),
        ]
    )


def _batches(items: list[models.PointStruct], size: int) -> Iterable[list[models.PointStruct]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
