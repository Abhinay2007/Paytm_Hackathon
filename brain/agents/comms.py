"""Sarvam-backed customer messages constrained to verified observations."""

import json
import os
import re
import time
from collections.abc import Mapping
from typing import Any

import httpx

from brain import llm


_VERIFIED_FIELDS = {
    "txn_id",
    "status",
    "bank_state",
    "bank_code",
    "retryable",
    "eta_s",
    "amount",
    "duplicate_pairs",
    "refund_count",
    "refund_status",
    "refund_amount",
    "verified_action",
    "link_url",
    "link_expires_at",
}

_SYSTEM_PROMPT = (
    "Write a customer support update using only the verified observations. "
    "Return plain text, not JSON. Use 2 or 3 short sentences in the requested "
    "language; Hindi must be in Devanagari. Never claim an action that is not "
    "explicitly verified."
)


class CommsOutputError(ValueError):
    def __init__(
        self,
        message: str,
        attempts: list[dict[str, Any]],
    ) -> None:
        self.sarvam_attempts = attempts
        latest = attempts[-1] if attempts else {}
        self.usage = latest.get("usage", {})
        self.request_max_tokens = latest.get("request_max_tokens")
        self.reasoning_effort = latest.get("reasoning_effort")
        self.reasoning_effort_sent = latest.get("reasoning_effort_sent", False)
        self.finish_reason = latest.get("finish_reason")
        self.elapsed_ms = latest.get("elapsed_ms")
        self.content_length = latest.get("content_length", 0)
        self.attempt = latest.get("attempt")
        super().__init__(message)


class CommsAgent:
    def __init__(self) -> None:
        self.sarvam_attempts: list[dict[str, Any]] = []
        self.fallback_info: dict[str, Any] | None = None

    def compose(
        self,
        language: str,
        verified_observations: Mapping[str, Any],
        *,
        outcome: str = "waiting",
    ) -> str:
        self.sarvam_attempts = []
        self.fallback_info = None
        try:
            return self._compose(language, verified_observations, outcome=outcome)
        except (CommsOutputError, ValueError) as exc:
            if isinstance(exc, CommsOutputError):
                self.sarvam_attempts = exc.sarvam_attempts
            latest = self.sarvam_attempts[-1] if self.sarvam_attempts else {}
            cause = exc.__cause__
            reason = type(cause).__name__ if cause is not None else type(exc).__name__
            self.fallback_info = {
                "fallback": True,
                "reason": reason,
                "usage": latest.get("usage", {}),
            }
            return self.template(language, outcome, verified_observations)

    def _compose(
        self,
        language: str,
        verified_observations: Mapping[str, Any],
        *,
        outcome: str,
    ) -> str:
        self.sarvam_attempts = []
        self.fallback_info = None
        observations = {
            key: value
            for key, value in verified_observations.items()
            if key in _VERIFIED_FIELDS
        }
        safe_input = json.dumps(
            {"language": language, "verified_observations": observations},
            ensure_ascii=False,
        )
        try:
            base_tokens = _configured_max_tokens()
        except ValueError as exc:
            raise CommsOutputError(str(exc), self.sarvam_attempts) from exc
        request_tokens = base_tokens
        timeout_retried = False

        for attempt in range(1, 3):
            llm.clear_call_metadata()
            started = time.perf_counter()
            try:
                reply, finish_reason = llm.chat(
                    _SYSTEM_PROMPT,
                    safe_input,
                    max_tokens=request_tokens,
                    temperature=0.2,
                    reasoning_effort=os.getenv("SARVAM_REASONING_EFFORT", "").strip()
                    or None,
                    allow_reasoning_fallback=False,
                    attempt=attempt,
                )
            except (TimeoutError, httpx.TimeoutException) as exc:
                self._record_attempt(
                    started,
                    safe_input,
                    attempt,
                    request_tokens,
                    finish_reason=None,
                )
                if attempt == 1 and not timeout_retried:
                    timeout_retried = True
                    continue
                raise CommsOutputError(
                    f"Sarvam customer message timed out ({type(exc).__name__})",
                    self.sarvam_attempts,
                ) from exc
            except Exception as exc:
                self._record_attempt(
                    started,
                    safe_input,
                    attempt,
                    request_tokens,
                    finish_reason=None,
                )
                raise CommsOutputError(
                    f"Sarvam customer message failed ({type(exc).__name__})",
                    self.sarvam_attempts,
                ) from exc

            self._record_attempt(
                started,
                safe_input,
                attempt,
                request_tokens,
                finish_reason=finish_reason,
                content_length=len(reply) if isinstance(reply, str) else 0,
            )
            if not isinstance(reply, str) or not reply.strip():
                if finish_reason == "length" and attempt == 1:
                    request_tokens = min(base_tokens * 2, 8000)
                    continue
                raise CommsOutputError(
                    "Sarvam returned empty customer-message content",
                    self.sarvam_attempts,
                )
            try:
                validate_customer_message(reply, language, observations)
            except ValueError as exc:
                raise CommsOutputError(
                    f"Sarvam customer message was invalid ({exc})",
                    self.sarvam_attempts,
                ) from exc
            return reply.strip()

        raise CommsOutputError(
            f"Sarvam customer message failed for outcome {outcome!r}",
            self.sarvam_attempts,
        )

    @staticmethod
    def template(
        language: str,
        outcome: str,
        verified_observations: Mapping[str, Any] | None = None,
    ) -> str:
        observations = verified_observations or {}
        txn_id = str(observations.get("txn_id") or "your transaction")
        if language.lower().startswith("hi"):
            if outcome == "link_created":
                return (
                    f"लेन-देन {txn_id} के लिए भुगतान लिंक की पुष्टि हो गई है। "
                    f"आप यहाँ सुरक्षित रूप से भुगतान कर सकते हैं: {observations.get('link_url', '')}"
                )
            if outcome == "refund_done":
                amount = observations.get("refund_amount", observations.get("amount", ""))
                return (
                    f"₹{amount} का रिफंड लेन-देन {txn_id} के लिए प्रोसेस होने की पुष्टि हुई है। "
                    "कृपया अपने खाते में अपडेट देखें।"
                )
            if outcome == "awaiting_approval":
                return (
                    "आपका अनुरोध मानव समीक्षा की प्रतीक्षा कर रहा है। "
                    "समीक्षा पूरी होने पर हम आपको अपडेट देंगे।"
                )
            if outcome == "rejected":
                return (
                    "मानव समीक्षक ने प्रस्तावित कार्रवाई को स्वीकृति नहीं दी। "
                    "एक व्यक्ति आपसे आगे संपर्क करेगा।"
                )
            if outcome == "blocked":
                return (
                    "मैं इस अनुरोध को सुरक्षित रूप से पूरा नहीं कर सकता। "
                    "एक व्यक्ति आपसे आगे संपर्क करेगा।"
                )
            return (
                "आपका भुगतान अभी लंबित है। "
                "स्थिति बदलने पर हम आपको अपडेट देंगे।"
            )

        if outcome == "link_created":
            return (
                f"I verified a payment link for transaction {txn_id}. "
                f"You can continue securely here: {observations.get('link_url', '')}"
            )
        if outcome == "refund_done":
            amount = observations.get("refund_amount", observations.get("amount", ""))
            return (
                f"I verified that the ₹{amount} refund for transaction {txn_id} was processed. "
                "Please check your account for the update."
            )
        if outcome == "awaiting_approval":
            return (
                "Your request is awaiting human approval. "
                "We will update you when the review is complete."
            )
        if outcome == "rejected":
            return (
                "A human reviewer did not approve the proposed action. "
                "A person will follow up with you."
            )
        if outcome == "blocked":
            return (
                "I can't safely carry out this request. "
                "A human will follow up with you."
            )
        return "Your payment is still pending. We will update you when its status changes."

    def _record_attempt(
        self,
        started: float,
        user: str,
        attempt: int,
        request_tokens: int,
        *,
        finish_reason: str | None,
        content_length: int = 0,
    ) -> None:
        elapsed_s = time.perf_counter() - started
        metadata = llm.last_call_metadata() or {
            "usage": {},
            "request_max_tokens": request_tokens,
            "reasoning_effort": os.getenv("SARVAM_REASONING_EFFORT", "").strip() or None,
            "reasoning_effort_sent": bool(
                os.getenv("SARVAM_REASONING_EFFORT", "").strip()
            ),
            "input_length": len(_SYSTEM_PROMPT) + len(user),
            "elapsed_s": round(elapsed_s, 3),
            "elapsed_ms": round(elapsed_s * 1000),
            "finish_reason": finish_reason,
            "content_length": content_length,
            "reasoning_content_length": 0,
            "attempt": attempt,
        }
        metadata["attempt"] = attempt
        metadata["request_max_tokens"] = request_tokens
        metadata.setdefault("usage", {})
        metadata.setdefault("reasoning_effort", None)
        metadata.setdefault("reasoning_effort_sent", False)
        metadata.setdefault("finish_reason", finish_reason)
        metadata.setdefault("elapsed_ms", round(metadata.get("elapsed_s", 0) * 1000))
        metadata.setdefault("content_length", content_length)
        self.sarvam_attempts.append(metadata)
        llm.update_last_call_metadata(metadata)


def validate_customer_message(
    message: str,
    language: str,
    verified_observations: Mapping[str, Any] | None = None,
) -> None:
    text = message.strip()
    if not text:
        raise ValueError("Customer message must not be empty")
    try:
        json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        raise ValueError("Customer message must be plain text, not JSON")
    sentences = [part for part in re.split(r"(?<=[.!?।])\s+", text) if part.strip()]
    if not 2 <= len(sentences) <= 3:
        raise ValueError("Customer message must contain 2 or 3 short sentences")
    if language.lower().startswith("hi") and not any(
        "\u0900" <= character <= "\u097f" for character in text
    ):
        raise ValueError("Hindi customer messages must use Devanagari")

    observations = verified_observations or {}
    action = observations.get("verified_action")
    link_claim = re.search(
        r"\b(?:created|sent|issued|generated|available|ready)\b.{0,30}\b(?:payment )?link\b"
        r"|\b(?:payment )?link\b.{0,30}\b(?:created|sent|issued|generated|available|ready)\b"
        r"|लिंक.{0,20}(?:बन|भेज|जारी|उपलब्ध)",
        text,
        re.I,
    )
    refund_claim = re.search(
        r"\b(?:refund|refunded)\b.{0,40}\b(?:processed|completed|issued|sent|credited)\b"
        r"|\b(?:processed|completed|issued|sent|credited)\b.{0,40}\brefund\b"
        r"|\brefunded\b|रिफंड.{0,30}(?:प्रोसेस|जारी|भेज|मिल)",
        text,
        re.I,
    )
    refund_is_verified = (
        action == "create_refund" and observations.get("refund_status") == "PROCESSED"
    )
    link_is_verified = action == "create_link" and bool(observations.get("link_url"))
    if refund_claim and not refund_is_verified:
        raise ValueError("Message claims an unverified refund")
    if link_claim and not link_is_verified:
        raise ValueError("Message claims an unverified payment link")
    generic_action_claim = re.search(
        r"\b(?:created|generated|issued|sent|processed|completed|refunded|credited|"
        r"recovered|paid)\b|(?:बनाया|भेजा|जारी|प्रोसेस|पूरा किया|वापस किया|जमा किया)",
        text,
        re.I,
    )
    if generic_action_claim and not (refund_claim and refund_is_verified) and not (
        link_claim and link_is_verified
    ):
        raise ValueError("Message claims an unverified action")


def _configured_max_tokens() -> int:
    raw = os.getenv("BRAIN_MAX_TOKENS", "3000").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("BRAIN_MAX_TOKENS must be a positive integer") from exc
    if value <= 0:
        raise ValueError("BRAIN_MAX_TOKENS must be a positive integer")
    return value
