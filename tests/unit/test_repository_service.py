import subprocess
from pathlib import Path

import pytest

from ash_agent.services.repository_service import (
    RepositoryError,
    RepositoryService,
    validate_repository,
)


def git(*arguments: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def remote_repository(tmp_path: Path) -> tuple[Path, str, str]:
    origin = tmp_path / "owner" / "repo.git"
    origin.parent.mkdir(parents=True)
    git("init", "--bare", str(origin), cwd=tmp_path)

    work = tmp_path / "work"
    work.mkdir()
    git("init", cwd=work)
    git("config", "user.email", "test@example.com", cwd=work)
    git("config", "user.name", "Test", cwd=work)
    (work / "calculator.py").write_text("def calculate_total():\n    return 99.0\n")
    git("add", ".", cwd=work)
    git("commit", "-m", "initial", cwd=work)
    git("branch", "-M", "main", cwd=work)
    git("remote", "add", "origin", str(origin), cwd=work)
    git("push", "origin", "main", cwd=work)
    sha = git("rev-parse", "HEAD", cwd=work)
    return tmp_path, sha, "main"


class TestRepositoryValidation:
    def test_accepts_owner_and_name(self) -> None:
        assert validate_repository("The-Tourist-170/dummy_project") == (
            "The-Tourist-170/dummy_project"
        )

    @pytest.mark.parametrize(
        "repository",
        ["", "owner", "owner/", "/repo", "owner/repo/extra", "../repo", "owner/../repo"],
    )
    def test_rejects_invalid_names(self, repository: str) -> None:
        with pytest.raises(RepositoryError):
            validate_repository(repository)


class TestRepositoryCheckout:
    async def test_clones_pinned_commit_and_reuses_checkout(
        self, remote_repository: tuple[Path, str, str], tmp_path: Path
    ) -> None:
        base, sha, _ = remote_repository
        service = RepositoryService(
            workspace_dir=tmp_path / "workspaces",
            base_url=base.as_uri(),
            timeout_seconds=30,
        )

        checkout = await service.ensure_checkout("owner/repo", commit_sha=sha)

        assert checkout.commit_sha == sha
        assert (checkout.path / "calculator.py").read_text().startswith("def calculate_total")

        reused = await service.ensure_checkout("owner/repo", commit_sha=sha)
        assert reused.path == checkout.path

    async def test_resolves_ref_head_when_no_commit_given(
        self, remote_repository: tuple[Path, str, str], tmp_path: Path
    ) -> None:
        base, sha, ref = remote_repository
        service = RepositoryService(
            workspace_dir=tmp_path / "workspaces",
            base_url=base.as_uri(),
            timeout_seconds=30,
        )

        checkout = await service.ensure_checkout("owner/repo", ref=ref)

        assert checkout.commit_sha == sha

    async def test_rejects_invalid_commit_sha(
        self, remote_repository: tuple[Path, str, str], tmp_path: Path
    ) -> None:
        base, _, _ = remote_repository
        service = RepositoryService(
            workspace_dir=tmp_path / "workspaces",
            base_url=base.as_uri(),
        )

        with pytest.raises(RepositoryError, match="invalid commit SHA"):
            await service.ensure_checkout("owner/repo", commit_sha="not-a-sha")
