from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from ash_agent.intelligence.chunking import CodeChunk, tokenize


@dataclass(frozen=True)
class ScoredChunk:
    chunk: CodeChunk
    score: float
    sources: tuple[str, ...]


class BM25Index:
    def __init__(self, chunks: list[CodeChunk], *, k1: float = 1.5, b: float = 0.75) -> None:
        self._chunks = {chunk.id: chunk for chunk in chunks}
        self._tokens = {chunk.id: tokenize(chunk.embedding_text) for chunk in chunks}
        self._lengths = {chunk_id: len(tokens) for chunk_id, tokens in self._tokens.items()}
        self._average_length = (
            sum(self._lengths.values()) / len(self._lengths) if self._lengths else 0.0
        )
        self._document_frequency: Counter[str] = Counter()
        for tokens in self._tokens.values():
            self._document_frequency.update(set(tokens))
        self._k1 = k1
        self._b = b

    def search(self, query: str, limit: int) -> list[tuple[str, float]]:
        query_tokens = tokenize(query)
        if not query_tokens or not self._chunks:
            return []

        total = len(self._chunks)
        scores: dict[str, float] = {}
        for chunk_id, tokens in self._tokens.items():
            frequencies = Counter(tokens)
            length = self._lengths[chunk_id]
            score = 0.0
            for token in query_tokens:
                frequency = frequencies.get(token, 0)
                if frequency == 0:
                    continue
                document_frequency = self._document_frequency[token]
                idf = math.log(1 + (total - document_frequency + 0.5) / (document_frequency + 0.5))
                denominator = frequency + self._k1 * (
                    1 - self._b + self._b * length / max(self._average_length, 1.0)
                )
                score += idf * frequency * (self._k1 + 1) / denominator
            if score > 0:
                scores[chunk_id] = score

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:limit]


def hybrid_search(
    chunks: list[CodeChunk],
    query: str,
    *,
    dense_ids: list[str],
    top_k: int,
    rrf_k: int = 60,
) -> list[ScoredChunk]:
    by_id = {chunk.id: chunk for chunk in chunks}
    lexical = BM25Index(chunks).search(query, top_k * 2)
    lexical_ids = [chunk_id for chunk_id, _ in lexical if chunk_id in by_id]

    scores: dict[str, float] = {}
    sources: dict[str, set[str]] = {}
    for rank, chunk_id in enumerate(dense_ids):
        if chunk_id not in by_id:
            continue
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (rrf_k + rank + 1)
        sources.setdefault(chunk_id, set()).add("dense")
    for rank, chunk_id in enumerate(lexical_ids):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (rrf_k + rank + 1)
        sources.setdefault(chunk_id, set()).add("lexical")

    ranked = sorted(
        scores.items(),
        key=lambda item: (-item[1], by_id[item[0]].path, by_id[item[0]].start_line),
    )
    return [
        ScoredChunk(
            chunk=by_id[chunk_id],
            score=score,
            sources=tuple(sorted(sources[chunk_id])),
        )
        for chunk_id, score in ranked[:top_k]
    ]


def build_retrieval_query(state: Mapping[str, object]) -> str:
    parts: list[str] = []
    for key in ("issue_title", "issue_body", "root_cause", "plan"):
        value = state.get(key)
        if value:
            parts.append(str(value))

    suspected = state.get("suspected_files") or []
    if isinstance(suspected, list):
        parts.extend(str(path) for path in suspected if path)

    return "\n".join(parts)[:4000]
