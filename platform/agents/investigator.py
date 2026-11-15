"""Evidence-based payment investigation for the Brain workflow."""

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from platform.agents.base import AgentBase


class InvestigatorOutput(BaseModel):
    """Internal analysis separating supplied facts from possible explanations."""

    model_config = ConfigDict(extra="forbid")

    likely_cause: str = Field(description="Best-supported explanation, or that the cause is unknown.")
    confirmed_facts: list[str] = Field(
        description="Facts directly supported by the supplied evidence."
    )
    hypotheses: list[str] = Field(
        description="Plausible explanations not confirmed by the supplied evidence."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in the likely-cause assessment.")
    retryable: bool | None = Field(
        description="Retryability only when supported by evidence; otherwise null."
    )
    recommended_next_step: str = Field(
        description="A safe next investigation or proposed action for the Supervisor."
    )


INVESTIGATOR_SYSTEM_PROMPT = """You are the Investigator in a payment-support system. Analyze only the evidence supplied in the user message.
Evidence may include transaction status, bank status, error codes, duplicate-charge information, and customer context in English or Hindi.
Use the provided facts to identify a likely cause, but keep confirmed facts separate from hypotheses. Never turn a hypothesis into a confirmed fact.
Do not infer missing transaction details, bank outcomes, or whether funds were deducted. In particular, a failed transaction alone does not prove that funds were not deducted.
Set retryable to null unless the supplied evidence establishes it. Keep confidence between 0 and 1 and lower it when evidence is incomplete or conflicting.
Recommend only a next investigation or a proposed action for the Supervisor. Never execute tools, initiate payments, issue refunds, approve actions, or claim that an operation has happened.
Return only JSON matching the requested schema. Understand English and Hindi customer context."""


class InvestigatorAgent(AgentBase[InvestigatorOutput]):
    """Analyze payment evidence supplied by the Supervisor; never fetch evidence."""

    def __init__(self, *, max_retries: int = 2, retry_delay: float = 0.0) -> None:
        super().__init__(
            INVESTIGATOR_SYSTEM_PROMPT,
            InvestigatorOutput,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )

    def investigate(self, evidence: Mapping[str, Any]) -> InvestigatorOutput:
        """Return structured analysis of the provided evidence mapping."""
        evidence_json = json.dumps(dict(evidence), ensure_ascii=False, sort_keys=True)
        return self.run(f"Analyze only this supplied evidence JSON:\n{evidence_json}")
