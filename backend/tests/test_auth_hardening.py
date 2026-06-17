"""Phase-3 auth hardening (DESIGN §9 / §20.8): cookie sessions, refresh
rotation, logout revocation, password reset and rate limiting.

These tests exercise the HTTP surface directly so they cover the cookie wiring,
the rotating ``auth_sessions`` rows and the password-reset token lifecycle —
the behaviors that the dual-mode (cookie-primary, bearer-fallback) design adds
on top of the Phase-1 bearer flow, which ``test_auth.py`` still guards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_USERNAME, API, UserFactory

if TYPE_CHECKING:
    from fastapi import FastAPI

pytestmark = pytest.mark.asyncio(loop_scope="session")

_ACCESS_COOKIE = "wos_access"
_REFRESH_COOKIE = "wos_refresh"


async def _login(
    client: AsyncClient,
    identifier: str = ADMIN_USERNAME,
    password: str = ADMIN_PASSWORD,
    *,
    remember: bool = False,
) -> tuple[int, dict[str, object]]:
    response = await client.post(
        f"{API}/auth/login",
        json={
            "identifier": identifier,
            "password": password,
            "remember": remember,
        },
    )
    return response.status_code, response.json()


# ---------------------------------------------------------------------------
# Cookie login
# ---------------------------------------------------------------------------
async def test_login_sets_both_session_cookies(client: AsyncClient) -> None:
    response = await client.post(
        f"{API}/auth/login",
        json={"identifier": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    # Phase-1 body shape is preserved alongside the new cookies.
    body = response.json()
    assert body["token"]["token_type"] == "bearer"
    assert body["user"]["username"] == ADMIN_USERNAME

    set_cookie_headers = response.headers.get_list("set-cookie")
    joined = "\n".join(set_cookie_headers)
    assert _ACCESS_COOKIE in joined
    assert _REFRESH_COOKIE in joined
    # Security attributes (DESIGN §9): both HttpOnly + SameSite=Lax; the
    # refresh cookie is scoped to /api/v1/auth.
    assert "httponly" in joined.lower()
    assert "samesite=lax" in joined.lower()
    assert "path=/api/v1/auth" in joined.lower()
    # Not production → Secure must NOT be set.
    assert "secure" not in joined.lower()


async def test_me_via_cookie_without_bearer_header(app: "FastAPI") -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as cookie_client:
        status, _ = await _login(cookie_client)
        assert status == 200
        assert cookie_client.cookies.get(_ACCESS_COOKIE)
        # No Authorization header — the cookie alone must authenticate.
        response = await cookie_client.get(f"{API}/auth/me")
    assert response.status_code == 200
    assert response.json()["username"] == ADMIN_USERNAME


async def test_remember_me_extends_refresh_cookie_lifetime(
    client: AsyncClient,
) -> None:
    response = await client.post(
        f"{API}/auth/login",
        json={
            "identifier": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
            "remember": True,
        },
    )
    assert response.status_code == 200
    refresh_cookie = next(
        h
        for h in response.headers.get_list("set-cookie")
        if h.startswith(f"{_REFRESH_COOKIE}=")
    )
    # 30 days remember (settings default) → 30*24*60*60 seconds.
    assert "max-age=2592000" in refresh_cookie.lower()


# ---------------------------------------------------------------------------
# Refresh rotation
# ---------------------------------------------------------------------------
async def test_refresh_rotates_and_old_token_is_rejected(
    app: "FastAPI",
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as session_client:
        status, _ = await _login(session_client)
        assert status == 200
        old_refresh = session_client.cookies.get(_REFRESH_COOKIE)
        assert old_refresh

        rotate = await session_client.post(f"{API}/auth/refresh")
        assert rotate.status_code == 200
        new_refresh = session_client.cookies.get(_REFRESH_COOKIE)
        assert new_refresh and new_refresh != old_refresh
        # The new access cookie still authenticates.
        me_new = await session_client.get(f"{API}/auth/me")
        assert me_new.status_code == 200

    # Presenting the now-revoked old refresh token must fail (no rotation).
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as replay_client:
        replay_client.cookies.set(
            _REFRESH_COOKIE, old_refresh, path="/api/v1/auth"
        )
        replay = await replay_client.post(f"{API}/auth/refresh")
    assert replay.status_code == 401
    assert replay.json()["code"] == "auth_error"


async def test_refresh_without_cookie_is_401(client: AsyncClient) -> None:
    response = await client.post(f"{API}/auth/refresh")
    assert response.status_code == 401
    assert response.json()["code"] == "auth_error"


# ---------------------------------------------------------------------------
# Logout revocation
# ---------------------------------------------------------------------------
async def test_logout_revokes_session_and_clears_cookies(
    app: "FastAPI",
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as session_client:
        status, _ = await _login(session_client)
        assert status == 200
        refresh_before = session_client.cookies.get(_REFRESH_COOKIE)
        assert refresh_before

        logout = await session_client.post(f"{API}/auth/logout")
        assert logout.status_code == 200
        assert logout.json() == {"ok": True}

    # The revoked refresh token can no longer be rotated.
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as replay_client:
        replay_client.cookies.set(
            _REFRESH_COOKIE, refresh_before, path="/api/v1/auth"
        )
        replay = await replay_client.post(f"{API}/auth/refresh")
    assert replay.status_code == 401


async def test_logout_without_cookie_is_ok(client: AsyncClient) -> None:
    # Logout is best-effort; no session cookie still clears the client.
    response = await client.post(f"{API}/auth/logout")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


# ---------------------------------------------------------------------------
# Password reset flow
# ---------------------------------------------------------------------------
async def test_password_reset_request_is_ok_for_unknown_email(
    client: AsyncClient,
) -> None:
    # No user enumeration: an unknown address still returns {"ok": true}.
    response = await client.post(
        f"{API}/auth/password-reset/request",
        json={"email": "nobody-here@wealthos.test"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_password_reset_request_and_confirm_flow(
    client: AsyncClient, user_factory: UserFactory
) -> None:
    from sqlalchemy import select

    from app.core.security import (
        generate_reset_token,
        verify_password,
    )
    from app.db.session import SessionLocal
    from app.models.auth_session import PasswordResetToken
    from app.models.user import User

    user = await user_factory()

    request = await client.post(
        f"{API}/auth/password-reset/request",
        json={"email": user.email},
    )
    assert request.status_code == 200
    assert request.json() == {"ok": True}

    # A hashed (never plaintext) token row was created for the user; mint a raw
    # token, store its hash, and confirm against it (the raw token is logged
    # server-side in production, not returned by the API).
    raw_token, token_hash = generate_reset_token()
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(PasswordResetToken).where(
                    PasswordResetToken.user_id == user.id
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        # The persisted token is a hash, not the secret.
        assert rows[0].token_hash != ""
        # Re-point the stored hash to a token we control to drive confirm().
        rows[0].token_hash = token_hash
        await db.commit()

    new_password = "Brand-New-Pass-123"
    confirm = await client.post(
        f"{API}/auth/password-reset/confirm",
        json={"token": raw_token, "new_password": new_password},
    )
    assert confirm.status_code == 200
    assert confirm.json() == {"ok": True}

    # The new password works; the token cannot be replayed.
    status, _ = await _login(client, user.username, new_password)
    assert status == 200
    replay = await client.post(
        f"{API}/auth/password-reset/confirm",
        json={"token": raw_token, "new_password": "Another-Pass-456"},
    )
    assert replay.status_code == 401

    # The stored password hash actually changed in the DB.
    async with SessionLocal() as db:
        refreshed = await db.get(User, user.id)
        assert refreshed is not None
        assert verify_password(new_password, refreshed.password_hash)


async def test_password_reset_confirm_invalid_token_is_401(
    client: AsyncClient,
) -> None:
    response = await client.post(
        f"{API}/auth/password-reset/confirm",
        json={"token": "not-a-real-token", "new_password": "Whatever-123"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "auth_error"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
async def test_login_rate_limited_after_ten_attempts(app: "FastAPI") -> None:
    """The 11th login from one IP within a minute returns 429 + Retry-After.

    Rate limiting is disabled session-wide in conftest; this test re-enables it
    on the cached Settings instance and uses a unique client IP so its sliding
    window is isolated from every other test's traffic.
    """
    from app.core.config import get_settings

    settings = get_settings()
    original = settings.RATE_LIMIT_ENABLED
    settings.RATE_LIMIT_ENABLED = True
    try:
        transport = ASGITransport(app=app, client=("203.0.113.7", 5555))
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ip_client:
            # 10 attempts are allowed (wrong password still counts as a hit).
            for _ in range(10):
                resp = await ip_client.post(
                    f"{API}/auth/login",
                    json={
                        "identifier": ADMIN_USERNAME,
                        "password": "wrong-on-purpose",
                    },
                )
                assert resp.status_code in (401, 200)
            # The 11th is throttled.
            throttled = await ip_client.post(
                f"{API}/auth/login",
                json={
                    "identifier": ADMIN_USERNAME,
                    "password": ADMIN_PASSWORD,
                },
            )
        assert throttled.status_code == 429
        assert throttled.json()["code"] == "rate_limited"
        retry_after = throttled.headers.get("retry-after")
        assert retry_after is not None and int(retry_after) >= 1
    finally:
        settings.RATE_LIMIT_ENABLED = original


async def test_rate_limit_disabled_allows_many_logins(
    client: AsyncClient,
) -> None:
    # With the flag off (conftest default), repeated logins never throttle.
    for _ in range(15):
        status, _ = await _login(client)
        assert status == 200


async def test_login_admin_email_case_insensitive_with_cookies(
    client: AsyncClient,
) -> None:
    response = await client.post(
        f"{API}/auth/login",
        json={"identifier": ADMIN_EMAIL.upper(), "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    assert _ACCESS_COOKIE in "\n".join(
        response.headers.get_list("set-cookie")
    )
