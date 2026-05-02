from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import AuditLog, ExceptionItem, TransactionMatch
from app.services.matching_service import MatchResult
from app.services.xero_service import XeroService


@dataclass
class ReconciliationResult:
    status: str
    auto: bool


class ReconciliationService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def reconcile(self, match: MatchResult, xero: XeroService) -> ReconciliationResult:
        if match.matched and match.confidence_score >= 0.95 and match.xero_transaction_id:
            await xero.reconcile_bank_transaction(match.xero_transaction_id, match.xero_invoice_id or "")

            result = await self._db.execute(
                select(TransactionMatch).where(
                    TransactionMatch.source_reference == match.source_reference,
                    TransactionMatch.xero_transaction_id == match.xero_transaction_id,
                )
            )
            row = result.scalar_one_or_none()
            if row:
                row.status = "matched"

            audit = AuditLog(
                action="auto_reconciled",
                entity_type="transaction_match",
                entity_id=row.id if row else match.source_reference,
                confidence_score=match.confidence_score,
            )
            self._db.add(audit)
            await self._db.commit()
            return ReconciliationResult(status="reconciled", auto=True)

        reason = "No match available."
        if match.matched and match.confidence_score < 0.95:
            reason = "Confidence below auto-reconciliation threshold."

        exception = ExceptionItem(
            source_reference=match.source_reference,
            xero_transaction_id=match.xero_transaction_id,
            reason=reason,
            status="open",
        )
        audit = AuditLog(
            action="escalated_to_exception",
            entity_type="exception",
            entity_id=exception.id,
            confidence_score=match.confidence_score,
        )
        self._db.add(exception)
        self._db.add(audit)
        await self._db.commit()
        return ReconciliationResult(status="exception", auto=False)

    async def run_batch(self, matches: list[MatchResult], xero: XeroService) -> dict[str, int]:
        auto_reconciled = 0
        exceptions_created = 0

        for match in matches:
            result = await self.reconcile(match, xero)
            if result.auto:
                auto_reconciled += 1
            else:
                exceptions_created += 1

        return {
            "auto_reconciled": auto_reconciled,
            "exceptions_created": exceptions_created,
            "total": len(matches),
        }
