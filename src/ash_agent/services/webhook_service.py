import hashlib
import hmac
from dataclasses import dataclass

FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
ISSUE_ACTIONS = frozenset({"opened", "reopened"})
SIGNATURE_PREFIX = "sha256="


@dataclass(frozen=True, slots=True)
class RepairContext:
    source: str
    repository: str
    commit_sha: str | None
    ref: str | None
    workflow_name: str | None = None
    workflow_run_id: int | None = None
    run_url: str | None = None
    issue_number: int | None = None
    issue_title: str | None = None
    issue_body: str | None = None


def verify_github_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not secret or not signature_header or not signature_header.startswith(SIGNATURE_PREFIX):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix(SIGNATURE_PREFIX)
    return hmac.compare_digest(expected, provided)


def extract_repair_context(event_type: str, payload: dict) -> RepairContext | None:
    if event_type == "workflow_run":
        return extract_failure_context(payload)
    if event_type == "issues" and payload.get("action") in ISSUE_ACTIONS:
        return extract_issue_context(payload)
    return None


def extract_failure_context(payload: dict) -> RepairContext | None:
    if payload.get("action") != "completed":
        return None

    workflow_run = payload.get("workflow_run") or {}
    if workflow_run.get("conclusion") not in FAILED_CONCLUSIONS:
        return None

    repository = (payload.get("repository") or {}).get("full_name")
    commit_sha = workflow_run.get("head_sha")
    if not repository or not commit_sha:
        return None

    workflow_run_id = workflow_run.get("id")
    return RepairContext(
        source="workflow_run",
        repository=repository,
        commit_sha=commit_sha,
        ref=workflow_run.get("head_branch"),
        workflow_name=workflow_run.get("name"),
        workflow_run_id=workflow_run_id if isinstance(workflow_run_id, int) else None,
        run_url=workflow_run.get("html_url"),
    )


def extract_issue_context(payload: dict) -> RepairContext | None:
    issue = payload.get("issue") or {}
    if issue.get("pull_request"):
        return None

    repository_payload = payload.get("repository") or {}
    repository = repository_payload.get("full_name")
    issue_number = issue.get("number")
    if not repository or not isinstance(issue_number, int):
        return None

    return RepairContext(
        source="issue",
        repository=repository,
        commit_sha=None,
        ref=repository_payload.get("default_branch"),
        issue_number=issue_number,
        issue_title=issue.get("title"),
        issue_body=issue.get("body"),
        run_url=issue.get("html_url"),
    )
