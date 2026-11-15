"""Sarvam-backed action selection from compact, verified facts."""

import json
from collections.abc import Mapping
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from brain.agents.base import AgentBase
from brain.agents.comms import validate_customer_message
from brain import llm


class Action(str, Enum):
    CREATE_LINK = "create_link"
    CREATE_REFUND = "create_refund"
    WAIT = "wait"
    ESCALATE = "escalate"


class ResolverResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Action
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        return _ACTION_ALIASES.get(normalized, normalized)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in _CONFIDENCE_LABELS:
                return _CONFIDENCE_LABELS[normalized]
            try:
                return float(normalized)
            except ValueError:
                return value
        return value


class ResolverCommsResult(ResolverResult):
    customer_message: str


_ACTION_ALIASES = {
    "retry_payment": Action.CREATE_LINK.value,
    "resend_link": Action.CREATE_LINK.value,
    "send_payment_link": Action.CREATE_LINK.value,
    "retry": Action.CREATE_LINK.value,
    "payment_link": Action.CREATE_LINK.value,
    "create_payment_link": Action.CREATE_LINK.value,
    "refund": Action.CREATE_REFUND.value,
    "issue_refund": Action.CREATE_REFUND.value,
    "process_refund": Action.CREATE_REFUND.value,
    "wait_for_settlement": Action.WAIT.value,
    "hold": Action.WAIT.value,
    "human_review": Action.ESCALATE.value,
    "escalate_to_human": Action.ESCALATE.value,
}
_CONFIDENCE_LABELS = {"high": 0.9, "medium": 0.6, "low": 0.3}


_COMPACT_FACT_KEYS = (
    "txn_id",
    "status",
    "bank_state",
    "bank_code",
    "retryable",
    "eta_s",
    "amount",
    "links_issued",
    "duplicate_of",
    "duplicate_pairs",
    "refund_count",
    "transaction_customer_id",
)


class ResolverAgent(AgentBase):
    def resolve(self, case_type: str, facts: Mapping[str, Any]) -> ResolverResult:
        prompt = {
            "case_type": case_type,
            "facts": _compact_facts(facts),
        }
        return self.run(
            _RESOLVER_SYSTEM,
            json.dumps(prompt, ensure_ascii=False),
            ResolverResult,
            max_tokens=llm.MAX_TOKENS,
            allow_reasoning_fallback=False,
            retry_budget_on_length=True,
        )

    def resolve_with_comms(
        self,
        case_type: str,
        facts: Mapping[str, Any],
        language: str,
    ) -> ResolverCommsResult:
        system, user = build_combined_prompt(case_type, facts, language)
        result = self.run(
            system,
            user,
            ResolverCommsResult,
        )
        validate_customer_message(result.customer_message, language)
        return result


def build_combined_prompt(
    case_type: str,
    facts: Mapping[str, Any],
    language: str,
) -> tuple[str, str]:
    prompt = {
        "case_type": case_type,
        "facts": _compact_facts(facts),
        "language": language,
    }
    return _COMBINED_SYSTEM, json.dumps(prompt, ensure_ascii=False)


def _compact_facts(facts: Mapping[str, Any]) -> dict[str, Any]:
    return {key: facts[key] for key in _COMPACT_FACT_KEYS if key in facts}


_RESOLVER_SYSTEM = (
    "Choose a safe next action for a Paytm merchant case using only the supplied compact "
    "facts. Return JSON with action, reason, confidence. Action must be create_link, "
    "create_refund, wait, or escalate. These are the only allowed actions. Map "
    "retry_payment, resend_link, and send_payment_link to create_link, refund to "
    "create_refund, and hold to wait. Never invent or assume facts."
)
_COMBINED_SYSTEM = (
    "Choose a safe next action for a Paytm merchant case using only the supplied compact "
    "facts, and write a customer message in the requested language. Return exactly one "
    "JSON object with keys action, reason, confidence, customer_message. Action must be "
    "exactly create_link, create_refund, wait, or escalate; confidence must be a number "
    "from 0 to 1. Example: "
    '{"action":"create_link","reason":"A retryable failed payment is verified.",'
    '"confidence":0.9,"customer_message":"I checked the verified transaction details. '
    'I will share the next step."} '
    "Use 2 or 3 short customer-message sentences in the requested language. For Hindi, "
    "write in Devanagari. The message may state only facts supplied; never claim that a "
    "refund or payment link has been created, sent, or completed."
)
