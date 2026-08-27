"""
test_level3.py
Level 3 regression tests.
"""
from __future__ import annotations

import ast
import inspect
import pytest
from httpx import AsyncClient, ASGITransport
import uuid

from proxy.mcp_proxy import app
import db.client as db

@pytest.mark.asyncio
async def test_endpoints_require_auth():
    """
    /init_workflow and /pay should return 401 Unauthorized if no API key is provided.
    """
    await db.run_migrations()
    await db.init_pool()
    try:
        WID = f"auth-test-{uuid.uuid4().hex[:8]}"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post("/init_workflow", json={"workflow_id": WID, "budget_limit": 10000.0})
            assert res.status_code == 401, f"Expected 401 Unauthorized, got {res.status_code}"
    finally:
        await db.close_pool()

def test_websocket_requires_auth():
    """
    /ws/{workflow_id} should reject connections without a token.
    FastAPI's TestClient websocket connect should raise a WebSocketDisconnect if rejected.
    """
    from fastapi.testclient import TestClient
    from fastapi.websockets import WebSocketDisconnect
    
    client = TestClient(app)
    WID = f"auth-test-{uuid.uuid4().hex[:8]}"
    
    rejected = False
    try:
        with client.websocket_connect(f"/ws/{WID}") as websocket:
            pass
    except WebSocketDisconnect:
        rejected = True
    except Exception as e:
        if "403" in str(e) or "401" in str(e):
            rejected = True
    
    assert rejected, "WebSocket connected successfully without an auth token!"


def test_cors_is_restricted():
    """
    allow_origins=["*"] must be removed from mcp_proxy.py
    """
    from proxy import mcp_proxy
    
    with open(mcp_proxy.__file__, "r", encoding="utf-8") as f:
        source = f.read()
    
    assert 'allow_origins=["*"]' not in source, "CORS is still completely open with allow_origins=['*']"
