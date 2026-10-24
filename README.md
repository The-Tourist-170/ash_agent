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
| LangChain + ChatOpenAI | Structured diagnosis over an OpenAI-compatible endpoint |
| Tree-sitter | Python symbol extraction |
| Neo4j | Code relationship graph (Phase 3) |
| Qdrant | Semantic code/log retrieval (Phase 3) |
| FastEmbed | Local code-chunk embeddings |
| Docker sandbox | Disposable test reproduction (Phase 4) |
| Repair memory | Verified, repository-scoped lessons (Phase 6) |

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
| GET | `/metrics` | Run, repair and PR counts |
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
ingest → diagnose → retrieve → reproduce → scope
                                              ├─ pytest targets → plan → patch → validate
                                              └─ runtime command → runtime → plan → patch → validate
                                                         ├─ passed            → publish   (passed)
                                                         ├─ failed, budget    → reflect → plan
                                                         └─ budget exhausted → escalate  (escalated)
```

Diagnosis uses `ChatOpenAI`; retrieval clones and indexes the repository; reproduction runs
`pytest` in a disposable Docker sandbox; the scope node narrows issue repairs to related failing
tests; patching and validation run through the LLM and a second sandbox. Publish opens a PR when a
token is configured.

Checkpoints use `AsyncPostgresSaver`, so the graph state is written to PostgreSQL after every
node. A worker restart or RQ retry re-invokes the graph with the same `thread_id` (the run id)
and resumes from the last completed node. The worker creates the checkpoint schema on startup.

| Setting | Default | Meaning |
|---|---|---|
| `MAX_ATTEMPTS` | 3 | Maximum patch attempts before escalation |
| `MOCK_REPAIR_SUCCESS_ATTEMPT` | 1 | Mock validation passes on this attempt (`99` = always escalate) |
| `JOB_MAX_RETRIES` | 2 | RQ retries for transient worker failures |
| `GRAPH_RECURSION_LIMIT` | 50 | LangGraph safety limit |
| `LLM_BASE_URL` | unset | OpenAI-compatible endpoint (e.g. `http://127.0.0.1:55990`) |
| `LLM_MODEL` | unset | Model id exposed by that endpoint |
| `LLM_API_KEY` | empty | Optional bearer token; leave empty when the endpoint needs none |
| `RETRIEVAL_ENABLED` | `true` | Clone/index the repository in the `retrieve` node |
| `WORKSPACE_DIR` | `.ash_agent/workspaces` | Cached repository checkouts |
| `NEO4J_URI` | `bolt://localhost:7687` | Code graph database |
| `QDRANT_URL` | `http://localhost:6333` | Vector store |
| `EMBEDDING_PROVIDER` | `fastembed` | `fastembed` or deterministic `hash` (tests) |
| `SANDBOX_ENABLED` | `true` | Run tests in the Docker sandbox |
| `SANDBOX_IMAGE` | `ash-agent-sandbox:py312` | Sandbox image |
| `SANDBOX_NETWORK` | `none` | Test-container network |
| `SANDBOX_TIMEOUT_SECONDS` | 120 | Test execution timeout |
| `SANDBOX_MEMORY_LIMIT` | `512m` | Container memory cap |
| `PUBLISH_ENABLED` | `true` | Open a pull request after validation |
| `GITHUB_TOKEN` | empty | Repo-scoped token; empty = local-only publish |
| `GITHUB_API_URL` | `https://api.github.com` | GitHub API base |
| `GITHUB_PR_BRANCH_PREFIX` | `ash-agent/fix` | Branch prefix |

### LLM diagnosis

When `LLM_BASE_URL` and `LLM_MODEL` are set, the `diagnose` node uses LangChain's `ChatOpenAI`
with `with_structured_output(Diagnosis, method="function_calling")` and stores the returned
`root_cause`, `suspected_files` and `plan` in the graph state. The endpoint needs no key: when
`LLM_API_KEY` is empty, a small `httpx` auth shim removes the SDK's `Authorization` header because
this proxy rejects any bearer token. `method="function_calling"` is required here — the endpoint
did not honor `json_schema` or `json_mode` structured output. If the call fails or returns unusable
data, the node falls back to the deterministic mock so the workflow still completes.

```bash
uv run python scripts/check_llm.py
```

Patching and validation remain deterministic mocks (Phase 5).

### Repository intelligence

The `retrieve` node clones the repository at the run's commit (resolving `HEAD` for issue runs),
extracts Python symbols with Tree-sitter, writes the code graph to Neo4j (`Repository`, `Commit`,
`File`, `Symbol` plus `CONTAINS`, `DEFINES`, `IMPORTS`, `CALLS`), embeds symbol-aware chunks with
FastEmbed, stores them in Qdrant, and fuses dense and BM25 rankings with reciprocal rank fusion.
The resolved commit SHA, checkout path and top chunks are persisted in graph state and the SHA is
copied to `runs.commit_sha`.

Set `RETRIEVAL_ENABLED=false` to use the deterministic fallback (the test suite does this). Clones
and the embedding model are cached under `.ash_agent/` (gitignored).

```bash
uv run python scripts/index_repository.py --query "calculate_total tax discount"
```

Expected: checkout path, resolved commit SHA, `backend: hybrid`, `core.py` / `calculate_total`
among the top chunks, Neo4j node/edge counts, and a Qdrant point count.

### Sandbox reproduction

Build the local sandbox image once:

```bash
docker build -t ash-agent-sandbox:py312 infra/sandbox
```

The `reproduce` node copies the checkout into `.ash_agent/sandboxes/<run>`, installs dependencies
from `uv.lock` / `requirements.txt` (network bridge), then runs `pytest` in a second container with
`--network none`. Both containers run as the host UID, with a read-only root filesystem, dropped
capabilities, `no-new-privileges`, memory/CPU/PID limits and a timeout. No host environment
variables, secrets, or Docker socket are mounted.

`reproduction_results` stores the install/test commands, exit codes, counts and failed test IDs;
`failure_logs` stores the captured output. A pytest exit code of 1 confirms reproduction. Install
failure, timeout, or no collected tests routes the run to `escalate`.

```bash
uv run python scripts/reproduce_repository.py
```

Expected for `dummy_project`: `confirmed: true`, `exit_code: 1`, `3 failed`, `3 passed`, and the
three failing test names. Set `SANDBOX_ENABLED=false` to use the deterministic mock.

### Issue-scoped repairs

Issue-triggered runs add a `scope` node between reproduction and planning. An LLM selects only the
failing tests that directly prove the reported issue; unrelated pre-existing failures are recorded
but not patched. Scope also records the only source/config files and symbols that may be edited.
Tests without issue evidence are dropped, and patch edits outside those files/symbols are rejected
before validation. Test files are never edited. Validation requires the target tests to pass and
introduces no new failures. A full-suite exit code of 1 is accepted when the remaining failures are
exactly the baseline unrelated set. The PR body lists target tests and untouched unrelated tests.

Repair attempts stop when the target tests pass; unrelated bugs remain for separate issues/PRs.

### Runtime-only issues

Some issues have no pytest coverage: a script crashes, a CLI prints the wrong value, or a Compose
service fails. When no issue-related failing test exists, the `scope` node extracts a runtime
command from the issue and, for Compose issues, from the service's `command` array. It records the
expected exit code and, when the issue states it explicitly, the expected output.

The `runtime` node runs that command in the same sandbox with network disabled, captures the
failure, and routes to planning only if the runtime behavior is reproduced. Patch generation is
still restricted to `scope_files`/`scope_symbols`; validation reruns the same command and checks
exit code/output. Test files are never edited.

The sandbox has no Docker socket, so a real `docker compose run` is not executed inside it. For a
Compose issue, the agent runs the service's underlying command directly (for example
`python scripts/compose_smoke.py`); it does not start the Compose container. Install and packaging
failures are out of scope and still escalate.

### Repair memory and pull requests

Only a patch that passes focused and full validation is stored in `repair_lessons`. The next run
for the same repository recalls the latest three lessons and adds them to the diagnosis and patch
prompts. This is a Mem0-compatible seam backed by PostgreSQL; no model-generated but unvalidated
fix is remembered.

After validation passes, the `publish` node opens a GitHub pull request using the Git Data API:
branch `ash-agent/fix-<run-id>`, one commit with the patched files, and an annotated body containing
the root cause, diff and test evidence. Human review and merge remain manual. Without
`GITHUB_TOKEN`, the run stays `passed` with `publish_status=skipped`.

```bash
curl -s http://localhost:8000/metrics | python -m json.tool
```

`GET /metrics` reports run counts by status, average attempts, published PRs and stored lessons.

On macOS the worker uses RQ's `SimpleWorker` (jobs run in-process) because RQ's forked child can
hit Objective-C fork-safety crashes; Linux keeps the standard forked `Worker`. The worker also
runs RQ's scheduler in-process so retries are dequeued.

### Logs

Console logs use a compact, color-coded layout:

```text
19:10:47 INFO    queue.jobs             | run ... claimed by worker
19:10:55 INFO    agent.diagnose         | LLM diagnosis completed for run ...
19:10:56 WARNING agent.retrieve         | retrieval failed, using fallback: RepositoryError
19:10:57 INFO    sandbox.executor       | sandbox reproduction: confirmed=True exit_code=1 failed=3
19:11:03 INFO    svc.repair_service     | validation for ... attempt 1: passed=True
```

`LOG_COLORS` controls ANSI colors:

- `auto` — colors when stdout is a TTY (default)
- `always` — force colors, useful when piping through `tee`
- `never` — plain text for log files/CI

`LOG_LEVEL=DEBUG` enables verbose agent logs. `NO_COLOR=1` also disables colors.

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Integration tests expect Postgres (`ash_agent_test` database) and Valkey on their default
ports. Override with `TEST_DATABASE_URL` and `TEST_VALKEY_URL`. The live retrieval and reproduction
scripts also expect Neo4j/Qdrant and the sandbox Docker image.

## Status

- Phase 1: foundation, infrastructure, webhook gateway, RQ worker — complete
- Phase 2: LangGraph workflow, PostgreSQL checkpoints, bounded retries and resumption — complete
- Phase 3: repository cloning, Tree-sitter symbols, Neo4j graph, Qdrant hybrid retrieval — complete
- Phase 4: isolated Docker reproduction, dependency install, pytest evidence — complete
- Phase 5: LLM patch generation, focused/full validation, bounded self-correction — complete
- Phase 6: verified repair memory, GitHub pull requests, audit fields and metrics — complete
- Phase 7: runtime-only reproduction and command validation for issues without pytest coverage — complete
