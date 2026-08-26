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
            CREATE TABLE IF NOT EXISTS transaction_steps (
                step_id VARCHAR(255) PRIMARY KEY,
                workflow_id VARCHAR(255) REFERENCES workflows(workflow_id),
                merchant_id VARCHAR(255),
                expected JSONB,
                actual JSONB,
                raw_gateway_response JSONB,
                status VARCHAR(50),
                idempotency_key VARCHAR(255) UNIQUE,
                full_entry JSONB,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
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
