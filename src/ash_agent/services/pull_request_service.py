from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


class PullRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class PullRequestResult:
    url: str
    number: int
    branch: str
    commit_sha: str


class GitHubPullRequestService:
    def __init__(
        self,
        *,
        token: str,
        api_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
        branch_prefix: str = "ash-agent/fix",
    ) -> None:
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = client
        self.branch_prefix = branch_prefix

    async def publish(self, state: dict) -> PullRequestResult:
        files = state.get("patched_files") or {}
        if not files:
            raise PullRequestError("no patched files to publish")

        repository = str(state["repository"])
        base_ref = str(state.get("ref") or "main")
        run_id = str(state.get("run_id") or "run")
        branch = f"{self.branch_prefix}-{run_id[:8]}"
        title = f"[ash-agent] {state.get('issue_title') or 'Fix failing tests'}"[:250]
        body = self._body(state)
        commit_sha = state.get("commit_sha")

        if self._client is not None:
            return await self._create(
                self._client,
                repository=repository,
                base_ref=base_ref,
                commit_sha=commit_sha,
                branch=branch,
                title=title,
                body=body,
                files=files,
            )

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            return await self._create(
                client,
                repository=repository,
                base_ref=base_ref,
                commit_sha=commit_sha,
                branch=branch,
                title=title,
                body=body,
                files=files,
            )

    async def _create(
        self,
        client: httpx.AsyncClient,
        *,
        repository: str,
        base_ref: str,
        commit_sha: str | None,
        branch: str,
        title: str,
        body: str,
        files: dict[str, str],
    ) -> PullRequestResult:
        base_commit = commit_sha or await self._resolve_base(client, repository, base_ref)
        commit_data = await self._request(
            client, "GET", f"/repos/{repository}/git/commits/{base_commit}"
        )
        base_tree = commit_data["tree"]["sha"]

        entries = []
        for path, content in sorted(files.items()):
            blob = await self._request(
                client,
                "POST",
                f"/repos/{repository}/git/blobs",
                json={"content": content, "encoding": "utf-8"},
            )
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})

        tree = await self._request(
            client,
            "POST",
            f"/repos/{repository}/git/trees",
            json={"base_tree": base_tree, "tree": entries},
        )
        commit = await self._request(
            client,
            "POST",
            f"/repos/{repository}/git/commits",
            json={
                "message": title,
                "tree": tree["sha"],
                "parents": [base_commit],
            },
        )

        ref = f"refs/heads/{branch}"
        try:
            await self._request(
                client,
                "POST",
                f"/repos/{repository}/git/refs",
                json={"ref": ref, "sha": commit["sha"]},
            )
        except PullRequestError as exc:
            if exc.status_code != 422:
                raise
            await self._request(
                client,
                "PATCH",
                f"/repos/{repository}/git/refs/heads/{branch}",
                json={"sha": commit["sha"], "force": True},
            )

        pull = await self._request(
            client,
            "POST",
            f"/repos/{repository}/pulls",
            json={
                "title": title,
                "head": branch,
                "base": base_ref,
                "body": body,
            },
        )
        return PullRequestResult(
            url=pull["html_url"],
            number=int(pull["number"]),
            branch=branch,
            commit_sha=commit["sha"],
        )

    async def _resolve_base(self, client: httpx.AsyncClient, repository: str, base_ref: str) -> str:
        response = await self._request(
            client, "GET", f"/repos/{repository}/git/ref/heads/{base_ref}"
        )
        return response["object"]["sha"]

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        **kwargs,
    ) -> dict:
        response = await client.request(
            method,
            f"{self.api_url}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            **kwargs,
        )
        if response.status_code >= 400:
            raise PullRequestError(
                f"GitHub API {method} {path} returned {response.status_code}: "
                f"{response.text[:400]}",
                status_code=response.status_code,
            )
        return response.json()

    def _body(self, state: dict) -> str:
        full = state.get("test_results") or {}
        focused = full.get("focused") or {}
        full_result = full.get("full") or {}
        runtime_mode = state.get("validation_mode") == "command"
        root_cause = state.get("root_cause")
        if runtime_mode:
            root_cause = state.get("patch_summary") or root_cause
        target_tests = state.get("target_tests") or []
        unrelated_tests = state.get("unrelated_failed_tests") or []
        scope_lines = "\n".join(f"- `{test}`" for test in target_tests) or "- n/a"
        unrelated_lines = (
            "\n".join(f"- `{test}`" for test in unrelated_tests) or "None (full suite clean)"
        )
        issue_number = state.get("issue_number")
        fixes = f"Fixes #{issue_number}\n\n" if issue_number else ""
        runtime_section = ""
        if state.get("validation_mode") == "command":
            commands = state.get("runtime_commands") or []
            runtime = state.get("runtime_validation") or {}
            runtime_lines = "\n".join(f"- `{' '.join(command)}`" for command in commands)
            runtime_section = f"""
## Runtime validation

{runtime_lines}

- passed: {runtime.get("passed", "n/a")}
- expected exit: {runtime.get("expected_exit", 0)}
- expected output: {runtime.get("expected_output") or "not specified"}
"""
        return f"""{fixes}## Root cause

{root_cause or "Not available"}

## Issue scope

Target failing tests:
{scope_lines}

Pre-existing unrelated failures left untouched:
{unrelated_lines}

{runtime_section}
## Patch

{state.get("patch_summary") or "See diff below."}

```diff
{(state.get("patch_diff") or "")[:8000]}
```

## Validation

- focused tests: {focused.get("passed", "n/a")} passed, {focused.get("failed", "n/a")} failed
- full suite: {full_result.get("passed", "n/a")} passed, {full_result.get("failed", "n/a")} failed
- commit: `{state.get("commit_sha") or "unknown"}`

Generated by ash-agent. Please review before merging.
"""


def build_pull_request_service(settings) -> GitHubPullRequestService | None:
    if not settings.publish_enabled:
        return None
    token = settings.github_token.get_secret_value()
    if not token:
        logger.warning("GITHUB_TOKEN is empty; pull request publishing is disabled")
        return None
    return GitHubPullRequestService(
        token=token,
        api_url=settings.github_api_url,
        branch_prefix=settings.github_pr_branch_prefix,
    )
