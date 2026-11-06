"""Small Telegram Bot API client: just what the reminders need."""
import json

import httpx

from . import config


class TelegramError(Exception):
    pass


def enabled() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN)


async def call(method: str, data: dict | None = None, files: dict | None = None, timeout: float = 30) -> dict | list:
    url = f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/{method}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, data=data, files=files)
    try:
        body = resp.json()
    except ValueError as exc:
        raise TelegramError(f"Telegram sent a reply I could not read ({resp.status_code}).") from exc
    if not body.get("ok"):
        raise TelegramError(body.get("description", "Telegram refused the request."))
    return body["result"]


_username: str = ""


async def bot_username() -> str:
    global _username
    if not _username:
        _username = (await call("getMe"))["username"]
    return _username


def _markup(buttons: list[list[dict]] | None) -> str | None:
    return json.dumps({"inline_keyboard": buttons}) if buttons else None


async def send_text(chat_id: int, text: str, buttons: list[list[dict]] | None = None) -> dict:
    data: dict = {"chat_id": chat_id, "text": text}
    if buttons:
        data["reply_markup"] = _markup(buttons)
    return await call("sendMessage", data)


async def send_voice(chat_id: int, audio_mp3: bytes, caption: str, buttons: list[list[dict]] | None = None) -> dict:
    data: dict = {"chat_id": chat_id, "caption": caption[:1000]}
    if buttons:
        data["reply_markup"] = _markup(buttons)
    return await call("sendVoice", data, files={"voice": ("reminder.mp3", audio_mp3, "audio/mpeg")}, timeout=60)


async def answer_callback(callback_id: str, text: str) -> None:
    await call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


async def clear_buttons(chat_id: int, message_id: int) -> None:
    await call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                          "reply_markup": json.dumps({"inline_keyboard": []})})
