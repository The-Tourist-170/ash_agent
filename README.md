# ash-agent

Autonomous self-healing SWE agent. A failed CI workflow arrives through an authenticated
GitHub webhook, is persisted, and is dispatched to a background worker. Later phases attach
the LangGraph diagnosis/repair loop and open an annotated pull request.

## Stack

| Technology | Responsibility |
|---|---|
| FastAPI | Webhook gateway, run API, health checks |
| RQ + Valkey | Background job queue and worker |
| PostgreSQL | Source of truth for deliveries, runs, checkpoints |
| Neo4j | Code relationship graph (Phase 3) |
| Qdrant | Semantic code/log retrieval (Phase 3) |
| Mem0 | Verified repair lessons (Phase 6) |

## Quick start

```bash
cp .env.example .env
docker compose -f infra/compose.yaml up -d
uv sync
uv run alembic upgrade head

# terminal 1
uv run ash-agent

# terminal 2
uv run ash-agent-worker
```

API docs: http://localhost:8000/docs

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/webhooks/github` | HMAC-verified GitHub webhook, deduplicated by delivery ID |
| GET | `/runs` | List repair runs (`status`, `limit`, `offset`) |
| GET | `/runs/{run_id}` | Inspect a single run |
| GET | `/health` | Liveness |
| GET | `/health/ready` | Postgres + Valkey readiness |

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Integration tests expect Postgres (`ash_agent_test` database) and Valkey on their default
ports. Override with `TEST_DATABASE_URL` and `TEST_VALKEY_URL`.

## Status

- Phase 1: foundation, infrastructure, webhook gateway, RQ worker — complete
- Phase 2 onward: see `NOTES.md`
