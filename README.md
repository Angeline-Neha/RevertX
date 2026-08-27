# 🛡️ RevertX — Razorpay Aegis

**Fault-Isolated Saga Orchestrator with Native UPI Reconciliation**

> *Single source of truth for AI-agent payment failure recovery.*

---

## What it does

Aegis sits between an AI agent and payment APIs. When the agent's multi-step payment workflow fails (hard crash or silent mismatch), Aegis:

1. **Detects** the failure via deterministic reconciliation (expected vs actual)
2. **Classifies** the fault — network/infrastructure vs agent logic — deterministically (zero LLM)
3. **Compensates** — walks backward through the workflow log, reads merchant policies (via an isolated policy-extraction service — the only LLM call in the payment-critical path), and refunds what can be refunded
4. **Routes** — generates either a UDIR-shaped dispute payload (network fault) or an Internal Liability Report (agent fault). Never both. Never wrong.

---

## Quick Start

### 1. Start Redis (Aegis-only, port 6380)

```bash
docker compose up -d
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Start the four services (separate terminals)

```powershell
# Terminal 1 — Merchant A (CRM)
uvicorn mock_merchants.merchant_a_crm:app --port 8001

# Terminal 2 — Merchant B (Hotel)
uvicorn mock_merchants.merchant_b_hotel:app --port 8002

# Terminal 3 — Merchant C (Domain)
uvicorn mock_merchants.merchant_c_domain:app --port 8003

# Terminal 4 — Aegis MCP Proxy (includes WebSocket)
uvicorn proxy.mcp_proxy:app --port 8000
```

### 4. Start the dashboard

```bash
cd dashboard
npm install
npm run dev
# Open http://localhost:5173
```

### 5. Run the demo

```bash
python primary_agent/procurement_agent.py
```

Copy the printed **workflow UUID**, paste it into the dashboard, and watch live.

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
    ↓ POST /pay
Aegis MCP Proxy (port 8000)
    ├── Writes TransactionLogEntry to Redis (port 6380)
    ├── Runs reconciliation (deterministic)
    ├── On budget 403 → triggers LangGraph compensating agent
    └── WebSocket /ws/{workflow_id} → dashboard
         
LangGraph Compensating Agent
    load_workflow_log → select_step → fetch_policy → extract_terms (Gemini) 
    → compute_refund (deterministic) → attempt_refund → classify_and_route
         → network_fault → UDIR payload
         → agent_fault  → Liability report (NO dispute filed)
```

---

## Key Design Invariants

- **LLM never touches the payment-critical path or does arithmetic** — `refund_math.compute_refund()` is pure Python; the fault classifier and reconciliation engine are pure `if/elif` on raw gateway response codes, no model involved.
- **Two LLM calls, each isolated to its own single-purpose service, neither ever blocking payment logic:**
  - `engine/policy_extractor.py`, served via `engine/policy_service.py` (port 8004) — reads a merchant's plain-English cancellation policy and extracts structured terms only (`refundable`, `penalty_percentage`, `conditions`). Never computes a ₹ figure. Fails safe to `refundable: false` on any parse failure.
  - `engine/anomaly_detector.py`, served via `engine/anomaly_service.py` (port 8005) — flags statistically unusual compensation patterns for human review only. Purely advisory: nothing in the compensation graph gates a refund, a UDIR filing, or a liability report on this service's output, and it's called fire-and-forget (`asyncio.create_task`) so a slow or failing call never delays the liability report itself. Fails safe to `is_anomalous: false` on any error.
- **Ambiguous fault classification → always `agent_fault`** — filing a false dispute is worse than under-filing.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | (required) | Google Gemini API key, used by both `policy_service` and `anomaly_service` |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6380` | Redis port (Aegis-only, not 6379) |
| `POLICY_SERVICE_URL` | `http://localhost:8004` | Isolated policy-extraction LLM service |
| `ANOMALY_SERVICE_URL` | `http://localhost:8005` | Isolated anomaly/triage LLM service (advisory only, non-blocking) |

*A full environment-variable audit (Postgres, RabbitMQ, API keys, CORS) is tracked as Phase 3 of the fix plan — this table isn't complete yet, only updated for what changed in this phase.*

---

## File Structure

```
aegis/
├── mock_merchants/          FastAPI apps for 3 mock merchants
├── proxy/
│   ├── schemas.py           Pydantic models (Sections 5.1-5.5)
│   └── mcp_proxy.py         Intercepts payments, WebSocket, budget tracking
├── engine/
│   ├── reconciliation.py    Deterministic expected vs actual comparison
│   ├── fault_classifier.py  Deterministic if/elif fault classification
│   └── policy_extractor.py  The ONE Gemini call (gemini-2.5-flash, JSON mode)
├── refund_math.py           Pure arithmetic (no LLM)
├── compensating_agent/
│   └── graph.py             LangGraph StateGraph (8 nodes)
├── primary_agent/
│   └── procurement_agent.py Demo: CRM + Hotel + budget-busting third payment
├── state_log/
│   └── redis_client.py      Redis on port 6380
├── test_harness/
│   ├── generate_scenarios.py  50 synthetic records with ground truth
│   └── run_batch_eval.py      Metrics: match rate, false-dispute rate, etc.
├── test_engine.py             Unit tests for fault classifier
├── dashboard/                 React + Vite + React Flow + Framer Motion
├── results.json               Committed batch eval output
└── docker-compose.yml         Redis on port 6380 only
```
