from ash_agent.agents.nodes.publish import make_publish_node
from ash_agent.services.pull_request_service import PullRequestResult


def state() -> dict:
    return {
        "run_id": "publish-run",
        "repository": "owner/repo",
        "patched_files": {"core.py": "fixed"},
    }


class FakePublish:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def publish(self, state):
        if self.error:
            raise self.error
        return self.result


class TestPublishNode:
    async def test_maps_pull_request_result(self) -> None:
        service = FakePublish(
            PullRequestResult(
                url="https://github.com/owner/repo/pull/3",
                number=3,
                branch="ash-agent/fix-123",
                commit_sha="b" * 40,
            )
        )

        result = await make_publish_node(service)(state())

        assert result["status"] == "passed"
        assert result["pr_url"] == "https://github.com/owner/repo/pull/3"
        assert result["pr_number"] == 3
        assert result["pr_branch"] == "ash-agent/fix-123"
        assert result["publish_status"] == "published"

    async def test_publish_error_keeps_validation_status(self) -> None:
        result = await make_publish_node(FakePublish(error=RuntimeError("bad credentials")))(
            state()
        )

        assert result["status"] == "passed"
        assert result["publish_status"] == "failed"
        assert "bad credentials" in result["pr_error"]
        assert "pr_url" not in result

    async def test_no_service_marks_skipped(self) -> None:
        result = await make_publish_node(None)(state())

        assert result["status"] == "passed"
        assert result["publish_status"] == "skipped"
