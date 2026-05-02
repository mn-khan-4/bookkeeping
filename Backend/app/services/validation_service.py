from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.supplier_registry import SupplierRegistry


@dataclass
class ValidationResult:
    is_valid: bool
    account_code: str | None
    gst_code: str | None
    warnings: list[str]
    errors: list[str]


class TransactionValidationService:
    def __init__(self, db: AsyncSession) -> None:
        self._registry = SupplierRegistry(db)

    async def validate(
        self,
        source_reference: str,
        supplier_name: str | None,
        total_amount: float | Decimal | None,
        document_date: date | datetime | None,
        currency: str | None,
        tax_amount: float | Decimal | None,
        net_amount: float | Decimal | None,
        client_id: str,
    ) -> ValidationResult:
        warnings: list[str] = []
        errors: list[str] = []

        if not supplier_name or not supplier_name.strip():
            errors.append("Supplier name is required.")

        if total_amount is None or Decimal(str(total_amount)) <= 0:
            errors.append("Total amount must be greater than zero.")

        if document_date is None:
            errors.append("Document date is required.")

        if currency and currency.upper() != "AUD":
            warnings.append(f"Currency is {currency}, expected AUD.")

        account_code: str | None = None
        gst_code: str | None = None

        if supplier_name:
            rule = await self._registry.get_rule(supplier_name=supplier_name, client_id=client_id)
            if rule:
                account_code = rule.account_code
                gst_code = rule.gst_code

        gst_applies = False
        if account_code and "GST" in account_code.upper():
            gst_applies = True
        if gst_code and gst_code.upper() not in {"GSTFREE", "FRE", "NONE"}:
            gst_applies = True

        if gst_applies and tax_amount is not None and net_amount is not None:
            expected_tax = Decimal(str(net_amount)) * Decimal("0.10")
            actual_tax = Decimal(str(tax_amount))
            if (actual_tax - expected_tax).copy_abs() > Decimal("0.01"):
                warnings.append("GST amount deviates from 10% of net amount.")

        return ValidationResult(
            is_valid=len(errors) == 0,
            account_code=account_code,
            gst_code=gst_code,
            warnings=warnings,
            errors=errors,
        )
