"""Evidence-based next-step recommendations for payment cases."""

import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from platform.agents.base import AgentBase

ResolverRecommendation = Literal[
    "wait",
    "recommend_payment_link",
    "recommend_refund_review",
    "check_payment_status",
    "human_review",
    "request_more_information",
    "no_action",
]


class ResolverOutput(BaseModel):
    """Internal recommendation for the Supervisor; it is not an execution command."""

    model_config = ConfigDict(extra="forbid")

    recommendation: ResolverRecommendation = Field(
        description="A proposed next step for the Supervisor, never an executed operation."
    )
    rationale: str = Field(description="Reasoning grounded in the supplied case context.")
    requires_human_approval: bool = Field(
        description="Whether the proposed path requires human approval before any action."
    )
    needs_more_information: bool = Field(
        description="Whether missing or conflicting evidence prevents a safe recommendation."
    )
    evidence_gaps: list[str] = Field(
        description="Missing or conflicting evidence relevant to the next step."
    )


RESOLVER_SYSTEM_PROMPT = """You are the Resolver in a payment-support system. Recommend a next step for the Supervisor using only the supplied case context and investigation evidence. Understand English and Hindi customer context.
Return one recommendation: wait for a pending payment; recommend_payment_link only for a verified retryable failure with no conflicting or duplicate-charge evidence; recommend_refund_review for a refund request; check_payment_status for a status query; human_review for suspected duplicate charges or material uncertainty; request_more_information when key evidence is missing; or no_action when the evidence supports no further step.
Refund paths and suspected duplicate charges always require human approval. Set requires_human_approval accordingly. If evidence is insufficient or conflicts, set needs_more_information and name the evidence gaps; prefer human_review or request_more_information over guessing.
Never invent transaction or bank information. A recommendation is advisory only: do not execute tools, initiate payment or retry, issue or approve a refund, modify a transaction, or claim an action has occurred. Do not bypass Guardian or human approval. Return only JSON matching the requested schema."""


class ResolverAgent(AgentBase[ResolverOutput]):
    """Recommend a safe next step from context supplied by the Supervisor."""

    def __init__(self, *, max_retries: int = 2, retry_delay: float = 0.0) -> None:
        super().__init__(
            RESOLVER_SYSTEM_PROMPT,
            ResolverOutput,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )

    def resolve(self, case_context: Mapping[str, Any]) -> ResolverOutput:
        """Return a validated advisory recommendation without fetching evidence."""
        context_json = json.dumps(dict(case_context), ensure_ascii=False, sort_keys=True)
        return self.run(f"Recommend a next step from only this supplied case context JSON:\n{context_json}")
