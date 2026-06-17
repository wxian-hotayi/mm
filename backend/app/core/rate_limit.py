"""In-memory sliding-window rate limiting (DESIGN §9, §15).

A single ASGI middleware enforces two independent per-client-IP budgets over a
60-second sliding window:

* a **sensitive** budget (default 10/min) on the auth endpoints that an
  attacker would target — ``POST /api/v1/auth/login`` and the password-reset
  request/confirm endpoints, and
* a **global** budget (default 240/min) across every API request.

Both share one monotonic-clock sliding-window store. When a budget is
exhausted the request is rejected with ``429 Too Many Requests`` and a
``Retry-After`` header (whole seconds until the oldest in-window hit expires),
matching the application's ``{"detail", "code"}`` error envelope.

The limiter is process-local (in-memory) — fine for the single-container
deployment; swapping the backing store for Redis is documented as a roadmap
item (MAINTENANCE.md). It honors ``settings.RATE_LIMIT_ENABLED`` (default
true) so the test suite can disable it deterministically.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("rate_limit")

_WINDOW_SECONDS: float = 60.0
_SENSITIVE_LIMIT_PER_MINUTE = 10
_GLOBAL_LIMIT_PER_MINUTE = 240

# Path suffixes (under the /api/v1 prefix) that share the sensitive budget.
_SENSITIVE_SUFFIXES: tuple[str, ...] = (
    "/auth/login",
    "/auth/password-reset/request",
    "/auth/password-reset/confirm",
)


class _SlidingWindow:
    """Per-key deque of monotonic hit timestamps within one rolling window."""

    def __init__(self, window_seconds: float) -> None:
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, now: float) -> float | None:
        """Record a hit for ``key`` if under ``limit``.

        Returns ``None`` when the request is allowed, or the number of seconds
        the client must wait (``Retry-After``, ≥ 1) when the budget is full.
        The hit is recorded only when allowed, so a rejected request does not
        extend the window further.
        """
        cutoff = now - self._window
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = self._window - (now - hits[0])
                return max(1.0, retry_after)
            hits.append(now)
            if not hits:
                # Defensive: keep the map from growing without bound.
                del self._hits[key]
            return None


class RateLimitMiddleware:
    """ASGI middleware applying the sensitive + global sliding-window budgets."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        sensitive_limit: int = _SENSITIVE_LIMIT_PER_MINUTE,
        global_limit: int = _GLOBAL_LIMIT_PER_MINUTE,
        window_seconds: float = _WINDOW_SECONDS,
    ) -> None:
        self._app = app
        self._sensitive_limit = sensitive_limit
        self._global_limit = global_limit
        self._sensitive = _SlidingWindow(window_seconds)
        self._global = _SlidingWindow(window_seconds)

    async def __call__(
        self,
        scope: dict[str, object],
        receive: Callable[[], Awaitable[dict[str, object]]],
        send: Callable[[dict[str, object]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http" or not get_settings().RATE_LIMIT_ENABLED:
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        response = self._enforce(request)
        if response is not None:
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)

    def _enforce(self, request: Request) -> Response | None:
        """Return a 429 response when a budget is exhausted, else ``None``."""
        ip = self._client_ip(request)
        path = request.url.path
        now = time.monotonic()

        if self._is_sensitive(request.method, path):
            retry_after = self._sensitive.check(
                ip, self._sensitive_limit, now
            )
            if retry_after is not None:
                return self._too_many(ip, path, "sensitive", retry_after)

        retry_after = self._global.check(ip, self._global_limit, now)
        if retry_after is not None:
            return self._too_many(ip, path, "global", retry_after)
        return None

    @staticmethod
    def _is_sensitive(method: str, path: str) -> bool:
        if method.upper() != "POST":
            return False
        return any(path.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)

    @staticmethod
    def _client_ip(request: Request) -> str:
        client = request.client
        return client.host if client is not None else "unknown"

    def _too_many(
        self, ip: str, path: str, scope_name: str, retry_after: float
    ) -> JSONResponse:
        retry_seconds = int(retry_after) + (
            1 if retry_after != int(retry_after) else 0
        )
        retry_seconds = max(1, retry_seconds)
        logger.warning(
            "Rate limit exceeded",
            extra={"ip": ip, "path": path, "scope": scope_name},
        )
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Too many requests. Please slow down and retry.",
                "code": "rate_limited",
            },
            headers={"Retry-After": str(retry_seconds)},
        )
