"""
Xero Service — manages Xero's OAuth 2.0 flow and Accounting API calls.

Responsibilities:
  - Full OAuth 2.0 Authorization Code + PKCE flow (authorize → callback → token)
  - Silent token refresh using stored refresh tokens
  - Tenant discovery and storage
  - Fetching invoices (Workflow 1)
    - Fetching unreconciled bank transactions (Workflow 1, Step 3)
    - Publishing draft bills / invoices to Xero (Workflow 1, Step 4)
  - CSV export of invoice data

Xero API docs: https://developer.xero.com/documentation/api/accounting/overview
"""

import base64
import csv
import hashlib
import io
import logging
import os
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    TokenRefreshError,
    XeroAPIError,
)
from app.models.xero import (
    XeroBankTransaction,
    XeroInvoice,
    XeroPublishResult,
    XeroTokenResponse,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level OAuth state store
# Keeps state + PKCE verifier alive between the /authorize and /callback
# requests, which use separate XeroService instances.
# In production: replace with a Redis-backed session store.
# ---------------------------------------------------------------------------
_OAUTH_STATE_STORE: dict[str, str] = {}   # { state → pkce_verifier }


def _resolve_env_path() -> Path | None:
    """
    Resolve the .env file location used by the backend settings.

    Priority:
      1. `BOOKKEEPING_ENV_FILE` environment variable (if set)
      2. Current working directory `.env`
      3. Backend-root `.env` (relative to this file)
    """
    explicit_env = os.getenv("BOOKKEEPING_ENV_FILE")
    if explicit_env:
        explicit_path = Path(explicit_env).expanduser().resolve()
        if explicit_path.exists():
            return explicit_path

    cwd_env = Path(".env").resolve()
    if cwd_env.exists():
        return cwd_env

    backend_env = Path(__file__).resolve().parents[2] / ".env"
    if backend_env.exists():
        return backend_env

    return None


def _persist_tokens_to_env(
    access_token: str,
    refresh_token: str,
    tenant_id: str | None = None,
) -> None:
    """
    Write the latest Xero tokens back to the .env file so they survive
    process restarts.  Mirrors the file-write pattern from the reference script.

    Only updates the three Xero token lines; all other .env content is
    preserved verbatim.
    """
    env_path = _resolve_env_path()
    if env_path is None:
        logger.warning("No .env file found — tokens NOT persisted.")
        return

    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

        def _replace_or_append(lines: list[str], key: str, value: str) -> list[str]:
            key_prefix = f"{key}="
            new_line = f"{key_prefix}{value}\n"
            for i, line in enumerate(lines):
                if line.strip().startswith(key_prefix):
                    lines[i] = new_line
                    return lines
            lines.append(new_line)
            return lines

        lines = _replace_or_append(lines, "XERO_ACCESS_TOKEN", access_token)
        lines = _replace_or_append(lines, "XERO_REFRESH_TOKEN", refresh_token)
        if tenant_id:
            lines = _replace_or_append(lines, "XERO_TENANT_ID", tenant_id)

        with open(env_path, "w", encoding="utf-8") as fh:
            fh.writelines(lines)

        logger.info("Xero tokens persisted to %s", env_path)
    except OSError as exc:
        logger.error("Failed to persist tokens to .env: %s", exc)


class XeroService:
    """
    Client for the Xero Accounting API.

    Usage (as async context manager):
        async with XeroService() as xero:
            transactions = await xero.get_unreconciled_bank_transactions()

    The OAuth2 flow works as follows:
    1. Call `get_authorization_url()` → redirect the user to Xero
    2. Xero redirects to XERO_REDIRECT_URI with `?code=…&state=…`
    3. Call `exchange_code_for_token(code, state)` → stores tokens in memory
       and persists them to .env for restart resilience
    4. `get_tenants()` is called automatically after token exchange to
       discover the active Xero organisation (tenant).
    5. All subsequent API calls use the stored access_token, auto-refreshing
       when needed.
    """

    AUTH_URL: str = settings.XERO_AUTH_URL
    TOKEN_URL: str = settings.XERO_TOKEN_URL
    API_BASE: str = f"{settings.XERO_BASE_URL}/api.xro/2.0"
    CONNECTIONS_URL: str = f"{settings.XERO_BASE_URL}/connections"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._access_token: str | None = settings.XERO_ACCESS_TOKEN or None
        self._refresh_token: str | None = settings.XERO_REFRESH_TOKEN or None
        self._tenant_id: str | None = settings.XERO_TENANT_ID or None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "XeroService":
        self._client = httpx.AsyncClient(
            base_url=self.API_BASE,
            headers=self._build_api_headers(),
            timeout=httpx.Timeout(30.0),
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _build_api_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._access_token and self._access_token.strip():
            headers["Authorization"] = f"Bearer {self._access_token.strip()}"
        if self._tenant_id and self._tenant_id.strip():
            headers["Xero-tenant-id"] = self._tenant_id.strip()
        return headers

    def _basic_auth_header(self) -> str:
        """Build the Basic auth header for token endpoint calls."""
        credentials = f"{settings.XERO_CLIENT_ID}:{settings.XERO_CLIENT_SECRET}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"

    @staticmethod
    def _generate_pkce_pair() -> tuple[str, str]:
        """Return (code_verifier, code_challenge) for PKCE."""
        verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return verifier, challenge

    @staticmethod
    def _build_oauth_scope() -> str:
        """
        Build a normalised Xero OAuth scope string.

        Xero's user-consent OAuth flow expects OIDC identity scopes to be
        present. If callers configure only accounting scopes in `.env`, Xero
        may return `unauthorized_client` / `invalid scope for client`.
        """
        required_scopes = ["openid", "profile", "email", "offline_access"]
        configured_scopes = (settings.XERO_SCOPES or "").split()

        final_scopes: list[str] = []
        for scope in [*required_scopes, *configured_scopes]:
            if scope and scope not in final_scopes:
                final_scopes.append(scope)

        return " ".join(final_scopes)

    # ------------------------------------------------------------------
    # OAuth 2.0 – Step 1: Build authorization URL
    # ------------------------------------------------------------------

    def get_authorization_url(self) -> dict[str, str]:
        """
        Generate the Xero OAuth 2.0 authorization URL.

        The generated `state` value and matching PKCE verifier are stored in
        the module-level `_OAUTH_STATE_STORE` so they can be retrieved by the
        callback endpoint, even though that uses a fresh XeroService instance.

        Returns:
            dict with keys: `authorization_url`, `state`
        """
        oauth_state = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
        pkce_verifier: str | None = None
        code_challenge: str | None = None

        if settings.XERO_USE_PKCE:
            pkce_verifier, code_challenge = self._generate_pkce_pair()

        # Persist across request boundary
        _OAUTH_STATE_STORE[oauth_state] = pkce_verifier or "NO_PKCE"
        # Trim store to last 20 pending logins
        if len(_OAUTH_STATE_STORE) > 20:
            oldest_key = next(iter(_OAUTH_STATE_STORE))
            del _OAUTH_STATE_STORE[oldest_key]

        params = {
            "response_type": "code",
            "client_id": settings.XERO_CLIENT_ID,
            "redirect_uri": settings.XERO_REDIRECT_URI,
            "scope": self._build_oauth_scope(),
            "state": oauth_state,
        }
        if settings.XERO_USE_PKCE and code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"

        url = f"{self.AUTH_URL}?{urllib.parse.urlencode(params)}"
        logger.info("Generated Xero authorization URL (state=%s).", oauth_state)
        return {"authorization_url": url, "state": oauth_state}

    # ------------------------------------------------------------------
    # OAuth 2.0 – Step 2: Exchange auth code for tokens
    # ------------------------------------------------------------------

    async def exchange_code_for_token(
        self, code: str, state: str
    ) -> XeroTokenResponse:
        """
        Exchange the authorization code received from Xero's callback for
        access + refresh tokens, then immediately fetch the active tenant.

        Mirrors the reference script's XeroFirstAuth() function:
          1. Validate state (CSRF protection)
          2. POST to /connect/token with the auth code
          3. Persist tokens to .env
          4. Auto-discover the Xero tenant ID

        Args:
            code:  The `code` query parameter from the Xero callback.
            state: The `state` query parameter — must match what was generated.

        Returns:
            XeroTokenResponse with the new tokens.

        Raises:
            AuthenticationError: On state mismatch or token exchange failure.
        """
        pkce_verifier = _OAUTH_STATE_STORE.pop(state, None)
        if pkce_verifier is None:
            raise AuthenticationError(
                "OAuth state mismatch or expired. Possible CSRF attack.",
                detail=f"Unknown state value: {state}",
            )

        logger.info("Exchanging Xero authorization code for tokens …")

        payload: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.XERO_REDIRECT_URI,
        }
        if settings.XERO_USE_PKCE:
            payload["code_verifier"] = pkce_verifier

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    self.TOKEN_URL,
                    data=payload,
                    headers={
                        "Authorization": self._basic_auth_header(),
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise AuthenticationError(
                    "Xero token exchange failed.",
                    detail=f"HTTP {exc.response.status_code}: {exc.response.text}",
                ) from exc

        token_data = response.json()
        token = XeroTokenResponse(**token_data)

        # Store in memory for this request's lifetime
        self._access_token = token.access_token
        self._refresh_token = token.refresh_token

        # Auto-discover tenant
        try:
            tenant_id = await self._fetch_first_tenant(token.access_token)
            self._tenant_id = tenant_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not auto-fetch tenant after auth: %s", exc)
            tenant_id = None

        # Persist everything to .env (mirrors reference script's file write)
        _persist_tokens_to_env(token.access_token, token.refresh_token, tenant_id)

        logger.info("Xero tokens obtained and persisted successfully.")
        return token

    # ------------------------------------------------------------------
    # OAuth 2.0 – Token refresh (mirrors XeroRefreshToken() in script)
    # ------------------------------------------------------------------

    async def refresh_tokens(self) -> XeroTokenResponse:
        """
        Public wrapper: refresh the Xero access token and persist the new
        tokens to .env.  Mirrors XeroRefreshToken() in the reference script.

        Returns:
            XeroTokenResponse with the new access + refresh tokens.

        Raises:
            TokenRefreshError: If no refresh token is available or refresh fails.
        """
        token_data = await self._refresh_access_token()
        token = XeroTokenResponse(
            access_token=self._access_token or "",
            refresh_token=self._refresh_token or "",
            expires_in=int(token_data.get("expires_in", 1800)),
            scope=token_data.get("scope"),
        )
        _persist_tokens_to_env(token.access_token, token.refresh_token, self._tenant_id)
        return token

    async def _refresh_access_token(self) -> dict[str, Any]:
        """
        Silently refresh the Xero access token using the stored refresh token.

        Raises:
            TokenRefreshError: If no refresh token is available or refresh fails.
        """
        if not self._refresh_token:
            raise TokenRefreshError(
                "No Xero refresh token available. User must re-authorise."
            )

        logger.info("Refreshing Xero access token …")

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    self.TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh_token,
                    },
                    headers={
                        "Authorization": self._basic_auth_header(),
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise TokenRefreshError(
                    "Xero token refresh failed.",
                    detail=f"HTTP {exc.response.status_code}: {exc.response.text}",
                ) from exc

        data: dict[str, Any] = response.json()
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)

        # Persist refreshed tokens so restart resilience matches reference flow.
        _persist_tokens_to_env(
            self._access_token,
            self._refresh_token or "",
            self._tenant_id,
        )

        # Update the live client headers
        if self._client:
            self._client.headers.update(self._build_api_headers())

        logger.info("Xero access token refreshed.")
        return data

    # ------------------------------------------------------------------
    # Internal API call wrapper (with auto-retry on 401)
    # ------------------------------------------------------------------

    async def _api_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Authenticated GET with automatic token-refresh on 401."""
        if not self._client:
            raise XeroAPIError("XeroService client not initialised. Use async context manager.")

        response = await self._client.get(path, params=params)

        if response.status_code == 401:
            logger.info("Xero returned 401 — refreshing token and retrying …")
            await self._refresh_access_token()
            self._client.headers.update(self._build_api_headers())
            response = await self._client.get(path, params=params)

        if not response.is_success:
            raise XeroAPIError(
                f"Xero GET {path} failed.",
                status_code=response.status_code,
                detail=response.text,
            )

        return response.json()

    async def _api_post(self, path: str, payload: dict[str, Any]) -> Any:
        """Authenticated POST with automatic token-refresh on 401."""
        if not self._client:
            raise XeroAPIError("XeroService client not initialised. Use async context manager.")

        response = await self._client.post(path, json=payload)

        if response.status_code == 401:
            await self._refresh_access_token()
            self._client.headers.update(self._build_api_headers())
            response = await self._client.post(path, json=payload)

        if not response.is_success:
            raise XeroAPIError(
                f"Xero POST {path} failed.",
                status_code=response.status_code,
                detail=response.text,
            )

        return response.json()

    # ------------------------------------------------------------------
    # Tenant discovery (mirrors XeroTenants() in script)
    # ------------------------------------------------------------------

    async def _fetch_first_tenant(self, access_token: str) -> str:
        """
        Hit the /connections endpoint and return the first tenant ID.
        Used internally after token exchange.
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                self.CONNECTIONS_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            tenants: list[dict] = resp.json()
            if not tenants:
                raise XeroAPIError("No Xero organisations found on this account.")
            return tenants[0]["tenantId"]

    async def get_tenants(self) -> list[dict[str, Any]]:
        """
        Return all Xero organisations (tenants) the current tokens can access.
        Mirrors XeroTenants() in the reference script.

        Auto-refreshes the token if expired.
        """
        if not self._access_token:
            raise AuthenticationError(
                "No Xero access token. Complete the OAuth flow first."
            )

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                self.CONNECTIONS_URL,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Accept": "application/json",
                },
            )

            if response.status_code == 401:
                await self._refresh_access_token()
                response = await client.get(
                    self.CONNECTIONS_URL,
                    headers={
                        "Authorization": f"Bearer {self._access_token}",
                        "Accept": "application/json",
                    },
                )

            response.raise_for_status()
            tenants: list[dict] = response.json()

        # Pin the first tenant if not already set
        if tenants and not self._tenant_id:
            self._tenant_id = tenants[0].get("tenantId")
            _persist_tokens_to_env(
                self._access_token or "",
                self._refresh_token or "",
                self._tenant_id,
            )

        return tenants

    # ------------------------------------------------------------------
    # Connection Health
    # ------------------------------------------------------------------

    async def check_connection(self) -> dict[str, Any]:
        """
        Validate the Xero connection by fetching the list of connected tenants.

        Returns a dict with keys: `status`, `message`, `tenant_id` (optional).
        """
        try:
            tenants = await self.get_tenants()

            if not tenants:
                return {
                    "status": "warning",
                    "message": "Connected to Xero but no organisations found.",
                }

            active_tenant = tenants[0]
            self._tenant_id = self._tenant_id or active_tenant.get("tenantId")
            return {
                "status": "ok",
                "message": "Xero connection is healthy.",
                "tenant_id": self._tenant_id,
                "organisation_name": active_tenant.get("tenantName"),
                "total_tenants": len(tenants),
            }

        except (AuthenticationError, TokenRefreshError):
            raise
        except httpx.HTTPStatusError as exc:
            logger.warning("Xero health check HTTP error: %s", exc.response.status_code)
            return {
                "status": "error",
                "message": "Xero API returned an error response.",
                "detail": f"HTTP {exc.response.status_code}",
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Xero health check exception: %s", exc)
            return {"status": "error", "message": str(exc)}

    # ------------------------------------------------------------------
    # Invoices (mirrors XeroRequests() in the reference script)
    # ------------------------------------------------------------------

    async def get_invoices(
        self,
        invoice_type: str | None = None,
        status: str | None = None,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """
        Fetch invoices from Xero.  Mirrors the XeroRequests() function in
        the reference script which calls GET /api.xro/2.0/Invoices.

        Args:
            invoice_type: Optional filter — ``ACCPAY`` (bills) or ``ACCREC``
                          (sales invoices).
            status:       Optional filter — e.g. ``AUTHORISED``, ``DRAFT``.
            page:         1-based page number (Xero returns 100/page).

        Returns:
            List of raw Xero Invoice dicts (as returned by the API).

        Raises:
            XeroAPIError: If the API call fails.
        """
        if not self._tenant_id:
            raise XeroAPIError(
                "No Xero tenant ID configured. Complete OAuth flow first."
            )

        params: dict[str, Any] = {"page": page}
        if invoice_type:
            params["Type"] = invoice_type
        if status:
            params["Statuses"] = status

        logger.info(
            "Fetching Xero invoices (page=%d, type=%s, status=%s) …",
            page,
            invoice_type or "all",
            status or "all",
        )

        data = await self._api_get("/Invoices", params=params)
        return data.get("Invoices", [])

    async def export_invoices_csv(
        self,
        invoice_type: str | None = None,
        status: str | None = None,
    ) -> str:
        """
        Export invoices to CSV format.  Mirrors the export_csv() function
        in the reference script.

        Fetches all invoices (page 1) and returns a CSV string with columns:
        Type, InvoiceNumber, Reference, Contact, Date, DueDate, Total, Status.

        Args:
            invoice_type: Optional Xero invoice type filter.
            status:       Optional Xero status filter.

        Returns:
            UTF-8 CSV string.
        """
        invoices = await self.get_invoices(invoice_type=invoice_type, status=status)

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["Type", "InvoiceNumber", "Reference", "Contact", "Date", "DueDate", "Total", "Status"],
            extrasaction="ignore",
        )
        writer.writeheader()

        for inv in invoices:
            contact_info = inv.get("Contact", {})
            writer.writerow({
                "Type": inv.get("Type", ""),
                "InvoiceNumber": inv.get("InvoiceNumber", ""),
                "Reference": inv.get("Reference", ""),
                "Contact": contact_info.get("Name", ""),
                "Date": inv.get("Date", ""),
                "DueDate": inv.get("DueDate", ""),
                "Total": inv.get("Total", 0),
                "Status": inv.get("Status", ""),
            })

        return output.getvalue()

    # ------------------------------------------------------------------
    # Workflow 1 — Step 3: Fetch unreconciled bank transactions
    # ------------------------------------------------------------------

    async def get_unreconciled_bank_transactions(
        self,
        bank_account_id: str | None = None,
        page: int = 1,
    ) -> list[XeroBankTransaction]:
        """
        **Workflow 1 — Step 3**: Retrieve unreconciled bank statement lines.

        Fetches bank transactions with Status=UNRECONCILED from the Xero
        Accounting API. These are the lines that need to be matched against
        source references.

        Args:
            bank_account_id: Optional UUID to filter by a specific bank account.
                             If None, fetch from all accounts.
            page:            Xero uses 1-based page numbers (100 records/page).

        Returns:
            List of XeroBankTransaction objects.

        Raises:
            XeroAPIError: If the API call fails.
        """
        if not self._tenant_id:
            raise XeroAPIError(
                "No Xero tenant ID configured. Complete OAuth flow first."
            )

        logger.info(
            "Fetching unreconciled Xero bank transactions (page=%d, account=%s) …",
            page,
            bank_account_id or "all",
        )

        params: dict[str, Any] = {
            "Status": "UNRECONCILED",
            "page": page,
        }
        if bank_account_id:
            params["BankAccountID"] = bank_account_id

        try:
            data = await self._api_get("/BankTransactions", params=params)
        except XeroAPIError:
            raise

        raw_transactions: list[dict] = data.get("BankTransactions", [])
        transactions: list[XeroBankTransaction] = []

        for txn in raw_transactions:
            try:
                transactions.append(
                    XeroBankTransaction(
                        bank_transaction_id=txn["BankTransactionID"],
                        bank_account_id=txn["BankAccount"]["AccountID"],
                        bank_account_name=txn["BankAccount"].get("Name"),
                        type=txn["Type"],
                        status=txn.get("Status", "UNRECONCILED"),
                        date=txn["Date"],
                        amount=txn.get("Total", 0),
                        currency_code=txn.get("CurrencyCode", "AUD"),
                        reference=txn.get("Reference"),
                        is_reconciled=txn.get("IsReconciled", False),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping malformed transaction %s: %s", txn.get("BankTransactionID"), exc)

        logger.info("Retrieved %d unreconciled transaction(s) from Xero.", len(transactions))
        return transactions

    # ------------------------------------------------------------------
    # Workflow 1 — Step 4: Publish a bill or invoice to Xero
    # ------------------------------------------------------------------

    async def publish_transaction_to_xero(
        self,
        invoice: XeroInvoice,
    ) -> XeroPublishResult:
        """
        **Workflow 1 — Step 4**: Create a Bill or Invoice in Xero from a
        source reference.

        Args:
            invoice: XeroInvoice data model ready for submission.

        Returns:
            XeroPublishResult indicating success/failure and the Xero Invoice ID.

        Raises:
            XeroAPIError: If the Xero API rejects the payload.
        """
        if not self._tenant_id:
            raise XeroAPIError(
                "No Xero tenant ID configured. Complete OAuth flow first."
            )

        logger.info(
            "Publishing source reference %s to Xero as %s …",
            invoice.source_reference,
            invoice.type,
        )

        payload: dict[str, Any] = {
            "Invoices": [
                {
                    "Type": invoice.type,
                    "Contact": {"ContactID": invoice.contact_id},
                    "Date": invoice.date.strftime("%Y-%m-%d"),
                    "DueDate": invoice.due_date.strftime("%Y-%m-%d") if invoice.due_date else None,
                    "CurrencyCode": invoice.currency_code,
                    "LineAmountTypes": invoice.line_amount_types,
                    "Reference": invoice.reference or invoice.source_reference,
                    "Status": invoice.status,
                    "LineItems": [],  # TODO: Build line items from source document data
                }
            ]
        }

        try:
            response_data = await self._api_post("/Invoices", payload)
        except XeroAPIError as exc:
            logger.error(
                "Failed to publish document %s to Xero: %s",
                invoice.source_reference,
                exc.message,
            )
            return XeroPublishResult(
                success=False,
                source_reference=invoice.source_reference or "",
                message=exc.message,
                status_code=exc.status_code,
            )

        created_invoices: list[dict] = response_data.get("Invoices", [])

        if not created_invoices:
            return XeroPublishResult(
                success=False,
                source_reference=invoice.source_reference or "",
                message="Xero returned an empty Invoices array.",
            )

        xero_invoice_id: str = created_invoices[0]["InvoiceID"]
        logger.info(
            "Successfully published document %s → Xero Invoice %s",
            invoice.source_reference,
            xero_invoice_id,
        )

        return XeroPublishResult(
            success=True,
            xero_invoice_id=xero_invoice_id,
            source_reference=invoice.source_reference or "",
            message="Document successfully published to Xero.",
            status_code=200,
        )

    # ------------------------------------------------------------------
    # Additional Xero API helpers (bank accounts, contacts, accounts, reports)
    # ------------------------------------------------------------------

    async def get_bank_accounts(self) -> list[dict[str, Any]]:
        """Fetch all bank accounts from Xero. Returns AccountID, Name, Type, CurrencyCode, Balance."""
        if not self._tenant_id:
            raise XeroAPIError("No Xero tenant ID configured. Complete OAuth flow first.")

        data = await self._api_get("/Accounts", params={"Type": "BANK"})
        return data.get("Accounts", [])

    async def get_contacts(self, search: str | None = None) -> list[dict[str, Any]]:
        """Fetch Xero contacts. Optional name search for supplier verification."""
        if not self._tenant_id:
            raise XeroAPIError("No Xero tenant ID configured. Complete OAuth flow first.")

        params: dict[str, Any] = {}
        if search:
            params["SearchTerm"] = search
        data = await self._api_get("/Contacts", params=params)
        return data.get("Contacts", [])

    async def reconcile_bank_transaction(
        self, bank_transaction_id: str, invoice_id: str
    ) -> dict[str, Any]:
        """
        Mark a bank transaction as reconciled by linking it to an invoice.
        Uses PUT /BankTransactions with IsReconciled=True.
        """
        if not self._tenant_id:
            raise XeroAPIError("No Xero tenant ID configured. Complete OAuth flow first.")
        if not self._client:
            raise XeroAPIError("XeroService client not initialised. Use async context manager.")

        payload = {
            "BankTransactions": [{
                "BankTransactionID": bank_transaction_id,
                "IsReconciled": True,
            }]
        }
        response = await self._client.put("/BankTransactions", json=payload)
        if response.status_code == 401:
            await self._refresh_access_token()
            self._client.headers.update(self._build_api_headers())
            response = await self._client.put("/BankTransactions", json=payload)
        if not response.is_success:
            raise XeroAPIError(
                f"Failed to reconcile bank transaction {bank_transaction_id}.",
                status_code=response.status_code,
                detail=response.text,
            )
        return response.json()

    async def get_accounts(self, account_type: str | None = None) -> list[dict[str, Any]]:
        """Fetch chart of accounts. Optional type filter e.g. EXPENSE, REVENUE."""
        if not self._tenant_id:
            raise XeroAPIError("No Xero tenant ID configured. Complete OAuth flow first.")

        params: dict[str, Any] = {}
        if account_type:
            params["Type"] = account_type
        data = await self._api_get("/Accounts", params=params)
        return data.get("Accounts", [])

    async def get_profit_and_loss(self, from_date: str, to_date: str) -> dict[str, Any]:
        """Fetch P&L report. Dates in YYYY-MM-DD format."""
        if not self._tenant_id:
            raise XeroAPIError("No Xero tenant ID configured. Complete OAuth flow first.")

        data = await self._api_get(
            "/Reports/ProfitAndLoss",
            params={
                "fromDate": from_date,
                "toDate": to_date,
            },
        )
        return data.get("Reports", [{}])[0]
