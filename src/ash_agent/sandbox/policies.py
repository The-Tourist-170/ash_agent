from __future__ import annotations

import shlex
from dataclasses import dataclass

from ash_agent.config import Settings


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxPolicy:
    image: str
    network: str
    install_network: str
    timeout_seconds: int
    install_timeout_seconds: int
    memory_limit: str
    cpus: float
    pids_limit: int
    install_dependencies: bool
    test_args: tuple[str, ...]
    keep_workspace: bool
    docker_binary: str

    @classmethod
    def from_settings(cls, settings: Settings) -> SandboxPolicy:
        return cls(
            image=settings.sandbox_image,
            network=settings.sandbox_network,
            install_network=settings.sandbox_install_network,
            timeout_seconds=settings.sandbox_timeout_seconds,
            install_timeout_seconds=settings.sandbox_install_timeout_seconds,
            memory_limit=settings.sandbox_memory_limit,
            cpus=settings.sandbox_cpus,
            pids_limit=settings.sandbox_pids_limit,
            install_dependencies=settings.sandbox_install_dependencies,
            test_args=tuple(shlex.split(settings.sandbox_test_args)),
            keep_workspace=settings.sandbox_keep_workspace,
            docker_binary=settings.docker_binary,
        )
