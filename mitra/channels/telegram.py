import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DESK_CHAT_ID = os.getenv("DESK_CHAT_ID", "")
CORE_URL = os.getenv("CORE_URL", "http://localhost:8001").rstrip("/")
_client = httpx.AsyncClient(timeout=10.0)
_desk_messages: dict[str, int] = {}
_USE_LOGGING_STUBS = False


def _api_url(method: str) -> str:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


async def _telegram_api(
    method: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float | None = None,
) -> Any:
    request_options: dict[str, Any] = {"json": payload or {}}
    if timeout is not None:
        request_options["timeout"] = timeout
    response = await _client.post(_api_url(method), **request_options)
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {result.get('description', result)}")
    return result.get("result")


def _is_integer_string(chat_id: int | str) -> bool:
    value = str(chat_id)
    if value.startswith("-"):
        value = value[1:]
    return bool(value) and value.isascii() and value.isdigit()


async def send_message(chat_id: int | str, text: str) -> Any:
    if not _is_integer_string(chat_id):
        logger.debug("Skipping Telegram message for non-numeric chat id %r", chat_id)
        return None
    return await _telegram_api(
        "sendMessage",
        {"chat_id": chat_id, "text": text},
    )


async def send_typing(chat_id: int | str) -> Any:
    if not _is_integer_string(chat_id):
        logger.debug("Skipping Telegram typing action for non-numeric chat id %r", chat_id)
        return None
    return await _telegram_api(
        "sendChatAction",
        {"chat_id": chat_id, "action": "typing"},
    )


async def send_desk_brief(case_id: str, text: str) -> None:
    if not DESK_CHAT_ID:
        raise RuntimeError("DESK_CHAT_ID is not configured")
    result = await _telegram_api(
        "sendMessage",
        {
            "chat_id": DESK_CHAT_ID,
            "text": text,
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "✅ Approve", "callback_data": f"d:{case_id}:a"},
                        {"text": "❌ Reject", "callback_data": f"d:{case_id}:r"},
                    ]
                ]
            },
        },
    )
    if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
        raise RuntimeError("Telegram did not return a desk message_id")
    _desk_messages[case_id] = result["message_id"]


async def mark_desk_decision(case_id: str, text: str) -> None:
    if not DESK_CHAT_ID:
        raise RuntimeError("DESK_CHAT_ID is not configured")
    message_id = _desk_messages.get(case_id)
    if message_id is None:
        raise RuntimeError(f"No desk message is recorded for case {case_id!r}")
    await _telegram_api(
        "editMessageText",
        {
            "chat_id": DESK_CHAT_ID,
            "message_id": message_id,
            "text": text,
            "reply_markup": {"inline_keyboard": []},
        },
    )


def _acknowledgement(text: str) -> str:
    if any("\u0900" <= character <= "\u097f" for character in text):
        return "आपका संदेश मिल गया है, मैं अभी जाँच कर रही हूँ…"
    return "Got it — looking into this now…"


async def _dispatch_inbound(
    chat_id: int,
    text: str,
    language_hint: str | None = None,
) -> None:
    if _USE_LOGGING_STUBS:
        await _logging_handle_inbound(chat_id, text, language_hint)
        return
    try:
        from mitra.inbound import handle_inbound
    except ImportError:
        handle_inbound = _logging_handle_inbound
    await handle_inbound(chat_id, text, language_hint=language_hint)


async def _dispatch_decision(case_id: str, approved: bool, by: str) -> None:
    if _USE_LOGGING_STUBS:
        await _logging_decide_case(case_id, approved, by)
        return
    try:
        from mitra.inbound import decide_case
    except ImportError:
        decide_case = _logging_decide_case
    await decide_case(case_id, approved, by=by)


async def _logging_handle_inbound(
    chat_id: int,
    text: str,
    language_hint: str | None = None,
) -> str:
    logger.info(
        "Inbound message stub: chat_id=%s language_hint=%s text=%s",
        chat_id,
        language_hint,
        text,
    )
    return ""


async def _logging_decide_case(case_id: str, approved: bool, by: str) -> None:
    logger.info(
        "Case decision stub: case_id=%s approved=%s by=%s",
        case_id,
        approved,
        by,
    )


def _transcribe_voice_file(path: str) -> tuple[str, str | None]:
    from sarvamai import SarvamAI

    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        raise RuntimeError("SARVAM_API_KEY is not configured")
    client = SarvamAI(api_subscription_key=api_key)
    with open(path, "rb") as audio_file:
        result = client.speech_to_text.transcribe(
            file=audio_file,
            model="saaras:v4",
            mode="transcribe",
        )
    return result.transcript, result.language_code


async def _transcribe_voice(file_id: str) -> tuple[str, str | None]:
    file_info = await _telegram_api("getFile", {"file_id": file_id})
    if not isinstance(file_info, dict) or not file_info.get("file_path"):
        raise RuntimeError("Telegram did not return a voice file path")
    file_url = (
        f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/"
        f"{file_info['file_path']}"
    )
    response = await _client.get(file_url)
    response.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as audio_file:
        audio_file.write(response.content)
        path = audio_file.name
    try:
        return await asyncio.to_thread(_transcribe_voice_file, path)
    finally:
        Path(path).unlink(missing_ok=True)


async def _handle_message(message: dict[str, Any]) -> None:
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        logger.warning("Ignoring Telegram message without chat id")
        return

    text = message.get("text")
    if isinstance(text, str):
        words = text.strip().split(maxsplit=1)
        command = words[0].split("@", maxsplit=1)[0] if words else ""
        if command == "/start":
            response = await _client.post(
                f"{CORE_URL}/admin/scenario",
                json={"scenario": "onboard", "customer_ref": str(chat_id)},
            )
            response.raise_for_status()
            await send_message(
                chat_id,
                "Hi! I'm Mitra, your Paytm support teammate. Tell me about any payment issue, in any language.",
            )
            return
        await send_typing(chat_id)
        await send_message(chat_id, _acknowledgement(text))
        await _dispatch_inbound(chat_id, text, language_hint=None)
        return

    voice = message.get("voice")
    if not isinstance(voice, dict) or os.getenv("ENABLE_VOICE") != "1":
        return
    try:
        await send_typing(chat_id)
        transcript, language_hint = await _transcribe_voice(str(voice["file_id"]))
    except Exception:
        logger.exception("Could not transcribe Telegram voice message")
        await send_message(chat_id, "Please send your message as text instead.")
        return
    await send_message(chat_id, _acknowledgement(transcript))
    await _dispatch_inbound(chat_id, transcript, language_hint=language_hint)


async def _handle_callback(callback: dict[str, Any]) -> None:
    data = callback.get("data")
    message = callback.get("message", {})
    callback_chat_id = message.get("chat", {}).get("id")
    try:
        desk_chat_id = int(DESK_CHAT_ID)
        if int(callback_chat_id) != desk_chat_id:
            logger.warning("Ignoring desk decision from an unauthorized chat")
            return
    except (TypeError, ValueError):
        logger.warning("Ignoring desk decision with an invalid chat id")
        return

    if not isinstance(data, str):
        return
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "d" or parts[2] not in {"a", "r"}:
        return
    case_id = parts[1]
    approved = parts[2] == "a"
    user = callback.get("from", {})
    user_id = user.get("id", "unknown")
    username = user.get("username") or user.get("first_name") or str(user_id)

    await _telegram_api(
        "answerCallbackQuery",
        {"callback_query_id": callback.get("id")},
    )
    await _dispatch_decision(case_id, approved, by=f"tg:{user_id}")
    verb = "Approved" if approved else "Rejected"
    icon = "✅" if approved else "❌"
    await mark_desk_decision(case_id, f"{icon} {verb} by @{username}")


async def _process_update(update: dict[str, Any]) -> None:
    message = update.get("message")
    if isinstance(message, dict):
        await _handle_message(message)
        return
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        await _handle_callback(callback)


async def run_poller() -> None:
    offset: int | None = None
    try:
        await _telegram_api("deleteWebhook", {"drop_pending_updates": True})
    except Exception:
        logger.exception("Could not delete the Telegram webhook")
        await asyncio.sleep(2)

    while True:
        try:
            updates = await _telegram_api(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": ["message", "callback_query"],
                },
                timeout=40.0,
            )
        except Exception:
            logger.exception("Telegram getUpdates failed")
            await asyncio.sleep(2)
            continue

        if not isinstance(updates, list):
            logger.error("Telegram getUpdates returned a non-list result")
            await asyncio.sleep(2)
            continue
        for update in updates:
            try:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                await _process_update(update)
            except httpx.HTTPError:
                logger.exception("Network error while processing Telegram update")
                await asyncio.sleep(2)
            except Exception:
                logger.exception("Could not process Telegram update")


def start_poller_task() -> asyncio.Task[None]:
    return asyncio.create_task(run_poller())


async def _main() -> None:
    global _USE_LOGGING_STUBS
    _USE_LOGGING_STUBS = True
    try:
        await run_poller()
    finally:
        await _client.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
