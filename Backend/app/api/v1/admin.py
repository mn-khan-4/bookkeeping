from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.base import get_db
from app.db.models import AuditLog, SupplierRule
from app.models.db import AuditLogOut, SupplierRuleOut
from app.services.supplier_registry import SupplierRegistry


router = APIRouter(prefix="/admin", tags=["Admin"])


class SupplierRuleRequest(BaseModel):
    supplier_name: str
    account_code: str
    gst_code: str
    client_id: str


@router.get(
    "/supplier-rules",
    summary="List supplier rules",
    response_model=list[SupplierRuleOut],
)
async def list_supplier_rules(
    client_id: str = Query(..., description="Client ID"),
    db: AsyncSession = Depends(get_db),
) -> list[SupplierRule]:
    if settings.DEMO_MODE:
        now = datetime.utcnow().isoformat()
        return [
            {
                "id": "demo-rule-1001",
                "supplier_name": "Officeworks",
                "account_code": "400",
                "gst_code": "GST",
                "client_id": client_id,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "demo-rule-1002",
                "supplier_name": "Meta Ads",
                "account_code": "410",
                "gst_code": "GST",
                "client_id": client_id,
                "created_at": now,
                "updated_at": now,
            },
        ]

    registry = SupplierRegistry(db)
    return await registry.get_all_rules(client_id)


@router.post(
    "/supplier-rules",
    summary="Create or update supplier rule",
    response_model=SupplierRuleOut,
)
async def upsert_supplier_rule(
    payload: SupplierRuleRequest,
    db: AsyncSession = Depends(get_db),
) -> SupplierRule:
    if settings.DEMO_MODE:
        now = datetime.utcnow().isoformat()
        return {
            "id": "demo-rule-new",
            "supplier_name": payload.supplier_name,
            "account_code": payload.account_code,
            "gst_code": payload.gst_code,
            "client_id": payload.client_id,
            "created_at": now,
            "updated_at": now,
        }

    registry = SupplierRegistry(db)
    return await registry.save_rule(
        supplier_name=payload.supplier_name,
        account_code=payload.account_code,
        gst_code=payload.gst_code,
        client_id=payload.client_id,
    )


@router.delete("/supplier-rules/{rule_id}", summary="Delete supplier rule")
async def delete_supplier_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    if settings.DEMO_MODE:
        return {"status": "deleted"}

    result = await db.execute(select(SupplierRule).where(SupplierRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found.")
    await db.delete(rule)
    await db.commit()
    return {"status": "deleted"}


@router.get(
    "/audit-log",
    summary="List audit log entries",
    response_model=list[AuditLogOut],
)
async def list_audit_log(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[AuditLog]:
    if settings.DEMO_MODE:
        now = datetime.utcnow().isoformat()
        return [
            {
                "id": "demo-audit-1001",
                "action": "auto_reconciled",
                "entity_type": "bank_transaction",
                "entity_id": "demo-bt-1002",
                "rule_applied": "Officeworks",
                "confidence_score": 0.92,
                "user_id": None,
                "timestamp": now,
            },
            {
                "id": "demo-audit-1002",
                "action": "exception_created",
                "entity_type": "exception",
                "entity_id": "demo-ex-1001",
                "rule_applied": None,
                "confidence_score": 0.6,
                "user_id": None,
                "timestamp": now,
            },
        ]

    result = await db.execute(
        select(AuditLog).order_by(desc(AuditLog.timestamp)).limit(limit).offset(offset)
    )
    return list(result.scalars().all())
