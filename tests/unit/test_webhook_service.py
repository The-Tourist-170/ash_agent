from ash_agent.services.webhook_service import (
    extract_failure_context,
    verify_github_signature,
)
from tests.conftest import sign, workflow_run_payload


class TestVerifyGithubSignature:
    def test_accepts_valid_signature(self) -> None:
        body = b'{"hello": "world"}'
        assert verify_github_signature("secret", body, sign(body, "secret")) is True

    def test_rejects_tampered_body(self) -> None:
        body = b'{"hello": "world"}'
        signature = sign(body, "secret")
        assert verify_github_signature("secret", b'{"hello": "moon"}', signature) is False

    def test_rejects_wrong_secret(self) -> None:
        body = b"payload"
        assert verify_github_signature("secret", body, sign(body, "other")) is False

    def test_rejects_missing_signature(self) -> None:
        assert verify_github_signature("secret", b"payload", None) is False

    def test_rejects_signature_without_prefix(self) -> None:
        body = b"payload"
        raw = sign(body, "secret").removeprefix("sha256=")
        assert verify_github_signature("secret", body, raw) is False

    def test_rejects_when_secret_is_empty(self) -> None:
        body = b"payload"
        assert verify_github_signature("", body, sign(body, "")) is False


class TestExtractFailureContext:
    def test_extracts_failed_workflow_run(self) -> None:
        context = extract_failure_context("workflow_run", workflow_run_payload("failure"))

        assert context is not None
        assert context.repository == "octo/demo"
        assert context.commit_sha == "a" * 40
        assert context.ref == "main"
        assert context.workflow_name == "CI"
        assert context.workflow_run_id == 123456789

    def test_timed_out_counts_as_failure(self) -> None:
        payload = workflow_run_payload("timed_out")
        assert extract_failure_context("workflow_run", payload) is not None

    def test_successful_run_is_not_repairable(self) -> None:
        assert extract_failure_context("workflow_run", workflow_run_payload("success")) is None

    def test_non_completed_action_is_ignored(self) -> None:
        payload = workflow_run_payload("failure")
        payload["action"] = "requested"
        assert extract_failure_context("workflow_run", payload) is None

    def test_other_event_types_are_ignored(self) -> None:
        assert extract_failure_context("push", workflow_run_payload("failure")) is None

    def test_missing_commit_sha_is_ignored(self) -> None:
        payload = workflow_run_payload("failure")
        payload["workflow_run"]["head_sha"] = None
        assert extract_failure_context("workflow_run", payload) is None
