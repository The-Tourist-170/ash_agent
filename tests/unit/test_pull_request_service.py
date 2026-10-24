import httpx
import pytest
from pydantic import SecretStr

from ash_agent.config import Settings
from ash_agent.services.pull_request_service import (
    GitHubPullRequestService,
    PullRequestError,
    build_pull_request_service,
)


def state() -> dict:
    return {
        "run_id": "run-12345678",
        "repository": "owner/repo",
        "ref": "main",
        "commit_sha": "a" * 40,
        "issue_title": "[BUG] tax",
        "root_cause": "tax before discount",
        "patch_summary": "apply discount first",
        "patch_diff": "--- a\n+++ b\n",
        "patched_files": {"src/core.py": "fixed\n"},
        "test_results": {
            "focused": {"passed": 3, "failed": 0},
            "full": {"passed": 6, "failed": 0},
        },
    }


class TestGitHubPullRequestService:
    async def test_creates_blob_tree_commit_ref_and_pr(self) -> None:
        requests: list[tuple[str, str]] = []
        blob_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal blob_count
            path = request.url.path
            requests.append((request.method, path))
            if request.method == "GET" and "/git/commits/" in path:
                return httpx.Response(200, json={"tree": {"sha": "base-tree"}})
            if path.endswith("/git/blobs"):
                blob_count += 1
                return httpx.Response(200, json={"sha": f"blob-{blob_count}"})
            if path.endswith("/git/trees"):
                return httpx.Response(200, json={"sha": "new-tree"})
            if path.endswith("/git/commits"):
                return httpx.Response(200, json={"sha": "new-commit"})
            if path.endswith("/git/refs"):
                return httpx.Response(201, json={"ref": "refs/heads/branch"})
            if path.endswith("/pulls"):
                return httpx.Response(
                    201,
                    json={
                        "html_url": "https://github.com/owner/repo/pull/7",
                        "number": 7,
                    },
                )
            return httpx.Response(404, json={"message": "unexpected"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = GitHubPullRequestService(
                token="token",
                client=client,
                branch_prefix="ash-agent/fix",
            )
            result = await service.publish(state())

        assert result.url == "https://github.com/owner/repo/pull/7"
        assert result.number == 7
        assert result.branch == "ash-agent/fix-run-1234"
        assert result.commit_sha == "new-commit"
        assert requests == [
            ("GET", f"/repos/owner/repo/git/commits/{'a' * 40}"),
            ("POST", "/repos/owner/repo/git/blobs"),
            ("POST", "/repos/owner/repo/git/trees"),
            ("POST", "/repos/owner/repo/git/commits"),
            ("POST", "/repos/owner/repo/git/refs"),
            ("POST", "/repos/owner/repo/pulls"),
        ]

    async def test_updates_existing_branch_on_conflict(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.method == "GET" and "/git/commits/" in path:
                return httpx.Response(200, json={"tree": {"sha": "base-tree"}})
            if path.endswith("/git/blobs"):
                return httpx.Response(200, json={"sha": "blob"})
            if path.endswith("/git/trees"):
                return httpx.Response(200, json={"sha": "new-tree"})
            if path.endswith("/git/commits"):
                return httpx.Response(200, json={"sha": "new-commit"})
            if path.endswith("/git/refs"):
                return httpx.Response(422, json={"message": "Reference already exists"})
            if "/git/refs/heads/" in path:
                return httpx.Response(200, json={"ref": "refs/heads/branch"})
            if path.endswith("/pulls"):
                return httpx.Response(
                    201,
                    json={
                        "html_url": "https://github.com/owner/repo/pull/8",
                        "number": 8,
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = GitHubPullRequestService(token="token", client=client)
            result = await service.publish(state())

        assert result.number == 8

    async def test_api_error_is_raised(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "Bad credentials"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = GitHubPullRequestService(token="bad", client=client)
            with pytest.raises(PullRequestError, match="401"):
                await service.publish(state())


class TestPullRequestFactory:
    def test_returns_none_without_token(self) -> None:
        settings = Settings(
            publish_enabled=True,
            github_token=SecretStr(""),
        )

        assert build_pull_request_service(settings) is None

    def test_returns_service_with_token(self) -> None:
        settings = Settings(
            publish_enabled=True,
            github_token=SecretStr("token"),
        )

        service = build_pull_request_service(settings)

        assert isinstance(service, GitHubPullRequestService)
