from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ash_agent.intelligence.chunking import CodeChunk, build_chunks
from ash_agent.intelligence.parser import ParsedFile, parse_python

logger = logging.getLogger(__name__)

_SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".ash_agent",
    }
)


@dataclass(frozen=True)
class RepositoryIndex:
    parsed_files: list[ParsedFile]
    chunks: list[CodeChunk]


def build_repository_index(
    root: str | Path,
    *,
    repository: str,
    commit_sha: str,
    max_files: int = 500,
    max_file_bytes: int = 200_000,
    chunk_lines: int = 60,
    chunk_overlap: int = 10,
) -> RepositoryIndex:
    root_path = Path(root)
    parsed_files: list[ParsedFile] = []

    for path in _python_files(root_path, max_files=max_files, max_file_bytes=max_file_bytes):
        relative = path.relative_to(root_path).as_posix()
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("skipping unreadable file %s: %s", relative, exc)
            continue
        parsed_files.append(parse_python(relative, source))

    chunks: list[CodeChunk] = []
    for parsed in parsed_files:
        chunks.extend(
            build_chunks(
                parsed,
                repository=repository,
                commit_sha=commit_sha,
                max_lines=chunk_lines,
                overlap=chunk_overlap,
            )
        )

    return RepositoryIndex(parsed_files=parsed_files, chunks=chunks)


def _python_files(root: Path, *, max_files: int, max_file_bytes: int):
    count = 0
    for path in sorted(root.rglob("*.py")):
        if count >= max_files:
            logger.warning("file limit reached (%s); skipping remaining files", max_files)
            return
        relative_parts = path.relative_to(root).parts
        if any(part in _SKIP_DIRECTORIES for part in relative_parts[:-1]):
            continue
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_size > max_file_bytes:
                logger.info("skipping large file %s", path.relative_to(root))
                continue
        except OSError:
            continue
        count += 1
        yield path
