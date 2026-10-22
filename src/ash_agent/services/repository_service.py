from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")


class RepositoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Checkout:
    path: Path
    repository: str
    commit_sha: str


def validate_repository(repository: str) -> str:
    if not REPOSITORY_PATTERN.fullmatch(repository) or ".." in repository:
        raise RepositoryError(f"invalid repository name: {repository!r}")
    return repository


class RepositoryService:
    def __init__(
        self,
        *,
        workspace_dir: str | Path,
        base_url: str = "https://github.com",
        token: str = "",
        timeout_seconds: int = 120,
    ) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self._locks: dict[str, asyncio.Lock] = {}

    async def ensure_checkout(
        self,
        repository: str,
        commit_sha: str | None = None,
        ref: str | None = None,
    ) -> Checkout:
        repository = validate_repository(repository)
        if commit_sha is not None and not SHA_PATTERN.fullmatch(commit_sha):
            raise RepositoryError(f"invalid commit SHA: {commit_sha!r}")

        target = commit_sha or await self._resolve_head(repository, ref)
        path = self.workspace_dir / self._directory_name(repository, target)

        async with self._lock(path):
            if await self._is_checkout_at(path, target):
                return Checkout(path=path, repository=repository, commit_sha=target)

            self.workspace_dir.mkdir(parents=True, exist_ok=True)
            temporary = path.parent / f"{path.name}.tmp-{uuid4().hex[:8]}"
            try:
                await self._clone(repository, target, temporary)
                if path.exists():
                    await asyncio.to_thread(shutil.rmtree, path)
                os.replace(temporary, path)
            except Exception:
                if temporary.exists():
                    await asyncio.to_thread(shutil.rmtree, temporary, True)
                raise

        resolved = await self._rev_parse(path, "HEAD")
        return Checkout(path=path, repository=repository, commit_sha=resolved)

    def _lock(self, path: Path) -> asyncio.Lock:
        return self._locks.setdefault(str(path), asyncio.Lock())

    def _directory_name(self, repository: str, commit_sha: str) -> str:
        return f"{repository.replace('/', '__')}__{commit_sha[:12]}"

    def _url(self, repository: str) -> str:
        return f"{self.base_url}/{repository}.git"

    async def _resolve_head(self, repository: str, ref: str | None) -> str:
        target_ref = f"refs/heads/{ref}" if ref else "HEAD"
        output = await self._run_git(["ls-remote", self._url(repository), target_ref])
        for line in output.splitlines():
            sha, _, name = line.partition("\t")
            if name.strip() == target_ref and SHA_PATTERN.fullmatch(sha.strip()):
                return sha.strip()
        raise RepositoryError(f"could not resolve {target_ref} for {repository}")

    async def _is_checkout_at(self, path: Path, commit_sha: str) -> bool:
        if not (path / ".git").exists():
            return False
        try:
            head = await self._rev_parse(path, "HEAD")
        except RepositoryError:
            return False
        return head.lower().startswith(commit_sha.lower())

    async def _clone(self, repository: str, commit_sha: str, destination: Path) -> None:
        await self._run_git(
            [
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                "--no-tags",
                self._url(repository),
                str(destination),
            ]
        )
        try:
            await self._run_git(["checkout", "--detach", commit_sha], cwd=destination)
        except RepositoryError:
            await self._run_git(["fetch", "--no-tags", "origin", commit_sha], cwd=destination)
            await self._run_git(["checkout", "--detach", commit_sha], cwd=destination)

        resolved = await self._rev_parse(destination, "HEAD")
        if not resolved.lower().startswith(commit_sha.lower()):
            raise RepositoryError(f"checkout mismatch: wanted {commit_sha}, got {resolved}")

    async def _rev_parse(self, path: Path, revision: str) -> str:
        return (await self._run_git(["rev-parse", revision], cwd=path)).strip()

    async def _run_git(self, arguments: list[str], cwd: Path | None = None) -> str:
        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"
        if self.token:
            environment["GIT_CONFIG_COUNT"] = "1"
            environment["GIT_CONFIG_KEY_0"] = "http.extraHeader"
            environment["GIT_CONFIG_VALUE_0"] = f"Authorization: Bearer {self.token}"

        process = await asyncio.create_subprocess_exec(
            "git",
            *arguments,
            cwd=str(cwd) if cwd is not None else None,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RepositoryError(
                f"git {arguments[0]} timed out after {self.timeout_seconds}s"
            ) from None

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[-500:]
            raise RepositoryError(f"git {arguments[0]} failed: {detail}")
        return stdout.decode("utf-8", errors="replace")
