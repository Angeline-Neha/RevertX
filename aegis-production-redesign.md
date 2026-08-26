# From Hackathon Demo to Production System: Redesigning Razorpay Aegis

*A senior-engineering review of RevertX / Razorpay Aegis, with a research-grounded path to a real product.*

---

## 0. How to read this document

I cloned and read every file in `RevertX` (proxy, engine, compensating agent, mock merchants, test harness, dashboard config) and your full technical spec. Everything in **Phase 1** is a direct diagnosis of the code as it exists today, not the spec's aspirations. Everything from **Phase 2 onward** is grounded in real products, real protocols, and one directly relevant academic paper — I've marked clearly wherever something is *my* architectural judgment rather than an established fact.

One honest framing up front: **your core design principle — deterministic everywhere, LLM only for one bounded extraction task, LLM never touches arithmetic — is the single best decision in this project, and it is also the industry's actual current answer to "how do you let AI touch money."** Nothing in this document asks you to abandon it. Everything asks you to make the parts around it survive contact with concurrency, crashes, and adversarial input.

---

## 1. Phase 1 — Current Project Diagnosis

### 1.1 Current architecture (as built, not as specified)

```
procurement_agent.py (scripted, no LLM)
   │  POST /init_workflow, POST /pay
   ▼
mcp_proxy.py (FastAPI, single process)
   ├── _budgets: dict[workflow_id, {limit, used}]   ← in-memory, module-level
   ├── forwards to merchant_a/b/c (hardcoded URLs, httpx, 8s timeout)
   ├── reconcile() — pure function, expected vs actual
   ├── write_step() → Redis SET (JSON blob) + PUBLISH
   └── on 403 → background_tasks.add_task(_trigger_compensation)
                       │
                       ▼
        compensating_agent/graph.py (LangGraph, in-process, fire-and-forget)
           load_workflow_log (Redis KEYS scan)
           → select_next_step_to_undo
           → fetch_policy (httpx GET, sync, inside async node)
           → extract_policy_terms (the one Gemini call, JSON mode, 2 retries, fail-safe = non-refundable)
           → compute_refund_amount (pure Python)
           → attempt_refund (httpx POST to merchant)
           → loop or → classify_and_route (pure if/elif on raw_gateway_response)
                → generate_udir_payload | generate_liability_report
   Every node writes a trace to Redis and publishes to `workflow:{id}:events`
   ▼
dashboard (React + Vite + React Flow + Framer Motion) — WebSocket-only, no polling
```

### 1.2 Current user flow

1. Demo script declares a workflow and a ₹35,000 budget.
2. Two payments settle normally through the proxy; each is reconciled synchronously in the request path.
3. A third payment is rejected by the proxy itself (not the merchant) because it would exceed budget — this is a **proxy-side mandate check**, a genuinely good pattern (fail before you even call the merchant).
4. That rejection triggers a LangGraph run as a FastAPI `BackgroundTask` — fire-and-forget, no supervision.
5. The graph walks previously-settled steps in reverse, fetches each merchant's policy text (if it has one), sends it through exactly one LLM call for structured extraction, computes the refund amount in pure Python, calls the merchant's real `/refund` endpoint, and repeats.
6. Once all steps are processed, it classifies the *original* triggering failure (not the refund attempts) as network or agent fault using only the raw gateway response, and emits either a UDIR-shaped payload or a Liability Report — never both.
7. The dashboard listens on a WebSocket per workflow and renders all of this live.

### 1.3 What's already good — preserve these

- **The one-LLM-call invariant is real, not just claimed.** I traced every call site: `policy_extractor.extract_policy_terms` is the only place `google.genai` is imported. Everything downstream of it (`refund_math.compute_refund`, `fault_classifier.classify_fault`) is plain Python with no model dependency. This is architecturally enforced, not just documented.
- **Defense in depth against a wrong LLM extraction already exists**, even if it isn't spelled out in the README: `merchant_c_domain.py`'s `/refund` endpoint *independently recomputes* the expected 10% penalty and rejects the refund with a 422 if the caller's amount doesn't match. `merchant_b_hotel.py`'s `/refund` independently re-checks the 7-day window using its own `booked_at` timestamp, regardless of what the LLM extracted. **The merchant server, not the LLM, is the final arbiter of whether money moves.** This is exactly the right shape for AI-adjacent-to-money systems and is worth stating explicitly as a design principle going forward, because right now it's an emergent property of how the mocks happen to be written rather than a documented contract.
- **The conservative-default fault classifier is genuinely conservative**, not just conservative-in-the-happy-path: ambiguous, unknown, and 4xx-non-mandate cases all fall through to `agent_fault`, and the only paths to `network_fault` are an explicit network `error_type` or a 5xx code — both drawn from a raw, un-inferred field. There's no branch where uncertainty produces `network_fault`.
- **The WebSocket-only, no-polling design in `mcp_proxy.py`** is correctly built on Redis pub/sub, not a re-implemented event bus, and it's the right primitive for this problem.
- **The synthetic batch-eval harness with a committed `results.json`** is a real deliverable, not a claim — the spec's insistence on this (Section 9.6) was correct and you followed it.

### 1.4 Current weaknesses — brutally honest

These are not spec gaps, they're things I found reading the actual code.

**Concurrency / correctness**
- `_budgets: dict[str, dict[str, float]]` in `mcp_proxy.py` is a module-level Python dict with no lock. The budget check (`if budget["used"] + amount > budget["limit"]`) and the budget update (`_budgets[wid]["used"] += actual_amount`) are two separate, non-atomic operations separated by a network call to the merchant. Two concurrent `/pay` calls for the same workflow can both read the pre-update value, both pass the check, and both commit — silently blowing through the exact mandate limit the whole system exists to enforce. This is a textbook check-then-act race.
- The budget dict is **in-memory and per-process**. Restart the proxy, or run two replicas behind a load balancer (the first thing you'd do for reliability), and budget state either resets or splits — an agent could exploit either to exceed budget by hitting different replicas.
- `get_workflow_steps` uses `redis.keys(pattern)`, which is an O(n) full-keyspace scan that blocks the Redis event loop. It works at demo scale (tens of keys) and will not work at any real scale — this is one of Redis's most commonly cited anti-patterns.

**Reliability**
- The compensating agent runs as a FastAPI `BackgroundTask` inside the same process that's also serving `/pay`. If that process crashes or is redeployed mid-saga, the compensation run is gone — no checkpoint, no resume, no record that it was even attempted beyond whatever trace events made it to Redis before the crash. LangGraph supports persistent checkpointers specifically for this; the current build uses none.
- `attempt_refund_node`'s only failure handling is a bare `try/except Exception` that records `outcome: "error"` and moves on. There is no retry, no backoff, no dead-letter queue. If a merchant's refund endpoint is down for 30 seconds, that money is never recovered and nothing ever tries again.
- No idempotency key anywhere in `/pay`. If the primary agent's HTTP client times out waiting for a response and retries (which is the *correct* client behavior under `httpx.TimeoutException`), the proxy will forward a second real charge to the merchant with no way to detect it's a duplicate of the first.

**Security**
- No authentication on any endpoint. `/pay`, `/init_workflow`, `/workflow/{id}` are open. Anyone who can reach port 8000 can declare workflows, initiate payments framed as any merchant, or read any workflow's full transaction history.
- `CORSMiddleware` is configured with `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` — appropriate for a hackathon demo, a real vulnerability in anything reachable from the internet.
- The WebSocket endpoint has no auth check at all — knowledge of a `workflow_id` (a UUID, so not guessable by brute force, but leaked in logs, URLs, or a shared dashboard link) is sufficient to stream a live feed of another workflow's payment amounts and payee names.
- The proxy trusts the caller-supplied `expected` block on `/pay` completely. Nothing binds it to a signed mandate from the actual agent that owns the workflow — this matters a lot once you compare it to how the industry is solving *authorization* (Section 2 below); Aegis currently has no equivalent for its own control plane.

**Observability**
- No structured logging, no metrics, no tracing. The only introspection is the Redis event stream, which is a product feature (the dashboard), not an operations tool — there's no way to know match rate, refund latency, or LLM cost in production without querying Redis by hand.

**Testing**
- `test_engine.py` (137 lines) covers the fault classifier's decision table. There is no test for `mcp_proxy.py`'s budget-check race, no integration test that exercises the full LangGraph saga end-to-end against the mock merchants, and no adversarial test data in `generate_scenarios.py` beyond the five clean categories already described in the spec — meaning the reported 100% accuracy is 100% on a distribution the classifier's own rules were written to match. That's a valid unit-test result; it is not evidence the classifier generalizes to real-world raw gateway response shapes it hasn't seen.

**Scope realism**
- Everything is three FastAPI mock services with in-memory `dict` stores. This is appropriate for a hackathon and says nothing bad about the current build — it's flagged here only because Phase 5 onward assumes you're deliberately choosing what to harden first, not that all of it needs hardening for the demo to be credible.

---

## 2. Phase 2/3 — Research Findings and Competitive Landscape

### 2.1 The three "agent payment" protocols that shipped in the last twelve months

Since late 2025, the payments industry has converged on a specific split: **authorization** (did the human really approve this) is being solved by network-level protocols, while **outcome verification** (did what actually happened match what was approved) is not being solved by any of them.

| Protocol | Owner | What it actually verifies | What it does *not* verify |
|---|---|---|---|
| **AP2 (Agent Payments Protocol)** | Google, with 60+ partners including Mastercard, PayPal, Coinbase | Cryptographically signed "mandates" (Intent Mandate, Cart Mandate, Payment Mandate) proving a human authorized an agent to spend, with a non-repudiable audit trail for who's accountable if something goes wrong — per AP2's own documentation, these are tamper-evident, signed credential objects that form the building blocks of a transaction | Whether the *outcome* of the authorized payment matched the *intent* — AP2's own stated goal is answering "did the user authorize this," not "did the agent get what it paid for" |
| **Visa Trusted Agent Protocol (TAP)** | Visa, built with Cloudflare | Whether the calling traffic is a *trusted, registered agent* versus a scraper/bot, and whether payment data reaching a merchant is unaltered — Visa's announcement describes agents carrying consumer-recognition and payment data to support a merchant's preferred checkout flow | Post-authorization outcome correctness; it's a checkout-time trust and anti-bot layer |
| **Mastercard Agent Pay** | Mastercard, also built on Cloudflare's Web Bot Auth | Authorization and data integrity for agent-to-merchant transactions — Cloudflare's writeup describes both Agent Pay and TAP using Web Bot Auth as the underlying agent-authentication layer so networks can verify traffic from registered AI shopping agents | Same gap as TAP — it's a trust/authentication layer, not a reconciliation or compensation layer |

**This is the single most important research finding for your product positioning**, and it's exactly the argument your spec already makes in Section 11 — I'm confirming it's accurate, not just persuasive: every major 2025–2026 agent-payment protocol answers "was this authorized," and none of them answer "did the outcome match the plan, and if not, whose fault was it." That gap is real and it is not closing on its own — AP2's own documentation frames the problem it solves as authorization and anti-hallucination-at-authorization-time, not settlement-outcome verification.

### 2.2 The academic precedent you should know about (and should cite, not hide from)

A Stanford paper, **SagaLLM** (Chang & Geng, 2025), independently arrived at almost exactly your architecture's premise: standard LLM planning frameworks have no transactional guarantees, so the fix is to bind the classic database **Saga pattern** — decompose a long workflow into compensable steps, and if one fails, run compensating actions for what already committed — to LLM-driven planning. Their own example is a travel-booking scenario where a failed flight reservation automatically triggers compensatory rollbacks of the associated hotel and train bookings to keep global state consistent, with no manual intervention — which is essentially your CRM+hotel+flights demo script, arrived at independently from the database-systems side rather than the payments side.

This is good news framed correctly and bad news framed carelessly:
- **Framed well**: "The general pattern — Saga + LLM compensation — is validated academic work, not a novelty I invented. What I built is different: I bound it to a real payment rail's dispute mechanism (UDIR), added a hard architectural constraint the academic version doesn't have (LLM literally cannot touch arithmetic), and built it to run at a payment gateway's trust boundary rather than as a general planning framework." This is a true, defensible claim and is close to what your own spec's Section 11 already says about the saga pattern being "known" — now you have the specific citation.
- **Framed carelessly**: claiming this pattern is unprecedented. It isn't, and a judge or interviewer who's read the recent literature will know that.

### 2.3 Competitor and adjacent-product analysis

| Product | Target users | Core approach | Strengths | Weaknesses (for *this* problem) | What to learn from it |
|---|---|---|---|---|---|
| **Modern Treasury** (Ledgers + Reconciliation, "RISE" engine) | Fintechs, neobanks, marketplaces | Double-entry ledger + rule-based/AI-assisted matching between internal records and bank statements | Proven at scale — Modern Treasury states its AI-suggested match rules help customers reach reconciliation rates of 90–100%; real double-entry ledger as source of truth | Built for human-initiated B2B/marketplace money movement reconciled *after the fact*; has no concept of an autonomous multi-step agent workflow or of *fault attribution* — it tells you two records don't match, not whose fault it was or what to do about it | The double-entry ledger as the durable source of truth (Section 4.2 below borrows this directly) |
| **Recko** (acquired by **Stripe**, 2021 — not Razorpay; verified, since it's easy to misremember) | E-commerce/fintech finance teams | API-driven matching of internal sales records against payment processor and bank data | Deep India payments-reconciliation expertise, now inside Stripe's stack | Same as above — reconciliation-as-audit, not reconciliation-as-trigger-for-autonomous-remediation | Confirms reconciliation is a proven, fundable problem in exactly your market (Bangalore-based, India payments) |
| **End Close** (YC-backed) | Payments companies, fintechs | AI-agent-*assisted* exception handling for reconciliation, explicitly targeting the fact that, per End Close's own framing, exception handling is where 99% of the manual work happens today, with payment-ops teams spending hours gathering context across data sources per ticket | Correctly identifies that the *exception path*, not the happy path, is where money and time are lost — same insight your spec is built on | Still reconciliation-first (bank/ledger mismatch), not agent-payment-workflow-first; no equivalent of a jurisdiction-specific dispute-routing decision | Validates your instinct that the compensation/exception path is the valuable 20%, not the happy-path 80% |
| **ReconHub / Simetrik** | Mid-market finance teams | Rule-based transaction-level matching across sales, processor, and bank data, posting reconciled entries to a ledger | Mature, works today for conventional e-commerce | No agent-payment awareness at all; assumes a human-run business, not an autonomous agent making the purchasing decisions | Table stakes UX for a reconciliation dashboard — useful reference for your dashboard's end-state panel |
| **AP2 / TAP / Mastercard Agent Pay** (see 2.1) | Payment networks, card issuers, wallet providers | Cryptographic authorization mandates at checkout time | Real cross-industry adoption, 60+ partners on AP2 alone | Authorization-only, as established above — this is the gap | The mandate/signature *pattern* is worth adopting at your own control-plane boundary (Section 6.3) even though you don't need to *implement* AP2 itself |
| **Razorpay's own Thirdwatch acquisition (2018)** | Razorpay merchants | Rule + ML-based transaction fraud scoring | Directly relevant prior art *inside Razorpay* — proves the company already has infrastructure and appetite for a fraud/anomaly-scoring layer bolted onto payment flows | Fraud scoring ≠ fault attribution for agent-initiated workflow failures — different problem, adjacent team | Anomaly scoring as a *triage* layer (Section 8.2) has a natural home next to this existing capability rather than needing to be invented from scratch |
| **SagaLLM** (Stanford, academic) | Researchers / multi-agent planning systems | Saga pattern + persistent memory + independent validation agents bound to general LLM planning | Rigorous, the closest academic relative to your architecture | Domain-general, not payments-specific; no rail-level dispute integration, no hard-coded "LLM never does arithmetic" constraint | The paper's own admission that standalone LLMs "frequently violate interdependent constraints or fail to recover from disruptions" is the best one-line justification for why you keep math out of the LLM's hands — use it |

### 2.4 The actual gap (not "no one has AI")

Nobody — not the card networks, not the reconciliation platforms, not the academic saga papers — combines all three of: **(1)** deterministic outcome verification specific to autonomous multi-step agent workflows, **(2)** a binary, conservative-by-default fault-attribution step gating **(3)** an automated compensation action that is itself constrained to what a payment gateway can actually execute and reports honestly when it can't. Reconciliation platforms do (1) for humans after the fact. Authorization protocols solve a different problem entirely. The academic saga work does the general pattern but not the payments-rail-specific, regulator-safe version of it. That's the real, defensible gap, and it's the same one your spec identified — the research supports the framing rather than replacing it.

---

## 3. Phase 4 — Product Direction

Rather than inventing new directions from scratch (adding blockchain, a chatbot, or an unrelated vertical would violate your own stated rules), the research supports **hardening and generalizing what you built**, because the gap analysis confirms the core idea is the correct one. I evaluated four directions and I'm recommending the first.

| Direction | Problem | Gap addressed | Difficulty | Recommendation |
|---|---|---|---|---|
| **A. Harden Aegis into a real Agent Payment Assurance layer** (recommended) | Outcome verification + fault-attributed compensation for autonomous agent payment workflows | The one identified in 2.4 | High, but incremental from current code | **Chosen** |
| B. Pivot to a general reconciliation SaaS (compete with Recko/End Close) | Post-hoc bank/ledger matching for any business | Already well-served; you'd be entering a market with funded incumbents and no differentiation | Medium | Rejected — no genuine gap, "AI" wouldn't be the differentiator here either |
| C. Build an AP2/TAP-style authorization mandate system | Pre-payment authorization for agents | Already being solved by Google/Visa/Mastercard with 60+ partners each | High | Rejected — you'd be competing with card networks on a problem they're structurally better positioned to own |
| D. General multi-agent orchestration framework (compete with LangGraph/AutoGen) | Agent coordination in general | Not a payments problem at all; SagaLLM already covers the academic version | Very high | Rejected — out of scope, dilutes the one genuinely sharp idea you have |

**Why A**: it's the only direction where the gap is real, unaddressed, and matches something Razorpay is structurally positioned to own (a PSP sits at the one point in the stack that can *see* both the intended payment and the actual settlement, and is the entity NPCI actually holds accountable for dispute-filing hygiene) — and it's an extension of your existing code, not a rewrite.

---

## 4. Phase 5 — System Architecture for the Chosen Direction

### 4.1 Component list (only components with a real reason to exist)

| Component | Responsibility | Why it exists (not included by default) |
|---|---|---|
| **API Gateway / Aegis Proxy** | Same role as today's `mcp_proxy.py`: intercept agent payment calls, enforce budget atomically, run synchronous reconciliation, emit events | This is the trust boundary — it's the one component that must exist |
| **Auth service (API keys / OAuth2 client-credentials)** | Authenticate which agent/tenant is calling; scope which merchants/workflows they can touch | Currently fully open — this is not optional for anything beyond a laptop demo |
| **Postgres — Transaction Ledger** | Durable, indexed, append-only source of truth for every step, superseding Redis-as-datastore | Redis's `KEYS` scan and lack of durability guarantees make it wrong as a system of record; it's right as a cache/pubsub layer |
| **Redis (unchanged role: pub/sub + cache)** | Live event fan-out to the dashboard, short-lived idempotency-key cache | Already correctly used for this in the current build — keep it, just stop also using it as the database |
| **Message broker (SQS/Kafka-class)** | Decouple "a mismatch was detected" from "the compensating agent ran" | Enables retries, backpressure, and multiple consumers without the current fire-and-forget `BackgroundTasks` fragility |
| **Reconciliation Engine** | Same deterministic logic as today, unchanged in spirit | Already correct — no LLM, stays that way |
| **Fault Classifier** | Same deterministic logic as today | Already correct — no LLM, stays that way |
| **Compensating Agent (LangGraph + persistent checkpointer)** | Same graph, but resumable after a crash via a Postgres-backed checkpointer | Current in-memory-only run is a single point of failure for the most important part of the demo |
| **Policy Extraction Service (the one LLM call)** | Unchanged responsibility, isolated as its own service with its own rate limit/circuit breaker | Isolating it means a Gemini outage or cost spike can't take down the payment-critical proxy path |
| **Anomaly/Triage Model (new, narrowly scoped)** | Flags statistically unusual mismatch patterns for human review — never triggers a refund or dispute itself | See Phase 7 for why this is the *only* additional AI component justified by the research |
| **Merchant Adapter Layer** | Replaces hardcoded `MERCHANT_URLS` dict with a registry (even a Postgres table) mapping merchant_id → base URL, capabilities, circuit-breaker state | Three hardcoded merchants doesn't scale past a demo; this is the minimum step toward a real merchant catalog |
| **Observability stack (structured logs, metrics, traces)** | Operate the system, not just demo it | Currently zero — not a "nice to have" for anything handling money |
| **Object storage (S3-class)** | Archival of compensation traces / UDIR payloads / liability reports past their hot-storage window | Regulatory retention requirements (Section 6.4) require this; Redis/Postgres hot storage isn't the right place for years-long retention |

Deliberately **not** added: a full microservices mesh, Kubernetes, a separate "AI orchestration platform," blockchain, or a second LLM. None of them solve a real problem this system has.

### 4.2 Revised architecture diagram

```
Primary Agent (or real AP2/TAP-authenticated agent, longer term)
        │  signed request (API key today, mandate-shaped later)
        ▼
┌───────────────────────────────────────────────────────────┐
│  Aegis API Gateway                                          │
│   - authn/authz                                              │
│   - idempotency-key check (Postgres unique constraint)       │
│   - atomic budget check (Postgres row lock or Redis Lua incr)│
│   - synchronous reconciliation (unchanged logic)             │
└───────┬───────────────────────────────────────────┬─────────┘
        │ write (outbox pattern)                     │ publish
        ▼                                             ▼
┌───────────────┐                            ┌─────────────────┐
│ Postgres       │                            │ Redis pub/sub    │
│ Transaction    │                            │ → Dashboard WS   │
│ Ledger (source │                            └─────────────────┘
│ of truth)      │
└───────┬────────┘
        │ CDC / outbox relay
        ▼
┌───────────────────┐
│ Message broker      │  mismatch_detected / mandate_exceeded events
└─────────┬───────────┘
          ▼
┌────────────────────────────────────────────────────┐
│ Compensating Agent (LangGraph, Postgres checkpointer)│
│  load_log → select_step → fetch_policy               │
│  → extract_policy_terms (LLM, isolated, rate-limited) │
│  → compute_refund (pure Python, unchanged)            │
│  → attempt_refund (circuit breaker + DLQ on failure)  │
│  → classify_and_route (pure Python, unchanged)        │
│       → UDIR payload | Liability report               │
└───────────────┬───────────────────┬───────────────────┘
                ▼                   ▼
     Anomaly/Triage model    Object storage (archival)
     (flags for human            + audit log
      review only — never
      triggers money movement)
```

---

## 5. Database Architecture

**Source of truth moves from Redis JSON blobs to Postgres.** Minimum schema (illustrative, not exhaustive):

- `workflows(workflow_id PK, tenant_id, budget_limit, budget_used, status, created_at)` — `budget_used` updated only via `UPDATE ... SET budget_used = budget_used + $1 WHERE workflow_id = $2 AND budget_used + $1 <= budget_limit RETURNING *`, which makes the budget check-and-update **atomic in the database** and closes the race condition described in 1.4. This single change fixes the most concrete correctness bug in the current build.
- `transaction_steps(step_id PK, workflow_id FK indexed, merchant_id, expected JSONB, actual JSONB, raw_gateway_response JSONB, status, idempotency_key UNIQUE, created_at indexed)` — replaces `workflow:{id}:step:{id}` Redis keys; the `idempotency_key` unique constraint is what makes `/pay` safe to retry.
- `reconciliation_results` / `fault_classifications` — could be columns on `transaction_steps` rather than separate tables at this scale; don't over-normalize a system this size.
- `compensation_runs(run_id PK, workflow_id FK, langgraph_checkpoint JSONB, status, started_at, completed_at)` — the checkpoint column is what lets a crashed worker resume instead of restarting.
- `udir_payloads` / `liability_reports` — append-only, never updated after creation (these are legal/audit artifacts).
- Redis retains exactly two jobs: pub/sub fan-out to the dashboard, and a short-TTL idempotency-key cache mirroring Stripe's own documented pattern — Stripe saves the resulting status code and body of the first request made for a given idempotency key, and allows keys to be pruned after they're at least 24 hours old — a proven, simple pattern worth copying directly rather than reinventing.

---

## 6. API, Security Architecture, and Threat Model

### 6.1 API architecture changes

- Every state-changing endpoint (`/pay`, `/init_workflow`) requires an `Idempotency-Key` header, checked against the Postgres unique constraint before any merchant call is made — directly closes the duplicate-charge-on-retry gap.
- `/pay` requires an authenticated, scoped caller (API key minimum; a signed-mandate model longer term, see 6.3).
- `/ws/{workflow_id}` requires the same auth token as a query param or subprotocol header, checked before `pubsub.subscribe`.

### 6.2 STRIDE threat table

| Threat (STRIDE) | Attack vector | Impact | Likelihood (as built today) | Mitigation |
|---|---|---|---|---|
| Spoofing | Unauthenticated `/pay` — anyone can claim to be the primary agent | Fraudulent payment/refund cycles | High | API-key auth minimum; mandate-signature verification longer term |
| Tampering | No integrity check on stored transaction logs; Redis blobs are plain JSON | An attacker with Redis access rewrites `expected` after the fact to hide a mismatch | Low today (needs Redis access) but High impact | Postgres with row-level audit columns; consider hash-chaining log entries (append-only ledger pattern) |
| Repudiation | No non-repudiable proof the *agent* actually authorized what `expected` claims | An agent operator disputes that their agent really intended a payment | Medium | Adopt a mandate-shaped signature at the control-plane boundary, borrowing AP2's pattern without needing full AP2 integration |
| Information disclosure | Open CORS + unauthenticated WebSocket | Payee names, amounts, budget details leak to any listener with a workflow_id | High | Auth on WebSocket; origin allowlist, not `*` |
| Denial of service | No rate limiting; `/pay` failures each trigger a real Gemini call | Attacker cheaply forces many `mandate_limit_exceeded` events, running up LLM cost or exhausting quota | High | Per-tenant rate limits; circuit breaker in front of the policy-extraction service |
| Elevation of privilege | No RBAC; any caller can trigger refunds against any merchant | Financial loss, merchant relationship damage | High | Scope API keys to specific merchant_ids; dashboard viewers get read-only tokens |

### 6.3 The 3–5 most dangerous realistic attacks

1. **Fraudulent-outcome injection**: an attacker calls `/pay` directly with a fabricated `expected` block for a merchant it colludes with, deliberately engineering a mismatch to trigger the compensating agent into issuing a real refund. Root cause: the proxy trusts caller-supplied `expected` with no binding to a signed mandate.
2. **Budget-race exploitation**: fire concurrent `/pay` requests to slip multiple payments through before the (currently in-memory, non-atomic) budget counter updates, defeating the entire mandate-limit premise.
3. **LLM cost/availability DoS**: repeatedly trip `mandate_limit_exceeded` to force repeated Gemini calls with no rate limit or circuit breaker in front of them.
4. **Unauthenticated WebSocket data exposure**: leaking full payment detail (amounts, payees) to anyone who obtains a workflow_id, e.g. via a shared dashboard link or server log.
5. **Silent merchant-adapter compromise**: because `MERCHANT_URLS` is a flat trusted dict with no mutual authentication, a compromised or malicious "merchant" endpoint could return a manipulated `/policy` response — the current design already defends against this well for the two mocked cases that self-validate refund math server-side (an important existing strength, see 1.3), but a *new* merchant onboarded without that same server-side re-validation habit would reopen the hole; this should become a documented integration requirement, not an accident of how the first three mocks happened to be written.

### 6.4 Compliance and privacy

- This system handles **financial transaction data and PII** (payee names, transaction amounts) at minimum. If deployed by a real Razorpay merchant, it inherits RBI's data-localization and record-retention expectations for payment data, and any UPI-adjacent component sits inside the NPCI/RBI regulatory perimeter for UPI itself — meaning the audit trail (UDIR payloads, liability reports) isn't just a nice dashboard feature, it's a genuine retention/audit artifact and should be treated as append-only and tamper-evident, not just "logged."
- I'm not going to invent specific retention-period numbers or cite a specific RBI circular I haven't verified — that's a real compliance question a production deployment would need actual legal/compliance review for, not an LLM's best guess.

---

## 7. AI Architecture

### 7.1 The existing LLM call — validated, not changed

Answering your own Phase 7 questions for the *existing* policy-extraction call:
1. **Why AI is necessary**: merchant cancellation policies are unstructured, arbitrary-length, plain-English text with no schema — this is a genuine natural-language-understanding problem.
2. **Why rules can't do it**: you cannot write a regex or keyword rule set that reliably extracts "10% penalty within 48 hours" from arbitrary vendor prose without it breaking the moment a new merchant phrases their policy differently.
3. **Data required**: none — it's zero-shot structured extraction, no training data needed.
4. **Evaluation**: the merchant server's own independent re-validation of the refund amount (already built, see 1.3) *is* the evaluation — every extraction that leads to a refund attempt is checked against ground truth the merchant itself holds.
5. **What happens when wrong**: the fail-safe already defaults to `refundable=False` after two failed parses — correctly conservative (matches the "never file a false dispute" philosophy applied to refunds too).
6. **Hallucination prevention**: JSON-mode output constrained to a fixed schema, plus the merchant-side re-validation as a second, independent check.
7. **Model-performance monitoring**: not currently instrumented — add a metric for extraction-parse-failure rate and fail-safe-triggered rate as a leading indicator of policy-text drift (Section 9).
8. **Drift detection**: not currently instrumented — same fix.
9. **Security risks introduced**: prompt injection via merchant-controlled policy text is the real one; mitigated in practice today by the merchant server's own re-validation, but this should be a documented contract for any new merchant, not an accident (see 6.3.5).
10. **Measurable metric that demonstrates it works**: the batch-eval harness's false-dispute rate is actually the wrong metric for *this* component specifically — it measures the fault classifier, not the extractor. Add a distinct metric: percentage of LLM-computed refund amounts accepted without rejection by the merchant's independent server-side check.

### 7.2 The one AI component I'd add — and the one I'd explicitly reject

**Add**: a narrowly-scoped **anomaly/triage model** sitting alongside the deterministic fault classifier, not inside its decision path. As the merchant catalog grows beyond three hand-written mocks to real-world gateway response shapes the rule-based classifier's authors never anticipated, a model trained on historical mismatch patterns can flag "this raw response doesn't look like anything our rules have seen" for a human review queue. Critically: **it never gets to trigger a refund or a UDIR filing itself** — it only ever adds an item to a human queue, which means it cannot move the false-dispute-rate off zero even if it's wrong. This is the "decision support, not decision-maker" pattern, and it's the only AI addition that survives your own Phase 7 test ("if AI doesn't genuinely improve the product, recommend not using it") — it improves *coverage of failure modes*, which a fixed if/elif ladder structurally cannot do, without touching the invariant that makes the system regulator-safe.

**Explicitly reject**: an LLM chatbot interface, LLM-based refund-amount computation (already correctly rejected in your own spec), and LLM-based fault classification (same). None of these solve a problem the current deterministic approach has.

---

## 8. Scalability and Reliability Strategy

### 8.1 Realistic scale — grounded, not invented

I'm deliberately not choosing "1 million users" or similar numbers with no basis. What I can ground: NPCI's UPI network processes on the order of **600 million transactions per day network-wide**, per recent reporting on UPI outages that estimates roughly 600 million transactions take place through UPI every day — but that's the *entire national network*, not a reasonable target for a single new agentic-payments feature inside one PSP. A realistic first-deployment target for an "Aegis-protected agentic workflow" feature is closer to **tens of thousands of agent-initiated workflows per day**, each averaging 3–8 payment steps — meaning peak load in the low hundreds of API calls per second, not the tens of thousands a core UPI switch handles. Compensation runs are inherently lower-frequency than payments (they only fire on failure) and can tolerate seconds-to-low-minutes of latency, especially given the system's *own* UDIR payload already declares a 48-hour expected turnaround time — the compensation path was never meant to be a hard real-time path, which is a useful and already-present design fact worth leaning into rather than fighting.

### 8.2 What changes at each order of magnitude

- **100s of workflows/day (current demo scale)**: today's architecture, patched for the concurrency and idempotency bugs in 1.4, is genuinely fine.
- **10,000s/day**: the Postgres migration (Section 5) and message-broker decoupling (Section 4.2) become necessary — synchronous in-request reconciliation is fine, but fire-and-forget `BackgroundTasks` for compensation stops being safe once crash-during-saga becomes a "when," not an "if."
- **1,000,000+/day**: partition the transaction ledger by workflow_id or tenant, move the anomaly/triage model to a proper feature-store-backed serving path, and separate the policy-extraction service into its own horizontally-scaled deployment with its own LLM-provider rate-limit budget so a spike in one tenant's failure rate can't starve another tenant's legitimate extraction calls.

### 8.3 Reliability patterns to add

- **Idempotency keys** (Section 5/6) for `/pay`.
- **Circuit breaker per merchant adapter**: if a merchant's `/refund` endpoint fails repeatedly, stop hammering it and route to the dead-letter queue instead — directly fixes the current bare `try/except` in `attempt_refund_node`.
- **Dead-letter queue with backoff** for failed compensation steps, with a human-visible "unresolved" state — the batch-eval harness's own "Unresolved" metric (currently always 0 because nothing has a path to actually get stuck) becomes meaningful for the first time.
- **LangGraph checkpointing** (Postgres-backed) so a crashed compensating-agent worker resumes at the last completed node instead of restarting the whole saga — genuinely demonstrates recovery, not just execution.

---

## 9. Observability Strategy

- **Structured logs** (JSON, one line per event) replacing print statements, correlated by `workflow_id` and `step_id`.
- **Metrics** (Prometheus-style): match rate, mismatch rate by `mismatch_type`, fault-classification distribution, refund success/failure/latency by merchant, LLM call latency and parse-failure rate, circuit-breaker open/closed state per merchant, and — critically — **false-dispute rate as a live-alerting metric, not just a batch-eval number**, since that's the one number this whole system exists to keep at zero in production, not just in a committed `results.json`.
- **Tracing**: a single trace per workflow spanning the proxy call, the reconciliation check, and every LangGraph node, so a slow or stuck compensation run is debuggable without grepping Redis.
- **Alerting**: page on false-dispute-rate > 0, on DLQ depth growth, and on policy-extraction fail-safe rate exceeding a threshold (a leading indicator that a merchant changed their policy page format and the extractor is silently degrading to "non-refundable" more often than it should).

---

## 10. Testing Strategy

- **Unit tests** (extend `test_engine.py`): keep the existing fault-classifier table tests; add budget-check atomicity tests using concurrent async requests against the Postgres-backed check to prove the race from 1.4 is actually closed.
- **Integration tests**: a full LangGraph saga run against the three mock merchants, asserting the graph reaches `generate_udir_payload` or `generate_liability_report` correctly for each of the five scenario categories in `generate_scenarios.py` — currently only the classifier function is tested in isolation, not the graph that calls it.
- **Adversarial test data**: extend `generate_scenarios.py` beyond the five known-clean categories with deliberately malformed/ambiguous raw gateway responses (missing fields, unexpected status codes, non-English policy text) to test whether the conservative-default actually holds outside its own design distribution — this is the single most valuable testing addition, because it's the only way to know if "100% accuracy" reflects real robustness or a self-fulfilling test set.
- **Load tests**: concurrent `/pay` bursts against the same `workflow_id` specifically to verify the budget race is closed under real concurrency, not just reasoned about.
- **Failure-injection tests**: kill the compensating-agent worker mid-saga and verify the Postgres checkpointer resumes it correctly — this is also demo-worthy (Section 12).
- **AI evaluation**: track the policy-extractor's parse-failure and fail-safe rates against a held-out set of real-world-style (messier, non-templated) merchant policy text, not just the two mock policies currently in the repo.

---

## 11. Production Deployment Architecture

- **Infra**: containerize each of the proxy, compensating-agent worker, and policy-extraction service separately (they already have different reliability/scaling needs — no reason to keep them as tightly coupled as they are today); CI runs unit + integration tests + the adversarial scenario suite on every PR.
- **Secrets**: `GEMINI_API_KEY` moves from `.env` to a real secrets manager (even just cloud-provider-native) before anything touches a shared environment.
- **Backups/DR**: Postgres gets standard point-in-time-recovery backups; Redis is explicitly *not* the durability boundary once Section 5 lands, so its failure mode changes from "we lost data" to "the dashboard briefly stops updating" — a much safer failure mode for a payments-adjacent system to have.
- **Security scanning**: dependency scanning (the `requirements.txt`/`package-lock.json` already exist as a natural hook point), container scanning, and secret scanning in CI before any of this touches real merchant credentials.

---

## 12. Implementation Roadmap

**Level 1 — Foundation (fixes real bugs, no new capability)**: Postgres transaction ledger, atomic budget check, idempotency keys. *Why first*: these are correctness bugs in the current build, not enhancements — nothing else here matters if a race condition can blow the budget the whole demo is about protecting.

**Level 2 — Production architecture**: message-broker decoupling, LangGraph checkpointing, circuit breakers + DLQ per merchant adapter. *Why next*: turns "the compensating agent probably ran" into "the compensating agent provably ran, or is provably queued to retry."

**Level 3 — Security**: authn/authz on all endpoints, scoped API keys, WebSocket auth, CORS allowlist. *Why here*: nothing above matters if anyone can call `/pay` unauthenticated.

**Level 4 — AI**: instrument the existing LLM call's parse-failure/fail-safe metrics; add the anomaly/triage model as a human-review-only signal, never a decision-maker.

**Level 5 — Scale**: ledger partitioning, per-tenant rate limits, horizontally-scaled policy-extraction service.

**Level 6 — Observability**: structured logs, metrics, tracing, and the false-dispute-rate live alert.

**Level 7 — Production simulation / demonstrability**: the scenarios in Section 13, run against the hardened system, recorded as evidence the architecture survives real failure, not just the happy path.

For a strong student team, Levels 1–3 are the difference between "impressive demo" and "I understand what production means"; Levels 4–7 are what turn it into a genuinely strong portfolio piece.

---

## 13. Technical Demo Scenarios (for an interview, not a hackathon stage)

1. **Kill the compensating-agent worker mid-saga** and show it resume from the LangGraph checkpoint instead of restarting — this is the single most convincing "I understand distributed systems" demo available in this codebase.
2. **Fire 20 concurrent `/pay` requests** at the same workflow right at the budget boundary and show the Postgres-atomic check lets exactly the right number through — directly demonstrates the race-condition fix.
3. **Retry the same `/pay` call twice with the same idempotency key** and show only one real charge lands.
4. **Simulate a merchant refund endpoint going down**, show the circuit breaker open and the failed refund land in the dead-letter queue, then bring the merchant back and show it drain.
5. **Feed the policy extractor a deliberately messy, non-templated cancellation policy** (not the two clean mock policies) and show the fail-safe correctly trigger `refundable=False` rather than guessing.
6. **Show the false-dispute-rate metric live-alerting at zero**, then intentionally misconfigure a rule to demonstrate the alert firing — proves the metric is actually wired to something, not just printed once in a README.
7. **Run the adversarial scenario suite** (Section 10) and show the batch-eval numbers on genuinely unseen malformed data, not just the five original categories — the credible version of the "100% accuracy" claim.

---

## 14. Final Tech Stack

| Layer | Technology | Reason it's there |
|---|---|---|
| Proxy / API gateway | FastAPI (unchanged) | Already correct; async, good fit for I/O-bound merchant calls |
| Ledger / system of record | PostgreSQL | Durable, indexable, supports atomic check-and-update — fixes the core correctness bug |
| Cache / pub-sub | Redis (role narrowed) | Already correctly used for pub/sub; keep it, stop using it as a database |
| Message broker | SQS or Kafka (either is defensible; pick based on team familiarity) | Decouples detection from compensation, enables retry/replay |
| Orchestration | LangGraph + persistent checkpointer | Already the right tool; just needs the checkpointer turned on |
| LLM | Gemini (unchanged), isolated as its own rate-limited service | No reason to change providers; the isolation is the fix, not the model |
| Anomaly/triage model | A simple gradient-boosted classifier or even a well-tuned logistic regression over structured features (status codes, amounts, timing) — deliberately *not* another LLM | Cheap, fast, explainable, and the task (flag statistically unusual patterns) doesn't need generative capability |
| Frontend | React + Vite + React Flow + Framer Motion (unchanged) | Already the right stack for this UI |
| Observability | OpenTelemetry + Prometheus/Grafana-class stack | Standard, vendor-neutral choice |
| Object storage | S3-compatible | Archival of audit artifacts past hot-storage retention |

---

## 15. Final Project Identity

**Product name**: **Aegis** (keep it — it's already good, and "Razorpay Aegis" reads like a real product line name).

**One-line description**: *An outcome-verification and fault-attributed compensation layer for autonomous agent payments — because authorization protocols prove a payment was approved, and nothing else proves it did what it was supposed to.*

**Problem statement**: Autonomous agents now make live, multi-step payments with no fixed script. When a step fails loudly (hits a spend limit) or silently (settles but doesn't match intent), no current system — including every 2025–2026 agent-payment authorization protocol — verifies the outcome or decides who's at fault before acting, and UPI's only real dispute mechanism (NPCI's UDIR) is not built for "an AI made a bad decision."

**Solution**: Deterministic reconciliation catches the mismatch; a deterministic, conservative-by-default classifier decides network-fault vs agent-fault; an LLM is used exactly once, only to read unstructured merchant policy text, never to touch money math; a saga-style compensating agent reverses what can be reversed and honestly reports what can't.

**Key technical differentiators**:
1. Architecturally enforced single-LLM-call boundary, verifiable by code inspection, not just documentation.
2. Merchant-side independent re-validation of every LLM-derived refund amount before money moves — defense in depth that doesn't depend on trusting the model.
3. Conservative-by-default fault classification with a provably zero false-dispute-rate design goal, tracked as a live production metric, not just a batch-eval number.
4. Resumable, crash-safe compensation via checkpointed saga execution — most agent-payment demos can't survive their own process restarting mid-refund.
5. Sits at the one gap the entire current wave of agent-payment protocols (AP2, TAP, Agent Pay) explicitly doesn't address: outcome verification after authorization, not instead of it.

**Resume bullets** (no invented metrics — only what's actually measurable from this build):
- Designed and built a fault-isolated saga compensation system for autonomous agent payment workflows, enforcing an architectural invariant that LLM usage is limited to exactly one bounded extraction call with zero involvement in financial arithmetic.
- Built a deterministic, conservative-by-default fault classifier and reconciliation engine achieving a 0.0% false-dispute rate across a 50-record synthetic evaluation harness with committed, reproducible output.
- Identified and closed a check-then-act concurrency race in budget enforcement by migrating from an in-memory counter to an atomic Postgres row-level update, and added idempotency-key handling to eliminate duplicate-charge-on-retry.
- Implemented crash-recoverable saga execution using LangGraph with a persistent checkpointer, replacing a fire-and-forget background task with a resumable, auditable compensation pipeline.
- Positioned the system against the current landscape of agent-payment authorization protocols (Google AP2, Visa TAP, Mastercard Agent Pay) by identifying and targeting the specific gap — outcome verification and fault-attributed compensation — that none of them address.

**GitHub README positioning**: lead with the gap (Section 2.4), not the feature list — most reviewers skimming a README have seen a dozen "AI agent + payments" projects; the thing that will make a senior engineer stop scrolling is a one-paragraph, correctly-cited explanation of why AP2/TAP/Agent Pay don't solve this problem, followed immediately by the architecture invariant (one LLM call, verifiable by grep) rather than a features list.

---

## Bottom line

What would make this genuinely impressive to a senior engineer isn't more features — it's proof that the three hardest things in this codebase (the budget race, the fire-and-forget compensation run, and the completely open trust boundary) were found and fixed by you, not glossed over. Everything in Sections 5–13 is sized to be buildable by a strong student team in the time you'd realistically have, and every piece of it maps back to a specific line of code I read in your repo, not a generic "production checklist."
