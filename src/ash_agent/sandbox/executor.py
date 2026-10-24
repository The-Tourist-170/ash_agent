from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from ash_agent.config import Settings
from ash_agent.sandbox.docker_manager import CommandResult, DockerSandbox
from ash_agent.sandbox.policies import SandboxPolicy
from ash_agent.services.repository_service import Checkout, RepositoryService

logger = logging.getLogger(__name__)

_IGNORED_PATHS = (
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".ash_agent",
    "node_modules",
    "dist",
    "build",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    ".ssh",
    ".aws",
    ".netrc",
    "credentials*",
    "secrets*",
)

_PYTEST_COUNT = re.compile(
    r"(\d+)[ \t]+(passed|failed|error|errors|skipped|xfailed|xpassed)", re.IGNORECASE
)
_PYTEST_FAILED = re.compile(r"^FAILED\s+(.+?)(?:\s+-\s+.*)?$", re.MULTILINE)


@dataclass(frozen=True)
class ReproductionOutcome:
    confirmed: bool
    logs: str
    test_results: dict
    backend: str


class ReproductionService:
    def __init__(
        self,
        settings: Settings,
        *,
        sandbox: DockerSandbox | None = None,
        cloner: RepositoryService | None = None,
    ) -> None:
        self.settings = settings
        self.policy = SandboxPolicy.from_settings(settings)
        self.sandbox = sandbox or DockerSandbox(self.policy)
        self.cloner = cloner or RepositoryService(
            workspace_dir=settings.workspace_dir,
            base_url=settings.github_base_url,
            token=settings.github_token.get_secret_value(),
            timeout_seconds=settings.git_clone_timeout_seconds,
        )

    async def reproduce(self, state: dict) -> ReproductionOutcome:
        checkout = await self._checkout(state)
        workspace = Path(self.settings.sandbox_workspace_dir) / _safe_name(
            str(state.get("run_id") or "run")
        )
        prepare_workspace_async = asyncio.to_thread(prepare_workspace, checkout.path, workspace)
        await prepare_workspace_async

        install_results: list[CommandResult] = []
        test_result: CommandResult | None = None
        try:
            for command in install_commands(workspace, self.policy):
                result = await self.sandbox.run(
                    command,
                    workspace,
                    network=self.policy.install_network,
                    timeout_seconds=self.policy.install_timeout_seconds,
                )
                install_results.append(result)
                if result.exit_code != 0 or result.timed_out:
                    break

            test_result = await self.sandbox.run(
                test_command(workspace, self.policy),
                workspace,
                network=self.policy.network,
                timeout_seconds=self.policy.timeout_seconds,
            )
        finally:
            if not self.policy.keep_workspace:
                await asyncio.to_thread(shutil.rmtree, workspace, True)

        result = _build_outcome(install_results, test_result, self.policy)
        logger.info(
            "sandbox reproduction: confirmed=%s exit_code=%s failed=%s",
            result.confirmed,
            result.test_results.get("exit_code"),
            result.test_results.get("failed"),
        )
        return result

    async def _checkout(self, state: dict) -> Checkout:
        repository_path = state.get("repository_path")
        repository_exists = bool(repository_path) and await asyncio.to_thread(
            Path(repository_path).exists
        )
        if repository_path and repository_exists:
            return Checkout(
                path=Path(repository_path),
                repository=str(state["repository"]),
                commit_sha=str(state.get("commit_sha") or "unknown"),
            )
        return await self.cloner.ensure_checkout(
            repository=str(state["repository"]),
            commit_sha=state.get("commit_sha"),
            ref=state.get("ref"),
        )


def prepare_workspace(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(*_IGNORED_PATHS),
        symlinks=True,
    )


def install_commands(workspace: Path, policy: SandboxPolicy) -> list[list[str]]:
    if not policy.install_dependencies:
        return []
    if (workspace / "uv.lock").exists() and (workspace / "pyproject.toml").exists():
        return [["uv", "sync", "--frozen", "--extra", "test", "--python", "3.12"]]
    if (workspace / "requirements.txt").exists():
        return [
            ["uv", "venv", ".venv", "--python", "3.12"],
            [
                "uv",
                "pip",
                "install",
                "--python",
                ".venv/bin/python",
                "-r",
                "requirements.txt",
                "pytest",
            ],
        ]
    if (workspace / "pyproject.toml").exists():
        return [["uv", "sync", "--extra", "test", "--python", "3.12"]]
    return []


def test_command(workspace: Path, policy: SandboxPolicy) -> list[str]:
    venv_python = workspace / ".venv/bin/python"
    use_venv = venv_python.exists() or venv_python.is_symlink()
    python = ".venv/bin/python" if use_venv else "python"
    return [python, "-m", "pytest", *policy.test_args]


def parse_pytest(output: str) -> dict:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for match in _PYTEST_COUNT.finditer(output):
        count = int(match.group(1))
        word = match.group(2).lower()
        if word in ("error", "errors"):
            counts["errors"] += count
        elif word in counts:
            counts[word] += count

    failed_tests = [match.group(1).strip() for match in _PYTEST_FAILED.finditer(output)]
    return {**counts, "failed_tests": failed_tests[:50]}


def _build_outcome(
    install_results: list[CommandResult],
    test_result: CommandResult | None,
    policy: SandboxPolicy,
) -> ReproductionOutcome:
    if test_result is None:
        return ReproductionOutcome(
            confirmed=False,
            logs="sandbox did not run tests",
            test_results={"backend": "docker", "sandbox_error": "tests not executed"},
            backend="docker",
        )

    install_ok = all(result.exit_code == 0 and not result.timed_out for result in install_results)
    counts = parse_pytest(test_result.output)
    confirmed = (
        install_ok
        and not test_result.timed_out
        and (
            test_result.exit_code == 1
            or (test_result.exit_code != 0 and (counts["failed"] or counts["errors"]))
        )
    )

    sections = []
    for index, result in enumerate(install_results, start=1):
        sections.append(f"=== install {index}: {' '.join(result.command)} ===")
        sections.append(result.output.strip())
    sections.append(f"=== tests: {' '.join(test_result.command)} ===")
    sections.append(test_result.output.strip())
    logs = "\n".join(section for section in sections if section)[:50_000]

    test_results = {
        "backend": "docker",
        "install_commands": [" ".join(result.command) for result in install_results],
        "install_exit_codes": [result.exit_code for result in install_results],
        "install_ok": install_ok,
        "install_duration_seconds": round(
            sum(result.duration_seconds for result in install_results), 2
        ),
        "test_command": " ".join(test_result.command),
        "exit_code": test_result.exit_code,
        "timed_out": test_result.timed_out,
        "duration_seconds": round(test_result.duration_seconds, 2),
        "network": policy.network,
        **counts,
    }
    return ReproductionOutcome(
        confirmed=confirmed,
        logs=logs,
        test_results=test_results,
        backend="docker",
    )


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value).strip("._") or "run"


def build_reproduction_service(settings: Settings) -> ReproductionService | None:
    if not settings.sandbox_enabled:
        return None
    return ReproductionService(settings)
