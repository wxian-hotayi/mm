"""Application settings loaded from environment variables and .env files.

The .env file is resolved both at the repository root and inside ``backend/``
so the application behaves identically whether started from either directory.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR: Path = Path(__file__).resolve().parents[2]
_REPO_ROOT: Path = _BACKEND_DIR.parent

_INSECURE_SECRET_PLACEHOLDER = "change-me-64-chars"


class Settings(BaseSettings):
    """Environment-driven configuration (see DESIGN.md section 17)."""

    model_config = SettingsConfigDict(
        env_file=(str(_REPO_ROOT / ".env"), str(_BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    ENV: str = "development"
    SECRET_KEY: str = _INSECURE_SECRET_PLACEHOLDER
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/wealthos.db"
    FRONTEND_ORIGIN: str = "http://localhost:5173"
    ADMIN_EMAIL: str = ""
    ADMIN_USERNAME: str = ""
    ADMIN_PASSWORD: str = ""
    ACCESS_TOKEN_MINUTES: int = 30
    REFRESH_DAYS_REMEMBER: int = 30
    REFRESH_DAYS_DEFAULT: int = 1
    RATE_LIMIT_ENABLED: bool = True
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    LOG_LEVEL: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.ENV.strip().lower() == "production"

    @model_validator(mode="after")
    def _require_strong_secret_in_production(self) -> "Settings":
        if self.is_production and (
            not self.SECRET_KEY.strip()
            or self.SECRET_KEY == _INSECURE_SECRET_PLACEHOLDER
        ):
            raise ValueError(
                "SECRET_KEY must be set to a strong random value when "
                "ENV=production (the development placeholder is not allowed)."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached Settings instance."""
    return Settings()
