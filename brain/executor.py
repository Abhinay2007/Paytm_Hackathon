"""Guarded payment-action execution and post-action verification."""

import hashlib
from collections.abc import Mapping
from typing import Any

from mitra import tools


class ExecutionVerificationError(RuntimeError):
    """Raised when an action's result cannot be confirmed from a read-back."""


class Executor:
    async def execute(
        self,
        action: str,
        *,
        case_id: str,
        customer_id: str,
        facts: Mapping[str, Any],
        policy_verdict: str,
        human_approved: bool = False,
        reason: str = "",
    ) -> dict[str, Any]:
        if not isinstance(action, str):
            raise ValueError("Action must be a string")
        if policy_verdict == "block":
            raise PermissionError("Blocked policy verdict cannot execute an action")
        if facts.get("txn_id") is not None and str(
            facts.get("transaction_customer_id")
        ) != str(customer_id):
            raise PermissionError("Transaction ownership is not verified for this customer")
        if action in {"wait", "escalate"}:
            return {
                "action": action,
                "executed": False,
                "verified": True,
                "observations": {
                    key: facts[key]
                    for key in ("txn_id", "status", "bank_code", "retryable", "eta_s")
                    if key in facts
                },
            }
        if action not in {"create_link", "create_refund"}:
            raise ValueError(f"Unsupported action {action!r}")
        if policy_verdict == "needs_approval" and not human_approved:
            raise PermissionError("Human approval is required before execution")
        if policy_verdict not in {"allow", "needs_approval"}:
            raise ValueError(f"Unsupported policy verdict {policy_verdict!r}")

        if str(facts.get("transaction_customer_id")) != str(customer_id):
            raise PermissionError("Transaction ownership is not verified for this customer")
        txn_id = facts.get("txn_id")
        if not isinstance(txn_id, str) or not txn_id:
            raise ValueError("A transaction id is required to execute an action")
        verdict = "human_approved" if human_approved else "approve"

        if action == "create_link":
            result = _mapping(
                await tools.call_tool(
                    "executor",
                    "create_link",
                    verdict=verdict,
                    txn_id=txn_id,
                )
            )
            return await self._verify_link(
                result, txn_id=txn_id, customer_id=customer_id, facts=facts
            )

        amount = facts.get("amount")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValueError("A positive integer refund amount is required")
        idempotency_key = hashlib.sha256(
            f"{case_id}:{txn_id}:refund".encode("utf-8")
        ).hexdigest()
        result = _mapping(
            await tools.call_tool(
                "executor",
                "create_refund",
                verdict=verdict,
                txn_id=txn_id,
                amount=amount,
                reason=reason or "Customer payment issue",
                idempotency_key=idempotency_key,
            )
        )
        return await self._verify_refund(result, txn_id=txn_id, amount=amount)

    async def _verify_link(
        self,
        result: Mapping[str, Any],
        *,
        txn_id: str,
        customer_id: str,
        facts: Mapping[str, Any],
    ) -> dict[str, Any]:
        transaction = _mapping(
            await tools.call_tool("executor", "get_txn", txn_id=txn_id)
        )
        before_links = facts.get("links_issued", 0)
        after_links = transaction.get("links_issued")
        verified = (
            transaction.get("id") == txn_id
            and str(transaction.get("customer_id")) == str(customer_id)
            and transaction.get("status") == "FAILED"
            and transaction.get("recovered_by") is None
            and isinstance(after_links, int)
            and isinstance(before_links, int)
            and after_links >= before_links
            and isinstance(result.get("link_id"), str)
            and bool(result.get("link_id"))
            and isinstance(result.get("url"), str)
            and bool(result.get("url"))
        )
        if not verified:
            raise ExecutionVerificationError("Payment link could not be verified")
        return {
            "action": "create_link",
            "executed": True,
            "verified": True,
            "observations": {
                "txn_id": txn_id,
                "status": transaction["status"],
                "link_id": result["link_id"],
                "link_url": result["url"],
                "link_expires_at": result.get("expires_at"),
            },
        }

    async def _verify_refund(
        self,
        result: Mapping[str, Any],
        *,
        txn_id: str,
        amount: int,
    ) -> dict[str, Any]:
        refund_id = result.get("refund_id")
        if not isinstance(refund_id, str) or not refund_id:
            raise ExecutionVerificationError("Refund creation returned no refund id")
        refund = _mapping(
            await tools.call_tool("executor", "get_refund", refund_id=refund_id)
        )
        verified = (
            refund.get("refund_id") == refund_id
            and refund.get("txn_id") == txn_id
            and refund.get("status") == "PROCESSED"
            and refund.get("amount") == amount
        )
        if not verified:
            raise ExecutionVerificationError("Refund could not be verified")
        return {
            "action": "create_refund",
            "executed": True,
            "verified": True,
            "observations": {
                "txn_id": txn_id,
                "refund_status": refund["status"],
                "refund_amount": refund["amount"],
            },
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("Core tool returned an invalid response")
    return value
