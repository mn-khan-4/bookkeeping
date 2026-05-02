from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.services.aba_service import ABAFileGenerator, PaymentRecord
from app.services.payables_service import PayablesService
from app.services.xero_service import XeroService
from app.api.v1.integrations import get_xero_service


router = APIRouter(prefix="/payables", tags=["Payables"])


class ProcessInvoiceRequest(BaseModel):
    client_id: str
    source_reference: str
    supplier_name: str
    invoice_date: str
    due_date: str | None = None
    amount: float
    tax_amount: float | None = None
    description: str
    currency: str = Field("AUD")


class ABARequestPayment(BaseModel):
    bsb: str
    account_number: str
    transaction_code: str
    amount: float
    account_name: str
    lodgement_ref: str
    trace_bsb: str
    trace_account: str
    remitter: str


class GenerateABARequest(BaseModel):
    human_approved: bool
    batch_description: str
    bsb: str
    account_number: str
    account_name: str
    payments: list[ABARequestPayment]


@router.post("/process-invoice", summary="Validate and create a draft bill")
async def process_invoice(
    payload: ProcessInvoiceRequest,
    db: AsyncSession = Depends(get_db),
    xero: XeroService = Depends(get_xero_service),
) -> dict[str, Any]:
    service = PayablesService(db)

    invoice_date = datetime.fromisoformat(payload.invoice_date)
    due_date = datetime.fromisoformat(payload.due_date) if payload.due_date else None

    validation = await service.validate_invoice(
        source_reference=payload.source_reference,
        supplier_name=payload.supplier_name,
        amount=payload.amount,
        invoice_date=invoice_date,
        currency=payload.currency,
        tax=payload.tax_amount,
        net=payload.amount - (payload.tax_amount or 0),
        client_id=payload.client_id,
    )

    if not validation.is_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=validation.errors)

    async with xero:
        contact = await service.verify_supplier_in_xero(payload.supplier_name, xero)
        if not contact:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supplier not found in Xero.")

        publish = await service.create_draft_bill(
            source_reference=payload.source_reference,
            contact_id=contact["ContactID"],
            date_value=invoice_date,
            due_date=due_date,
            amount=payload.amount,
            tax_amount=payload.tax_amount,
            description=payload.description,
            xero=xero,
        )

    return {
        "status": "ok",
        "publish": publish.model_dump(),
        "warnings": validation.warnings,
    }


@router.get("/outstanding", summary="List outstanding payables")
async def outstanding_payables(
    db: AsyncSession = Depends(get_db),
    xero: XeroService = Depends(get_xero_service),
) -> list[dict[str, Any]]:
    service = PayablesService(db)
    async with xero:
        return await service.get_outstanding_payables(xero)


@router.get("/summary", summary="Payables summary")
async def payables_summary(
    db: AsyncSession = Depends(get_db),
    xero: XeroService = Depends(get_xero_service),
) -> dict[str, Any]:
    service = PayablesService(db)
    async with xero:
        outstanding = await service.get_outstanding_payables(xero)

    total = sum(Decimal(str(item.get("amount") or 0)) for item in outstanding)
    due_dates = [item.get("due_date") for item in outstanding if item.get("due_date")]
    next_due = min(due_dates) if due_dates else None

    return {
        "total_owed": float(total),
        "invoice_count": len(outstanding),
        "next_due_date": next_due,
    }


@router.post("/generate-aba", summary="Generate ABA payment file")
async def generate_aba(
    payload: GenerateABARequest,
) -> Response:
    if payload.human_approved is not True:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Human approval required.")

    generator = ABAFileGenerator()
    records = [
        PaymentRecord(
            bsb=payment.bsb,
            account_number=payment.account_number,
            transaction_code=payment.transaction_code,
            amount=Decimal(str(payment.amount)),
            account_name=payment.account_name,
            lodgement_ref=payment.lodgement_ref,
            trace_bsb=payment.trace_bsb,
            trace_account=payment.trace_account,
            remitter=payment.remitter,
        )
        for payment in payload.payments
    ]

    content = generator.export(
        payments=records,
        batch_description=payload.batch_description,
        bsb=payload.bsb,
        account_number=payload.account_number,
        account_name=payload.account_name,
    )

    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=payments.aba"},
    )
