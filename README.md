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

## Testing with a real GitHub repository

Phase 1 reacts to two kinds of GitHub events:

- **`workflow_run`** completed with a failing conclusion (`failure`, `timed_out`, `startup_failure`)
- **`issues`** with action `opened` or `reopened` (pull requests are ignored)

Every other event is stored and marked `ignored`.

1. Add a CI workflow to your repository. For `The-Tourist-170/dummy_project`, copy
   [`examples/github-actions-ci.yml`](examples/github-actions-ci.yml) to
   `.github/workflows/ci.yml` and push it. Its tests fail on purpose.
2. Start the stack: `docker compose -f infra/compose.yaml up -d postgres valkey`,
   `uv run alembic upgrade head`, `uv run ash-agent`, and `uv run ash-agent-worker`.
3. Expose the local API:
   ```bash
   cloudflared tunnel --url http://127.0.0.1:8000
   ```
   Copy the printed `https://<random>.trycloudflare.com` URL.
4. In GitHub: **Settings → Webhooks → Add webhook**
   - Payload URL: `https://<random>.trycloudflare.com/webhooks/github`
   - Content type: `application/json`
   - Secret: the value of `GITHUB_WEBHOOK_SECRET` from `.env`
   - Events: **Workflow runs** + **Issues** (or "Send me everything")
5. Trigger it by pushing a failing commit, re-running the workflow, or opening an issue.
   GitHub shows a `202` delivery and a JSON body like
   `{"status":"accepted","delivery_id":"...","run_id":"..."}`.
6. Inspect it: `curl -s http://localhost:8000/runs | python -m json.tool`.

You can also send signed events without GitHub:

```bash
# failing workflow
uv run python scripts/send_test_webhook.py

# newly opened issue
uv run python scripts/send_test_webhook.py --event issues --issue-number 7
```

Quick-tunnel URLs change every time `cloudflared` restarts, so update the webhook URL when you
restart it.

## Agent workflow

The worker executes a LangGraph state machine for every run:

```text
ingest → diagnose → retrieve → reproduce → plan → patch → validate
                                                       ├─ passed            → publish   (passed)
                                                       ├─ failed, budget    → reflect → plan
                                                       └─ budget exhausted → escalate  (escalated)
```

Phase 2 nodes are deterministic mocks. Retrieval, patching and validation are replaced with real
implementations in later phases.

Checkpoints use `AsyncPostgresSaver`, so the graph state is written to PostgreSQL after every
node. A worker restart or RQ retry re-invokes the graph with the same `thread_id` (the run id)
and resumes from the last completed node. The worker creates the checkpoint schema on startup.

| Setting | Default | Meaning |
|---|---|---|
| `MAX_ATTEMPTS` | 3 | Maximum patch attempts before escalation |
| `MOCK_REPAIR_SUCCESS_ATTEMPT` | 1 | Mock validation passes on this attempt (`99` = always escalate) |
| `JOB_MAX_RETRIES` | 2 | RQ retries for transient worker failures |
| `GRAPH_RECURSION_LIMIT` | 50 | LangGraph safety limit |

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
- Phase 2: LangGraph workflow, PostgreSQL checkpoints, bounded retries and resumption — complete
- Phase 3 onward: see `NOTES.md`
