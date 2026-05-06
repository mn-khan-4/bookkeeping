from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.base import get_db
from app.db.models import AuditLog, ExceptionItem
from app.models.db import ExceptionItemOut


router = APIRouter(prefix="/exceptions", tags=["Exceptions"])


def _demo_exceptions() -> list[dict[str, str | None]]:
    now = datetime.utcnow()
    return [
        {
            "id": "demo-ex-1001",
            "source_reference": "demo-bt-1001",
            "xero_transaction_id": None,
            "reason": "Supplier not matched to rule.",
            "status": "open",
            "resolved_by": None,
            "resolution_note": None,
            "created_at": (now).isoformat(),
            "resolved_at": None,
        },
        {
            "id": "demo-ex-1002",
            "source_reference": "demo-bt-1003",
            "xero_transaction_id": None,
            "reason": "Amount variance exceeds threshold.",
            "status": "open",
            "resolved_by": None,
            "resolution_note": None,
            "created_at": (now).isoformat(),
            "resolved_at": None,
        },
    ]


class ExceptionResolveRequest(BaseModel):
    resolved_by: str
    resolution_note: str


@router.get("", summary="List open exceptions", response_model=list[ExceptionItemOut])
async def list_exceptions(
    db: AsyncSession = Depends(get_db),
) -> list[ExceptionItem]:
    if settings.DEMO_MODE:
        return _demo_exceptions()

    result = await db.execute(
        select(ExceptionItem)
        .where(ExceptionItem.status == "open")
        .order_by(desc(ExceptionItem.created_at))
    )
    return list(result.scalars().all())


@router.get("/summary", summary="Exception summary counts")
async def exception_summary(
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    if settings.DEMO_MODE:
        return {
            "open": 2,
            "resolved": 5,
            "total": 7,
        }

    open_result = await db.execute(
        select(func.count(ExceptionItem.id)).where(ExceptionItem.status == "open")
    )
    resolved_result = await db.execute(
        select(func.count(ExceptionItem.id)).where(ExceptionItem.status == "resolved")
    )
    total_result = await db.execute(select(func.count(ExceptionItem.id)))

    return {
        "open": int(open_result.scalar() or 0),
        "resolved": int(resolved_result.scalar() or 0),
        "total": int(total_result.scalar() or 0),
    }


@router.get("/{exception_id}", summary="Get exception detail", response_model=ExceptionItemOut)
async def get_exception(
    exception_id: str,
    db: AsyncSession = Depends(get_db),
) -> ExceptionItem:
    if settings.DEMO_MODE:
        for item in _demo_exceptions():
            if item["id"] == exception_id:
                return item
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exception not found.")

    result = await db.execute(
        select(ExceptionItem).where(ExceptionItem.id == exception_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exception not found.")
    return item


@router.post("/{exception_id}/resolve", summary="Resolve an exception")
async def resolve_exception(
    exception_id: str,
    payload: ExceptionResolveRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    if settings.DEMO_MODE:
        return {"status": "ok"}

    result = await db.execute(
        select(ExceptionItem).where(ExceptionItem.id == exception_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exception not found.")

    item.status = "resolved"
    item.resolved_by = payload.resolved_by
    item.resolution_note = payload.resolution_note
    item.resolved_at = datetime.utcnow()

    audit = AuditLog(
        action="exception_resolved",
        entity_type="exception",
        entity_id=item.id,
        user_id=payload.resolved_by,
    )
    db.add(audit)
    await db.commit()
    return {"status": "ok"}
