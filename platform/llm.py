"""Small synchronous client for Sarvam's chat completions API."""

import os

import httpx

_CHAT_COMPLETIONS_URL = "https://api.sarvam.ai/v1/chat/completions"
_TIMEOUT_SECONDS = 30.0


def chat(system: str, user: str) -> str:
    """Send a two-message conversation to Sarvam and return its text reply.

    Configuration is read on each call so applications and tests can update
    environment variables without reloading this module.
    """
    api_key = os.getenv("SARVAM_API_KEY", "").strip()
    model = os.getenv("SARVAM_MODEL", "").strip()
    if not api_key:
        raise ValueError("SARVAM_API_KEY is required")
    if not model:
        raise ValueError("SARVAM_MODEL is required")

    try:
        response = httpx.post(
            _CHAT_COMPLETIONS_URL,
            headers={"api-subscription-key": api_key},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException as exc:
        raise TimeoutError("Sarvam chat request timed out") from exc
    except httpx.RequestError as exc:
        raise RuntimeError("Sarvam chat request failed") from exc

    if response.status_code in (401, 403):
        raise PermissionError(f"Sarvam authentication failed (HTTP {response.status_code})")
    if response.is_error:
        raise RuntimeError(f"Sarvam chat request failed (HTTP {response.status_code})")

    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ValueError("Sarvam returned a malformed chat response") from exc
    if not isinstance(content, str):
        raise ValueError("Sarvam returned a malformed chat response: content must be text")
    return content
