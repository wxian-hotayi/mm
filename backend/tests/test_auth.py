"""Authentication: login (JWT bearer) and the /auth/me dependency chain."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_USERNAME, API, UserFactory

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _login(
    client: AsyncClient, identifier: str, password: str
) -> tuple[int, dict[str, object]]:
    response = await client.post(
        f"{API}/auth/login",
        json={"identifier": identifier, "password": password},
    )
    return response.status_code, response.json()


async def test_login_ok_by_username(client: AsyncClient) -> None:
    status, body = await _login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    assert status == 200
    token = body["token"]
    assert isinstance(token, dict)
    assert token["token_type"] == "bearer"
    assert isinstance(token["access_token"], str) and token["access_token"]
    assert token["expires_in"] == 30 * 60
    user = body["user"]
    assert isinstance(user, dict)
    assert user["username"] == ADMIN_USERNAME
    assert user["email"] == ADMIN_EMAIL
    assert user["role"] == "admin"
    assert user["base_currency"] == "MYR"


async def test_login_ok_by_email_case_insensitive(client: AsyncClient) -> None:
    status, body = await _login(client, ADMIN_EMAIL.upper(), ADMIN_PASSWORD)
    assert status == 200
    user = body["user"]
    assert isinstance(user, dict)
    assert user["username"] == ADMIN_USERNAME


async def test_login_wrong_password(client: AsyncClient) -> None:
    status, body = await _login(client, ADMIN_USERNAME, "definitely-wrong")
    assert status == 401
    assert body == {"detail": "Invalid credentials", "code": "auth_error"}


async def test_login_unknown_user(client: AsyncClient) -> None:
    status, body = await _login(client, "no-such-user", ADMIN_PASSWORD)
    assert status == 401
    assert body["code"] == "auth_error"


async def test_login_inactive_user_rejected(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    inactive = await user_factory(active=False)
    status, body = await _login(client, inactive.username, inactive.password)
    assert status == 401
    assert body["code"] == "auth_error"


async def test_failed_login_is_audited(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    # A wrong password for a real account must leave a queryable audit trail
    # (DESIGN section 15 / Phase-1 auth-event auditing), not just a stdout log.
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.audit import AuditEventType, AuditLog, AuditSeverity

    user = await user_factory()
    status, body = await _login(client, user.username, "definitely-wrong")
    assert status == 401
    assert body["code"] == "auth_error"

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user.id,
                    AuditLog.action == "LOGIN_FAILED",
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.event_type == AuditEventType.AUTH.value
    assert row.severity == AuditSeverity.WARNING.value
    # The attempted password is never persisted.
    assert "definitely-wrong" not in row.context
    assert user.username in row.context


async def test_failed_login_unknown_user_is_audited_without_user_id(
    client: AsyncClient,
) -> None:
    # Unknown identifiers are auditable too, with a NULL user_id.
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.audit import AuditLog

    identifier = "ghost-no-such-account"
    status, _ = await _login(client, identifier, ADMIN_PASSWORD)
    assert status == 401

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.user_id.is_(None),
                    AuditLog.action == "LOGIN_FAILED",
                )
            )
        ).scalars().all()
    assert any(identifier in row.context for row in rows)


async def test_me_with_token(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.get(f"{API}/auth/me", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == ADMIN_USERNAME
    assert body["role"] == "admin"
    assert "password_hash" not in body


async def test_me_without_token(client: AsyncClient) -> None:
    response = await client.get(f"{API}/auth/me")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["code"] == "auth_error"


async def test_me_with_garbage_token(client: AsyncClient) -> None:
    response = await client.get(
        f"{API}/auth/me",
        headers={"Authorization": "Bearer not.a.valid-jwt"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "auth_error"


async def test_me_with_token_of_deactivated_user(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    # The token itself is well-formed, but the account is inactive.
    inactive = await user_factory(active=False)
    response = await client.get(f"{API}/auth/me", headers=inactive.headers)
    assert response.status_code == 401
    assert response.json()["code"] == "auth_error"


async def test_protected_route_requires_auth(client: AsyncClient) -> None:
    response = await client.get(f"{API}/transactions")
    assert response.status_code == 401
