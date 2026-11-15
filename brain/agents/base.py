"""Shared JSON-output handling for Sarvam-backed agents."""

import json
import re
import time
from enum import Enum
from typing import Any, Literal, TypeVar, get_args, get_origin

import httpx
from pydantic import BaseModel, ValidationError

from brain import llm


ModelT = TypeVar("ModelT", bound=BaseModel)
_COMMON_KEYS = {
    "intent": ("classification", "category"),
    "transaction_id": ("txn_id", "transactionId"),
    "missing_information": ("missing_info",),
    "needs_clarification": ("clarification_needed",),
    "action": ("recommendation",),
    "reason": ("rationale", "explanation"),
    "customer_message": ("message", "reply", "text"),
}


class AgentOutputError(ValueError):
    def __init__(
        self,
        message: str,
        raw_reply: str,
        finish_reason: str | None,
        sarvam_attempts: list[dict[str, Any]] | None = None,
    ) -> None:
        self.http_status: int | None = None
        self.raw_reply = raw_reply
        self.finish_reason = finish_reason
        self.sarvam_attempts = sarvam_attempts or []
        latest = self.sarvam_attempts[-1] if self.sarvam_attempts else {}
        self.usage = latest.get("usage", {})
        self.request_max_tokens = latest.get("request_max_tokens")
        self.reasoning_effort = latest.get("reasoning_effort")
        self.reasoning_effort_sent = latest.get("reasoning_effort_sent", False)
        self.input_length = latest.get("input_length")
        self.elapsed_s = latest.get("elapsed_s")
        self.elapsed_ms = latest.get("elapsed_ms")
        self.content_length = latest.get("content_length", len(raw_reply))
        self.reasoning_content_length = latest.get("reasoning_content_length", 0)
        self.attempt = latest.get("attempt")
        super().__init__(f"{message} (finish_reason={finish_reason!r})")


class SarvamReasoningBudgetError(RuntimeError):
    def __init__(self, sarvam_attempts: list[dict[str, Any]]) -> None:
        latest = sarvam_attempts[-1] if sarvam_attempts else {}
        self.http_status: int | None = None
        self.raw_reply = ""
        self.finish_reason = "length"
        self.sarvam_attempts = sarvam_attempts
        self.usage = latest.get("usage", {})
        self.request_max_tokens = latest.get("request_max_tokens")
        self.reasoning_effort = latest.get("reasoning_effort")
        self.reasoning_effort_sent = latest.get("reasoning_effort_sent", False)
        self.input_length = latest.get("input_length")
        self.elapsed_s = latest.get("elapsed_s")
        self.elapsed_ms = latest.get("elapsed_ms")
        self.content_length = latest.get("content_length", 0)
        self.reasoning_content_length = latest.get("reasoning_content_length", 0)
        self.attempt = latest.get("attempt")
        super().__init__(
            "Sarvam returned empty content after reaching its reasoning budget "
            "(finish_reason='length')"
        )


def _strip_wrappers(text: str) -> str:
    without_thoughts = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.I | re.S)
    return re.sub(r"```(?:json)?|```", "", without_thoughts, flags=re.I).strip()


def _first_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise json.JSONDecodeError("No valid JSON object found", text, 0)


def _case_insensitive_value(value: Any, annotation: Any) -> Any:
    if not isinstance(value, str):
        return value
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        choices = args
    elif origin is not None and args:
        for option in args:
            matched = _case_insensitive_value(value, option)
            if matched != value:
                return matched
        return value
    elif isinstance(annotation, type) and issubclass(annotation, Enum):
        choices = tuple(member.value for member in annotation)
    else:
        return value

    for choice in choices:
        if isinstance(choice, str) and choice.casefold() == value.casefold():
            return choice
    return value


def _normalize_fields(value: dict[str, Any], schema: type[BaseModel]) -> dict[str, Any]:
    normalized = dict(value)
    for field_name, aliases in _COMMON_KEYS.items():
        if field_name in schema.model_fields and field_name not in normalized:
            for alias in aliases:
                if alias in normalized:
                    normalized[field_name] = normalized.pop(alias)
                    break

    for field_name, field in schema.model_fields.items():
        if field_name in normalized:
            normalized[field_name] = _case_insensitive_value(
                normalized[field_name], field.annotation
            )
    return normalized


class AgentBase:
    def __init__(self) -> None:
        self.sarvam_attempts: list[dict[str, Any]] = []

    def run(
        self,
        system: str,
        user: str,
        schema: type[ModelT],
        *,
        max_tokens: int | None = None,
        allow_reasoning_fallback: bool | None = None,
        retry_budget_on_length: bool = False,
    ) -> ModelT:
        invalid_json_retries = 0
        timeout_retries = 0
        length_retry_done = False
        last_reply = ""
        last_finish_reason: str | None = None
        self.sarvam_attempts = []
        base_tokens = llm.MAX_TOKENS if max_tokens is None else max_tokens
        request_tokens = base_tokens

        while True:
            llm.clear_call_metadata()
            started = time.perf_counter()
            try:
                options: dict[str, Any] = {}
                if max_tokens is not None or length_retry_done:
                    options["max_tokens"] = request_tokens
                if allow_reasoning_fallback is not None:
                    options["allow_reasoning_fallback"] = allow_reasoning_fallback
                if options:
                    last_reply, last_finish_reason = llm.chat(system, user, **options)
                else:
                    last_reply, last_finish_reason = llm.chat(system, user)
            except (TimeoutError, httpx.TimeoutException) as exc:
                self._record_attempt(
                    system, user, started, request_tokens=request_tokens
                )
                if timeout_retries < 1:
                    timeout_retries += 1
                    continue
                _attach_attempts(exc, self.sarvam_attempts)
                raise
            except Exception as exc:
                self._record_attempt(
                    system, user, started, last_finish_reason, request_tokens
                )
                _attach_attempts(exc, self.sarvam_attempts)
                raise
            self._record_attempt(
                system,
                user,
                started,
                request_tokens=request_tokens,
                content_length=len(last_reply) if isinstance(last_reply, str) else 0,
            )

            try:
                parsed = _first_json_object(_strip_wrappers(last_reply))
                return schema.model_validate(_normalize_fields(parsed, schema))
            except (json.JSONDecodeError, ValidationError) as exc:
                if last_finish_reason == "length" and retry_budget_on_length:
                    if not length_retry_done:
                        length_retry_done = True
                        request_tokens = min(base_tokens * 2, 8000)
                        continue
                    if (
                        not last_reply
                        and self.sarvam_attempts[-1].get("content_length", 0) == 0
                        and self.sarvam_attempts[-1].get("reasoning_content_length", 0) == 0
                    ):
                        raise SarvamReasoningBudgetError(self.sarvam_attempts) from exc
                    raise AgentOutputError(
                        "Sarvam reply was truncated at the expanded reasoning budget",
                        last_reply,
                        last_finish_reason,
                        self.sarvam_attempts,
                    ) from exc
                if invalid_json_retries < 1:
                    invalid_json_retries += 1
                    continue
                if (
                    not last_reply
                    and last_finish_reason == "length"
                    and self.sarvam_attempts[-1].get("content_length", 0) == 0
                    and self.sarvam_attempts[-1].get("reasoning_content_length", 0) == 0
                ):
                    raise SarvamReasoningBudgetError(self.sarvam_attempts) from exc
                raise AgentOutputError(
                    "Sarvam reply did not contain valid schema JSON",
                    last_reply,
                    last_finish_reason,
                    self.sarvam_attempts,
                ) from exc

    def _record_attempt(
        self,
        system: str,
        user: str,
        started: float,
        finish_reason: str | None = None,
        request_tokens: int | None = None,
        content_length: int = 0,
    ) -> None:
        metadata = llm.last_call_metadata() or {
            "usage": {},
            "request_max_tokens": request_tokens or llm.MAX_TOKENS,
            "reasoning_effort": None,
            "reasoning_effort_sent": False,
            "input_length": len(system) + len(user),
            "elapsed_s": round(time.perf_counter() - started, 3),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "finish_reason": finish_reason,
            "content_length": content_length,
            "reasoning_content_length": 0,
        }
        metadata.setdefault("finish_reason", finish_reason)
        if request_tokens is not None:
            metadata["request_max_tokens"] = request_tokens
        metadata.setdefault("reasoning_effort", None)
        metadata.setdefault("reasoning_effort_sent", False)
        metadata.setdefault("elapsed_ms", round(metadata.get("elapsed_s", 0) * 1000))
        metadata["attempt"] = len(self.sarvam_attempts) + 1
        self.sarvam_attempts.append(metadata)
        llm.update_last_call_metadata(metadata)


def _attach_attempts(exc: Exception, attempts: list[dict[str, Any]]) -> None:
    exc.sarvam_attempts = attempts
    if attempts:
        latest = attempts[-1]
        for field in (
            "usage",
            "request_max_tokens",
            "reasoning_effort",
            "reasoning_effort_sent",
            "input_length",
            "elapsed_s",
            "elapsed_ms",
            "content_length",
            "reasoning_content_length",
            "finish_reason",
            "attempt",
        ):
            setattr(exc, field, latest.get(field))
