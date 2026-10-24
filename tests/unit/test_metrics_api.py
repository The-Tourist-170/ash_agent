class TestMetricsApi:
    async def test_returns_run_and_lesson_counts(self, client) -> None:
        response = await client.get("/metrics")

        assert response.status_code == 200
        body = response.json()
        assert body["runs"]["total"] == 0
        assert body["runs"]["by_status"] == {}
        assert body["runs"]["average_attempts"] == 0
        assert body["pull_requests"]["published"] == 0
        assert body["repair_lessons"] == 0
