"""
db/client.py
Postgres connection pooling and query functions for Aegis Level 1.
"""
from __future__ import annotations

import json
import os
from typing import Any

import asyncpg
import psycopg2

PG_USER = os.getenv("PG_USER", "aegis")
PG_PASSWORD = os.getenv("PG_PASSWORD", "aegispassword")
PG_DB = os.getenv("PG_DB", "aegis")
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5433")

DSN = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"

pool: asyncpg.Pool | None = None

async def run_migrations() -> None:
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS workflows (
                workflow_id VARCHAR(255) PRIMARY KEY,
                budget_limit FLOAT NOT NULL,
                budget_used FLOAT NOT NULL DEFAULT 0.0,
                status VARCHAR(50) DEFAULT 'active',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # IMPORTANT: this table is the system of record for every payment and
        # refund Aegis has ever seen. It must NEVER be dropped as part of a
        # normal service startup (both mcp_proxy's lifespan and worker.main()
        # call run_migrations() on every boot). A destructive migration here
        # silently erases the audit trail the whole system exists to produce.
        # Table creation is idempotent; a genuine destructive reset is only
        # available via reset_database_for_testing(), which nothing in the
        # application startup path calls.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS transaction_steps (
                    step_id VARCHAR(255),
                    workflow_id VARCHAR(255) NOT NULL,
                    merchant_id VARCHAR(255) NOT NULL,
                    expected JSONB,
                    actual JSONB,
                    raw_gateway_response JSONB,
                    status VARCHAR(50),
                    idempotency_key VARCHAR(255),
                    full_entry JSONB,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (step_id, created_at)
                ) PARTITION BY RANGE (created_at);
            """)
        # The default partition is created separately and guarded, since
        # "CREATE TABLE ... PARTITION OF" has no direct "IF NOT EXISTS partition"
        # shorthand across all supported Postgres versions; check pg_class first.
        partition_exists = await conn.fetchrow(
            "SELECT 1 FROM pg_class WHERE relname = 'transaction_steps_current_month'"
        )
        if not partition_exists:
            await conn.execute("""
                CREATE TABLE transaction_steps_current_month PARTITION OF transaction_steps
                    FOR VALUES FROM ('2020-01-01') TO ('2030-01-01');
            """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                idempotency_key VARCHAR(255) PRIMARY KEY,
                response_body JSONB,
                status_code INT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS dlq_refunds (
                id SERIAL PRIMARY KEY,
                workflow_id VARCHAR(255),
                merchant_id VARCHAR(255),
                step_id VARCHAR(255),
                amount FLOAT,
                settlement_ref VARCHAR(255),
                reason TEXT,
                status VARCHAR(50) DEFAULT 'pending_retry',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS circuit_breakers (
                merchant_id VARCHAR(255) PRIMARY KEY,
                failures INT DEFAULT 0,
                state VARCHAR(50) DEFAULT 'closed',
                last_failure TIMESTAMP WITH TIME ZONE
            );
            CREATE INDEX IF NOT EXISTS idx_txn_workflow ON transaction_steps(workflow_id);
        """)
        # Phase 9.2 — action_type column was added after initial schema creation.
        # ALTER TABLE ... ADD COLUMN IF NOT EXISTS is idempotent so this runs
        # safely on every startup against both new and existing databases.
        # Applies to the parent table; Postgres propagates to all partitions.
        await conn.execute("""
            ALTER TABLE transaction_steps
                ADD COLUMN IF NOT EXISTS action_type VARCHAR(50) DEFAULT 'payment';
        """)
        # Agent Wallet — the AI agent's own financial authority, distinct
        # from the RazorpayX account balance. per_txn_limit/daily_limit are
        # policy inputs (set once, changed deliberately); spent_today/
        # last_reset are runtime state reset once per calendar day.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_wallet (
                agent_id VARCHAR(255) PRIMARY KEY,
                per_txn_limit FLOAT NOT NULL,
                daily_limit FLOAT NOT NULL,
                spent_today FLOAT NOT NULL DEFAULT 0.0,
                last_reset DATE NOT NULL DEFAULT CURRENT_DATE
            );
        """)
    finally:
        await conn.close()

async def reset_database_for_testing() -> None:
    """
    Destructively drops and recreates transaction_steps.

    This function exists ONLY for test fixtures that need a clean slate
    between test runs. It must never be called from application startup
    (mcp_proxy's lifespan, worker.main(), or any production entrypoint) —
    doing so would silently destroy the live transaction ledger every time
    a service restarts. If you're tempted to call this from anywhere other
    than a test's setup/teardown, that's a sign you want run_migrations()
    (idempotent, safe) instead.
    """
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DROP TABLE IF EXISTS transaction_steps CASCADE")
    finally:
        await conn.close()
    await run_migrations()


async def reset_workflow(workflow_id: str) -> None:
    """
    Clears state for exactly ONE workflow_id: its `workflows` row (budget
    tracking), its `transaction_steps` rows (settlement history), and its
    LangGraph checkpointer rows (checkpoints/checkpoint_blobs/checkpoint_writes,
    all keyed by thread_id) — nothing else. Unlike reset_database_for_testing(),
    this never touches the schema and never affects any other workflow's data.

    Exists for two compounding gaps when a workflow_id is deliberately reused
    (e.g. primary_agent/procurement_agent.py accepts one via CLI arg so a demo
    operator can keep the same dashboard URL across re-runs):

    1. create_workflow() uses `INSERT ... ON CONFLICT DO NOTHING`, so
       budget_used from the previous run silently persists — every payment
       in the new run gets rejected as MANDATE EXCEEDED against stale
       leftover budget even though nothing in the new run actually spent it.
    2. worker.py passes workflow_id as the LangGraph thread_id (see
       compensating_agent/worker.py). Clearing #1 alone still leaves the
       checkpointer's own tables intact for that thread_id, so
       AsyncPostgresSaver sees an existing checkpoint and — correctly, by
       design — treats the run as a RESUME: it skips re-executing nodes it
       already has cached output for, so no compensation_trace,
       llm_stream_chunk, math_computation, or final_output events fire on
       the second run, even though the final result (e.g. has_liability_report)
       is still accurate from the earlier checkpointed state. That's the
       checkpointer working as intended (Phase 2) — it's just fighting
       reuse of a demo ID. Checkpoint tables may not exist yet on a fresh
       DB (they're created by AsyncPostgresSaver.setup() on first worker
       startup), so each DELETE is wrapped individually.

    Called ONLY from procurement_agent.py's explicit-workflow_id path (see
    run_procurement()) — never from create_workflow() or any code path that
    handles a server-generated workflow_id, since those are never reused
    and must keep their real history (and real resumability) intact.
    """
    conn = await asyncpg.connect(DSN)
    try:
        async with conn.transaction():
            await conn.execute("DELETE FROM transaction_steps WHERE workflow_id = $1", workflow_id)
            await conn.execute("DELETE FROM dlq_refunds WHERE workflow_id = $1", workflow_id)
            await conn.execute("DELETE FROM workflows WHERE workflow_id = $1", workflow_id)
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            try:
                await conn.execute(f"DELETE FROM {table} WHERE thread_id = $1", workflow_id)
            except asyncpg.exceptions.UndefinedTableError:
                pass  # not created yet — nothing to clear
    finally:
        await conn.close()


async def init_pool() -> None:
    global pool
    pool = await asyncpg.create_pool(DSN)

async def close_pool() -> None:
    global pool
    if pool:
        await pool.close()
        pool = None

async def create_workflow(workflow_id: str, budget_limit: float) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, budget_limit, budget_used) VALUES ($1, $2, 0.0) ON CONFLICT DO NOTHING",
            workflow_id, budget_limit
        )

async def reserve_budget(workflow_id: str, amount: float) -> tuple[bool, float, float]:
    """
    Atomic budget check and reserve.
    Returns (reserved: bool, used: float, limit: float)
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE workflows
            SET budget_used = budget_used + $1
            WHERE workflow_id = $2 AND budget_used + $1 <= budget_limit
            RETURNING budget_used, budget_limit
        """, amount, workflow_id)
        
        if row:
            return True, row["budget_used"], row["budget_limit"]
        else:
            curr = await conn.fetchrow("SELECT budget_used, budget_limit FROM workflows WHERE workflow_id = $1", workflow_id)
            if curr:
                return False, curr["budget_used"], curr["budget_limit"]
            return False, 0.0, 0.0

async def rollback_budget(workflow_id: str, amount: float) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE workflows SET budget_used = budget_used - $1 WHERE workflow_id = $2
        """, amount, workflow_id)

async def commit_budget(workflow_id: str, expected_amount: float, actual_amount: float) -> None:
    if expected_amount == actual_amount:
        return
    diff = actual_amount - expected_amount
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE workflows SET budget_used = budget_used + $1 WHERE workflow_id = $2
        """, diff, workflow_id)

async def get_budget_state(workflow_id: str) -> tuple[float, float]:
    """
    Ground-truth (budget_used, budget_limit) for a workflow, read fresh from
    the DB. Use this for any client-facing response built AFTER a
    reserve_budget()/commit_budget()/rollback_budget() sequence, instead of
    reconstructing the total via arithmetic on reserve_budget()'s earlier
    return value — reserve_budget()'s returned `used` already reflects that
    call's own reservation, so adding the payment amount to it again
    double-counts (this was exactly the bug in mcp_proxy.py's /pay response:
    it added actual_amount on top of an already-updated current_used).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT budget_used, budget_limit FROM workflows WHERE workflow_id = $1", workflow_id
        )
        if row:
            return row["budget_used"], row["budget_limit"]
        return 0.0, 0.0


async def list_recent_workflows(limit: int = 20) -> list[dict[str, Any]]:
    """
    Most-recently-created workflows, newest first — backs the dashboard's
    workflow picker panel (Phase 5.2) so connecting no longer requires
    pasting a UUID copied from a terminal. `status` and budget fields are
    read straight from the `workflows` row (no per-step joins), so this
    stays cheap even as transaction_steps grows.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT workflow_id, budget_limit, budget_used, status, created_at
            FROM workflows
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [
            {
                "workflow_id": r["workflow_id"],
                "budget_limit": r["budget_limit"],
                "budget_used": r["budget_used"],
                "status": r["status"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]


async def get_idempotency_response(idem_key: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT response_body FROM idempotency_keys WHERE idempotency_key = $1", idem_key)
        if row:
            return json.loads(row["response_body"])
        return None

async def save_idempotency_response(idem_key: str, response_body: dict[str, Any], status_code: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO idempotency_keys (idempotency_key, response_body, status_code)
            VALUES ($1, $2, $3)
            ON CONFLICT (idempotency_key) DO NOTHING
        """, idem_key, json.dumps(response_body), status_code)

async def write_transaction_step(entry_dump: dict[str, Any], idem_key: str | None = None) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO transaction_steps (step_id, workflow_id, merchant_id, expected, actual, raw_gateway_response, status, idempotency_key, full_entry)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """, 
        entry_dump["step_id"], entry_dump["workflow_id"], entry_dump["merchant_id"],
        json.dumps(entry_dump["expected"]), json.dumps(entry_dump["actual"]),
        json.dumps(entry_dump.get("raw_gateway_response", {})), entry_dump["actual"].get("status", "pending"), idem_key, json.dumps(entry_dump))

def write_transaction_step_sync(entry_dump: dict[str, Any], idem_key: str | None = None) -> None:
    """Synchronous version for the compensating agent."""
    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO transaction_steps (step_id, workflow_id, merchant_id, expected, actual, raw_gateway_response, status, idempotency_key, full_entry)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                entry_dump["step_id"], entry_dump["workflow_id"], entry_dump["merchant_id"],
                json.dumps(entry_dump["expected"]), json.dumps(entry_dump["actual"]),
                json.dumps(entry_dump.get("raw_gateway_response", {})), entry_dump["actual"].get("status", "pending"), 
                idem_key, json.dumps(entry_dump)
            ))
            conn.commit()
    finally:
        conn.close()

def get_workflow_steps_sync(workflow_id: str) -> list[Any]:
    from proxy.schemas import TransactionLogEntry
    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT full_entry FROM transaction_steps WHERE workflow_id = %s ORDER BY created_at ASC", (workflow_id,))
            rows = cur.fetchall()
            entries = []
            for r in rows:
                entries.append(TransactionLogEntry.model_validate(r[0]))
            return entries
    finally:
        conn.close()

async def write_dlq_entry(workflow_id: str, merchant_id: str, step_id: str, amount: float, settlement_ref: str, reason: str):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO dlq_refunds (workflow_id, merchant_id, step_id, amount, settlement_ref, reason)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, workflow_id, merchant_id, step_id, amount, settlement_ref, reason)

async def record_merchant_failure(merchant_id: str) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO circuit_breakers (merchant_id, failures, state, last_failure)
            VALUES ($1, 1, 'closed', CURRENT_TIMESTAMP)
            ON CONFLICT (merchant_id) DO UPDATE SET
                failures = circuit_breakers.failures + 1,
                state = CASE WHEN circuit_breakers.failures + 1 >= 3 THEN 'open' ELSE circuit_breakers.state END,
                last_failure = CURRENT_TIMESTAMP
            RETURNING state
        """, merchant_id)
        return row["state"]

async def check_circuit_breaker(merchant_id: str) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT state FROM circuit_breakers WHERE merchant_id = $1", merchant_id)
        return row["state"] if row else "closed"

async def reset_circuit_breaker(merchant_id: str):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE circuit_breakers SET failures = 0, state = 'closed' WHERE merchant_id = $1", merchant_id)


async def fetch_wallet(agent_id: str, default_per_txn: float = 5000.0, default_daily: float = 20000.0) -> dict:
    """Creates a wallet row with defaults on first use so callers never
    have to provision it separately before checking authority."""
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO agent_wallet (agent_id, per_txn_limit, daily_limit)
            VALUES ($1, $2, $3) ON CONFLICT DO NOTHING
        """, agent_id, default_per_txn, default_daily)
        row = await conn.fetchrow("SELECT * FROM agent_wallet WHERE agent_id = $1", agent_id)
        return dict(row)

async def reset_wallet_spend(agent_id: str, reset_date) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_wallet SET spent_today = 0.0, last_reset = $1 WHERE agent_id = $2",
            reset_date, agent_id
        )

async def increment_wallet_spend(agent_id: str, amount: float) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_wallet SET spent_today = spent_today + $1 WHERE agent_id = $2",
            amount, agent_id
        )


async def get_session_metrics() -> dict:
    """
    Phase 9.2 — headline recovery metric for the dashboard.
    Returns zeros gracefully if the column migration hasn't run yet,
    so the MetricsBar polling never causes a 500 cascade.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COALESCE(SUM((actual->>'amount')::float), 0.0) AS total_recovered,
                    COUNT(DISTINCT workflow_id)                     AS workflows_resolved
                FROM transaction_steps
                WHERE action_type = 'refund'
                  AND actual->>'status' = 'refunded'
            """)
            return {
                "total_recovered_inr": float(row["total_recovered"]),
                "disputes_resolved": int(row["workflows_resolved"]),
            }
    except Exception:
        # Column not yet migrated or pool not ready — return safe zeros.
        return {"total_recovered_inr": 0.0, "disputes_resolved": 0}
