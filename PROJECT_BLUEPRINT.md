# DisputeDesk: Multi-Agent Card-Dispute Resolution Platform

> Portfolio project. Target: build in 1-2 days with Claude Code. Runs locally first (docker-compose), deployable to AWS later.
> Not production-grade, but production-style: guardrails, observability, evals, security, MCP and RAG are all first-class.

---

## 1. Project Description

A customer disputes a card transaction. DisputeDesk triages the dispute, gathers evidence through MCP tools, checks it against dispute-rule documents (RAG), drafts a decision plus a merchant-response letter, reviews its own draft, and routes high-risk cases to a human for approval.

**Why this domain:** payments disputes are regulated and audit-heavy, so guardrails, security and evals have a real reason to exist.

### Feature checklist

| # | Requirement | How it is met |
|---|---|---|
| 1 | Multi-agent | LangGraph graph: Router, Evidence, Policy, Resolution, Reviewer, Human gate |
| 2 | Guardrails | NeMo Guardrails (input/output rails) + Presidio (PII) + Pydantic (schemas) + plain-Python policy rules |
| 3 | Observability | OpenTelemetry traces, Langfuse (self-hosted), cost/latency/guardrail metrics |
| 4 | Evals | Golden dataset, Ragas/DeepEval metrics, Promptfoo red-team, CI gate |
| 5 | Security | JWT auth, per-agent least-privilege tool scopes, injection tests, secret scanning, dependency audit, audit log |
| 6 | MCP | 3 FastMCP servers: `transactions`, `merchant`, `cases` |
| 7 | RAG | pgvector, hybrid search (BM25 + vectors), reranker (optional), cited answers |

### Non-goals
Real card-network integrations, real PII, latency tuning, horizontal scale, multi-tenant billing.

---

## 2. Architecture

```
                 +--------------------+
  Reviewer UI -->|  FastAPI Gateway   |<-- JWT auth, rate limit, request IDs
 (Streamlit)     +---------+----------+
                           |
                 +---------v----------+      input rails (NeMo + Presidio)
                 |  LangGraph Runtime |----> output rails (PII, grounding)
                 +---------+----------+
        +---------+--------+---------+-----------+
        v         v        v         v           v
     Router   Evidence   Policy   Resolution   Reviewer --> Human approval gate
                 |         |                                  (LangGraph interrupt)
                 | MCP     | RAG
        +--------v---+  +--v-----------+
        | MCP servers|  | pgvector     |
        | txn/merch/ |  | policy docs  |
        | cases      |  +--------------+
        +-----+------+
              v
          Postgres (cases, audit_log, synthetic txns)

  Cross-cutting: OpenTelemetry -> Langfuse | Audit log | Secrets via env (local) / Secrets Manager (AWS)
```

### Agents

| Agent | Responsibility | Tools allowed |
|---|---|---|
| Router | Classify reason code (fraud, not-received, duplicate, not-as-described) and risk tier | none |
| Evidence | Gather transaction history, merchant profile, delivery data | MCP: `transactions.*`, `merchant.*` (read-only) |
| Policy | Retrieve applicable dispute rules, return cited summary | RAG retriever only |
| Resolution | Propose accept / reject / escalate and draft letter | MCP: `cases.draft_*` |
| Reviewer | Critique draft against evidence + policy, block ungrounded claims | none |
| Human gate | Approval for refunds above threshold or low-confidence | UI/API |

### Tech stack

Python 3.12, uv, FastAPI, LangGraph, MCP Python SDK (FastMCP), Postgres + pgvector, NeMo Guardrails, Presidio, Pydantic v2, OpenTelemetry, Langfuse, Ragas/DeepEval, Promptfoo, Streamlit, Docker Compose, GitHub Actions, ruff, mypy, pytest.

LLM access goes through one `llm.py` factory. Providers are Anthropic API (default), Bedrock (AWS phase) and Ollama (offline).

### Repo layout

```
disputedesk/
  CLAUDE.md
  PROJECT_BLUEPRINT.md
  README.md
  docker-compose.yml
  pyproject.toml
  .env.example
  .github/workflows/{ci.yml,evals.yml,security.yml,cd.yml}
  docs/adr/                      # architecture decision records
  data/{policies/,seed/}         # synthetic rule docs + seed data
  src/disputedesk/
    api/                         # FastAPI app, auth, routes
    agents/                      # graph.py, router.py, evidence.py, policy.py, resolution.py, reviewer.py
    mcp_servers/{transactions,merchant,cases}/
    rag/                         # ingest.py, retriever.py
    guardrails/                  # interface.py, nemo/, presidio.py, policy_rules.py, bedrock.py (later)
    observability/               # otel.py, langfuse.py
    security/                    # jwt.py, scopes.py, audit.py
    llm.py, config.py
  evals/{datasets/,run_evals.py,promptfoo/}
  tests/{unit/,integration/}
  infra/                         # Terraform/CDK (phase 2)
  ui/                            # Streamlit reviewer app
```

---

## 3. Milestones

Each milestone ends with a **working, committed, tested** state. Do not start the next until the acceptance criteria pass.

### M0: Scaffold (about 45 min)
**Goal:** a repo that builds, lints, tests and runs infra locally.
- uv project, `pyproject.toml`, ruff, mypy, pytest, pre-commit
- `docker-compose.yml`: Postgres+pgvector, Langfuse (+ its DB), OTel collector
- `.env.example`, `config.py` (pydantic-settings), `llm.py` provider factory
- `CLAUDE.md` (see section 5), `README.md` skeleton, ADR-001 (stack choices)
- Initial CI workflow (lint + test)
**Accept:** `docker compose up` is healthy; `uv run pytest` passes (one smoke test); CI is green on first push.

### M1: Data and MCP servers (about 2 h)
**Goal:** agents have tools and data to call.
- Synthetic seed data (200 transactions, 20 merchants, 50 customers, 40 disputes) via a seed script. All data is fake.
- Three FastMCP servers: `transactions` (get_txn, list_txns, get_history), `merchant` (get_profile, get_dispute_rate, get_delivery_status), `cases` (get_case, save_draft, update_status).
- Typed tool schemas, input validation, read-only vs write separation
- Every tool call writes to `audit_log`
**Accept:** each server starts standalone; an MCP client script lists and calls every tool; unit tests cover each tool, including invalid input.

### M2: RAG over dispute policies (about 2 h)
**Goal:** cited policy answers.
- Write about 15 markdown policy docs in `data/policies/` (reason codes, evidence requirements, time limits, refund rules)
- `ingest.py`: chunk with heading-aware splitting, embed, store in pgvector, plus a BM25/tsvector index
- `retriever.py`: hybrid search with reciprocal rank fusion, optional reranker; returns chunks with `source` and `section`
- Policy agent function returns structured `PolicyAnswer{summary, citations[]}`
**Accept:** a 10-question retrieval test set reaches hit@5 of at least 0.8; every answer carries at least one citation; ingestion is idempotent.

### M3: Multi-agent graph (about 3 h)
**Goal:** one dispute goes end-to-end through the agents.
- LangGraph state schema (Pydantic), nodes for each agent, conditional edges by risk tier
- Reviewer loop: at most 2 revision cycles, then escalate
- Human-approval interrupt with resumable checkpoints (Postgres checkpointer)
- FastAPI endpoints: `POST /disputes`, `GET /disputes/{id}`, `POST /disputes/{id}/approve`
- Structured outputs everywhere (Pydantic), with retries and timeouts on every LLM and tool call
**Accept:** 5 seeded disputes complete end-to-end; a high-value dispute pauses for approval and resumes after `approve`; integration test covers happy path and reviewer-rejection path.

### M4: Guardrails (about 2 h)
**Goal:** unsafe input/output is blocked, deterministic rules are enforced in code.
- `guardrails/interface.py`: `check_input()`, `check_output()`, `check_tool_call()`
- NeMo Guardrails config: jailbreak/injection input rail, off-topic rail, output rail for PII and grounding. NeMo wraps the edges only; LangGraph still owns orchestration.
- Presidio redaction on inputs, outputs and logs
- Plain-Python rules in `policy_rules.py`: refund cap, per-agent tool allowlist, required-evidence check
- Guardrail events are emitted as trace attributes and metrics
**Accept:** unit tests show a PII-containing input is redacted, a jailbreak prompt is blocked, a tool call outside an agent's allowlist is denied, a refund over the cap forces human approval.

### M5: Security (about 1.5 h)
**Goal:** a defensible threat model, not just a feature list.
- JWT auth (roles: `analyst`, `approver`, `admin`), scope checks on every route
- Per-agent tool scopes enforced at the MCP client layer
- Untrusted-content handling: merchant-supplied text is wrapped and labelled as data, never as instructions
- Append-only `audit_log` (who, what, which agent, which tool, case ID, timestamp)
- `docs/THREAT_MODEL.md` (STRIDE-lite, 1 page): prompt injection via evidence, tool abuse, data exfiltration, privilege escalation
- Secrets only via env; gitleaks, pip-audit and Trivy in CI
**Accept:** unauthenticated and wrong-role requests are rejected (tests); an injection planted in merchant evidence does not change the decision (test); security workflow is green.

### M6: Observability (about 1.5 h)
**Goal:** every case is traceable end to end.
- OpenTelemetry instrumentation: FastAPI, LangGraph nodes, MCP calls, DB
- Langfuse tracing for LLM calls (prompt, response, tokens, cost)
- Custom attributes: `case_id`, `agent`, `guardrail.triggered`, `risk_tier`
- Metrics: latency per agent, cost per case, guardrail trigger rate, revision-loop count
- One saved Langfuse dashboard (screenshot for the README)
**Accept:** a single case shows one connected trace across API, agents and MCP calls; cost per case is visible; screenshot committed to `docs/`.

### M7: Evals (about 2 h)
**Goal:** measurable quality and a CI regression gate.
- `evals/datasets/golden.jsonl`: 30-50 synthetic disputes with expected reason code, expected decision and expected policy citations. Include 8-10 adversarial and injection cases.
- Metrics: router accuracy, decision accuracy, citation faithfulness and answer relevance (Ragas/DeepEval), letter quality (LLM-as-judge with a rubric), guardrail catch rate
- Promptfoo red-team config for injection and jailbreak
- `run_evals.py` outputs `evals/results/latest.json` and a markdown summary table
- Thresholds in `evals/thresholds.yml` (for example router accuracy at least 0.9, decision accuracy at least 0.8, faithfulness at least 0.85, injection pass rate 100%)
**Accept:** eval run exits non-zero when any threshold fails; results table is pasted into the README.

### M8: UI, docs and polish (about 2 h)
**Goal:** a repo a recruiter understands in 2 minutes.
- Streamlit reviewer UI: case list, agent trace summary, approve/reject
- README: problem, architecture diagram, quickstart (3 commands), eval results table, trace screenshot, threat-model link, "what I'd do next"
- 3-4 ADRs: LangGraph vs alternatives, NeMo + Presidio choice, hybrid RAG, MCP boundaries
- Record a 60-90 second demo GIF
**Accept:** a fresh clone runs with `cp .env.example .env && docker compose up && make seed`; README is complete.

### M9 (Phase 2): AWS deployment
**Goal:** the same app on AWS, with infrastructure as code.
- Terraform or CDK: VPC, ECS Fargate services (api, 3 MCP servers, ui), RDS Postgres + pgvector, ALB, ECR, Secrets Manager, Cognito (replaces local JWT), CloudWatch + ADOT
- Switch LLM to Bedrock (Claude), add `bedrock.py` guardrail implementation behind the same interface
- Enable the CD workflow (section 4.4)

**Accept:** a tagged release deploys to a dev environment; smoke test passes against the ALB URL.

**Suggested schedule:** Day 1 is M0-M3. Day 2 is M4-M8. M9 is separate.
**If time runs short, cut in this order:** Streamlit UI (use Swagger), reranker, Langfuse dashboard polish. Do not cut evals or injection tests.

---

## 4. GitHub Actions

### 4.1 `ci.yml`: lint, type check, tests

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint-test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: disputedesk
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 5s --health-timeout 5s --health-retries 10
    env:
      DATABASE_URL: postgresql://postgres:postgres@localhost:5432/disputedesk
      LLM_PROVIDER: fake        # deterministic stub so unit/integration tests need no API key
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv python install 3.12
      - run: uv sync --all-extras --dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy src
      - run: uv run pytest tests/unit -q
      - run: uv run python -m disputedesk.rag.ingest --seed
      - run: uv run pytest tests/integration -q
```

### 4.2 `evals.yml`: quality gate

Runs on PRs that touch agents, prompts, guardrails or RAG. It uses a real LLM, so it needs a repository secret.

```yaml
name: Evals
on:
  pull_request:
    paths:
      - "src/disputedesk/agents/**"
      - "src/disputedesk/guardrails/**"
      - "src/disputedesk/rag/**"
      - "evals/**"
  workflow_dispatch:

jobs:
  evals:
    # skip for forks (no access to secrets)
    if: github.event.pull_request.head.repo.full_name == github.repository || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: disputedesk
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 5s --health-timeout 5s --health-retries 10
    env:
      DATABASE_URL: postgresql://postgres:postgres@localhost:5432/disputedesk
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      LLM_PROVIDER: anthropic
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv python install 3.12
      - run: uv sync --all-extras --dev
      - run: uv run python -m disputedesk.rag.ingest --seed
      - name: Run golden-set evals (fails if below thresholds)
        run: uv run python evals/run_evals.py --thresholds evals/thresholds.yml
      - name: Promptfoo red-team
        run: npx --yes promptfoo@latest eval -c evals/promptfoo/redteam.yaml --no-cache
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: eval-results
          path: evals/results/
      - name: Post summary
        if: always()
        run: cat evals/results/summary.md >> "$GITHUB_STEP_SUMMARY"
```

> Keep the golden set small (30-50 cases) so a run costs cents, not dollars.

### 4.3 `security.yml`: secrets, dependencies, containers

```yaml
name: Security
on:
  push:
    branches: [main]
  pull_request:
  schedule:
    - cron: "0 9 * * 1"

permissions:
  contents: read
  security-events: write

jobs:
  gitleaks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

  dependencies:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-extras
      - run: uvx pip-audit

  container-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t disputedesk-api:scan -f Dockerfile .
      - uses: aquasecurity/trivy-action@0.28.0
        with:
          image-ref: disputedesk-api:scan
          severity: CRITICAL,HIGH
          exit-code: "1"
          ignore-unfixed: true

  codeql:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: github/codeql-action/init@v3
        with:
          languages: python
      - uses: github/codeql-action/analyze@v3
```

### 4.4 `cd.yml`: deploy to AWS (Phase 2, M9)

Uses GitHub OIDC, so there are no long-lived AWS keys in GitHub. Create the IAM role and OIDC provider with Terraform first.

```yaml
name: CD
on:
  push:
    tags: ["v*"]
  workflow_dispatch:

permissions:
  id-token: write
  contents: read

env:
  AWS_REGION: us-east-1
  ECR_REPOSITORY: disputedesk-api
  ECS_CLUSTER: disputedesk-dev
  ECS_SERVICE: disputedesk-api

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: dev            # add required reviewers in repo settings for prod
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}
      - id: ecr
        uses: aws-actions/amazon-ecr-login@v2
      - name: Build and push
        run: |
          IMAGE=${{ steps.ecr.outputs.registry }}/${{ env.ECR_REPOSITORY }}:${{ github.sha }}
          docker build -t "$IMAGE" -f Dockerfile .
          docker push "$IMAGE"
          echo "IMAGE=$IMAGE" >> "$GITHUB_ENV"
      - name: Render task definition
        id: task
        uses: aws-actions/amazon-ecs-render-task-definition@v1
        with:
          task-definition: infra/ecs/api-task-def.json
          container-name: api
          image: ${{ env.IMAGE }}
      - name: Deploy
        uses: aws-actions/amazon-ecs-deploy-task-definition@v2
        with:
          task-definition: ${{ steps.task.outputs.task-definition }}
          service: ${{ env.ECS_SERVICE }}
          cluster: ${{ env.ECS_CLUSTER }}
          wait-for-service-stability: true
      - name: Smoke test
        run: curl --fail --retry 5 --retry-delay 5 "${{ secrets.DEV_BASE_URL }}/healthz"
```

### Repo settings to enable
- Branch protection on `main`: require CI + Security to pass, require PR.
- Secrets: `ANTHROPIC_API_KEY`, and later `AWS_DEPLOY_ROLE_ARN` and `DEV_BASE_URL`.
- Dependabot for `pip` and `github-actions`.
---
