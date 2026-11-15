"""Sarvam-backed customer-message classification."""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from brain.agents.base import AgentBase


class Intent(str, Enum):
    FAILED_PAYMENT = "failed_payment"
    PENDING_PAYMENT = "pending_payment"
    DUPLICATE_CHARGE = "duplicate_charge"
    REFUND_REQUEST = "refund_request"
    SUSPICIOUS_REQUEST = "suspicious_request"
    OTHER = "other"


class ConciergeResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Intent = Intent.OTHER
    transaction_id: str | None = None
    summary: str = ""
    missing_information: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    language: Literal["en", "hi"] = "en"

    @field_validator("missing_information", mode="before")
    @classmethod
    def normalize_missing_information(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: Any) -> Any:
        return "" if value is None else value

    @field_validator("needs_clarification", mode="before")
    @classmethod
    def normalize_clarification(cls, value: Any) -> Any:
        return False if value is None else value


class ConciergeAgent(AgentBase):
    def interpret(self, message: str, language: str | None = None) -> ConciergeResult:
        target_language = _language_code(language)
        system = (
            "Classify a Paytm merchant support message. Support English and Hindi. "
            "Return one JSON object with intent, transaction_id, summary, "
            "missing_information, needs_clarification, and language. "
            "Intent must be one of failed_payment, pending_payment, duplicate_charge, "
            "refund_request, suspicious_request, other. Keep the summary concise and "
            "in the customer's language. Do not invent transaction IDs."
        )
        result = self.run(
            system,
            f"Customer language: {target_language}\nMessage: {message}",
            ConciergeResult,
        )
        return result.model_copy(update={"language": target_language})


def _language_code(language: str | None) -> Literal["en", "hi"]:
    code = (language or "en").strip().lower().split("-", maxsplit=1)[0]
    if code not in {"en", "hi"}:
        raise ValueError(f"Unsupported language {language!r}; expected 'en' or 'hi'")
    return code  # type: ignore[return-value]
