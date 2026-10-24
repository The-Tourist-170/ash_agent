from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from ash_agent.sandbox.policies import SandboxError, SandboxPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float
    command: tuple[str, ...]

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


class DockerSandbox:
    def __init__(self, policy: SandboxPolicy) -> None:
        self.policy = policy

    async def available(self) -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                self.policy.docker_binary,
                "image",
                "inspect",
                self.policy.image,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            return False
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()
            return False
        return process.returncode == 0

    async def run(
        self,
        command: list[str],
        workspace: Path,
        *,
        network: str,
        timeout_seconds: int,
    ) -> CommandResult:
        if not await self.available():
            raise SandboxError(
                f"sandbox image {self.policy.image!r} is unavailable; "
                f"build it with: docker build -t {self.policy.image} infra/sandbox"
            )

        container = f"ash-sandbox-{uuid4().hex[:12]}"
        arguments = build_run_args(
            self.policy,
            workspace=workspace,
            command=command,
            container=container,
            network=network,
        )
        started = time.monotonic()

        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise SandboxError(f"could not start docker: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()
            logs = await self._container_logs(container)
            await self._remove(container)
            return CommandResult(
                exit_code=-1,
                stdout=logs,
                stderr=f"sandbox timed out after {timeout_seconds}s",
                timed_out=True,
                duration_seconds=time.monotonic() - started,
                command=tuple(command),
            )

        return CommandResult(
            exit_code=process.returncode if process.returncode is not None else -1,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            timed_out=False,
            duration_seconds=time.monotonic() - started,
            command=tuple(command),
        )

    async def _container_logs(self, container: str) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                self.policy.docker_binary,
                "logs",
                container,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
        except (OSError, TimeoutError):
            return ""
        return stdout.decode("utf-8", errors="replace")

    async def _remove(self, container: str) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                self.policy.docker_binary,
                "rm",
                "-f",
                container,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), timeout=10)
        except (OSError, TimeoutError):
            logger.warning("could not remove sandbox container %s", container)


def build_run_args(
    policy: SandboxPolicy,
    *,
    workspace: Path,
    command: list[str],
    container: str,
    network: str,
) -> list[str]:
    uid = getattr(os, "getuid", lambda: 1000)()
    gid = getattr(os, "getgid", lambda: 1000)()

    return [
        policy.docker_binary,
        "run",
        "--rm",
        "--init",
        "--name",
        container,
        "--pull=never",
        "--network",
        network,
        "--memory",
        policy.memory_limit,
        "--memory-swap",
        policy.memory_limit,
        "--cpus",
        str(policy.cpus),
        "--pids-limit",
        str(policy.pids_limit),
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,size=256m",
        "--ipc",
        "none",
        "--ulimit",
        "nofile=256:256",
        "--user",
        f"{uid}:{gid}",
        "-e",
        "HOME=/tmp",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "PYTHONUNBUFFERED=1",
        "-e",
        "PYTHONPATH=/workspace/src:/workspace",
        "-e",
        "UV_PYTHON=3.12",
        "-e",
        "UV_PYTHON_DOWNLOADS=never",
        "-v",
        f"{workspace.resolve()}:/workspace:rw",
        "-w",
        "/workspace",
        policy.image,
        *command,
    ]
