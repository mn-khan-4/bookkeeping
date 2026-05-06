from __future__ import annotations

from datetime import datetime, time

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.base import get_db
from app.db.models import AuditLog, ExceptionItem, TransactionMatch
from app.services.matching_service import TransactionMatchingService
from app.services.reconciliation_service import ReconciliationService
from app.services.xero_service import XeroService
from app.api.v1.integrations import get_xero_service


router = APIRouter(prefix="/reconciliation", tags=["Reconciliation"])


class ReconciliationItem(BaseModel):
    source_reference: str
    supplier_name: str | None = None
    amount: float
    date: str


class ReconciliationRunRequest(BaseModel):
    client_id: str
    items: list[ReconciliationItem]


@router.post(
    "/run",
    summary="Run matching and reconciliation",
    status_code=status.HTTP_200_OK,
)
async def run_reconciliation(
    payload: ReconciliationRunRequest,
    db: AsyncSession = Depends(get_db),
    xero: XeroService = Depends(get_xero_service),
) -> dict[str, int]:
    if settings.DEMO_MODE:
        total = len(payload.items)
        auto_reconciled = max(0, total - 1)
        exceptions_created = 1 if total else 0
        return {
            "auto_reconciled": auto_reconciled,
            "exceptions_created": exceptions_created,
            "total": total,
        }

    matcher = TransactionMatchingService(db)
    reconciler = ReconciliationService(db)

    items = [item.model_dump() for item in payload.items]
    async with xero:
        matches = await matcher.run_batch(items=items, client_id=payload.client_id, xero=xero)
        summary = await reconciler.run_batch(matches=matches, xero=xero)
    return summary


@router.get(
    "/status",
    summary="Get reconciliation status counts",
    status_code=status.HTTP_200_OK,
)
async def reconciliation_status(
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    if settings.DEMO_MODE:
        return {
            "total_processed_today": 18,
            "auto_reconciled_today": 16,
            "exceptions_pending": 2,
        }

    today_start = datetime.combine(datetime.utcnow().date(), time.min)

    total_result = await db.execute(
        select(func.count(TransactionMatch.id)).where(TransactionMatch.created_at >= today_start)
    )
    auto_result = await db.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "auto_reconciled",
            AuditLog.timestamp >= today_start,
        )
    )
    exception_result = await db.execute(
        select(func.count(ExceptionItem.id)).where(ExceptionItem.status == "open")
    )

    return {
        "total_processed_today": int(total_result.scalar() or 0),
        "auto_reconciled_today": int(auto_result.scalar() or 0),
        "exceptions_pending": int(exception_result.scalar() or 0),
    }
