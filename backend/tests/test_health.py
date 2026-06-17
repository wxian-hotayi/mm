"""Health endpoint: public liveness probe with a real database round-trip."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from conftest import API

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_health_ok_without_auth(client: AsyncClient) -> None:
    response = await client.get(f"{API}/health")
    assert response.status_code == 200
    from app.api.v1 import API_VERSION

    assert response.json() == {
        "status": "ok",
        "version": API_VERSION,
        "db": "ok",
    }
