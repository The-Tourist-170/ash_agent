class TestHealth:
    async def test_liveness(self, client) -> None:
        response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "components": None}

    async def test_readiness_reports_dependencies(self, client) -> None:
        response = await client.get("/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["components"] == {"postgres": "ok", "valkey": "ok"}
