from pathlib import Path

from ash_agent.config import Settings
from ash_agent.sandbox.docker_manager import CommandResult
from ash_agent.sandbox.executor import (
    ReproductionService,
    install_commands,
    parse_pytest,
    prepare_workspace,
)
from ash_agent.sandbox.executor import (
    test_command as build_test_command,
)
from ash_agent.sandbox.policies import SandboxPolicy
from ash_agent.services.repository_service import Checkout


def policy(**overrides) -> SandboxPolicy:
    values = {
        "image": "ash-agent-sandbox:py312",
        "network": "none",
        "install_network": "bridge",
        "timeout_seconds": 120,
        "install_timeout_seconds": 180,
        "memory_limit": "512m",
        "cpus": 1.0,
        "pids_limit": 128,
        "install_dependencies": True,
        "test_args": ("-q", "--maxfail=5"),
        "keep_workspace": False,
        "docker_binary": "docker",
    }
    values.update(overrides)
    return SandboxPolicy(**values)


class FakeSandbox:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = iter(results)
        self.calls: list[tuple[tuple[str, ...], str]] = []

    async def run(self, command, workspace, *, network, timeout_seconds):
        self.calls.append((tuple(command), network))
        return next(self.results)


class FakeCloner:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def ensure_checkout(self, repository, commit_sha=None, ref=None) -> Checkout:
        return Checkout(path=self.root, repository=repository, commit_sha=commit_sha or "a" * 40)


def result(
    exit_code: int,
    *,
    stdout: str = "",
    timed_out: bool = False,
    command: tuple[str, ...] = ("python", "-m", "pytest"),
) -> CommandResult:
    return CommandResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        timed_out=timed_out,
        duration_seconds=0.5,
        command=command,
    )


def settings(tmp_path: Path) -> Settings:
    return Settings(
        sandbox_enabled=True,
        sandbox_workspace_dir=str(tmp_path / "sandboxes"),
        sandbox_keep_workspace=False,
        sandbox_install_dependencies=True,
    )


class TestPrepareWorkspace:
    def test_excludes_secrets_and_caches(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        (source / ".git").mkdir(parents=True)
        (source / ".git" / "config").write_text("secret")
        (source / ".venv" / "bin").mkdir(parents=True)
        (source / ".venv" / "bin" / "python").write_text("")
        (source / "src").mkdir()
        (source / "src" / "core.py").write_text("VALUE = 1\n")
        (source / ".env").write_text("TOKEN=secret")
        (source / "id_rsa").write_text("secret")
        (source / "README.md").write_text("hello")

        target = tmp_path / "target"
        prepare_workspace(source, target)

        assert (target / "src" / "core.py").exists()
        assert (target / "README.md").exists()
        assert not (target / ".git").exists()
        assert not (target / ".venv").exists()
        assert not (target / ".env").exists()
        assert not (target / "id_rsa").exists()


class TestCommands:
    def test_uv_lock_uses_frozen_sync(self, tmp_path: Path) -> None:
        (tmp_path / "uv.lock").write_text("")
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")

        commands = install_commands(tmp_path, policy())

        assert commands == [["uv", "sync", "--frozen", "--extra", "test", "--python", "3.12"]]

    def test_requirements_uses_workspace_venv(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("requests\n")

        commands = install_commands(tmp_path, policy())

        assert commands[0] == ["uv", "venv", ".venv", "--python", "3.12"]
        assert "--python" in commands[1]
        assert "pytest" in commands[1]

    def test_no_dependency_file_skips_install(self, tmp_path: Path) -> None:
        assert install_commands(tmp_path, policy()) == []

    def test_uses_venv_python_when_present(self, tmp_path: Path) -> None:
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "python").write_text("")

        command = build_test_command(tmp_path, policy())

        assert command[:2] == [".venv/bin/python", "-m"]


class TestPytestParsing:
    def test_parses_counts_and_failed_tests(self) -> None:
        output = (
            "FAILED tests/test_core.py::test_discount - AssertionError\n"
            "FAILED tests/test_core.py::test_pages\n"
            "3 failed, 3 passed, 1 skipped in 0.12s\n"
        )

        parsed = parse_pytest(output)

        assert parsed["failed"] == 3
        assert parsed["passed"] == 3
        assert parsed["skipped"] == 1
        assert len(parsed["failed_tests"]) == 2


class TestReproductionService:
    async def test_reproduces_failing_tests(self, tmp_path: Path) -> None:
        source = tmp_path / "repo"
        source.mkdir()
        (source / "pyproject.toml").write_text("[project]\nname='x'\n")
        (source / "uv.lock").write_text("")
        sandbox = FakeSandbox(
            [
                result(0, command=("uv", "sync")),
                result(
                    1,
                    stdout="1 failed, 1 passed in 0.10s\nFAILED tests/test_core.py::test_x",
                    command=("python", "-m", "pytest"),
                ),
            ]
        )
        service = ReproductionService(
            settings(tmp_path),
            sandbox=sandbox,
            cloner=FakeCloner(source),
        )

        outcome = await service.reproduce(
            {
                "run_id": "run-1",
                "repository": "owner/repo",
                "commit_sha": "a" * 40,
                "repository_path": str(source),
            }
        )

        assert outcome.confirmed is True
        assert outcome.test_results["failed"] == 1
        assert outcome.test_results["install_ok"] is True
        assert "FAILED tests/test_core.py" in outcome.logs
        assert [call[1] for call in sandbox.calls] == ["bridge", "none"]
        assert not (tmp_path / "sandboxes" / "run-1").exists()

    async def test_install_failure_is_not_confirmed(self, tmp_path: Path) -> None:
        source = tmp_path / "repo"
        source.mkdir()
        (source / "uv.lock").write_text("")
        (source / "pyproject.toml").write_text("[project]\nname='x'\n")
        sandbox = FakeSandbox(
            [
                result(1, stdout="resolution failed"),
                result(1, stdout="1 failed"),
            ]
        )
        service = ReproductionService(
            settings(tmp_path),
            sandbox=sandbox,
            cloner=FakeCloner(source),
        )

        outcome = await service.reproduce(
            {"run_id": "run-2", "repository": "owner/repo", "repository_path": str(source)}
        )

        assert outcome.confirmed is False
        assert outcome.test_results["install_ok"] is False

    async def test_timeout_is_not_confirmed(self, tmp_path: Path) -> None:
        source = tmp_path / "repo"
        source.mkdir()
        (source / "test_x.py").write_text("def test_x(): assert False\n")
        sandbox = FakeSandbox([result(-1, timed_out=True)])
        service = ReproductionService(
            settings(tmp_path),
            sandbox=sandbox,
            cloner=FakeCloner(source),
        )

        outcome = await service.reproduce(
            {"run_id": "run-3", "repository": "owner/repo", "repository_path": str(source)}
        )

        assert outcome.confirmed is False
        assert outcome.test_results["timed_out"] is True
