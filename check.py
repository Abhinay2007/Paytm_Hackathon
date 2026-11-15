import asyncio
import ipaddress
import os
import time
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

REQUIRED_ENV_VARS = (
    "SARVAM_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "DESK_CHAT_ID",
    "CORE_URL",
    "PLATFORM_URL",
    "PUBLIC_CORE_URL",
    "CORE_SECRET",
)


def report(
    label: str,
    started: float,
    *,
    ok: bool,
    detail: str,
    warning: bool = False,
) -> None:
    icon = "⚠️" if warning else "✅" if ok else "❌"
    print(f"{icon} {label} — {(time.perf_counter() - started) * 1000:.0f} ms — {detail}")


def sarvam_reply(data: object) -> str:
    if not isinstance(data, dict):
        raise ValueError("response was not a JSON object")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("response did not contain a completion")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ValueError("response did not contain reply text")
    return message["content"]


def request_error(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    if isinstance(error, httpx.TimeoutException):
        return "request timed out"
    if isinstance(error, ValueError):
        return str(error)
    return f"request failed ({type(error).__name__})"


async def check_sarvam(client: httpx.AsyncClient, api_key: str | None) -> bool:
    started = time.perf_counter()
    if not api_key:
        report("Sarvam", started, ok=False, detail="SARVAM_API_KEY is missing")
        return False
    try:
        response = await client.post(
            "https://api.sarvam.ai/v1/chat/completions",
            headers={"api-subscription-key": api_key},
            json={
                "model": "sarvam-105b",
                "messages": [{"role": "user", "content": "Reply with OK"}],
                "max_tokens": 500,
            },
        )
        response.raise_for_status()
        reply = sarvam_reply(response.json())
        report("Sarvam", started, ok=True, detail=f"reply={reply[:40]!r}")
        return True
    except (httpx.HTTPError, ValueError) as error:
        report("Sarvam", started, ok=False, detail=request_error(error))
        return False


async def check_telegram(
    client: httpx.AsyncClient,
    token: str | None,
    desk_chat_id: str | None,
) -> bool:
    started = time.perf_counter()
    if not token:
        report("Telegram", started, ok=False, detail="TELEGRAM_BOT_TOKEN is missing")
        return False
    base_url = f"https://api.telegram.org/bot{token}"
    try:
        me_response = await client.get(f"{base_url}/getMe")
        me_response.raise_for_status()
        me = me_response.json()
        if not isinstance(me, dict) or not me.get("ok") or not isinstance(me.get("result"), dict):
            raise ValueError("getMe did not return a bot")
        username = me["result"].get("username")
        if not username:
            raise ValueError("getMe response did not contain a username")
        report("Telegram getMe", started, ok=True, detail=f"@{username}")
    except (httpx.HTTPError, ValueError) as error:
        report("Telegram getMe", started, ok=False, detail=request_error(error))
        return False

    send_started = time.perf_counter()
    if not desk_chat_id:
        report(
            "Telegram desk message",
            send_started,
            ok=False,
            detail="DESK_CHAT_ID is missing",
        )
        return False
    try:
        message_response = await client.post(
            f"{base_url}/sendMessage",
            json={"chat_id": desk_chat_id, "text": "preflight ✅"},
        )
        message_response.raise_for_status()
        message = message_response.json()
        if not isinstance(message, dict) or not message.get("ok"):
            raise ValueError("Telegram did not accept the preflight message")
        report("Telegram desk message", send_started, ok=True, detail="sent")
        return True
    except (httpx.HTTPError, ValueError) as error:
        report("Telegram desk message", send_started, ok=False, detail=request_error(error))
        return False


async def check_service(
    client: httpx.AsyncClient,
    label: str,
    base_url: str | None,
    path: str,
) -> bool:
    started = time.perf_counter()
    if not base_url:
        report(label, started, ok=False, detail="service URL is missing")
        return False
    try:
        response = await client.get(f"{base_url.rstrip('/')}{path}")
        if response.status_code != 200:
            raise ValueError(f"expected HTTP 200, got HTTP {response.status_code}")
        report(label, started, ok=True, detail="HTTP 200")
        return True
    except (httpx.HTTPError, ValueError) as error:
        report(label, started, ok=False, detail=request_error(error))
        return False


def check_public_core_url(value: str | None) -> bool:
    started = time.perf_counter()
    try:
        host = urlparse(value or "").hostname
    except ValueError:
        host = None
    is_local = not host or host.lower() == "localhost" or host.lower().endswith(".localhost")
    if host and not is_local:
        try:
            address = ipaddress.ip_address(host)
            is_local = address.is_loopback or address.is_unspecified
        except ValueError:
            pass
    if is_local:
        report(
            "Public core URL",
            started,
            ok=False,
            warning=True,
            detail="not phone-reachable; payment links must be reachable from the phone",
        )
        return False
    report("Public core URL", started, ok=True, detail=f"phone-reachable host={host}")
    return True


async def main() -> int:
    load_dotenv()
    started = time.perf_counter()
    missing = [name for name in REQUIRED_ENV_VARS if not os.getenv(name, "").strip()]
    if missing:
        report("Required environment", started, ok=False, detail=f"missing: {', '.join(missing)}")
    else:
        report("Required environment", started, ok=True, detail="all required variables are present")

    async with httpx.AsyncClient(timeout=30.0) as client:
        sarvam_ok = await check_sarvam(client, os.getenv("SARVAM_API_KEY"))
        telegram_ok = await check_telegram(
            client,
            os.getenv("TELEGRAM_BOT_TOKEN"),
            os.getenv("DESK_CHAT_ID"),
        )
        core_ok = await check_service(
            client,
            "Core simulator",
            os.getenv("CORE_URL"),
            "/txns?status=FAILED",
        )
        platform_ok = await check_service(
            client,
            "Platform API",
            os.getenv("PLATFORM_URL"),
            "/api/cases",
        )

    check_public_core_url(os.getenv("PUBLIC_CORE_URL"))
    hard_checks = [not missing, sarvam_ok, telegram_ok, core_ok, platform_ok]
    return 0 if all(hard_checks) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
