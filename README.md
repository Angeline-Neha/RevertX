# RevertX

**Fault-Isolated Saga Orchestrator with Native UPI Reconciliation**

> *Single source of truth for AI-agent payment failure recovery.*

---

## What it does

Aegis sits between an AI agent and payment APIs. When the agent's multi-step payment workflow fails (hard crash or silent mismatch), Aegis:

1. **Detects** the failure via deterministic reconciliation (expected vs actual)
2. **Classifies** the fault — network/infrastructure vs agent logic — deterministically (zero LLM)
3. **Compensates** — walks backward through the workflow log, reads merchant policies (via an isolated policy-extraction LLM service), and refunds what can be refunded, with a second isolated LLM service flagging unusual patterns for human review afterward (advisory-only, never gating a refund or dispute)
4. **Routes** — generates either a UDIR-shaped dispute payload (network fault) or an Internal Liability Report (agent fault). Never both. Never wrong.

---

## Quick Start

The system has three pieces of infrastructure (Postgres, Redis, RabbitMQ)
and six Python processes (3 mock merchants + 2 isolated LLM services + the
proxy), plus a background worker and the dashboard. `run_demo.py` starts
everything for you and is the easiest way to see it work; the steps below
are what it's doing under the hood, for when you need to run something by
hand or debug a piece in isolation.

### 0. Configure environment variables

```bash
cp .env.example .env
# fill in GEMINI_API_KEY at minimum — everything else has a working default
```

### 1. Start infrastructure (Postgres, Redis, RabbitMQ)

```bash
docker compose up -d redis-aegis postgres-aegis rabbitmq-aegis
```

This is Redis on port **6380** (not the default 6379, so it won't clash
with anything else on your machine), Postgres on port **5433**, and
RabbitMQ on port **5673**. `db/client.py`'s migrations run automatically
the first time the proxy or worker starts — no manual schema setup needed.

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Start the six application services (separate terminals)

```bash
# Terminal 1 — Merchant A (CRM)
uvicorn mock_merchants.merchant_a_crm:app --port 8001

# Terminal 2 — Merchant B (Hotel)
uvicorn mock_merchants.merchant_b_hotel:app --port 8002

# Terminal 3 — Merchant C (Domain)
uvicorn mock_merchants.merchant_c_domain:app --port 8003

# Terminal 4 — Policy-extraction LLM service (isolated, single-purpose)
uvicorn engine.policy_service:app --port 8004

# Terminal 5 — Anomaly/triage LLM service (isolated, single-purpose, advisory-only)
uvicorn engine.anomaly_service:app --port 8005

# Terminal 6 — Aegis MCP Proxy (includes the WebSocket for the dashboard)
uvicorn proxy.mcp_proxy:app --port 8000
```

`engine/policy_service.py` and `engine/anomaly_service.py` can also run as
Docker containers (`policy-extractor` / `anomaly-detector` in
`docker-compose.yml`) if you'd rather not manage two more local terminals —
`run_demo.py` runs them locally via uvicorn instead, for faster
edit-and-restart iteration during development.

### 4. Start the compensating-agent worker (separate terminal)

```bash
python -m compensating_agent.worker
# or: python run_worker.py [--debug]
```

This is the process that actually runs the LangGraph compensation saga
when a workflow fails. It builds a real Postgres-backed checkpointer at
startup — see [Key Design Invariants](#key-design-invariants) below for
why that matters. On Windows, both launch paths automatically use the
Selector event loop instead of the default Proactor loop, since
`psycopg`'s async driver (which the checkpointer needs) doesn't work
under Proactor.

### 5. Start the dashboard

```bash
cd dashboard
echo "VITE_PROXY_API_KEY=test-key-123" > .env   # must match PROXY_API_KEY
npm install
npm run dev
# Open http://localhost:5173
```

### 6. Run the demo

```bash
python -m primary_agent.procurement_agent
```

Copy the printed **workflow UUID**, paste it into the dashboard, and watch
live.

### All of the above, one command

```bash
python run_demo.py
```

Starts infrastructure, waits for it to actually be reachable (fails fast
with a clear message if it isn't, rather than 15 seconds into a demo that's
about to fail confusingly), starts all six application services plus the
worker, waits for those to come up, then runs the demo client automatically
and tears everything down afterward.

On Windows, `start_servers.ps1` does the same thing as background jobs in
one PowerShell window: it also clears out any processes still holding
ports 8000-8005 from a previous run before starting new ones (an orphaned
process from a closed terminal otherwise silently serves stale code for
the rest of the session with no error surfaced anywhere), and polls for
each port to come up with a real timeout instead of a fixed sleep.

### Re-running against the same workflow ID

`primary_agent/procurement_agent.py` accepts an optional workflow_id via
CLI arg, so you can keep the same dashboard URL open across re-runs
instead of pasting a new UUID each time:

```bash
python -m primary_agent.procurement_agent <existing-workflow-id>
```

Reusing an ID automatically resets that workflow's budget, transaction
history, and any LangGraph checkpoint state tied to it first — otherwise
budget_used from the previous run would silently persist (every payment
in the new run gets rejected as MANDATE EXCEEDED against stale leftover
budget), and the real checkpointer from Phase 2 would — correctly, by
design — treat the reused ID as a resume rather than a fresh run, so
nothing would re-fire on the dashboard. This reset only ever touches the
one workflow_id you pass in; a server-generated ID (the normal, no-arg
path) is never touched by it.

### Demoing the "Silent Break" (spec Failure Mode 2)

```bash
python demo_silent_break.py [workflow_id]
```

A second failure mode, distinct from the budget-limit crash above: no
error is ever thrown to the agent at all, but the actual outcome doesn't
match what was intended. This script makes Merchant C settle a charge for
real internally while returning what looks exactly like a gateway
timeout to the caller — then shows Aegis's reconciliation engine catching
the mismatch against Merchant C's actual ledger, the deterministic fault
classifier correctly tagging it `network_fault`, and the compensating
agent generating a UDIR-shaped dispute payload (not a liability report —
this one genuinely is the gateway's fault). See the script's own
docstring for why this is a separate script rather than a flag on the
live `/pay` path.

---

## Batch Evaluation (required deliverable)

```bash
python test_harness/run_batch_eval.py
```

Outputs exact metrics to stdout and writes `results.json`. Numbers below are from the last committed run:

---

## Batch Eval Results

| Metric | Value |
|---|---|
| Total records | 50 |
| Match rate | **100.0%** |
| Mismatch detection rate | **100.0%** |
| Fault classification accuracy | **100.0%** |
| **False-dispute rate ★** | **0.0%** |
| Unresolved | 0 |

> ★ False-dispute rate is the number judges ask for first. It must be 0.0% — filing a UDIR dispute when the fault was the agent''s own mistake would spam NPCI''s dispute network.

---

## Unit Tests

```bash
python -m pytest test_engine.py -v
```

Tests assert:
- 5xx gateway → `network_fault`
- 2xx + mismatch → `agent_fault`
- Ambiguous/empty → `agent_fault` (conservative default)
- Zero false disputes across all known agent-fault cases

---

## Architecture

```
Primary Agent
    ↓ POST /pay  (X-API-Key required)
Aegis MCP Proxy (port 8000)
    ├── Writes TransactionLogEntry to Postgres (durable ledger, port 5433)
    ├── Publishes events to Redis (port 6380) → dashboard WebSocket
    ├── Runs reconciliation (deterministic)
    ├── On budget 403 → publishes to RabbitMQ (port 5673) compensation_requests
    └── WebSocket /ws/{workflow_id}?token=... → dashboard

Compensating-Agent Worker (separate process, consumes RabbitMQ)
    Builds its LangGraph with a real Postgres-backed checkpointer at startup.
    load_workflow_log → select_step → fetch_policy
        → extract_terms (HTTP → policy-extractor service, port 8004, Gemini)
        → compute_refund (deterministic) → attempt_refund → classify_and_route
             → network_fault → UDIR payload
             → agent_fault  → Liability report (NO dispute filed)
                 → fire-and-forget HTTP → anomaly-detector service (port 8005, Gemini)
                     → advisory-only; never blocks or gates the liability report
```

Crash recovery: if the worker process dies mid-saga, RabbitMQ redelivers
the still-unacked message to whichever worker consumes it next, and the
resumed run picks up from the last completed checkpoint instead of
restarting the whole saga. See Key Design Invariants below.

---

## Key Design Invariants

- **LLM never touches the payment-critical path or does arithmetic** — `refund_math.compute_refund()` is pure Python; the fault classifier and reconciliation engine are pure `if/elif` on raw gateway response codes, no model involved.
- **Two LLM calls, each isolated to its own single-purpose service, neither ever blocking payment logic:**
  - `engine/policy_extractor.py`, served via `engine/policy_service.py` (port 8004) — reads a merchant's plain-English cancellation policy and extracts structured terms only (`refundable`, `penalty_percentage`, `conditions`). Never computes a ₹ figure. Fails safe to `refundable: false` on any parse failure.
  - `engine/anomaly_detector.py`, served via `engine/anomaly_service.py` (port 8005) — flags statistically unusual compensation patterns for human review only. Purely advisory: nothing in the compensation graph gates a refund, a UDIR filing, or a liability report on this service's output, and it's called fire-and-forget (`asyncio.create_task`) so a slow or failing call never delays the liability report itself. Fails safe to `is_anomalous: false` on any error.
- **Ambiguous fault classification → always `agent_fault`** — filing a false dispute is worse than under-filing.
- **Compensation runs are crash-recoverable, not fire-and-forget.** `compensating_agent/worker.py` compiles its LangGraph with a real Postgres-backed checkpointer (`AsyncPostgresSaver`), keyed by `workflow_id` as the LangGraph thread. If the worker process dies mid-saga, RabbitMQ redelivers the still-unacked compensation request to whichever worker picks it up next, and `run_compensation()` detects the existing checkpoint and resumes from the last completed undo step instead of restarting the whole saga — which would otherwise double-refund whatever had already succeeded. This is verified behaviorally (not just checked for the `checkpointer=` keyword) in `test_level2.py::test_checkpointer_resumes_after_interruption`, which actually interrupts a run and resumes it against a freshly built graph object before asserting each merchant's refund endpoint was only called once.
- **`classify_fault(step_id, raw_gateway_response)` deliberately keeps `step_id`**, unlike the spec's one-argument form — the returned `FaultClassification` embeds `step_id` for downstream tracing (the compensating agent's graph and the batch-eval harness both key their logging off it). See the docstring in `engine/fault_classifier.py` for the full reasoning.
- **All ₹ figures shown to a client are read fresh from Postgres, never reconstructed via arithmetic on an earlier snapshot** — `db.get_budget_state()` exists specifically because `/pay`'s response used to add `actual_amount` on top of an already-committed `current_used`, silently double-counting every settled payment in the number shown on the dashboard.

---

## Environment Variables

See `.env.example` for the full list with explanations — copy it to `.env`
to get started. Summary:

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | (required) | Google Gemini API key, used by both `policy_service` and `anomaly_service` |
| `REDIS_HOST` / `REDIS_PORT` | `localhost` / `6380` | Aegis-only Redis instance |
| `PG_USER` / `PG_PASSWORD` / `PG_DB` / `PG_HOST` / `PG_PORT` | `aegis` / `aegispassword` / `aegis` / `localhost` / `5433` | Durable transaction ledger + LangGraph checkpoints |
| `PROXY_API_KEY` | `test-key-123` | Required `X-API-Key` header / WebSocket `?token=` value. This is the canonical name — the demo client and the dashboard both resolve to this same value by default. |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | CORS allow-list, comma-separated |
| `POLICY_SERVICE_URL` | `http://localhost:8004` | Isolated policy-extraction LLM service |
| `ANOMALY_SERVICE_URL` | `http://localhost:8005` | Isolated anomaly/triage LLM service (advisory only, non-blocking) |
| `VITE_PROXY_API_KEY` (dashboard's own `.env`, not the root one) | `test-key-123` | Must match `PROXY_API_KEY` or the dashboard's WebSocket connection gets rejected |

---

## File Structure

```
aegis/
├── .env.example              Every env var actually read by the code
├── docker-compose.yml        Redis, Postgres, RabbitMQ + optional containerized LLM services
├── mock_merchants/           FastAPI apps for 3 mock merchants
├── proxy/
│   ├── schemas.py            Pydantic models (Sections 5.1-5.5)
│   └── mcp_proxy.py          Intercepts payments, auth, CORS, WebSocket, budget tracking
├── db/
│   └── client.py             Postgres pool, migrations, ledger + circuit-breaker queries
├── engine/
│   ├── reconciliation.py     Deterministic expected vs actual comparison
│   ├── fault_classifier.py   Deterministic if/elif fault classification
│   ├── policy_extractor.py   LLM call #1 (Gemini) — policy term extraction only
│   ├── policy_service.py     Isolated FastAPI service wrapping policy_extractor (port 8004)
│   ├── anomaly_detector.py   LLM call #2 (Gemini) — advisory anomaly/triage flag only
│   └── anomaly_service.py    Isolated FastAPI service wrapping anomaly_detector (port 8005)
├── refund_math.py            Pure arithmetic (no LLM)
├── compensating_agent/
│   ├── graph.py               LangGraph StateGraph, real Postgres-backed checkpointer
│   └── worker.py               RabbitMQ consumer; the process that actually runs the graph
├── run_worker.py              Convenience launcher for the worker (--debug for verbose logs)
├── run_demo.py                One-command: infra + all services + demo, with pre-flight checks
├── run_bg.py                  Starts all app services in the background (no infra, no demo run)
├── primary_agent/
│   └── procurement_agent.py   Demo: CRM + Hotel + budget-busting third payment
├── demo_silent_break.py       Live trigger for spec Failure Mode 2 (Silent Break)
├── state_log/
│   └── redis_client.py        Redis on port 6380 — events + short-lived state
├── test_harness/
│   ├── generate_scenarios.py  50 synthetic records with ground truth
│   └── run_batch_eval.py      Metrics: match rate, false-dispute rate, etc.
├── test_engine.py             Unit tests for the deterministic fault classifier
├── test_level1*.py – test_level6.py   Regression tests per redesign-doc milestone
├── dashboard/                 React + Vite + React Flow + Framer Motion
├── results.json               Committed batch eval output
└── docker-compose.yml         Redis, Postgres, RabbitMQ, + optional LLM-service containers
```
