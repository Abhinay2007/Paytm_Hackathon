"""Deterministic Guardian checks for proposed payment-case actions."""

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

PolicyAction = Literal[
    "read_transaction",
    "check_bank_status",
    "check_payment_status",
    "retry_payment",
    "create_payment_link",
    "refund",
    "resolve_duplicate_charge",
]

_READ_ONLY_ACTIONS = {
    "read_transaction",
    "check_bank_status",
    "check_payment_status",
}
_RETRY_ACTIONS = {"retry_payment", "create_payment_link"}


class PolicyDecision(BaseModel):
    """Result of a deterministic policy check; contains no execution behavior."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    reason: str
    requires_human_approval: bool


def check_policy(
    proposed_action: str,
    case_context: Mapping[str, Any],
    *,
    human_approved: bool = False,
) -> PolicyDecision:
    """Check a proposed action against supplied evidence without executing it."""
    action = proposed_action.strip().lower()
    if action in _READ_ONLY_ACTIONS:
        return PolicyDecision(
            allowed=True,
            reason="Read-only status or transaction checks are permitted.",
            requires_human_approval=False,
        )

    transaction = case_context.get("transaction")
    bank_status = case_context.get("bank_status")
    transaction = transaction if isinstance(transaction, Mapping) else {}
    bank_status = bank_status if isinstance(bank_status, Mapping) else {}

    transaction_state = transaction.get("status")
    bank_state = bank_status.get("state")
    if transaction_state is not None and bank_state is not None and transaction_state != bank_state:
        return PolicyDecision(
            allowed=False,
            reason="Transaction and bank statuses conflict; investigate before taking action.",
            requires_human_approval=action in {"refund", "resolve_duplicate_charge"},
        )

    if action in _RETRY_ACTIONS:
        if not isinstance(bank_status, Mapping) or not bank_status:
            return PolicyDecision(
                allowed=False,
                reason="Bank status evidence is missing.",
                requires_human_approval=False,
            )
        if bank_status.get("state") != "FAILED":
            return PolicyDecision(
                allowed=False,
                reason="Payment-link or retry action requires a confirmed FAILED bank state.",
                requires_human_approval=False,
            )
        if bank_status.get("retryable") is False:
            return PolicyDecision(
                allowed=False,
                reason="Evidence marks this failure as non-retryable.",
                requires_human_approval=False,
            )
        if bank_status.get("retryable") is not True:
            return PolicyDecision(
                allowed=False,
                reason="Retryability evidence is missing or invalid.",
                requires_human_approval=False,
            )
        if transaction_state is not None and transaction_state != "FAILED":
            return PolicyDecision(
                allowed=False,
                reason="Transaction status conflicts with a failed-payment retry path.",
                requires_human_approval=False,
            )
        return PolicyDecision(
            allowed=True,
            reason="Evidence confirms a failed, retryable bank status.",
            requires_human_approval=False,
        )

    if action == "refund":
        if not human_approved:
            return PolicyDecision(
                allowed=False,
                reason="Refunds require human approval.",
                requires_human_approval=True,
            )
        return PolicyDecision(
            allowed=True,
            reason="Human approval is recorded for the refund proposal.",
            requires_human_approval=True,
        )

    if action == "resolve_duplicate_charge":
        if "duplicate_of" not in transaction:
            return PolicyDecision(
                allowed=False,
                reason="Duplicate-charge evidence is missing.",
                requires_human_approval=True,
            )
        if transaction.get("duplicate_of") is None:
            return PolicyDecision(
                allowed=False,
                reason="Supplied transaction evidence does not confirm a duplicate charge.",
                requires_human_approval=True,
            )
        if not human_approved:
            return PolicyDecision(
                allowed=False,
                reason="Suspected duplicate-charge resolution requires human approval.",
                requires_human_approval=True,
            )
        return PolicyDecision(
            allowed=True,
            reason="Duplicate-charge evidence is present and human approval is recorded.",
            requires_human_approval=True,
        )

    return PolicyDecision(
        allowed=False,
        reason="Unknown or unsupported proposed action.",
        requires_human_approval=False,
    )
