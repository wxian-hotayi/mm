"""Application error hierarchy and FastAPI exception handlers.

Services raise :class:`AppError` subclasses; the registered handler renders
them as ``{"detail": <message>, "code": <code>}`` JSON responses. FastAPI's
own 422 request-validation errors pass through unchanged.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger("errors")


class AppError(Exception):
    """Base application error carrying an HTTP status and machine code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(404, "not_found", message)


class ValidationFailed(AppError):
    def __init__(self, message: str = "Validation failed") -> None:
        super().__init__(422, "validation_failed", message)


class AuthError(AppError):
    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(401, "auth_error", message)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Not allowed") -> None:
        super().__init__(403, "forbidden", message)


class ConflictError(AppError):
    def __init__(self, message: str = "Conflict with existing resource") -> None:
        super().__init__(409, "conflict", message)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the AppError handler to a FastAPI application."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status >= 500:
            logger.error(
                "Unhandled application error",
                extra={"code": exc.code, "path": request.url.path},
                exc_info=exc,
            )
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse(
            status_code=exc.status,
            content={"detail": exc.message, "code": exc.code},
            headers=headers,
        )
