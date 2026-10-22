from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from ash_agent.intelligence.parser import ParsedFile

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "if",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "with",
    }
)


@dataclass(frozen=True)
class CodeChunk:
    id: str
    path: str
    start_line: int
    end_line: int
    content: str
    symbols: tuple[str, ...]
    kind: str

    @property
    def embedding_text(self) -> str:
        header = f"{self.path} {' '.join(self.symbols)} {self.kind}"
        return f"{header}\n{self.content}"


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN.findall(text):
        for camel_part in _CAMEL.split(raw.replace("-", "_")):
            for part in camel_part.split("_"):
                token = part.lower()
                if len(token) > 1 and token not in _STOPWORDS:
                    tokens.append(token)
    return tokens


def build_chunks(
    parsed: ParsedFile,
    *,
    repository: str,
    commit_sha: str,
    max_lines: int = 60,
    overlap: int = 10,
) -> list[CodeChunk]:
    lines = parsed.source.splitlines()
    chunks: list[CodeChunk] = []

    class_chunks = {symbol.name for symbol in parsed.symbols if symbol.kind == "class"}
    methods = {symbol.parent for symbol in parsed.symbols if symbol.kind == "method"}
    skip_classes = class_chunks & methods

    for symbol in parsed.symbols:
        if symbol.kind == "class" and symbol.name in skip_classes:
            continue
        chunks.extend(
            _split_symbol(
                parsed,
                symbol,
                lines,
                repository=repository,
                commit_sha=commit_sha,
                max_lines=max_lines,
                overlap=overlap,
            )
        )

    first_symbol_line = min((symbol.start_line for symbol in parsed.symbols), default=1)
    header_end = min(max(first_symbol_line - 1, 0), 40)
    if header_end >= 2 and lines:
        header = "\n".join(lines[:header_end])
        if "import " in header or "from " in header:
            chunks.append(
                _make_chunk(
                    repository=repository,
                    commit_sha=commit_sha,
                    path=parsed.path,
                    start_line=1,
                    end_line=header_end,
                    content=header,
                    symbols=(),
                    kind="module",
                )
            )

    if not chunks:
        chunks.extend(
            _split_file(
                parsed,
                lines,
                repository=repository,
                commit_sha=commit_sha,
                max_lines=max_lines,
                overlap=overlap,
            )
        )

    return chunks


def _split_symbol(
    parsed: ParsedFile,
    symbol,
    lines: list[str],
    *,
    repository: str,
    commit_sha: str,
    max_lines: int,
    overlap: int,
) -> list[CodeChunk]:
    chunks: list[CodeChunk] = []
    start = symbol.start_line
    while start <= symbol.end_line:
        end = min(start + max_lines - 1, symbol.end_line)
        content = "\n".join(lines[start - 1 : end])
        chunks.append(
            _make_chunk(
                repository=repository,
                commit_sha=commit_sha,
                path=parsed.path,
                start_line=start,
                end_line=end,
                content=content,
                symbols=(symbol.qualname,),
                kind=symbol.kind,
            )
        )
        if end == symbol.end_line:
            break
        start = max(end - overlap + 1, start + 1)
    return chunks


def _split_file(
    parsed: ParsedFile,
    lines: list[str],
    *,
    repository: str,
    commit_sha: str,
    max_lines: int,
    overlap: int,
) -> list[CodeChunk]:
    chunks: list[CodeChunk] = []
    total = len(lines)
    start = 1
    while start <= total:
        end = min(start + max_lines - 1, total)
        chunks.append(
            _make_chunk(
                repository=repository,
                commit_sha=commit_sha,
                path=parsed.path,
                start_line=start,
                end_line=end,
                content="\n".join(lines[start - 1 : end]),
                symbols=(),
                kind="file",
            )
        )
        if end == total:
            break
        start = max(end - overlap + 1, start + 1)
    return chunks


def _make_chunk(
    *,
    repository: str,
    commit_sha: str,
    path: str,
    start_line: int,
    end_line: int,
    content: str,
    symbols: tuple[str, ...],
    kind: str,
) -> CodeChunk:
    key = f"{repository}:{commit_sha}:{path}:{start_line}:{end_line}:{kind}:{','.join(symbols)}"
    return CodeChunk(
        id=str(uuid5(NAMESPACE_URL, key)),
        path=path,
        start_line=start_line,
        end_line=end_line,
        content=content,
        symbols=symbols,
        kind=kind,
    )
