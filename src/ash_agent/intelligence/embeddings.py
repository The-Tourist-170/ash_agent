from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Protocol

from ash_agent.config import Settings

_DIMENSION = 384


class Embedder(Protocol):
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Deterministic fallback used when a local model is unavailable."""

    def __init__(self, dimension: int = _DIMENSION) -> None:
        self.dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in text.lower().split():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest, "big") % self.dimension
            vector[bucket] += 1.0
        norm = sum(value * value for value in vector) ** 0.5
        if norm:
            return [value / norm for value in vector]
        return vector


class FastEmbedder:
    def __init__(
        self,
        *,
        model_name: str,
        cache_dir: str | Path,
        dimension: int = _DIMENSION,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = str(cache_dir)
        self.dimension = dimension
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(
                model_name=self.model_name,
                cache_dir=self.cache_dir,
            )
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._embed_sync, texts)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        return [list(map(float, vector)) for vector in model.embed(texts)]


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider.lower() == "hash":
        return HashingEmbedder(settings.embedding_dimension)
    return FastEmbedder(
        model_name=settings.embedding_model,
        cache_dir=settings.embedding_cache_dir,
        dimension=settings.embedding_dimension,
    )
