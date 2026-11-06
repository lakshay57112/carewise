"""Reminders agent: watches the clock and reminds the person at each dose time.

Two channels, either or both: a Telegram voice message, and a notification on the phone itself.
Two loops run in the background while the server is up:
  poll_loop       listens to the bot (a person linking their chat, tapping Taken, or typing a question)
  scheduler_loop  every 20 seconds, sends the reminder for any dose that is due
"""
import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

import json

from . import config, orchestrator, push, store, telegram
from .agents import explainer
from .audio import wav_to_mp3
from .gnani import GnaniError, gnani
from .state import LINK_CODES, SESSIONS
from .trace import Trace

log = logging.getLogger("carewise.reminders")
GRACE_MIN = 10  # a reminder still goes out if the server was busy or just woke up


def now_local() -> dt.datetime:
    return dt.datetime.now(ZoneInfo(config.TIMEZONE))


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def is_linked(session: dict) -> bool:
    return bool((session.get("telegram") or {}).get("chat_id"))


def tg_active(session: dict) -> bool:
    tg = session.get("telegram") or {}
    return bool(tg.get("chat_id") and tg.get("enabled"))


def push_active(session: dict) -> bool:
    p = session.get("push") or {}
    return bool(p.get("subs") and p.get("enabled"))


def _skip_past(session: dict, now: dt.datetime) -> None:
    """Right after connecting, only doses still ahead fire; don't replay the morning's ones."""
    mins = now.hour * 60 + now.minute
    _new_day(session, now)
    session["sent"] = sorted(set(session.get("sent", [])) | {s["key"] for s in session["schedule"] if _minutes(s["time"]) <= mins})


def _new_day(session: dict, now: dt.datetime) -> None:
    day = now.strftime("%Y-%m-%d")
    if session.get("day") != day:
        session.update(day=day, adherence={}, sent=[], alert="")


def due_slots(session: dict, now: dt.datetime) -> list[dict]:
    if not (tg_active(session) or push_active(session)):
        return []
    _new_day(session, now)
    mins = now.hour * 60 + now.minute
    sent = session.setdefault("sent", [])
    return [s for s in session["schedule"] if s["key"] not in sent and 0 <= mins - _minutes(s["time"]) <= GRACE_MIN]


async def send_telegram(session: dict, slot: dict, test: bool = False) -> dict:
    tg = session["telegram"]
    lang = tg.get("language", "hi-IN")
    en, hi = explainer.compose("reminder", {"slot": slot, "test": test})
    text, _ = await explainer.localize(en, hi, lang)
    buttons = [[{"text": "Taken", "callback_data": f"taken:{session['sid']}:{slot['key']}"}]]

    mp3 = None
    if gnani.enabled:
        try:
            mp3 = wav_to_mp3(await gnani.synthesize(text, lang))
        except GnaniError as exc:
            log.warning("voice failed, sending text instead: %s", exc)
    if mp3:
        try:
            await telegram.send_voice(tg["chat_id"], mp3, text, buttons)
            return {"voice": True, "text": text}
        except telegram.TelegramError as exc:
            log.warning("voice message refused, sending text instead: %s", exc)
    await telegram.send_text(tg["chat_id"], text, buttons)
    return {"voice": False, "text": text}


async def reminder_text(session: dict, slot: dict, lang: str, test: bool = False) -> str:
    en, hi = explainer.compose("reminder", {"slot": slot, "test": test})
    return (await explainer.localize(en, hi, lang))[0]


async def send_push(session: dict, slot: dict, test: bool = False) -> dict:
    """Notifies every phone subscribed for this person. Returns how many got it."""
    p = session["push"]
    lang = p.get("language", "hi-IN")
    text = await reminder_text(session, slot, lang, test)
    payload = json.dumps({
        "title": "Carewise", "body": text, "tag": f"dose-{slot['key']}", "sid": session["sid"],
        "slot": slot["key"], "lang": lang, "test": test,
    }, ensure_ascii=False)
    delivered, last_error = 0, None
    for sub in list(p["subs"]):
        try:
            await push.send(sub, payload)
            delivered += 1
        except push.PushGone:
            p["subs"].remove(sub)  # the phone cancelled; stop trying it
            store.save(SESSIONS)
        except push.PushError as exc:
            last_error = exc
            log.warning("push failed: %s", exc)
    if not delivered and last_error:
        raise last_error
    return {"delivered": delivered, "text": text}


async def send_reminder(session: dict, slot: dict, test: bool = False, channels=("telegram", "push")) -> dict:
    """Sends one reminder on every channel that is switched on. Raises only if nothing got through."""
    out, errors = {}, []
    tg_ok = tg_active(session) or (test and (session.get("telegram") or {}).get("chat_id"))
    push_ok = push_active(session) or (test and (session.get("push") or {}).get("subs"))
    if "telegram" in channels and tg_ok:
        try:
            out.update(await send_telegram(session, slot, test))
        except Exception as exc:
            errors.append(exc)
    if "push" in channels and push_ok:
        try:
            out["pushed"] = (await send_push(session, slot, test))["delivered"]
        except Exception as exc:
            errors.append(exc)
    if not out and errors:
        raise errors[0]
    return out


async def tick(now: dt.datetime | None = None) -> int:
    """Sends everything that is due right now. Returns how many reminders went out."""
    now = now or now_local()
    count = 0
    for session in list(SESSIONS.values()):
        for slot in due_slots(session, now):
            try:
                await send_reminder(session, slot)
            except Exception as exc:  # one person's failure must not stop the others
                log.warning("reminder for %s failed: %s", session.get("sid"), exc)
                continue
            session["sent"].append(slot["key"])
            count += 1
    if count:
        store.save(SESSIONS)
    return count


async def scheduler_loop() -> None:
    while True:
        try:
            await tick()
        except Exception as exc:
            log.warning("scheduler error: %s", exc)
        await asyncio.sleep(20)


# ---------- listening to the bot ----------

def session_for_chat(chat_id: int) -> dict | None:
    return next((s for s in SESSIONS.values() if (s.get("telegram") or {}).get("chat_id") == chat_id), None)


async def _link(chat_id: int, code: str) -> None:
    sid = LINK_CODES.pop(code, None) if code else None
    session = SESSIONS.get(sid) if sid else None
    if not session:
        await telegram.send_text(chat_id, "Open Carewise, read a summary, then tap Connect Telegram to link this chat.")
        return
    now = now_local()
    tg = session.setdefault("telegram", {})
    tg.update(chat_id=chat_id, enabled=True)
    tg.setdefault("language", "hi-IN")
    session.update(day=now.strftime("%Y-%m-%d"), adherence={}, alert="", sent=[])
    _skip_past(session, now)
    store.save(SESSIONS)
    en, hi = explainer.compose("linked", {"times": [s["time"] for s in session["schedule"]]})
    text, _ = await explainer.localize(en, hi, tg["language"])
    await telegram.send_text(chat_id, text)


async def _on_callback(cb: dict) -> None:
    parts = (cb.get("data") or "").split(":")
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if len(parts) == 3 and parts[0] == "taken":
        session = SESSIONS.get(parts[1])
        # Only the chat that was linked may tick a dose off.
        if session and (session.get("telegram") or {}).get("chat_id") == chat_id:
            session["adherence"][parts[2]] = True
            store.save(SESSIONS)
            await telegram.answer_callback(cb["id"], "Marked as taken.")
            try:
                await telegram.clear_buttons(chat_id, msg["message_id"])
            except (telegram.TelegramError, KeyError):
                pass
            return
    await telegram.answer_callback(cb["id"], "That reminder is no longer active.")


async def handle_update(update: dict) -> None:
    if "callback_query" in update:
        return await _on_callback(update["callback_query"])
    msg = update.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return
    if text.startswith("/start"):
        bits = text.split(maxsplit=1)
        return await _link(chat_id, bits[1].strip() if len(bits) > 1 else "")
    session = session_for_chat(chat_id)
    if not session:
        return await telegram.send_text(chat_id, "Open Carewise and tap Connect Telegram first.")
    if text.startswith("/stop"):
        session["telegram"]["enabled"] = False
        store.save(SESSIONS)
        return await telegram.send_text(chat_id, "Reminders are off. Turn them back on in Carewise.")
    # Anything else is a question, answered by the same agents as on the website.
    lang = session["telegram"].get("language", "hi-IN")
    result = await orchestrator.handle(session, text, lang, now_local().strftime("%H:%M"), Trace())
    store.save(SESSIONS)
    await telegram.send_text(chat_id, result["reply"])


async def poll_loop() -> None:
    offset = 0
    try:
        await telegram.call("deleteWebhook")  # polling and webhooks can't be used together
    except telegram.TelegramError as exc:
        log.warning("could not clear webhook: %s", exc)
    while True:
        try:
            updates = await telegram.call(
                "getUpdates", {"offset": offset, "timeout": 25, "allowed_updates": '["message","callback_query"]'},
                timeout=40)
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    await handle_update(u)
                except Exception as exc:
                    log.warning("could not handle update: %s", exc)
        except telegram.TelegramError as exc:
            log.warning("telegram: %s", exc)  # e.g. 409 if the same bot is polled from two places
            await asyncio.sleep(10)
        except Exception as exc:
            log.warning("telegram poll error: %s", exc)
            await asyncio.sleep(3)
