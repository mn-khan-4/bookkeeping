from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class PaymentRecord:
    bsb: str
    account_number: str
    transaction_code: str
    amount: Decimal
    account_name: str
    lodgement_ref: str
    trace_bsb: str
    trace_account: str
    remitter: str


class ABAFileGenerator:
    def _clean(self, value: str) -> str:
        return value.replace("-", "").strip()

    def _pad(self, value: str, length: int, align: str = "left") -> str:
        value = value[:length]
        if align == "right":
            return value.rjust(length)
        return value.ljust(length)

    def _amount_to_cents(self, amount: Decimal) -> str:
        cents = int((amount * Decimal("100")).to_integral_value())
        return str(cents).rjust(10, "0")

    def generate(
        self,
        payments: list[PaymentRecord],
        batch_description: str,
        bsb: str,
        account_number: str,
        account_name: str,
    ) -> str:
        if not payments:
            return ""

        record_lines: list[str] = []
        today = datetime.now().strftime("%d%m%y")

        bank_code = self._pad(self._clean(bsb)[:3], 3)
        user_id = self._pad(self._clean(account_number)[-6:], 6, align="right")
        description = self._pad(batch_description.upper(), 12)
        account_name_clean = self._pad(account_name.upper(), 26)

        header = (
            "0"
            + self._pad("", 7)
            + "01"
            + bank_code
            + user_id
            + description
            + today
            + account_name_clean
            + self._pad("", 40)
        )
        record_lines.append(self._pad(header, 120))

        credit_total = Decimal("0")
        debit_total = Decimal("0")

        for payment in payments:
            amount_cents = self._amount_to_cents(payment.amount)
            credit_total += payment.amount
            detail = (
                "1"
                + self._pad(self._clean(payment.bsb), 7)
                + self._pad(self._clean(payment.account_number), 9, align="right")
                + self._pad(payment.transaction_code, 2, align="right")
                + amount_cents
                + self._pad(payment.account_name.upper(), 32)
                + self._pad(payment.lodgement_ref.upper(), 18)
                + self._pad(self._clean(payment.trace_bsb), 7)
                + self._pad(self._clean(payment.trace_account), 9, align="right")
                + self._pad(payment.remitter.upper(), 16)
                + self._pad("", 8)
            )
            record_lines.append(self._pad(detail, 120))

        net_total = credit_total - debit_total
        total_record = (
            "7"
            + self._pad("999999", 6)
            + self._pad("", 12)
            + self._amount_to_cents(net_total)
            + self._amount_to_cents(credit_total)
            + self._amount_to_cents(debit_total)
            + self._pad("", 24)
            + str(len(payments) + 2).rjust(6, "0")
            + self._pad("", 40)
        )
        record_lines.append(self._pad(total_record, 120))

        return "\n".join(record_lines) + "\n"

    def export(
        self,
        payments: list[PaymentRecord],
        batch_description: str,
        bsb: str,
        account_number: str,
        account_name: str,
    ) -> bytes:
        content = self.generate(payments, batch_description, bsb, account_number, account_name)
        return content.encode("utf-8")
