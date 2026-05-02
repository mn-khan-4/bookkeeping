from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, TransactionMatch
from app.models.xero import XeroBankTransaction
from app.services.xero_service import XeroService


@dataclass
class MatchResult:
    matched: bool
    source_reference: str
    xero_transaction_id: str | None
    confidence_score: float
    match_method: str
    requires_human_review: bool
    xero_invoice_id: str | None = None


class TransactionMatchingService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    def _coerce_date(self, value: date | datetime | str) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return datetime.fromisoformat(value).date()

    def _coerce_datetime(self, value: date | datetime | str) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        return datetime.fromisoformat(value)

    def _supplier_matches(self, supplier_name: str | None, txn: XeroBankTransaction) -> bool:
        if not supplier_name:
            return False
        if not txn.contact or not txn.contact.name:
            return False
        return supplier_name.strip().lower() == txn.contact.name.strip().lower()

    def _amount_matches(self, amount: Decimal, txn: XeroBankTransaction) -> bool:
        return amount == Decimal(str(txn.amount))

    def match(
        self,
        source_reference: str,
        supplier_name: str | None,
        amount: float | Decimal,
        date_value: date | datetime | str,
        transactions: list[XeroBankTransaction],
    ) -> MatchResult:
        target_date = self._coerce_date(date_value)
        target_amount = Decimal(str(amount))

        def date_within(txn: XeroBankTransaction, days: int) -> bool:
            return abs((txn.date.date() - target_date).days) <= days

        # Strategy 1: Exact
        for txn in transactions:
            if (
                self._amount_matches(target_amount, txn)
                and date_within(txn, 3)
                and self._supplier_matches(supplier_name, txn)
            ):
                return MatchResult(
                    matched=True,
                    source_reference=source_reference,
                    xero_transaction_id=txn.bank_transaction_id,
                    confidence_score=0.98,
                    match_method="exact",
                    requires_human_review=False,
                )

        # Strategy 2: Amount + date
        for txn in transactions:
            if self._amount_matches(target_amount, txn) and date_within(txn, 5):
                return MatchResult(
                    matched=True,
                    source_reference=source_reference,
                    xero_transaction_id=txn.bank_transaction_id,
                    confidence_score=0.85,
                    match_method="amount_date",
                    requires_human_review=True,
                )

        # Strategy 3: Amount + supplier
        for txn in transactions:
            if (
                self._amount_matches(target_amount, txn)
                and self._supplier_matches(supplier_name, txn)
                and date_within(txn, 30)
            ):
                return MatchResult(
                    matched=True,
                    source_reference=source_reference,
                    xero_transaction_id=txn.bank_transaction_id,
                    confidence_score=0.80,
                    match_method="amount_supplier",
                    requires_human_review=True,
                )

        # Strategy 4: Amount only
        for txn in transactions:
            if self._amount_matches(target_amount, txn) and date_within(txn, 7):
                return MatchResult(
                    matched=True,
                    source_reference=source_reference,
                    xero_transaction_id=txn.bank_transaction_id,
                    confidence_score=0.65,
                    match_method="amount_only",
                    requires_human_review=True,
                )

        return MatchResult(
            matched=False,
            source_reference=source_reference,
            xero_transaction_id=None,
            confidence_score=0.0,
            match_method="no_match",
            requires_human_review=True,
        )

    async def run_batch(
        self,
        items: list[dict[str, Any]],
        client_id: str,
        xero: XeroService,
    ) -> list[MatchResult]:
        transactions = await xero.get_unreconciled_bank_transactions()
        results: list[MatchResult] = []

        for item in items:
            result = self.match(
                source_reference=item["source_reference"],
                supplier_name=item.get("supplier_name"),
                amount=item["amount"],
                date_value=item["date"],
                transactions=transactions,
            )
            results.append(result)

            match_row = TransactionMatch(
                source_reference=result.source_reference,
                supplier_name=item.get("supplier_name"),
                amount=float(item["amount"]),
                transaction_date=self._coerce_datetime(item["date"]),
                xero_transaction_id=result.xero_transaction_id,
                xero_invoice_id=result.xero_invoice_id,
                confidence_score=result.confidence_score,
                match_method=result.match_method,
                status="matched" if result.matched else "exception",
            )
            self._db.add(match_row)
            await self._db.flush()

            audit_row = AuditLog(
                action="match_attempt",
                entity_type="transaction_match",
                entity_id=match_row.id,
                rule_applied=result.match_method,
                confidence_score=result.confidence_score,
                user_id=client_id,
            )
            self._db.add(audit_row)

        await self._db.commit()
        return results
