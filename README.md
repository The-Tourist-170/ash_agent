<div align="center">

# ash-agent

### From a GitHub bug report to a validated repair pull request.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/Workflow-LangGraph-6366F1?style=flat-square)
![Docker](https://img.shields.io/badge/Sandbox-Docker-2496ED?style=flat-square&logo=docker&logoColor=white)
![Status](https://img.shields.io/badge/Status-Experimental-F59E0B?style=flat-square)

An event-driven software engineering agent that diagnoses failures, retrieves relevant code,
reproduces bugs in Docker, generates scoped patches, and opens pull requests for human review.

[Overview](#overview) · [Architecture](#architecture-and-workflow) · [Setup](#installation) · [Real repository guide](#use-with-a-real-github-repository)

</div>

---

## Index

1. [Overview](#overview)
2. [Capabilities](#capabilities)
3. [Architecture and workflow](#architecture-and-workflow)
4. [Technology stack](#technology-stack)
5. [Prerequisites](#prerequisites)
6. [Installation](#installation)
7. [Environment configuration](#environment-configuration)
8. [Run the project](#run-the-project)
9. [Use with a real GitHub repository](#use-with-a-real-github-repository)
10. [Monitor runs and inspect results](#monitor-runs-and-inspect-results)
11. [Security and current limitations](#security-and-current-limitations)
12. [Troubleshooting](#troubleshooting)
13. [Development](#development)
14. [Project structure](#project-structure)

## Overview

**ash-agent is an autonomous repair agent for Python repositories.** It receives GitHub webhooks
for failed CI workflows or newly opened/reopened issues, records each delivery, and queues a
background repair run.

A checkpointed LangGraph workflow then:

1. Uses an LLM to diagnose the reported behavior.
2. Clones the repository at a specific commit and retrieves relevant code.
3. Reproduces the failure in an isolated Docker sandbox.
4. Determines which tests, files, and symbols belong to the reported issue.
5. Generates a minimal patch and validates it against the selected evidence.
6. Opens an annotated GitHub pull request and records a verified repair lesson.

The goal is to **fix the reported issue, not every bug in the repository**. Unrelated baseline
failures are tracked separately. If the agent cannot reproduce or safely scope an issue, or
exhausts its repair budget, it escalates rather than publishing an unvalidated fix.

**Human review is the final gate. The agent does not merge pull requests.**

## Capabilities

| Capability | What it provides |
|---|---|
| Authenticated event ingestion | HMAC-verified webhooks and delivery-ID deduplication |
| Background processing | RQ jobs backed by Valkey, with bounded retries |
| Resumable workflows | PostgreSQL checkpoints after graph nodes |
| Repository intelligence | Tree-sitter symbols, Neo4j relationships, and hybrid dense/BM25 retrieval |
| Local embeddings | FastEmbed code embeddings cached locally |
| Pytest-based repair | Reproduce failures, validate focused targets, and check for new failures |
| Runtime-only repair | Reproduce crashes or wrong output using an issue-provided command when pytest does not cover the behavior |
| Scope enforcement | File/symbol boundaries checked before applying edits and again before publishing; test files are not editable |
| Bounded self-correction | Reflect on validation failures and retry within `MAX_ATTEMPTS` |
| Repair memory | Recall recent, repository-scoped lessons from validated patches |
| Reviewable output | Branch, commit, PR, patch summary, and validation evidence |

## Architecture and workflow

This single diagram shows both the infrastructure and the repair decision flow.
**Cyan** arrows represent ingestion, **violet** execution, **amber** supporting services,
**green** successful outcomes, and **rose** retries or escalation.

```mermaid
flowchart TB
    GH["GitHub<br/>Issues and failed workflows"] --> API["FastAPI webhook gateway<br/>HMAC verification and deduplication"]
    API --> DB[("PostgreSQL<br/>Deliveries, runs, checkpoints, lessons")]
    API --> QUEUE["Valkey + RQ<br/>Background job queue"]
    QUEUE --> WORKER["Repair worker"]

    subgraph GRAPH["Checkpointed LangGraph workflow"]
        INGEST["Ingest"] --> DIAGNOSE["Diagnose"]
        DIAGNOSE --> RETRIEVE["Clone and retrieve code"]
        RETRIEVE --> REPRODUCE["Baseline pytest reproduction"]
        REPRODUCE --> SCOPE{"Scope the issue"}
        SCOPE -->|"Related pytest targets"| PLAN["Plan repair"]
        SCOPE -->|"Runtime command, no related test"| RUNTIME["Reproduce runtime behavior"]
        RUNTIME -->|"Failure confirmed"| PLAN
        RUNTIME -->|"Not reproduced"| ESCALATE["Escalate for investigation"]
        SCOPE -->|"No supported evidence"| ESCALATE
        PLAN --> PATCH["Generate patch"]
        PATCH --> GUARD{"File and symbol scope guard"}
        GUARD -->|"In scope"| VALIDATE["Validate focused tests and baseline<br/>or rerun the runtime command"]
        GUARD -->|"Rejected edit"| REFLECT["Reflect within attempt budget"]
        VALIDATE -->|"Passed"| PUBLISH["Recheck scope and publish"]
        VALIDATE -->|"Failed, attempts remain"| REFLECT
        REFLECT --> PLAN
        VALIDATE -->|"Budget exhausted"| ESCALATE
    end

    WORKER --> INGEST
    WORKER -. "Persist and resume state" .-> DB
    DIAGNOSE -. "Structured diagnosis" .-> LLM["OpenAI-compatible LLM<br/>LangChain ChatOpenAI"]
    PATCH -. "Structured edits" .-> LLM
    RETRIEVE -. "Index and search" .-> INTEL["Tree-sitter + FastEmbed<br/>Neo4j + Qdrant + BM25"]
    REPRODUCE -. "Execute" .-> SANDBOX["Disposable Docker sandbox<br/>Resource limits, no runtime network"]
    RUNTIME -. "Execute" .-> SANDBOX
    VALIDATE -. "Execute" .-> SANDBOX
    PUBLISH --> PR["GitHub pull request<br/>Human review and manual merge"]
    PUBLISH --> MEMORY["Record validated repair lesson"]
    MEMORY -. "Store" .-> DB
    DB -. "Recall repository lessons" .-> DIAGNOSE

    classDef ingress fill:#083344,stroke:#22d3ee,color:#ecfeff
    classDef execution fill:#2e1065,stroke:#a78bfa,color:#f5f3ff
    classDef supporting fill:#451a03,stroke:#fbbf24,color:#fffbeb
    classDef success fill:#052e16,stroke:#4ade80,color:#f0fdf4
    classDef attention fill:#4c0519,stroke:#fb7185,color:#fff1f2
    class GH,API,QUEUE,WORKER ingress
    class INGEST,DIAGNOSE,RETRIEVE,REPRODUCE,SCOPE,RUNTIME,PLAN,PATCH,GUARD,VALIDATE execution
    class DB,LLM,INTEL,SANDBOX supporting
    class PUBLISH,PR,MEMORY success
    class ESCALATE,REFLECT attention
    linkStyle default stroke:#a78bfa,stroke-width:2px
    linkStyle 0,1,2,3,21 stroke:#22d3ee,stroke-width:2px
    linkStyle 22,23,24,25,26,27,28,31,32 stroke:#fbbf24,stroke-width:2px
    linkStyle 17,29,30 stroke:#4ade80,stroke-width:2px
    linkStyle 11,12,16,18,19,20 stroke:#fb7185,stroke-width:2px
```

Workflow-run failures that cannot be reproduced also escalate. Scope violations consume the
bounded repair budget; they do not authorize the model to widen the issue scope.

## Technology stack

| Technology | Responsibility |
|---|---|
| Python 3.11+ and uv | Application runtime, dependency management, and CLI entry points |
| FastAPI | Webhook gateway, run inspection, metrics, and health checks |
| LangGraph | State machine, conditional routing, retries, and checkpoint resumption |
| LangChain / ChatOpenAI | Structured LLM diagnosis and patch proposals |
| PostgreSQL | Deliveries, run records, graph checkpoints, and repair lessons |
| RQ + Valkey | Background job dispatch and worker execution |
| Tree-sitter | Python symbol extraction |
| Neo4j | Repository, file, symbol, import, and call relationships |
| Qdrant + FastEmbed | Dense code retrieval with local embeddings |
| BM25 + reciprocal rank fusion | Combine lexical and dense search rankings |
| Docker | Disposable reproduction and validation environments |
| GitHub REST / Git Data APIs | Create branches, commits, and pull requests |

## Prerequisites

Before starting, install or prepare:

- **Python 3.11 or newer** for the agent. The provided sandbox uses Python **3.12**.
- **uv**, **Git**, and **Docker with Docker Compose v2**.
- A running Docker engine, such as Docker Desktop or OrbStack.
- A **GitHub repository you control**, with permission to configure webhooks and review agent PRs.
- An **OpenAI-compatible LLM endpoint and model supporting tool/function calling**. Real patch
  generation requires an LLM; leaving it unconfigured enables mock behavior, not real repairs.
- A **GitHub token** with access to the target repository if you want PR creation or private clones.
- **cloudflared** or another HTTPS tunnel for GitHub to reach your local API. A tunnel is not
  required for local signed-webhook tests.
- Network access for dependency/image downloads and the initial FastEmbed model download.

The Compose file supplies PostgreSQL, Valkey, Neo4j, and Qdrant. Default host ports are
`5432`, `6379`, `7474`, `7687`, `6333`, and `6334`; the API uses `8000`.

## Installation

Clone this repository and run all subsequent agent commands from its root directory:

```bash
git clone https://github.com/The-Tourist-170/ash_agent.git
cd ash_agent
uv sync
cp .env.example .env
```

Configure `.env` before starting the services or worker. Do not commit this file.

## Environment configuration

[`.env.example`](.env.example) is the full configuration reference. The following groups cover
what you need for a real repair run.

### 1. Local services and webhook authentication

The defaults match the supplied Compose stack:

```dotenv
ENVIRONMENT=development
API_HOST=0.0.0.0
API_PORT=8000
LOG_LEVEL=INFO
LOG_COLORS=auto

DATABASE_URL=postgresql+asyncpg://ash:ash@localhost:5432/ash_agent
VALKEY_URL=redis://localhost:6379/0
RQ_QUEUE_NAME=ash_agent:jobs

NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=ashagent
QDRANT_URL=http://localhost:6333

GITHUB_WEBHOOK_SECRET=replace-with-a-long-random-secret
```

Generate a webhook secret locally:

```bash
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

Copy that value into `.env` and into the GitHub webhook's **Secret** field. It is not your GitHub
API token. If you change database credentials or exposed ports, update both the Compose settings
and the application's connection URLs. The bundled passwords are development defaults.

### 2. LLM configuration

```dotenv
LLM_BASE_URL=<your-openai-compatible-base-url>
LLM_MODEL=<model-id-exposed-by-your-provider>
LLM_API_KEY=<your-provider-api-key>
LLM_TIMEOUT_SECONDS=180
```

Replace the angle-bracket values; do not leave them literally in `.env`.

- The client adds `/v1` if it is not already present in `LLM_BASE_URL`.
- The model must support structured tool/function calls. This project uses
  `with_structured_output(..., method="function_calling")`.
- For a local endpoint that requires no authentication, leave `LLM_API_KEY=` empty. The client
  removes the SDK's `Authorization` header in that case.
- A diagnosis request can fall back to a deterministic mock if it fails. Check the logs and
  resulting evidence rather than assuming every completed run used a real diagnosis.

Verify connectivity before sending a real issue:

```bash
uv run python scripts/check_llm.py
```

### 3. GitHub access and publishing

```dotenv
GITHUB_TOKEN=<your-github-personal-access-token>
GITHUB_BASE_URL=https://github.com
GITHUB_API_URL=https://api.github.com
PUBLISH_ENABLED=true
GITHUB_PR_BRANCH_PREFIX=ash-agent/fix
```

To create a fine-grained token:

1. Open GitHub **Settings → Developer settings → Personal access tokens → Fine-grained tokens**.
2. Select **Generate new token**, choose the repository owner, and set an expiration.
3. Under **Repository access**, select the target repository or repositories.
4. Grant **Contents: Read and write** and **Pull requests: Read and write**.
5. If you intend to permit workflow-file changes, also grant **Workflows: Read and write**.
6. Generate the token and save it as `GITHUB_TOKEN` in `.env`. Complete any organization approval
   or SSO requirements that apply to the repository.

A classic PAT with the `repo` scope is another option, but grants broader access. Prefer a
repository-scoped token for experiments. Do not rely on your machine's Git credential helper to
prove token access; the worker uses the configured token.

Without a token, PR publishing is skipped. Public cloning can still work, but private cloning
requires access. A validated local run is not the same as a published PR.

### 4. Retrieval, sandbox, and repair controls

Keep these enabled for a real end-to-end run:

```dotenv
RETRIEVAL_ENABLED=true
EMBEDDING_PROVIDER=fastembed
SANDBOX_ENABLED=true
REPAIR_ENABLED=true
PUBLISH_ENABLED=true

SANDBOX_IMAGE=ash-agent-sandbox:py312
SANDBOX_NETWORK=none
SANDBOX_INSTALL_NETWORK=bridge
SANDBOX_TIMEOUT_SECONDS=120
SANDBOX_MEMORY_LIMIT=512m
SANDBOX_CPUS=1.0
SANDBOX_PIDS_LIMIT=128

MAX_ATTEMPTS=3
JOB_MAX_RETRIES=2
GRAPH_RECURSION_LIMIT=50
RUN_JOB_TIMEOUT_SECONDS=3600
```

| Setting | Purpose |
|---|---|
| `MAX_ATTEMPTS` | Maximum patch/validation attempts before escalation |
| `JOB_MAX_RETRIES` | RQ retries for worker exceptions; separate from patch attempts |
| `GRAPH_RECURSION_LIMIT` | LangGraph step-count safety limit |
| `SANDBOX_INSTALL_TIMEOUT_SECONDS` | Dependency installation timeout; default `180` seconds |
| `SANDBOX_TEST_ARGS` | Pytest arguments; default `-q --disable-warnings --maxfail=5` |
| `SANDBOX_KEEP_WORKSPACE` | Keep reproduction workspaces for debugging; default `false` |
| `WORKSPACE_DIR` | Cached checkouts; default `.ash_agent/workspaces` |
| `EMBEDDING_CACHE_DIR` | Local model cache; default `.ash_agent/models` |
| `MOCK_REPAIR_SUCCESS_ATTEMPT` | Mock validation control, not a real-repair success setting |

Disabling retrieval or sandbox/repair services can activate fallback or mock paths. Use mock mode
for development, not as evidence that the agent repaired a real repository.

**Restart the API and worker after changing `.env`.** Both processes read settings independently
and must use the same queue and database.

## Run the project

### 1. Start infrastructure

```bash
docker compose --env-file .env -f infra/compose.yaml up -d
docker compose --env-file .env -f infra/compose.yaml ps
```

Wait for PostgreSQL, Valkey, and Neo4j to become healthy. Qdrant should be running.

### 2. Apply migrations and build the sandbox

```bash
uv run alembic upgrade head
docker build -t ash-agent-sandbox:py312 infra/sandbox
```

The image tag must match `SANDBOX_IMAGE`. The worker initializes the LangGraph checkpoint schema
on startup; Alembic manages the application tables.

### 3. Start the API and worker in separate terminals

Terminal 1:

```bash
uv run ash-agent
```

Terminal 2:

```bash
uv run ash-agent-worker
```

On macOS the worker uses RQ's `SimpleWorker` to avoid fork-safety crashes. Linux uses the standard
forked worker. Both entry points run on the host; Compose runs the supporting services.

### 4. Verify readiness

```bash
curl -s http://localhost:8000/health/ready | uv run python -m json.tool
```

Expected response:

```json
{
  "status": "ok",
  "components": {
    "postgres": "ok",
    "valkey": "ok"
  }
}
```

Readiness checks PostgreSQL and Valkey, not the LLM, search stores, or sandbox image. Run the LLM
check above and inspect the worker logs for those integrations.

Open **http://localhost:8000/docs** for the interactive API documentation.

### Stop the project

Press **Ctrl+C** in the API, worker, and tunnel terminals. To stop the backing services without
removing their volumes:

```bash
docker compose --env-file .env -f infra/compose.yaml down
```

## Use with a real GitHub repository

### 1. Choose a controlled test repository

Use a repository you own, or a fork with permission to run the agent. The project has been
exercised against `The-Tourist-170/dummy_project` for pytest failures and
`The-Tourist-170/calculator-testing` for issue-scoped/runtime repairs. Repository visibility and
existing fixes may change; make sure your selected commit still contains the bug you report.

For the first run, choose a small Python project with:

- A reproducible bug and a clear expected result.
- A supported dependency layout: `uv.lock` with a `test` extra, a suitable `pyproject.toml`, or
  `requirements.txt`.
- Failing pytest tests for test-based repairs, **or** an explicit sandbox-compatible runtime
  command for behavior without pytest coverage.
- No requirement for production credentials, external databases, or host-only services during
  validation.

Make sure your token includes **this exact repository**, especially for a private fork. Do not
use a valuable production repository for an initial experiment.

### 2. Expose the local webhook endpoint

In a third terminal:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

Copy the HTTPS URL printed by cloudflared. Append `/webhooks/github` to that URL; this is the
webhook destination you will enter in GitHub. Keep the tunnel running. Quick-tunnel URLs change
on restart, so update the webhook if you restart the tunnel.

### 3. Configure the repository webhook

In the **target repository**, open **Settings → Webhooks → Add webhook**:

| GitHub field | Value |
|---|---|
| Payload URL | The tunnel's HTTPS URL followed by `/webhooks/github` |
| Content type | `application/json` |
| Secret | The exact `GITHUB_WEBHOOK_SECRET` from the agent's `.env` |
| Events | Select individual events: **Issues** and **Workflow runs** |
| Active | Enabled |

Save the webhook. GitHub's initial ping is not a repair request.

Supported repair triggers are:

- `issues` with action `opened` or `reopened`. Pull-request issue payloads are ignored.
- `workflow_run` with action `completed` and conclusion `failure`, `timed_out`, or `startup_failure`.

Other deliveries are recorded but ignored. A **202 Accepted** response means the delivery was
handled, not necessarily that a repair was queued. Check the response's `status` and `run_id`.

### 4. Open an issue with enough evidence

For a pytest-covered bug, include the failing behavior, expected behavior, relevant function/file,
and test command. For example, on a calculator repository where `add(2, 3)` incorrectly returns
`-1`:

````markdown
## Bug
`add(2, 3)` returns `-1` instead of `5`.

## Reproduction
```bash
uv run pytest tests/test_calculator.py::test_addition_returns_sum -q
```

## Expected behavior
Only `add` should be corrected to return the sum. Other calculator defects are separate issues.
````

For a runtime-only issue, include an executable command and an explicit output expectation:

````markdown
## Bug
`raise_to_power(2, 3)` produces `5` instead of `8`.

## Reproduction
```bash
PYTHONPATH=src python3 -c 'from calculator_bug_lab import raise_to_power; print(raise_to_power(2, 3))'
```

## Expected behavior
`raise_to_power(2, 3)` should return `8`.

There is no pytest coverage for this behavior. Other calculator functions are out of scope.
````

Adapt the file and function names to the repository you selected. The example is not a command
to run in the ash-agent repository itself.

Runtime commands are extracted from inline or fenced code blocks. Leading environment assignments
are passed as separate `env` arguments inside Docker, not expanded by a host shell. Quoted
`Expected output:` and `should return` / `should print` expectations are recognized. Output
matching currently uses a substring, not a general semantic assertion.

If the issue reproduces with `docker compose run`, the agent can extract the named service's
underlying `command` array and run it directly. It **does not start the Compose service** or
validate its container networking.

### 5. Alternatively, trigger a failed workflow

In the target repository, add a CI workflow if needed. The included
[`examples/github-actions-ci.yml`](examples/github-actions-ci.yml) is a starting point for a
uv-based Python project with a `test` extra. Copy it to `.github/workflows/ci.yml` **in the target
repository**, review it, and commit/push it there.

Push a commit that reproduces the failure, use **Actions → Run workflow**, or rerun a failing job.
GitHub sends the completed `workflow_run` event to ash-agent. Unlike an issue, a workflow-run
repair targets the failing workflow's test set rather than a narrowly described issue.

### 6. Check the delivery and run

1. Open **Settings → Webhooks → your webhook → Recent Deliveries** in the target repository.
2. Open the issue/workflow delivery and inspect the response.
3. An accepted repair includes a `run_id`. Copy it.
4. Query the run locally:

```bash
curl -s http://localhost:8000/runs | uv run python -m json.tool
```

For a specific run, replace `RUN_ID` with the returned UUID:

```bash
curl -s http://localhost:8000/runs/RUN_ID | uv run python -m json.tool
```

### 7. Verify the pull request—not just the job log

A published repair should have:

- `status: "passed"` and `publish_status: "published"`.
- A nonempty `pr_url` pointing to the target repository.
- A branch such as `ash-agent/fix-<run-id-prefix>`.
- Only issue-related edits in the diff.
- Validation evidence in the PR body and `Fixes #N` for an issue-triggered repair.

Review the diff, test results, and repository CI before merging manually. A runtime repair proves
the recorded command's behavior; it is not proof that every integration or deployment works.

For issue-scoped pytest repairs, a full suite may still fail on the recorded unrelated baseline
failures. The selected target tests must pass without introducing new failures.

### Optional: send a signed event without a tunnel

After creating an actual issue, you can exercise the same local webhook handler with a synthetic
signed delivery. This does not test GitHub-to-tunnel connectivity. Replace the repository and
issue number with ones you control; this example uses the original calculator issue:

```bash
uv run python scripts/send_test_webhook.py \
  --repo The-Tourist-170/calculator-testing \
  --branch main \
  --event issues \
  --issue-number 1 \
  --issue-title '[BUG] Addition subtracts instead of adding' \
  --issue-body 'add(2, 3) returns -1. Expected: 5. Fix only add.'
```

The script reads the webhook secret from `.env` and prints the response and run ID. It can create
real repair runs and PRs when publishing is enabled. Do not run it against repositories you do
not intend to modify. The same delivery ID is deduplicated; a new ID creates a separate run.

## Monitor runs and inspect results

### Run states

| State or field | Meaning |
|---|---|
| `pending` / `queued` | A run exists and is awaiting dispatch or a worker |
| `running` | The worker claimed the run |
| `passed` | The configured validation path passed; inspect publishing separately |
| `escalated` | Reproduction/scope could not be established, a guard blocked publication, or repair attempts were exhausted |
| `failed` | Worker execution failed after exception retries |
| `publish_status: skipped` | Publishing disabled or token unavailable |
| `publish_status: failed` | Validation passed, but the GitHub publishing step failed |
| `publish_status: published` | A PR URL was recorded |

**RQ's “Job OK” means the job function completed—not that a PR was created.** An escalated run
can still be a successfully processed RQ job. Always inspect `status`, `last_error`,
`publish_status`, and `pr_url`.

Detailed diagnosis, scope, reproduction logs, and runtime evidence are stored in PostgreSQL
checkpoints; the run API exposes summary/audit fields, not the full graph state.

### API reference

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/webhooks/github` | Receive signed GitHub events |
| `GET` | `/runs` | List runs; accepts `status`, `limit`, and `offset` |
| `GET` | `/runs/{run_id}` | Inspect one run |
| `GET` | `/metrics` | Run counts, average attempts, published PRs, and stored lessons |
| `GET` | `/health` | Liveness |
| `GET` | `/health/ready` | PostgreSQL and Valkey readiness |
| `GET` | `/docs` | Interactive OpenAPI documentation |

```bash
curl -s 'http://localhost:8000/runs?status=escalated&limit=10' | uv run python -m json.tool
curl -s http://localhost:8000/metrics | uv run python -m json.tool
```

### Logs

Application logs use a compact timestamp, level, shortened component name, and message.
Levels and components have distinct colors in a terminal. Use `LOG_LEVEL=DEBUG` for more detail.

- `LOG_COLORS=auto`: color when stdout is a terminal.
- `LOG_COLORS=always`: preserve color when piping to a viewer or `tee`.
- `LOG_COLORS=never`: plain text for log files.
- `NO_COLOR=1`: disable automatic color output.

An escalated terminal run is not automatically restarted by restarting the worker. After fixing
the cause, close/reopen the issue to send a fresh supported event. Avoid duplicate concurrent
runs for the same issue.

## Security and current limitations

- **Experimental and Python-focused.** The sandbox and parser target Python projects; a command
  prefix being recognized does not mean its runtime is installed in the provided image.
- **Review is mandatory.** Scope anchoring and deterministic file/symbol guards reduce unrelated
  edits, but do not prove semantic correctness of every line inside an allowed scope.
- **Docker is isolation, not a guarantee against hostile code.** Use controlled test repositories.
  Containers run as the host UID with a read-only root filesystem, dropped capabilities,
  `no-new-privileges`, resource limits, and timeouts. No host secrets or Docker socket are mounted.
- **Network boundaries differ by step.** Dependency installation uses the install network; test
  and runtime execution use `SANDBOX_NETWORK=none` by default.
- **Runtime-only validation is deliberately narrow.** It runs the extracted command without a
  separate dependency-install stage. Missing dependencies, unsupported commands, ambiguous
  expectations, or unavailable external services can prevent a useful reproduction.
- **Packaging/build repair is out of scope.** Invalid wheels, broken console entry points, and
  installation failures are not supported repair targets.
- **Compose execution is indirect.** The underlying service command runs in the agent sandbox;
  real Compose services, networking, volumes, and startup behavior are not reproduced.
- **Local embeddings, external LLM.** FastEmbed runs locally, but relevant code and logs are sent
  to your configured LLM endpoint. Use a provider appropriate for your repository's data.
- **Local-development API.** Webhook HMAC verification does not authenticate the run/metrics/docs
  endpoints. Add access controls before exposing the entire API beyond a controlled environment.
- **No auto-merge or deployment.** PR creation is the final automated repository action.

## Troubleshooting

| Symptom | Check / resolution |
|---|---|
| Webhook returns `401` | Match the GitHub secret to `GITHUB_WEBHOOK_SECRET`; restart the API after editing `.env`. |
| Delivery is ignored | Verify event type, action, and workflow conclusion. Plain `push`, issue edits, and PR issue payloads are not repair triggers. |
| Run stays queued | Start the worker; ensure API and worker share `RQ_QUEUE_NAME`, `VALKEY_URL`, and `DATABASE_URL`. |
| Readiness returns `503` | Inspect Compose status and PostgreSQL/Valkey connectivity. |
| No PR despite “Job OK” | Read the run's terminal status, `last_error`, `publish_status`, and `pr_url`. |
| Run escalates at scope | Provide a concrete reproduction command, expected result, and relevant file/function; do not use unrelated failing tests as evidence. |
| Runtime exits `0` but output is wrong | Include an explicit quoted expectation such as `Expected output: \`8\`` or `should return \`8\``. |
| Old runs still escalated after a fix | Restart the worker, then close/reopen the issue for a new run; terminal runs are not automatically retried. |
| Clone or publish returns `403` | Confirm the PAT includes the exact repository, required write permissions, and organization approval/SSO. |
| Publishing is skipped | Configure `GITHUB_TOKEN` and `PUBLISH_ENABLED=true`; restart the worker. |
| LLM fails or diagnosis says `mock` | Check model ID, base URL, authentication, tool-call support, and `scripts/check_llm.py`. |
| Sandbox image unavailable | Build `infra/sandbox` with the exact `SANDBOX_IMAGE` tag. |
| Installation fails | Check the repository's test extra/lockfile and captured install logs. Packaging failures are not repaired. |
| Neo4j authentication fails | Match `.env` credentials to the database's existing credentials; changing `.env` does not reset stored credentials. |
| Tunnel stops receiving events | Restart the tunnel and update GitHub's Payload URL to the new HTTPS URL. |

## Development

The development checks are:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The suite uses PostgreSQL and Valkey even for many unit tests. Create a **dedicated** test database
once, after starting infrastructure with the default development credentials:

```bash
docker compose --env-file .env -f infra/compose.yaml exec postgres \
  createdb -U ash ash_agent_test
```

If it already exists, do not recreate it. Default test connections are:

```dotenv
TEST_DATABASE_URL=postgresql+asyncpg://ash:ash@localhost:5432/ash_agent_test
TEST_VALKEY_URL=redis://localhost:6379/15
```

Export overrides in the shell when using different test services. **Never point tests at your
application or production database:** fixtures recreate application tables and flush the selected
Valkey database.

Additional integration checks:

```bash
uv run python scripts/check_llm.py
uv run python scripts/index_repository.py --query 'calculate_total tax discount'
uv run python scripts/reproduce_repository.py
```

The last two helpers default to the sample repository and require their corresponding live
services, repository access, and sandbox image. The normal test suite uses deterministic fallbacks
for LLM/retrieval/sandbox execution; passing it is not a substitute for checking a real repair PR.

## Project structure

```text
src/ash_agent/
├── agents/          # LangGraph state, nodes, routing, and graph assembly
├── api/             # Webhook, run, metrics, and health endpoints
├── integrations/    # Structured OpenAI-compatible LLM client
├── intelligence/    # Parsing, retrieval, issue scoping, and patch guards
├── observability/   # Console logging
├── persistence/     # Database models, search stores, and checkpoints
├── queue/           # RQ connection, jobs, and worker entry point
├── sandbox/         # Docker execution and reproduction policies
└── services/        # Repository, repair, retrieval, memory, and PR orchestration
infra/               # Local backing services and sandbox image
migrations/          # Alembic application schema migrations
scripts/             # Connectivity, indexing, reproduction, and webhook helpers
tests/               # Unit and integration tests
examples/            # Example target-repository CI workflow
```

The public setup reference is [`.env.example`](.env.example). Local phase reports and interview
study notes under `reports/` are intentionally excluded from version control; they are not required
to install or use the project.
