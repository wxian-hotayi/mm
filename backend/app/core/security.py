"""Password hashing (bcrypt) and JWT access-token handling (PyJWT HS256)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import AuthError

_BCRYPT_ROUNDS = 12
_BCRYPT_MAX_PASSWORD_BYTES = 72
_JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"

# Opaque refresh / reset secrets are 48 random bytes (DESIGN §9), URL-safe
# base64-encoded for cookie transport, and stored only as a SHA-256 hex digest.
_REFRESH_TOKEN_BYTES = 48
_RESET_TOKEN_BYTES = 48


def _password_bytes(password: str) -> bytes:
    """Encode a password for bcrypt, truncating explicitly at 72 bytes.

    bcrypt only considers the first 72 bytes of input; modern bcrypt versions
    raise on longer inputs instead of silently truncating, so we perform the
    truncation deliberately and consistently for hashing and verification.
    """
    return password.encode("utf-8")[:_BCRYPT_MAX_PASSWORD_BYTES]


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt at cost factor 12."""
    hashed = bcrypt.hashpw(
        _password_bytes(password), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    )
    return hashed.decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time check of a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(
            _password_bytes(password), password_hash.encode("ascii")
        )
    except (ValueError, UnicodeEncodeError):
        return False


# A fixed dummy hash (computed once at import) so the login path can spend
# bcrypt time even when the account is missing or inactive, defeating the
# username/email-enumeration timing oracle.
_DUMMY_PASSWORD_HASH: str = hash_password("wealthos-dummy-password")


def verify_dummy_password(password: str) -> bool:
    """Run a bcrypt verification against a fixed dummy hash and discard the
    result. Used on the no-user / inactive-user login branch so every login
    attempt performs comparable bcrypt work (constant-time-ish), always
    returning ``False``."""
    return verify_password(password, _DUMMY_PASSWORD_HASH)


def create_access_token(user_id: int, role: str) -> str:
    """Create a signed JWT access token for the given user."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate an access token; raise AuthError when invalid.

    Returns the claim payload with ``sub`` (user id as string), ``role``,
    ``type``, ``iat`` and ``exp``.
    """
    settings = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[_JWT_ALGORITHM],
            options={"require": ["sub", "exp", "iat"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Access token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid access token") from exc
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise AuthError("Invalid token type")
    return payload


def hash_token(raw: str) -> str:
    """Return the SHA-256 hex digest of an opaque token (DESIGN §9).

    Refresh and password-reset secrets are stored only as this digest; a
    lookup hashes the presented raw token and compares against the column.
    SHA-256 (not bcrypt) is appropriate here because the secret is itself a
    high-entropy 48-byte random value, so there is nothing to brute-force.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_refresh_token() -> tuple[str, str]:
    """Mint a new refresh token; return ``(raw, sha256_hash)`` (DESIGN §9).

    ``raw`` is the URL-safe secret placed in the ``wos_refresh`` cookie;
    ``sha256_hash`` is what is persisted in ``auth_sessions``.
    """
    raw = secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)
    return raw, hash_token(raw)


def generate_reset_token() -> tuple[str, str]:
    """Mint a new password-reset token; return ``(raw, sha256_hash)``.

    ``raw`` is logged server-side and (later) emailed; ``sha256_hash`` is
    persisted in ``password_reset_tokens``.
    """
    raw = secrets.token_urlsafe(_RESET_TOKEN_BYTES)
    return raw, hash_token(raw)
