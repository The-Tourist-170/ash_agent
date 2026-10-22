import hashlib
import hmac
from dataclasses import dataclass

FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
SIGNATURE_PREFIX = "sha256="


@dataclass(frozen=True, slots=True)
class FailureContext:
    repository: str
    commit_sha: str
    ref: str | None
    workflow_name: str | None
    workflow_run_id: int | None
    run_url: str | None


def verify_github_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not secret or not signature_header or not signature_header.startswith(SIGNATURE_PREFIX):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix(SIGNATURE_PREFIX)
    return hmac.compare_digest(expected, provided)


def extract_failure_context(event_type: str, payload: dict) -> FailureContext | None:
    if event_type != "workflow_run":
        return None
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
    return FailureContext(
        repository=repository,
        commit_sha=commit_sha,
        ref=workflow_run.get("head_branch"),
        workflow_name=workflow_run.get("name"),
        workflow_run_id=workflow_run_id if isinstance(workflow_run_id, int) else None,
        run_url=workflow_run.get("html_url"),
    )
