from pathlib import Path

from ash_agent.sandbox.docker_manager import build_run_args
from ash_agent.sandbox.policies import SandboxPolicy


def policy() -> SandboxPolicy:
    return SandboxPolicy(
        image="ash-agent-sandbox:py312",
        network="none",
        install_network="bridge",
        timeout_seconds=120,
        install_timeout_seconds=180,
        memory_limit="512m",
        cpus=1.0,
        pids_limit=128,
        install_dependencies=True,
        test_args=("-q",),
        keep_workspace=False,
        docker_binary="docker",
    )


class TestDockerRunArgs:
    def test_uses_isolation_and_limits(self, tmp_path: Path) -> None:
        args = build_run_args(
            policy(),
            workspace=tmp_path,
            command=["python", "-m", "pytest"],
            container="sandbox-test",
            network="none",
        )

        assert args[:2] == ["docker", "run"]
        assert args[args.index("--network") + 1] == "none"
        assert "--read-only" in args
        assert args[args.index("--cap-drop") + 1] == "ALL"
        assert args[args.index("--security-opt") + 1] == "no-new-privileges"
        assert args[args.index("--memory") + 1] == "512m"
        assert args[args.index("--pids-limit") + 1] == "128"
        assert args[args.index("--user") + 1].count(":") == 1
        assert args[args.index("--pull=never")] == "--pull=never"

    def test_mounts_only_the_sandbox_workspace(self, tmp_path: Path) -> None:
        args = build_run_args(
            policy(),
            workspace=tmp_path,
            command=["python", "-m", "pytest"],
            container="sandbox-test",
            network="none",
        )

        mounts = [args[index + 1] for index, value in enumerate(args) if value == "-v"]
        assert mounts == [f"{tmp_path.resolve()}:/workspace:rw"]
        assert all("docker.sock" not in mount for mount in mounts)

    def test_does_not_pass_host_environment(self, tmp_path: Path) -> None:
        args = build_run_args(
            policy(),
            workspace=tmp_path,
            command=["python", "-m", "pytest"],
            container="sandbox-test",
            network="none",
        )

        env_values = [args[index + 1] for index, value in enumerate(args) if value == "-e"]
        assert all("TOKEN" not in value and "SECRET" not in value for value in env_values)
        assert "PYTHONPATH=/workspace/src:/workspace" in env_values
