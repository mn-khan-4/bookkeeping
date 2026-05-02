from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, TransactionMatch
from app.models.xero import XeroInvoice, XeroPublishResult
from app.services.validation_service import TransactionValidationService, ValidationResult
from app.services.xero_service import XeroService


class PayablesService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._validator = TransactionValidationService(db)

    async def validate_invoice(
        self,
        source_reference: str,
        supplier_name: str,
        amount: float | Decimal,
        invoice_date: date | datetime,
        currency: str,
        tax: float | Decimal | None,
        net: float | Decimal | None,
        client_id: str,
    ) -> ValidationResult:
        result = await self._validator.validate(
            source_reference=source_reference,
            supplier_name=supplier_name,
            total_amount=amount,
            document_date=invoice_date,
            currency=currency,
            tax_amount=tax,
            net_amount=net,
            client_id=client_id,
        )

        invoice_dt = invoice_date if isinstance(invoice_date, datetime) else datetime.combine(invoice_date, datetime.min.time())
        recent_cutoff = invoice_dt - timedelta(days=7)
        future_cutoff = invoice_dt + timedelta(days=7)

        duplicate = await self._db.execute(
            select(TransactionMatch).where(
                TransactionMatch.supplier_name == supplier_name,
                TransactionMatch.amount == float(amount),
                TransactionMatch.transaction_date >= recent_cutoff,
                TransactionMatch.transaction_date <= future_cutoff,
            )
        )
        if duplicate.scalar_one_or_none():
            result.warnings.append("Possible duplicate detected in recent transactions.")

        return result

    async def verify_supplier_in_xero(
        self, supplier_name: str, xero: XeroService
    ) -> dict[str, Any] | None:
        contacts = await xero.get_contacts(search=supplier_name)
        return contacts[0] if contacts else None

    async def create_draft_bill(
        self,
        source_reference: str,
        contact_id: str,
        date_value: date | datetime,
        due_date: date | datetime | None,
        amount: float | Decimal,
        tax_amount: float | Decimal | None,
        description: str,
        xero: XeroService,
    ) -> XeroPublishResult:
        invoice = XeroInvoice(
            type="ACCPAY",
            status="DRAFT",
            contact_id=contact_id,
            date=date_value,
            due_date=due_date,
            currency_code="AUD",
            total=Decimal(str(amount)),
            total_tax=Decimal(str(tax_amount)) if tax_amount is not None else None,
            reference=description,
            source_reference=source_reference,
        )

        result = await xero.publish_transaction_to_xero(invoice)
        audit = AuditLog(
            action="draft_bill_created",
            entity_type="invoice",
            entity_id=result.xero_invoice_id or source_reference,
        )
        self._db.add(audit)
        await self._db.commit()
        return result

    async def get_outstanding_payables(self, xero: XeroService) -> list[dict[str, Any]]:
        invoices = await xero.get_invoices(invoice_type="ACCPAY", status="AUTHORISED,SUBMITTED")
        results: list[dict[str, Any]] = []
        for inv in invoices:
            contact = inv.get("Contact", {})
            results.append(
                {
                    "supplier": contact.get("Name"),
                    "amount": inv.get("Total"),
                    "due_date": inv.get("DueDate"),
                    "invoice_number": inv.get("InvoiceNumber"),
                }
            )
        return results
