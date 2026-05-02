from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SupplierRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    supplier_name: str
    account_code: str
    gst_code: str
    client_id: str
    created_at: datetime
    updated_at: datetime


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    action: str
    entity_type: str
    entity_id: str
    rule_applied: str | None
    confidence_score: float | None
    user_id: str | None
    timestamp: datetime


class ExceptionItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_reference: str
    xero_transaction_id: str | None
    reason: str
    status: str
    resolved_by: str | None
    resolution_note: str | None
    created_at: datetime
    resolved_at: datetime | None
