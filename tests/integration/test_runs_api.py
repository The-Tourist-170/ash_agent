from uuid import uuid4

from tests.conftest import post_webhook, workflow_run_payload


class TestRunsApi:
    async def test_reads_created_run(self, client) -> None:
        _, accepted = await post_webhook(client, workflow_run_payload("failure"))

        response = await client.get(f"/runs/{accepted['run_id']}")

        assert response.status_code == 200
        run = response.json()
        assert run["id"] == accepted["run_id"]
        assert run["repository"] == "octo/demo"
        assert run["status"] == "queued"

    async def test_unknown_run_returns_404(self, client) -> None:
        response = await client.get(f"/runs/{uuid4()}")

        assert response.status_code == 404

    async def test_lists_runs(self, client) -> None:
        await post_webhook(client, workflow_run_payload("failure"))

        response = await client.get("/runs?limit=10")

        assert response.status_code == 200
        runs = response.json()
        assert len(runs) == 1
        assert runs[0]["status"] == "queued"

    async def test_filters_invalid_status(self, client) -> None:
        response = await client.get("/runs?status=not-a-status")

        assert response.status_code == 422
