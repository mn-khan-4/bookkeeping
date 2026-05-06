"""
Core configuration module.

Uses pydantic-settings to load and validate all environment variables
from the .env file. Provides a single, importable `settings` object
for use across the entire application.
"""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables / .env file.
    All fields are validated and type-coerced by Pydantic automatically.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Silently ignore any extra keys in .env
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    APP_NAME: str = "AI Bookkeeping Agent Platform"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"  # development | staging | production
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    DEMO_MODE: bool = False

    # ------------------------------------------------------------------
    # Xero API Credentials  (OAuth 2.0 PKCE / Client-Credentials)
    # ------------------------------------------------------------------
    XERO_BASE_URL: str = "https://api.xero.com"
    XERO_AUTH_URL: str = "https://login.xero.com/identity/connect/authorize"
    XERO_TOKEN_URL: str = "https://identity.xero.com/connect/token"
    XERO_CLIENT_ID: str
    XERO_CLIENT_SECRET: str
    XERO_REDIRECT_URI: str = "http://localhost:8000/api/v1/integrations/xero/callback"
    XERO_SCOPES: str = "openid profile email offline_access accounting.invoices accounting.payments accounting.banktransactions accounting.contacts accounting.settings"
    XERO_USE_PKCE: bool = False
    XERO_ACCESS_TOKEN: Optional[str] = None
    XERO_REFRESH_TOKEN: Optional[str] = None
    XERO_TENANT_ID: Optional[str] = None        # Active Xero organisation ID

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    SECRET_KEY: str = "change-me-in-production-32-chars-min"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    DATABASE_URL: str

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    LOG_LEVEL: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns the cached Settings singleton.
    Using @lru_cache ensures the .env file is only read once per process.
    """
    return Settings()


# Convenience re-export so callers can simply do:
#   from app.core.config import settings
settings: Settings = get_settings()
