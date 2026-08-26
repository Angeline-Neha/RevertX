"""
state_log/redis_client.py
Redis key pattern: workflow:{workflow_id}:step:{step_id}
Redis is on localhost:6380 (Aegis-only port, see docker-compose.yml).
"""
from __future__ import annotations

import json
import os
from typing import List

import redis

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6380"))  # 6380 = Aegis Redis, never 6379

_sync_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _sync_client
    if _sync_client is None:
        _sync_client = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, decode_responses=True
        )
    return _sync_client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_step(entry: "TransactionLogEntry") -> None:  # type: ignore[name-defined]
    """Persist a log entry and publish an event for the WebSocket feed."""
    from proxy.schemas import TransactionLogEntry  # local import to avoid circles

    r = _get_client()
    key = f"workflow:{entry.workflow_id}:step:{entry.step_id}"
    r.set(key, entry.model_dump_json())
    _publish(r, entry.workflow_id, "step_written", entry.model_dump())


def get_workflow_steps(workflow_id: str) -> List["TransactionLogEntry"]:
    """Return all steps for a workflow, sorted by timestamp ascending."""
    from proxy.schemas import TransactionLogEntry

    r = _get_client()
    pattern = f"workflow:{workflow_id}:step:*"
    keys = r.keys(pattern)
    entries: list[TransactionLogEntry] = []
    for key in keys:
        raw = r.get(key)
        if raw:
            entries.append(TransactionLogEntry.model_validate_json(raw))
    entries.sort(key=lambda e: e.timestamp)
    return entries


def get_seen_settlement_refs(workflow_id: str) -> set[str]:
    """Return the set of settlement_ref values already recorded for a workflow."""
    steps = get_workflow_steps(workflow_id)
    return {s.actual.settlement_ref for s in steps if s.actual.settlement_ref}


def publish_event(workflow_id: str, event_type: str, data: dict) -> None:
    """Publish any structured event to the workflow pub/sub channel."""
    r = _get_client()
    _publish(r, workflow_id, event_type, data)


def write_compensation_trace(workflow_id: str, trace_entry: dict) -> None:
    """Append a compensation trace entry and broadcast it."""
    r = _get_client()
    key = f"workflow:{workflow_id}:compensation_trace"
    r.rpush(key, json.dumps(trace_entry))
    _publish(r, workflow_id, "compensation_trace", trace_entry)


def get_compensation_trace(workflow_id: str) -> list[dict]:
    r = _get_client()
    key = f"workflow:{workflow_id}:compensation_trace"
    items = r.lrange(key, 0, -1)
    return [json.loads(i) for i in items]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _publish(r: redis.Redis, workflow_id: str, event_type: str, data: dict) -> None:
    payload = json.dumps({"event_type": event_type, "data": data})
    r.publish(f"workflow:{workflow_id}:events", payload)
