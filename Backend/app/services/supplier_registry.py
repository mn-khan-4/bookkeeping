from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SupplierRule


class SupplierRegistry:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_rule(self, supplier_name: str, client_id: str) -> SupplierRule | None:
        result = await self._db.execute(
            select(SupplierRule).where(
                SupplierRule.supplier_name == supplier_name,
                SupplierRule.client_id == client_id,
            )
        )
        rule = result.scalar_one_or_none()
        if rule:
            return rule

        search_term = f"%{supplier_name.lower()}%"
        result = await self._db.execute(
            select(SupplierRule)
            .where(SupplierRule.client_id == client_id)
            .where(func.lower(SupplierRule.supplier_name).like(search_term))
        )
        return result.scalars().first()

    async def save_rule(
        self,
        supplier_name: str,
        account_code: str,
        gst_code: str,
        client_id: str,
    ) -> SupplierRule:
        result = await self._db.execute(
            select(SupplierRule).where(
                SupplierRule.supplier_name == supplier_name,
                SupplierRule.client_id == client_id,
            )
        )
        rule = result.scalar_one_or_none()
        if rule:
            rule.account_code = account_code
            rule.gst_code = gst_code
        else:
            rule = SupplierRule(
                supplier_name=supplier_name,
                account_code=account_code,
                gst_code=gst_code,
                client_id=client_id,
            )
            self._db.add(rule)

        await self._db.commit()
        await self._db.refresh(rule)
        return rule

    async def get_all_rules(self, client_id: str) -> list[SupplierRule]:
        result = await self._db.execute(
            select(SupplierRule).where(SupplierRule.client_id == client_id)
        )
        return list(result.scalars().all())
