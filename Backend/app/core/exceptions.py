"""
Custom exception types for the Bookkeeping Agent Platform.

Keeping exceptions in a dedicated module allows services to raise
domain-specific errors that API routers can catch and translate into
clean HTTP responses.
"""


class BookkeepingAgentException(Exception):
    """Base exception for all platform errors."""

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


# ------------------------------------------------------------------
# Authentication / Token errors
# ------------------------------------------------------------------

class AuthenticationError(BookkeepingAgentException):
    """Raised when an external service rejects our credentials."""


class TokenExpiredError(AuthenticationError):
    """Raised when an OAuth2 access token has expired."""


class TokenRefreshError(AuthenticationError):
    """Raised when the token refresh attempt itself fails."""


# ------------------------------------------------------------------
# External API / Network errors
# ------------------------------------------------------------------

class ExternalAPIError(BookkeepingAgentException):
    """Raised when an external API returns an unexpected error response."""

    def __init__(
        self,
        message: str,
        service: str,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message, detail)
        self.service = service
        self.status_code = status_code


class XeroAPIError(ExternalAPIError):
    """Specific error for Xero API failures."""

    def __init__(self, message: str, status_code: int | None = None, detail: str | None = None) -> None:
        super().__init__(message, service="Xero", status_code=status_code, detail=detail)


# ------------------------------------------------------------------
# Data / Business logic errors
# ------------------------------------------------------------------

class DataValidationError(BookkeepingAgentException):
    """Raised when incoming data fails business-rule validation."""


class ReconciliationError(BookkeepingAgentException):
    """Raised when a transaction cannot be reconciled."""
