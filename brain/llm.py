"""Small synchronous Sarvam chat-completions client."""

import contextvars
import os
import threading
import time
from collections import deque
from collections.abc import Mapping
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

CHAT_COMPLETIONS_URL = "https://api.sarvam.ai/v1/chat/completions"
MAX_TOKENS = 3000
TEMPERATURE = 0.2

_CALL_METADATA: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "sarvam_call_metadata",
    default=None,
)
_RECENT_CALLS: deque[dict[str, Any]] = deque(maxlen=200)
_RECENT_CALLS_LOCK = threading.Lock()
_CALL_SEQUENCE = 0


class SarvamError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        finish_reason: str | None = None,
        raw_reply: str = "",
    ) -> None:
        self.http_status = http_status
        self.finish_reason = finish_reason
        self.raw_reply = raw_reply
        details = f"HTTP {http_status}" if http_status is not None else "HTTP status unavailable"
        details += f", finish_reason={finish_reason!r}"
        super().__init__(f"{message} ({details})")


class SarvamTimeoutError(TimeoutError):
    def __init__(self, message: str) -> None:
        self.http_status: int | None = None
        self.finish_reason: str | None = None
        self.raw_reply = ""
        super().__init__(f"{message} (HTTP status unavailable, finish_reason=None)")


def _finish_reason(payload: Any) -> str | None:
    try:
        value = payload["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError):
        return None
    return value if isinstance(value, str) else None


def clear_call_metadata() -> None:
    _CALL_METADATA.set(None)


def last_call_metadata() -> dict[str, Any] | None:
    value = _CALL_METADATA.get()
    return {key: item for key, item in value.items() if key != "_call_id"} if value else None


def recent_call_metadata(limit: int = 20) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    with _RECENT_CALLS_LOCK:
        calls = list(_RECENT_CALLS)[-limit:]
    return [
        {key: value for key, value in call.items() if key != "_call_id"}
        for call in reversed(calls)
    ]


def update_last_call_metadata(metadata: Mapping[str, Any]) -> None:
    current = _CALL_METADATA.get()
    if current is None:
        return
    updated = {**current, **metadata}
    _CALL_METADATA.set(updated)
    call_id = current.get("_call_id")
    if call_id is not None:
        with _RECENT_CALLS_LOCK:
            for entry in reversed(_RECENT_CALLS):
                if entry.get("_call_id") == call_id:
                    entry.update(updated)
                    break


def _record_call(
    system: str,
    user: str,
    started: float,
    usage: Any = None,
    *,
    request_max_tokens: int = MAX_TOKENS,
    temperature: float = TEMPERATURE,
    reasoning_effort: str | None = None,
    attempt: int = 1,
    http_status: int | None = None,
    finish_reason: str | None = None,
    content_length: int = 0,
    reasoning_content_length: int = 0,
) -> None:
    global _CALL_SEQUENCE
    elapsed_s = time.perf_counter() - started
    with _RECENT_CALLS_LOCK:
        _CALL_SEQUENCE += 1
        call_id = _CALL_SEQUENCE
    metadata = {
        "usage": dict(usage) if isinstance(usage, Mapping) else {},
        "request_max_tokens": request_max_tokens,
        "reasoning_effort": reasoning_effort,
        "reasoning_effort_sent": reasoning_effort is not None,
        "input_length": len(system) + len(user),
        "elapsed_s": round(elapsed_s, 3),
        "elapsed_ms": round(elapsed_s * 1000),
        "attempt": attempt,
        "http_status": http_status,
        "finish_reason": finish_reason,
        "content_length": content_length,
        "reasoning_content_length": reasoning_content_length,
        "_call_id": call_id,
    }
    _CALL_METADATA.set(metadata)
    with _RECENT_CALLS_LOCK:
        _RECENT_CALLS.append(dict(metadata))


def chat(
    system: str,
    user: str,
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    allow_reasoning_fallback: bool = True,
    attempt: int = 1,
) -> tuple[str, str | None]:
    started = time.perf_counter()
    request_max_tokens = MAX_TOKENS if max_tokens is None else max_tokens
    request_temperature = TEMPERATURE if temperature is None else temperature
    configured_effort = (
        reasoning_effort
        if reasoning_effort is not None
        else os.getenv("SARVAM_REASONING_EFFORT", "").strip() or None
    )
    if request_max_tokens <= 0:
        raise ValueError("Sarvam max_tokens must be a positive integer")
    api_key = os.getenv("SARVAM_API_KEY", "").strip()
    if not api_key:
        _record_call(
            system,
            user,
            started,
            request_max_tokens=request_max_tokens,
            temperature=request_temperature,
            reasoning_effort=configured_effort,
            attempt=attempt,
        )
        raise SarvamError("SARVAM_API_KEY is not configured")
    model = os.getenv("SARVAM_MODEL", "").strip() or "sarvam-105b"
    timeout = httpx.Timeout(90.0, connect=10.0)

    try:
        with httpx.Client(timeout=timeout) as client:
            request_body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": request_temperature,
                "max_tokens": request_max_tokens,
            }
            if configured_effort is not None:
                request_body["reasoning_effort"] = configured_effort
            response = client.post(
                CHAT_COMPLETIONS_URL,
                headers={"api-subscription-key": api_key},
                json=request_body,
            )
    except httpx.TimeoutException as exc:
        _record_call(
            system,
            user,
            started,
            request_max_tokens=request_max_tokens,
            temperature=request_temperature,
            reasoning_effort=configured_effort,
            attempt=attempt,
        )
        raise SarvamTimeoutError("Sarvam chat request timed out") from exc
    except httpx.RequestError as exc:
        _record_call(
            system,
            user,
            started,
            request_max_tokens=request_max_tokens,
            temperature=request_temperature,
            reasoning_effort=configured_effort,
            attempt=attempt,
        )
        raise SarvamError("Sarvam chat request failed") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = None
    finish_reason = _finish_reason(payload)
    usage = payload.get("usage") if isinstance(payload, Mapping) else None
    if response.is_error:
        _record_call(
            system,
            user,
            started,
            usage,
            request_max_tokens=request_max_tokens,
            temperature=request_temperature,
            reasoning_effort=configured_effort,
            attempt=attempt,
            http_status=response.status_code,
            finish_reason=finish_reason,
        )
        raise SarvamError(
            "Sarvam chat request failed",
            http_status=response.status_code,
            finish_reason=finish_reason,
        )

    try:
        message = payload["choices"][0]["message"]
        content = message.get("content")
        reasoning_content = message.get("reasoning_content")
        content_length = len(content) if isinstance(content, str) else 0
        reasoning_content_length = (
            len(reasoning_content) if isinstance(reasoning_content, str) else 0
        )
        if (not isinstance(content, str) or not content) and allow_reasoning_fallback:
            content = reasoning_content
        text = content if isinstance(content, str) else ""
    except (KeyError, IndexError, TypeError) as exc:
        _record_call(
            system,
            user,
            started,
            usage,
            request_max_tokens=request_max_tokens,
            temperature=request_temperature,
            reasoning_effort=configured_effort,
            attempt=attempt,
            http_status=response.status_code,
            finish_reason=finish_reason,
        )
        raise SarvamError(
            "Sarvam returned a malformed chat response",
            http_status=response.status_code,
            finish_reason=finish_reason,
        ) from exc
    _record_call(
        system,
        user,
        started,
        usage,
        request_max_tokens=request_max_tokens,
        temperature=request_temperature,
        reasoning_effort=configured_effort,
        attempt=attempt,
        http_status=response.status_code,
        finish_reason=finish_reason,
        content_length=content_length,
        reasoning_content_length=reasoning_content_length,
    )
    return text, finish_reason
