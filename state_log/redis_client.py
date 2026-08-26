"""
state_log/redis_client.py
Redis is used ONLY for pub/sub (WebSockets) and compensation trace logging.
System of record has been moved to Postgres (Level 1).
"""
from __future__ import annotations

import json
import os
from typing import List

import redis
import db.client as db

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6380"))

_sync_client: redis.Redis | None = None

def _get_client() -> redis.Redis:
    global _sync_client
    if _sync_client is None:
        _sync_client = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, decode_responses=True
        )
    return _sync_client


def get_workflow_steps(workflow_id: str) -> List["TransactionLogEntry"]:
    """Read steps from Postgres, preserving signature for downstream callers."""
    return db.get_workflow_steps_sync(workflow_id)


def get_seen_settlement_refs(workflow_id: str) -> set[str]:
    steps = get_workflow_steps(workflow_id)
    return {s.actual.settlement_ref for s in steps if s.actual.settlement_ref}


def publish_event(workflow_id: str, event_type: str, data: dict) -> None:
    r = _get_client()
    _publish(r, workflow_id, event_type, data)


def write_compensation_trace(workflow_id: str, trace_entry: dict) -> None:
    r = _get_client()
    key = f"workflow:{workflow_id}:compensation_trace"
    r.rpush(key, json.dumps(trace_entry))
    _publish(r, workflow_id, "compensation_trace", trace_entry)


def get_compensation_trace(workflow_id: str) -> list[dict]:
    r = _get_client()
    key = f"workflow:{workflow_id}:compensation_trace"
    items = r.lrange(key, 0, -1)
    return [json.loads(i) for i in items]


def _publish(r: redis.Redis, workflow_id: str, event_type: str, data: dict) -> None:
    payload = json.dumps({"event_type": event_type, "data": data})
    r.publish(f"workflow:{workflow_id}:events", payload)
