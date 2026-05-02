"""
Pydantic models for Xero domain objects.

These cover the core entities needed for Phase 1: bank transactions,
invoices, and contacts. Designed to match the Xero Accounting API
response schema closely, while keeping only the fields relevant to
the reconciliation workflow.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field


class BankTransactionType(StrEnum):
    RECEIVE = "RECEIVE"
    SPEND = "SPEND"
    RECEIVE_OVERPAYMENT = "RECEIVE-OVERPAYMENT"
    SPEND_OVERPAYMENT = "SPEND-OVERPAYMENT"
    RECEIVE_PREPAYMENT = "RECEIVE-PREPAYMENT"
    SPEND_PREPAYMENT = "SPEND-PREPAYMENT"


class ReconciliationStatus(StrEnum):
    UNRECONCILED = "UNRECONCILED"
    RECONCILED = "RECONCILED"
    VOIDED = "VOIDED"


class XeroContact(BaseModel):
    """Simplified Xero Contact (supplier / customer)."""

    contact_id: str
    name: str
    email_address: Optional[str] = None
    is_supplier: bool = False
    is_customer: bool = False


class XeroBankTransaction(BaseModel):
    """
    Represents a statement line / bank transaction in Xero.
    Returned by GET /BankTransactions with Status=UNRECONCILED.
    """

    bank_transaction_id: str = Field(..., description="Xero's UUID for this transaction")
    bank_account_id: str = Field(..., description="UUID of the bank account")
    bank_account_name: Optional[str] = Field(None, description="Human-readable account name")

    type: BankTransactionType
    status: ReconciliationStatus = ReconciliationStatus.UNRECONCILED

    contact: Optional[XeroContact] = None

    date: datetime = Field(..., description="Transaction date")
    amount: Decimal = Field(..., description="Positive = money in, negative = money out")
    currency_code: str = Field("AUD", description="ISO 4217 currency code")

    reference: Optional[str] = Field(None, description="Bank reference / narrative")
    description: Optional[str] = Field(None, description="Additional description")

    # Populated after matching
    source_reference: Optional[str] = Field(None, description="Source reference for traceability")
    is_reconciled: bool = False

    class Config:
        use_enum_values = True


class XeroInvoice(BaseModel):
    """Simplified Xero Invoice / Bill model used when publishing to Xero."""

    invoice_id: Optional[str] = Field(None, description="Xero UUID – None before creation")
    type: str = Field("ACCPAY", description="ACCPAY = Bill, ACCREC = Invoice")
    contact_id: str = Field(..., description="Xero Contact UUID")
    contact_name: Optional[str] = None

    date: datetime
    due_date: Optional[datetime] = None
    currency_code: str = "AUD"

    line_amount_types: str = "Exclusive"  # Tax exclusive line amounts
    sub_total: Optional[Decimal] = None
    total_tax: Optional[Decimal] = None
    total: Optional[Decimal] = None

    reference: Optional[str] = None
    source_reference: Optional[str] = Field(None, description="Source reference for traceability")
    status: str = "DRAFT"  # DRAFT | SUBMITTED | AUTHORISED

    class Config:
        use_enum_values = True


class XeroPublishResult(BaseModel):
    """Result returned after attempting to publish a document to Xero."""

    success: bool
    xero_invoice_id: Optional[str] = None
    source_reference: str
    message: str
    status_code: Optional[int] = None


class XeroTokenResponse(BaseModel):
    """OAuth2 token response from Xero's identity server."""

    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"
    scope: Optional[str] = None
