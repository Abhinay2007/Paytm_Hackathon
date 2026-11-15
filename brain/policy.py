"""Deterministic Guardian rules for proposed payment actions."""

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


AUTO_REFUND_LIMIT_RUPEES = 1000

Verdict = Literal["allow", "needs_approval", "block"]

_ALLOWED_ACTIONS = {
    "failed_payment": {"create_link", "wait", "escalate"},
    "pending_payment": {"wait", "escalate"},
    "pending_settlement": {"wait", "escalate"},
    "duplicate_charge": {"create_refund", "wait", "escalate"},
    "refund_request": {"create_refund", "wait", "escalate"},
    "suspicious_request": {"wait", "escalate"},
    "other": {"wait", "escalate"},
}
_SUSPICIOUS_PATTERNS = (
    re.compile(r"\bredirect(?:\s+the)?\s+(?:money|refund|payment)\b", re.I),
    re.compile(
        r"\b(?:redirect|send|transfer|route|pay)\b.{0,60}"
        r"\b(?:another|other|different|someone else'?s)\b.{0,60}"
        r"\b(?:account|customer|person|data|money|refund)\b",
        re.I | re.S,
    ),
    re.compile(
        r"\b(?:another|other)\s+customer'?s?\s+(?:data|account|payment)\b",
        re.I,
    ),
    re.compile(r"\b(?:different|another|other)\s+(?:person'?s\s+)?account\b", re.I),
)
_SUSPICIOUS_HINDI = (
    "दूसरे खाते",
    "किसी और के खाते",
    "दूसरे व्यक्ति",
    "किसी दूसरे व्यक्ति",
    "दूसरे ग्राहक",
    "अन्य ग्राहक",
)


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Verdict
    reason: str


class Guardian:
    def evaluate(
        self,
        action: str,
        case_type: str,
        customer_id: str,
        facts: Mapping[str, Any],
        request_text: str = "",
    ) -> PolicyDecision:
        return check_policy(action, case_type, customer_id, facts, request_text)


def check_policy(
    action: str,
    case_type: str,
    customer_id: str,
    facts: Mapping[str, Any],
    request_text: str = "",
) -> PolicyDecision:
    if not isinstance(action, str) or not isinstance(case_type, str):
        return PolicyDecision(verdict="block", reason="Action and case type must be strings.")
    if not isinstance(facts, Mapping):
        return PolicyDecision(verdict="block", reason="Verified transaction facts are required.")
    normalized_action = action.strip().lower()
    normalized_case_type = case_type.strip().lower()
    if _is_suspicious(
        normalized_case_type,
        facts,
        request_text if isinstance(request_text, str) else "",
    ):
        return PolicyDecision(
            verdict="block",
            reason="Suspicious requests to redirect money or access another customer's data are blocked.",
        )
    if normalized_case_type not in _ALLOWED_ACTIONS:
        return PolicyDecision(verdict="block", reason="Unknown case type.")
    if normalized_action not in _ALLOWED_ACTIONS[normalized_case_type]:
        return PolicyDecision(
            verdict="block",
            reason=f"Action {normalized_action!r} is not allowed for {normalized_case_type!r}.",
        )

    if normalized_action in {"create_link", "create_refund"} or facts.get("txn_id") is not None:
        owner_id = facts.get("transaction_customer_id")
        if owner_id is None or str(owner_id) != str(customer_id):
            return PolicyDecision(
                verdict="block",
                reason="Transaction ownership could not be verified for this customer.",
            )

    if normalized_action == "create_link":
        if facts.get("status") != "FAILED" or facts.get("retryable") is not True:
            return PolicyDecision(
                verdict="block",
                reason="A payment link requires a confirmed retryable failed payment.",
            )

    if normalized_action == "create_refund":
        if facts.get("status") != "SUCCESS":
            return PolicyDecision(
                verdict="block",
                reason="A refund requires a confirmed successful transaction.",
            )
        amount = facts.get("amount")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            return PolicyDecision(verdict="block", reason="Refund amount is invalid.")
        if normalized_case_type == "duplicate_charge" and not _has_duplicate_evidence(facts):
            return PolicyDecision(
                verdict="block",
                reason="Duplicate-charge evidence is missing.",
            )
        if amount > AUTO_REFUND_LIMIT_RUPEES:
            return PolicyDecision(
                verdict="needs_approval",
                reason=(
                    f"Refunds above ₹{AUTO_REFUND_LIMIT_RUPEES} require human approval."
                ),
            )

    return PolicyDecision(verdict="allow", reason="Action is permitted by policy.")


def _has_duplicate_evidence(facts: Mapping[str, Any]) -> bool:
    if facts.get("duplicate_of") is not None:
        return True
    pairs = facts.get("duplicate_pairs")
    return isinstance(pairs, list) and any(
        isinstance(pair, Mapping)
        and pair.get("txn_id") is not None
        and pair.get("duplicate_of") is not None
        for pair in pairs
    )


def _is_suspicious(
    case_type: str,
    facts: Mapping[str, Any],
    request_text: str,
) -> bool:
    if case_type == "suspicious_request" or facts.get("suspicious") is True:
        return True
    if any(pattern.search(request_text) for pattern in _SUSPICIOUS_PATTERNS):
        return True
    return any(phrase in request_text for phrase in _SUSPICIOUS_HINDI)
