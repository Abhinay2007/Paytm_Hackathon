"""Tool-only fact gathering for customer payment cases."""

import asyncio
from collections.abc import Mapping
from typing import Any

from mitra import tools


class InvestigatorAgent:
    async def investigate(self, customer_id: str, txn_id: str | None) -> dict[str, Any]:
        if not isinstance(customer_id, str) or not customer_id:
            raise ValueError("A customer id is required for investigation")
        if txn_id is not None and not isinstance(txn_id, str):
            raise ValueError("Transaction id must be a string")
        history_result = await tools.call_tool(
            "investigator", "history", customer_id=customer_id, limit=10
        )
        if not isinstance(history_result, Mapping):
            raise TypeError("History tool returned an invalid response")
        transactions = history_result.get("transactions", [])
        if not txn_id and transactions:
            txn_id = str(transactions[0].get("id") or "")
        if not txn_id:
            return self._facts(customer_id, None, history_result, None, None)

        txn_result, bank_result = await asyncio.gather(
            tools.call_tool("investigator", "get_txn", txn_id=txn_id),
            tools.call_tool("investigator", "bank_status", txn_id=txn_id),
        )
        if not isinstance(txn_result, Mapping) or not isinstance(bank_result, Mapping):
            raise TypeError("Transaction tools returned an invalid response")
        return self._facts(customer_id, txn_id, history_result, txn_result, bank_result)

    @staticmethod
    def _facts(
        customer_id: str,
        txn_id: str | None,
        history: Mapping[str, Any],
        transaction: Mapping[str, Any] | None,
        bank: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        transaction = transaction or {}
        bank = bank or {}
        duplicate_pairs = history.get("duplicate_pairs", [])
        if not isinstance(duplicate_pairs, list):
            raise TypeError("History tool returned invalid duplicate-pair facts")
        compact_pairs = [
            {"txn_id": str(pair["txn_id"]), "duplicate_of": str(pair["duplicate_of"])}
            for pair in duplicate_pairs
            if isinstance(pair, Mapping)
            and pair.get("txn_id") is not None
            and pair.get("duplicate_of") is not None
        ]
        return {
            "txn_id": txn_id,
            "customer_id": customer_id,
            "transaction_customer_id": transaction.get("customer_id"),
            "status": transaction.get("status"),
            "bank_state": bank.get("state"),
            "bank_code": bank.get("code") or transaction.get("bank_code"),
            "retryable": bank.get("retryable"),
            "eta_s": bank.get("eta_s"),
            "amount": transaction.get("amount"),
            "links_issued": transaction.get("links_issued", 0),
            "duplicate_of": transaction.get("duplicate_of"),
            "duplicate_pairs": compact_pairs,
            "refund_count": history.get("refund_count", 0),
        }
