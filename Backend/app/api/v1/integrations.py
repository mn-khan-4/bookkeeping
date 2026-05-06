"""
Integration endpoints — Xero OAuth2 flow + Accounting API health check.

Router: /api/v1/integrations

Endpoints:
    GET  /health/xero                   → Test Xero API connectivity

    GET  /xero/authorize                → Start Xero OAuth2 flow
    GET  /xero/callback                 → Handle Xero OAuth2 callback (Step 2)
    GET  /xero/tenants                  → List all Xero organisations
    POST /xero/refresh                  → Manually refresh the access token
    GET  /xero/invoices                 → Fetch invoices (mirrors XeroRequests)
    GET  /xero/invoices/export          → Download invoice data as CSV
    GET  /xero/bank-transactions        → Fetch unreconciled bank transactions
    GET  /xero/bank-accounts            → Fetch bank accounts
    GET  /xero/contacts                 → Fetch contacts
    GET  /xero/accounts                 → Fetch chart of accounts
    GET  /xero/reports/profit-and-loss  → Fetch profit & loss report
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse, Response

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    TokenRefreshError,
    XeroAPIError,
)
from app.services.xero_service import XeroService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/integrations",
    tags=["Integrations"],
)


def _demo_bank_transactions() -> list[dict[str, Any]]:
    now = datetime.utcnow()
    return [
        {
            "bank_transaction_id": "demo-bt-1001",
            "bank_account_id": "demo-account-01",
            "bank_account_name": "Business Checking",
            "type": "SPEND",
            "status": "UNRECONCILED",
            "date": (now - timedelta(days=1)).isoformat(),
            "amount": -245.75,
            "currency_code": "AUD",
            "reference": "Officeworks",
            "description": "Stationery & supplies",
            "contact": {"name": "Officeworks"},
            "source_reference": None,
            "is_reconciled": False,
        },
        {
            "bank_transaction_id": "demo-bt-1002",
            "bank_account_id": "demo-account-01",
            "bank_account_name": "Business Checking",
            "type": "RECEIVE",
            "status": "UNRECONCILED",
            "date": (now - timedelta(days=2)).isoformat(),
            "amount": 1890.0,
            "currency_code": "AUD",
            "reference": "Client payment",
            "description": "ACME Co",
            "contact": {"name": "ACME Co"},
            "source_reference": None,
            "is_reconciled": False,
        },
        {
            "bank_transaction_id": "demo-bt-1003",
            "bank_account_id": "demo-account-02",
            "bank_account_name": "Savings",
            "type": "SPEND",
            "status": "UNRECONCILED",
            "date": (now - timedelta(days=3)).isoformat(),
            "amount": -520.4,
            "currency_code": "AUD",
            "reference": "Marketing spend",
            "description": "Meta Ads",
            "contact": {"name": "Meta Ads"},
            "source_reference": None,
            "is_reconciled": False,
        },
    ]


# ======================================================================
#  Dependency factories — one service instance per request
# ======================================================================
async def get_xero_service() -> XeroService:
    """FastAPI dependency that yields an initialised XeroService."""
    service = XeroService()
    try:
        yield service
    finally:
        await service.close()


@router.get(
    "/health/xero",
    summary="Xero connection health check",
    response_description="Connection status of the Xero integration",
    status_code=status.HTTP_200_OK,
)
async def health_check_xero(
    xero: XeroService = Depends(get_xero_service),
) -> dict[str, Any]:
    """
    ## Xero API Health Check

    Calls the Xero `/connections` endpoint to verify:
    - OAuth 2.0 tokens are valid (auto-refreshes if expired)
    - At least one Xero organisation (tenant) is connected

    Returns a `status` of `"ok"`, `"warning"`, or `"error"`.
    """
    if settings.DEMO_MODE:
        return {
            "status": "ok",
            "service": "Xero",
            "organisation_name": "Demo Organisation",
            "message": "Demo mode enabled. Xero calls are mocked.",
        }

    try:
        result = await xero.check_connection()
        if result.get("status") == "error":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=result,
            )
        return result
    except (AuthenticationError, XeroAPIError, TokenRefreshError) as exc:
        logger.error("Xero health check failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "error",
                "service": "Xero",
                "message": exc.message,
                "detail": exc.detail,
            },
        ) from exc


# ======================================================================
#  Xero OAuth 2.0 Flow
# ======================================================================

@router.get(
    "/xero/authorize",
    summary="Initiate Xero OAuth 2.0 Authorization",
    response_description="Redirects the user to Xero's login page",
    status_code=status.HTTP_302_FOUND,
)
async def xero_authorize(
    xero: XeroService = Depends(get_xero_service),
) -> RedirectResponse:
    """
    ## Start Xero OAuth2 Flow  *(Step 1 — XeroFirstAuth)*

    Generates the Xero authorization URL with PKCE challenge and redirects
    the user's browser to Xero's login page.

    After the user grants access, Xero will redirect to:
    `GET /api/v1/integrations/xero/callback?code=…&state=…`
    """
    if settings.DEMO_MODE:
        return RedirectResponse(url="/dashboard")

    auth_info = xero.get_authorization_url()
    logger.info("Redirecting to Xero authorization URL (state=%s)", auth_info["state"])
    return RedirectResponse(url=auth_info["authorization_url"])


@router.get(
    "/xero/callback",
    summary="Xero OAuth 2.0 Callback",
    response_description="Exchanges the authorization code for tokens and fetches the active tenant",
    status_code=status.HTTP_200_OK,
)
async def xero_callback(
    code: str = Query(..., description="Authorization code from Xero"),
    state: str = Query(..., description="State parameter for CSRF validation"),
    xero: XeroService = Depends(get_xero_service),
) -> dict[str, Any]:
    """
    ## Xero OAuth2 Callback  *(Steps 2–5 — XeroFirstAuth + XeroTenants)*

    Xero redirects here after the user grants (or denies) access.
    This endpoint:
    1. Validates the `state` parameter to prevent CSRF
    2. Exchanges the `code` for an access token + refresh token
    3. **Automatically discovers and stores the active Xero tenant ID**
    4. Persists all tokens + tenant ID to `.env` (survives server restarts)

    Returns a confirmation with token expiry and tenant information.
    """
    if settings.DEMO_MODE:
        return {
            "status": "ok",
            "message": "Demo mode enabled. Tokens not required.",
            "expires_in_seconds": 3600,
            "scope": "demo",
            "tenants": [{"tenant_id": "demo-tenant", "name": "Demo Organisation"}],
        }

    try:
        token = await xero.exchange_code_for_token(code=code, state=state)
        tenants = await xero.get_tenants()
        return {
            "status": "ok",
            "message": "Xero authorised successfully. Tokens and tenant stored.",
            "expires_in_seconds": token.expires_in,
            "scope": token.scope,
            "tenants": [
                {"tenant_id": t.get("tenantId"), "name": t.get("tenantName")}
                for t in tenants
            ],
        }
    except AuthenticationError as exc:
        logger.error("Xero callback error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "message": exc.message,
                "detail": exc.detail,
            },
        ) from exc


# ======================================================================
#  Xero Tenant Management  (mirrors XeroTenants() in reference script)
# ======================================================================

@router.get(
    "/xero/tenants",
    summary="List connected Xero organisations",
    response_description="All Xero tenants accessible with current tokens",
    status_code=status.HTTP_200_OK,
)
async def xero_tenants(
    xero: XeroService = Depends(get_xero_service),
) -> dict[str, Any]:
    """
    ## List Xero Tenants  *(XeroTenants)*

    Returns all Xero organisations (tenants) accessible with the current
    OAuth2 tokens.  Mirrors `XeroTenants()` from the reference script.

    The first tenant in the list is automatically set as the active tenant
    for all subsequent API calls.
    """
    if settings.DEMO_MODE:
        return {
            "status": "ok",
            "total": 1,
            "tenants": [
                {
                    "tenant_id": "demo-tenant",
                    "name": "Demo Organisation",
                    "tenant_type": "ORGANISATION",
                }
            ],
        }

    try:
        tenants = await xero.get_tenants()
        return {
            "status": "ok",
            "total": len(tenants),
            "tenants": [
                {
                    "tenant_id": t.get("tenantId"),
                    "name": t.get("tenantName"),
                    "tenant_type": t.get("tenantType"),
                }
                for t in tenants
            ],
        }
    except (AuthenticationError, TokenRefreshError, XeroAPIError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "message": exc.message},
        ) from exc


# ======================================================================
#  Xero Data Endpoints
# ======================================================================

@router.get(
    "/xero/bank-accounts",
    summary="Fetch Xero bank accounts",
    response_description="List of bank accounts",
    status_code=status.HTTP_200_OK,
)
async def xero_bank_accounts(
    xero: XeroService = Depends(get_xero_service),
) -> dict[str, Any]:
    if settings.DEMO_MODE:
        accounts = [
            {
                "AccountID": "demo-account-01",
                "Name": "Business Checking",
                "Code": "090",
                "Type": "BANK",
                "CurrencyCode": "AUD",
            },
            {
                "AccountID": "demo-account-02",
                "Name": "Savings",
                "Code": "091",
                "Type": "BANK",
                "CurrencyCode": "AUD",
            },
        ]
        return {"status": "ok", "total": len(accounts), "accounts": accounts}

    async with xero:
        try:
            accounts = await xero.get_bank_accounts()
            return {"status": "ok", "total": len(accounts), "accounts": accounts}
        except (AuthenticationError, TokenRefreshError, XeroAPIError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": "error", "message": exc.message},
            ) from exc


@router.get(
    "/xero/contacts",
    summary="Fetch Xero contacts",
    response_description="List of contacts",
    status_code=status.HTTP_200_OK,
)
async def xero_contacts(
    search: str | None = Query(None, description="Optional contact search term"),
    xero: XeroService = Depends(get_xero_service),
) -> dict[str, Any]:
    if settings.DEMO_MODE:
        contacts = [
            {"ContactID": "demo-contact-01", "Name": "Officeworks"},
            {"ContactID": "demo-contact-02", "Name": "ACME Co"},
            {"ContactID": "demo-contact-03", "Name": "Meta Ads"},
        ]
        if search:
            contacts = [c for c in contacts if search.lower() in c["Name"].lower()]
        return {"status": "ok", "total": len(contacts), "contacts": contacts}

    async with xero:
        try:
            contacts = await xero.get_contacts(search=search)
            return {"status": "ok", "total": len(contacts), "contacts": contacts}
        except (AuthenticationError, TokenRefreshError, XeroAPIError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": "error", "message": exc.message},
            ) from exc


@router.get(
    "/xero/accounts",
    summary="Fetch chart of accounts",
    response_description="List of accounts",
    status_code=status.HTTP_200_OK,
)
async def xero_accounts(
    account_type: str | None = Query(None, description="Optional account type filter"),
    xero: XeroService = Depends(get_xero_service),
) -> dict[str, Any]:
    if settings.DEMO_MODE:
        accounts = [
            {"AccountID": "demo-gl-01", "Name": "Office Supplies", "Code": "400", "Type": "EXPENSE"},
            {"AccountID": "demo-gl-02", "Name": "Advertising", "Code": "410", "Type": "EXPENSE"},
            {"AccountID": "demo-gl-03", "Name": "Sales", "Code": "200", "Type": "REVENUE"},
        ]
        if account_type:
            accounts = [a for a in accounts if a["Type"].lower() == account_type.lower()]
        return {"status": "ok", "total": len(accounts), "accounts": accounts}

    async with xero:
        try:
            accounts = await xero.get_accounts(account_type=account_type)
            return {"status": "ok", "total": len(accounts), "accounts": accounts}
        except (AuthenticationError, TokenRefreshError, XeroAPIError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": "error", "message": exc.message},
            ) from exc


@router.get(
    "/xero/reports/profit-and-loss",
    summary="Fetch profit and loss report",
    response_description="Profit and loss report",
    status_code=status.HTTP_200_OK,
)
async def xero_profit_and_loss(
    from_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    to_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    xero: XeroService = Depends(get_xero_service),
) -> dict[str, Any]:
    if settings.DEMO_MODE:
        return {
            "status": "ok",
            "report": {
                "from": from_date,
                "to": to_date,
                "total_income": 58200,
                "total_expenses": 31250,
                "net_profit": 26950,
            },
        }

    async with xero:
        try:
            report = await xero.get_profit_and_loss(from_date=from_date, to_date=to_date)
            return {"status": "ok", "report": report}
        except (AuthenticationError, TokenRefreshError, XeroAPIError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": "error", "message": exc.message},
            ) from exc


# ======================================================================
#  Xero Token Refresh  (mirrors XeroRefreshToken() in reference script)
# ======================================================================

@router.post(
    "/xero/refresh",
    summary="Manually refresh the Xero access token",
    response_description="New access and refresh token details",
    status_code=status.HTTP_200_OK,
)
async def xero_refresh_token(
    xero: XeroService = Depends(get_xero_service),
) -> dict[str, Any]:
    """
    ## Refresh Xero Token  *(XeroRefreshToken)*

    Forces a token refresh using the stored refresh token.  The new tokens
    are **persisted to `.env`** so the server doesn't need to re-authorise
    after a restart.

    Normally this is done automatically on 401 responses, but this endpoint
    allows you to trigger it manually (e.g. from a cron job or monitoring).
    """
    if settings.DEMO_MODE:
        return {
            "status": "ok",
            "message": "Demo mode enabled. Tokens not required.",
            "expires_in_seconds": 3600,
            "scope": "demo",
        }

    try:
        token = await xero.refresh_tokens()
        return {
            "status": "ok",
            "message": "Xero access token refreshed and persisted to .env.",
            "expires_in_seconds": token.expires_in,
            "scope": token.scope,
        }
    except TokenRefreshError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "message": exc.message, "detail": exc.detail},
        ) from exc


# ======================================================================
#  Xero Invoices  (mirrors XeroRequests() in reference script)
# ======================================================================

@router.get(
    "/xero/invoices",
    summary="Fetch invoices from Xero",
    response_description="List of Xero invoices",
    status_code=status.HTTP_200_OK,
)
async def xero_get_invoices(
    invoice_type: str | None = Query(
        None,
        description="Filter by invoice type: ACCPAY (bills) or ACCREC (sales invoices)",
        example="ACCPAY",
    ),
    invoice_status: str | None = Query(
        None,
        alias="status",
        description="Filter by Xero status: DRAFT, SUBMITTED, AUTHORISED, PAID, VOIDED",
    ),
    page: int = Query(1, ge=1, description="Page number (100 records per page)"),
    xero: XeroService = Depends(get_xero_service),
) -> dict[str, Any]:
    """
    ## Fetch Xero Invoices  *(XeroRequests)*

    Retrieves invoices from the active Xero organisation.  Mirrors the
    `XeroRequests()` function in the reference script, which calls
    `GET /api.xro/2.0/Invoices`.

    The access token is **automatically refreshed** if expired before the call.
    """
    if settings.DEMO_MODE:
        now = datetime.utcnow()
        invoices = [
            {
                "InvoiceNumber": "BILL-1001",
                "Contact": {"Name": "Officeworks"},
                "Date": (now - timedelta(days=10)).date().isoformat(),
                "DueDate": (now + timedelta(days=5)).date().isoformat(),
                "Total": 245.75,
                "Status": "AUTHORISED",
            },
            {
                "InvoiceNumber": "BILL-1002",
                "Contact": {"Name": "Meta Ads"},
                "Date": (now - timedelta(days=7)).date().isoformat(),
                "DueDate": (now + timedelta(days=10)).date().isoformat(),
                "Total": 520.4,
                "Status": "SUBMITTED",
            },
            {
                "InvoiceNumber": "BILL-1003",
                "Contact": {"Name": "ACME Co"},
                "Date": (now - timedelta(days=3)).date().isoformat(),
                "DueDate": (now + timedelta(days=14)).date().isoformat(),
                "Total": 1890.0,
                "Status": "PAID",
            },
        ]
        return {
            "status": "ok",
            "page": page,
            "count": len(invoices),
            "invoices": invoices,
        }

    async with xero:
        try:
            invoices = await xero.get_invoices(
                invoice_type=invoice_type,
                status=invoice_status,
                page=page,
            )
            return {
                "status": "ok",
                "page": page,
                "count": len(invoices),
                "invoices": invoices,
            }
        except (XeroAPIError, TokenRefreshError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"status": "error", "message": exc.message},
            ) from exc
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": "error", "message": exc.message},
            ) from exc


@router.get(
    "/xero/invoices/export",
    summary="Export Xero invoices as CSV",
    response_description="CSV file download with Type and Total columns",
    status_code=status.HTTP_200_OK,
)
async def xero_export_invoices_csv(
    invoice_type: str | None = Query(
        None,
        description="Optional type filter: ACCPAY or ACCREC",
    ),
    invoice_status: str | None = Query(
        None,
        alias="status",
        description="Optional status filter",
    ),
    xero: XeroService = Depends(get_xero_service),
) -> Response:
    """
    ## Export Xero Invoices as CSV  *(export_csv)*

    Generates a CSV file from Xero invoice data.  Mirrors the `export_csv()`
    function in the reference script.

    Columns: **Type, InvoiceNumber, Reference, Contact, Date, DueDate, Total, Status**

    Returns a downloadable `xero_invoices.csv` attachment.
    """
    if settings.DEMO_MODE:
        csv_data = "Type,InvoiceNumber,Reference,Contact,Date,DueDate,Total,Status\n"
        csv_data += "ACCPAY,BILL-1001,,Officeworks,2024-06-12,2024-06-30,245.75,AUTHORISED\n"
        csv_data += "ACCPAY,BILL-1002,,Meta Ads,2024-06-15,2024-07-05,520.40,SUBMITTED\n"
        return Response(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=xero_invoices.csv"},
        )

    async with xero:
        try:
            csv_data = await xero.export_invoices_csv(
                invoice_type=invoice_type,
                status=invoice_status,
            )
        except (XeroAPIError, TokenRefreshError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"status": "error", "message": exc.message},
            ) from exc
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": "error", "message": exc.message},
            ) from exc

    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=xero_invoices.csv"},
    )


# ======================================================================
#  Xero Bank Transactions  (Workflow 1 – Step 3)
# ======================================================================

@router.get(
    "/xero/bank-transactions",
    summary="Fetch unreconciled Xero bank transactions",
    response_description="List of unreconciled bank statement lines",
    status_code=status.HTTP_200_OK,
)
async def xero_get_bank_transactions(
    bank_account_id: str | None = Query(
        None,
        description="Optional Xero bank account UUID to filter by",
    ),
    page: int = Query(1, ge=1, description="Page number (100 records per page)"),
    xero: XeroService = Depends(get_xero_service),
) -> dict[str, Any]:
    """
    ## Fetch Unreconciled Bank Transactions  *(Workflow 1 — Step 3)*

    Retrieves bank statement lines with `Status=UNRECONCILED` from Xero.
    These are the raw lines that need to be matched against source references.
    """
    if settings.DEMO_MODE:
        transactions = _demo_bank_transactions()
        return {
            "status": "ok",
            "page": page,
            "count": len(transactions),
            "transactions": transactions,
        }

    async with xero:
        try:
            transactions = await xero.get_unreconciled_bank_transactions(
                bank_account_id=bank_account_id,
                page=page,
            )
            return {
                "status": "ok",
                "page": page,
                "count": len(transactions),
                "transactions": [t.model_dump() for t in transactions],
            }
        except (XeroAPIError, TokenRefreshError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"status": "error", "message": exc.message},
            ) from exc
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": "error", "message": exc.message},
            ) from exc
