from __future__ import annotations

import logging
from pathlib import PurePosixPath

from neo4j import AsyncDriver, AsyncGraphDatabase

from ash_agent.intelligence.parser import ImportRef, ParsedFile

logger = logging.getLogger(__name__)


class Neo4jCodeGraph:
    def __init__(
        self,
        *,
        uri: str,
        user: str,
        password: str,
        database: str = "neo4j",
        timeout_seconds: float = 15.0,
    ) -> None:
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self.timeout_seconds = timeout_seconds
        self._driver: AsyncDriver | None = None

    async def __aenter__(self) -> Neo4jCodeGraph:
        self._driver = AsyncGraphDatabase.driver(
            self.uri,
            auth=(self.user, self.password),
            connection_timeout=self.timeout_seconds,
        )
        await self._driver.verify_connectivity()
        await self.ensure_constraints()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    async def ensure_constraints(self) -> None:
        statements = [
            "CREATE CONSTRAINT repository_key IF NOT EXISTS "
            "FOR (n:Repository) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT commit_key IF NOT EXISTS FOR (n:Commit) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT file_key IF NOT EXISTS FOR (n:File) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT symbol_key IF NOT EXISTS FOR (n:Symbol) REQUIRE n.key IS UNIQUE",
        ]
        async with self._session() as session:
            for statement in statements:
                await _execute(session, statement)

    async def replace_commit(
        self,
        repository: str,
        commit_sha: str,
        parsed_files: list[ParsedFile],
    ) -> None:
        commit_key = f"{repository}@{commit_sha}"
        files, symbols = _file_and_symbol_rows(repository, commit_sha, parsed_files)
        paths = {row["path"] for row in files}
        import_edges = _import_edges(repository, commit_sha, parsed_files, paths)
        call_edges = _call_edges(repository, commit_sha, parsed_files)

        async with self._session() as session:
            await _execute(
                session,
                "MATCH (n) WHERE n.repository = $repository AND n.commit_sha = $commit_sha "
                "DETACH DELETE n",
                repository=repository,
                commit_sha=commit_sha,
            )
            await _execute(
                session,
                "MERGE (r:Repository {key: $repository}) SET r.name = $repository "
                "MERGE (c:Commit {key: $commit_key}) "
                "SET c.repository = $repository, c.commit_sha = $commit_sha "
                "MERGE (r)-[:HAS_COMMIT]->(c)",
                repository=repository,
                commit_sha=commit_sha,
                commit_key=commit_key,
            )
            await _execute(
                session,
                "UNWIND $files AS row "
                "MERGE (f:File {key: row.key}) "
                "SET f.repository = $repository, f.commit_sha = $commit_sha, f.path = row.path "
                "WITH f MATCH (c:Commit {key: $commit_key}) "
                "MERGE (c)-[:CONTAINS]->(f)",
                files=files,
                repository=repository,
                commit_sha=commit_sha,
                commit_key=commit_key,
            )
            await _execute(
                session,
                "UNWIND $symbols AS row "
                "MERGE (s:Symbol {key: row.key}) "
                "SET s.repository = $repository, s.commit_sha = $commit_sha, "
                "s.name = row.name, s.kind = row.kind, s.path = row.path, "
                "s.start_line = row.start_line, s.end_line = row.end_line, "
                "s.parent = row.parent "
                "WITH s, row MATCH (f:File {key: row.file_key}) "
                "MERGE (f)-[:DEFINES]->(s)",
                symbols=symbols,
                repository=repository,
                commit_sha=commit_sha,
            )
            if import_edges:
                await _execute(
                    session,
                    "UNWIND $edges AS edge "
                    "MATCH (src:File {key: edge.src}), (dst:File {key: edge.dst}) "
                    "MERGE (src)-[:IMPORTS]->(dst)",
                    edges=import_edges,
                )
            if call_edges:
                await _execute(
                    session,
                    "UNWIND $edges AS edge "
                    "MATCH (caller) WHERE (caller:File OR caller:Symbol) "
                    "AND caller.key = edge.caller "
                    "MATCH (callee:Symbol {key: edge.callee}) "
                    "MERGE (caller)-[:CALLS]->(callee)",
                    edges=call_edges,
                )

    async def summary(self, repository: str, commit_sha: str) -> dict[str, int]:
        async with self._session() as session:
            node_result = await session.run(
                "MATCH (n) WHERE n.repository = $repository AND n.commit_sha = $commit_sha "
                "RETURN labels(n)[0] AS label, count(*) AS count",
                repository=repository,
                commit_sha=commit_sha,
            )
            counts = {record["label"]: record["count"] async for record in node_result}
            edge_result = await session.run(
                "MATCH (a)-[r]->(b) "
                "WHERE a.repository = $repository AND a.commit_sha = $commit_sha "
                "AND b.repository = $repository AND b.commit_sha = $commit_sha "
                "RETURN type(r) AS type, count(*) AS count",
                repository=repository,
                commit_sha=commit_sha,
            )
            counts.update(
                {f"rel:{record['type']}": record["count"] async for record in edge_result}
            )
        return counts

    def _session(self):
        if self._driver is None:
            raise RuntimeError("Neo4j driver is not open")
        return self._driver.session(database=self.database)


def _file_and_symbol_rows(
    repository: str, commit_sha: str, parsed_files: list[ParsedFile]
) -> tuple[list[dict], list[dict]]:
    files: list[dict] = []
    symbols: list[dict] = []
    for parsed in parsed_files:
        file_key = _file_key(repository, commit_sha, parsed.path)
        files.append({"key": file_key, "path": parsed.path})
        for symbol in parsed.symbols:
            symbols.append(
                {
                    "key": _symbol_key(
                        repository, commit_sha, parsed.path, symbol.name, symbol.start_line
                    ),
                    "file_key": file_key,
                    "name": symbol.name,
                    "kind": symbol.kind,
                    "path": parsed.path,
                    "start_line": symbol.start_line,
                    "end_line": symbol.end_line,
                    "parent": symbol.parent,
                }
            )
    return files, symbols


def _import_edges(
    repository: str,
    commit_sha: str,
    parsed_files: list[ParsedFile],
    paths: set[str],
) -> list[dict]:
    edges: list[dict] = []
    for parsed in parsed_files:
        source_key = _file_key(repository, commit_sha, parsed.path)
        for import_ref in parsed.imports:
            target = _resolve_import(parsed.path, import_ref, paths)
            if target is None:
                continue
            edges.append({"src": source_key, "dst": _file_key(repository, commit_sha, target)})
    return edges


def _call_edges(repository: str, commit_sha: str, parsed_files: list[ParsedFile]) -> list[dict]:
    symbols_by_name: dict[str, list[str]] = {}
    local_symbols: dict[tuple[str, str], str] = {}
    for parsed in parsed_files:
        for symbol in parsed.symbols:
            key = _symbol_key(repository, commit_sha, parsed.path, symbol.name, symbol.start_line)
            symbols_by_name.setdefault(symbol.name, []).append(key)
            local_symbols[(parsed.path, symbol.local_key)] = key

    edges: list[dict] = []
    for parsed in parsed_files:
        file_key = _file_key(repository, commit_sha, parsed.path)
        for call in parsed.calls:
            targets = symbols_by_name.get(call.name, [])
            if len(targets) != 1:
                continue
            caller = (
                local_symbols.get((parsed.path, call.enclosing))
                if call.enclosing is not None
                else None
            )
            edges.append({"caller": caller or file_key, "callee": targets[0]})
    return edges


def _resolve_import(source_path: str, import_ref: ImportRef, paths: set[str]) -> str | None:
    module_path = import_ref.module.replace(".", "/")

    if import_ref.level:
        base = PurePosixPath(source_path).parent
        for _ in range(import_ref.level - 1):
            base = base.parent
    else:
        base = PurePosixPath()

    candidate = base / module_path if module_path else base
    candidates = [
        f"{candidate.as_posix()}.py",
        f"{(candidate / '__init__').as_posix()}.py",
    ]
    if not module_path:
        candidates.insert(0, (base / "__init__.py").as_posix())

    for path in candidates:
        normalized = PurePosixPath(path).as_posix()
        if normalized in paths:
            return normalized
    return None


def _file_key(repository: str, commit_sha: str, path: str) -> str:
    return f"{repository}@{commit_sha}:{path}"


async def _execute(session, query: str, **parameters) -> None:
    result = await session.run(query, **parameters)
    await result.consume()


def _symbol_key(repository: str, commit_sha: str, path: str, name: str, start_line: int) -> str:
    return f"{repository}@{commit_sha}:{path}:{name}:{start_line}"
