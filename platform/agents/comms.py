"""Generate customer-facing and human-desk text from supplied case context."""

import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from platform.agents.base import AgentBase


class CommsOutput(BaseModel):
    """Generated message text; sending is handled elsewhere."""

    model_config = ConfigDict(extra="forbid")

    customer_message: str = Field(min_length=1)
    desk_brief: str = Field(min_length=1)
    language: Literal["English", "Hindi"]


COMMS_SYSTEM_PROMPT = """You are Comms for a payment-support system. Using only the supplied case context, produce a concise, polite, understandable customer message and a factual human-desk brief. Support English and Hindi, and use the customer's language when it is clear; otherwise use English.
The desk brief must summarize the issue, supplied evidence, investigation findings, and the decision still pending. Include only details present in the context; never invent transaction IDs, payment links, amounts, bank responses, or outcomes.
Never say a payment, refund, or retry succeeded unless the supplied evidence explicitly confirms that result. An approved decision is not proof that an operation has completed. Be clear when status is pending or unknown, and avoid promises.
Generate text only. Do not send messages, call Telegram or other tools, execute payment operations, or make decisions. Return only JSON matching the requested schema."""


class CommsAgent(AgentBase[CommsOutput]):
    """Compose customer and human-desk messages without sending them."""

    def __init__(self, *, max_retries: int = 2, retry_delay: float = 0.0) -> None:
        super().__init__(
            COMMS_SYSTEM_PROMPT,
            CommsOutput,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )

    def compose(self, case_context: Mapping[str, Any]) -> CommsOutput:
        """Return validated message text from context supplied by the Supervisor."""
        context_json = json.dumps(dict(case_context), ensure_ascii=False, sort_keys=True)
        return self.run(f"Compose messages from only this supplied case context JSON:\n{context_json}")
