from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models import AuditLog, ExceptionItem
from app.models.db import ExceptionItemOut


router = APIRouter(prefix="/exceptions", tags=["Exceptions"])


class ExceptionResolveRequest(BaseModel):
    resolved_by: str
    resolution_note: str


@router.get("", summary="List open exceptions", response_model=list[ExceptionItemOut])
async def list_exceptions(
    db: AsyncSession = Depends(get_db),
) -> list[ExceptionItem]:
    result = await db.execute(
        select(ExceptionItem)
        .where(ExceptionItem.status == "open")
        .order_by(desc(ExceptionItem.created_at))
    )
    return list(result.scalars().all())


@router.get("/{exception_id}", summary="Get exception detail", response_model=ExceptionItemOut)
async def get_exception(
    exception_id: str,
    db: AsyncSession = Depends(get_db),
) -> ExceptionItem:
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


@router.get("/summary", summary="Exception summary counts")
async def exception_summary(
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
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
