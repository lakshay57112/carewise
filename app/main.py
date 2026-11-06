"""Carewise API. Run with: uvicorn app.main:app --reload"""
import asyncio
import base64
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, llm, orchestrator, push, reminders, store, telegram
from .agents import document, schedule, verifier
from .gnani import GnaniError, gnani
from .state import LINK_CODES, SESSIONS
from .trace import Trace

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SAMPLE = ROOT / "app" / "samples" / "discharge_sample.txt"

@asynccontextmanager
async def lifespan(_: FastAPI):
    tasks: list[asyncio.Task] = []
    if telegram.enabled() or push.enabled():  # reminders only run when a channel is set up
        store.load_into(SESSIONS)
        tasks = [asyncio.create_task(reminders.scheduler_loop())]
        if telegram.enabled():
            tasks.append(asyncio.create_task(reminders.poll_loop()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Carewise", lifespan=lifespan)


def public(sid: str, session: dict) -> dict:
    return {
        "session_id": sid,
        "data": session["data"].model_dump(),
        "flags": [f.model_dump() for f in session["flags"]],
        "pending": session["pending"],
        "stats": session["stats"],
        "schedule": session["schedule"],
        "adherence": session["adherence"],
        "alert": session["alert"],
    }


def get_session(sid: str) -> dict:
    session = SESSIONS.get(sid)
    if not session:
        raise HTTPException(404, "Session not found. Read a summary first.")
    return session


@app.get("/api/health")
async def health():
    return {
        "voice": gnani.enabled,
        "telegram": telegram.enabled(),
        "push": push.enabled(),
        "reader": "model" if llm.enabled() else "basic",
        "languages": config.LANGUAGES,
        "tts_model": config.TTS_MODEL,
        "stt_model": config.STT_MODEL,
    }


@app.get("/api/sample")
async def sample():
    return {"text": SAMPLE.read_text()}


@app.post("/api/document")
async def read_document(text: str = Form(""), image: UploadFile | None = File(None)):
    trace = Trace()
    img = None
    if image is not None and image.filename:
        img = (image.content_type or "image/jpeg", await image.read())
    try:
        data, source = await document.run(trace, text=text, image=img)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except llm.LLMError as exc:
        raise HTTPException(502, str(exc)) from exc

    with trace.step("verifier", "Checking every item against the original") as s:
        flags, stats = verifier.run(data, source)
        s.detail = f"{stats['passed']}/{stats['checks']} checks passed, {len(flags)} to confirm"
    with trace.step("schedule", "Building the daily timetable") as s:
        sched = schedule.build(data)
        s.detail = f"{len(sched)} time slots"

    sid = uuid.uuid4().hex[:12]
    session = {
        "sid": sid, "data": data, "source": source, "flags": flags, "stats": stats,
        "pending": [f.id for f in flags], "schedule": sched, "adherence": {}, "alert": "",
    }
    SESSIONS[sid] = session
    return {**public(sid, session), "trace": trace.events}


class Confirm(BaseModel):
    session_id: str
    flag_ids: list[str] | None = None  # None means confirm everything


@app.post("/api/confirm")
async def confirm(body: Confirm):
    session = get_session(body.session_id)
    ids = set(body.flag_ids) if body.flag_ids is not None else set(session["pending"])
    session["pending"] = [i for i in session["pending"] if i not in ids]
    return public(body.session_id, session)


class Dose(BaseModel):
    session_id: str
    slot_key: str
    taken: bool


@app.post("/api/dose")
async def log_dose(body: Dose):
    session = get_session(body.session_id)
    session["adherence"][body.slot_key] = body.taken
    store.save(SESSIONS)
    return {"adherence": session["adherence"]}


@app.post("/api/ask")
async def ask(
    session_id: str = Form(...),
    language: str = Form("hi-IN"),
    now: str = Form("09:00"),
    text: str = Form(""),
    audio: UploadFile | None = File(None),
    speak: bool = Form(True),
    intent: str = Form(""),
    hint: str = Form(""),
):
    session = get_session(session_id)
    trace = Trace()
    transcript = text.strip()

    if audio is not None and audio.filename:
        try:
            with trace.step("listener", "Listening (Gnani Prisma)") as s:
                heard = await gnani.transcribe(await audio.read(), language)
                transcript = heard["transcript"]
                s.detail = f"\"{transcript[:70]}\"" if transcript else "heard nothing"
        except GnaniError as exc:
            return JSONResponse({"error": str(exc), "trace": trace.events}, status_code=502)
    if not transcript:
        return JSONResponse({"error": "I did not catch that. Try again.", "trace": trace.events}, status_code=400)

    result = await orchestrator.handle(session, transcript, language, now, trace, forced=intent, hint=hint)

    audio_b64 = None
    if speak and gnani.enabled:
        try:
            with trace.step("speaker", "Speaking the reply (Gnani Timbre)") as s:
                wav = await gnani.synthesize(result["reply"], language)
                audio_b64 = base64.b64encode(wav).decode()
                s.detail = f"{len(wav) // 1024} KB audio"
        except GnaniError as exc:
            result["voice_error"] = str(exc)

    return {**result, "transcript": transcript, "audio_b64": audio_b64, "trace": trace.events,
            "state": public(session_id, session)}


class Speak(BaseModel):
    text: str
    language: str = "hi-IN"


@app.post("/api/speak")
async def speak(body: Speak):
    try:
        wav = await gnani.synthesize(body.text, body.language)
    except GnaniError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"audio_b64": base64.b64encode(wav).decode()}


@app.get("/api/state")
async def state(session_id: str):
    return public(session_id, get_session(session_id))


# ---------- voice reminders on Telegram ----------

class LinkReq(BaseModel):
    session_id: str
    language: str = "hi-IN"


@app.post("/api/telegram/link")
async def tg_link(body: LinkReq):
    if not telegram.enabled():
        raise HTTPException(400, "Voice reminders are not set up on this server yet (TELEGRAM_BOT_TOKEN).")
    session = get_session(body.session_id)
    if session["pending"]:
        raise HTTPException(409, "Confirm the highlighted items first, so reminders use the right times.")
    code = secrets.token_urlsafe(6)
    LINK_CODES[code] = body.session_id
    session.setdefault("telegram", {})["language"] = body.language
    try:
        username = await telegram.bot_username()
    except telegram.TelegramError as exc:
        raise HTTPException(502, f"Telegram: {exc}") from exc
    return {"link": f"https://t.me/{username}?start={code}", "bot": username}


@app.get("/api/telegram/status")
async def tg_status(session_id: str):
    session = get_session(session_id)
    tg = session.get("telegram") or {}
    return {
        "configured": telegram.enabled(),
        "linked": reminders.is_linked(session),
        "enabled": bool(tg.get("enabled")),
        "language": tg.get("language", "hi-IN"),
        "times": [s["time"] for s in session["schedule"]],
        "blocked": bool(session["pending"]),
    }


class ToggleReq(BaseModel):
    session_id: str
    enabled: bool | None = None
    language: str | None = None


@app.post("/api/telegram/settings")
async def tg_settings(body: ToggleReq):
    session = get_session(body.session_id)
    if not reminders.is_linked(session):
        raise HTTPException(409, "Connect Telegram first.")
    tg = session["telegram"]
    if body.enabled is not None:
        tg["enabled"] = body.enabled
    if body.language:
        tg["language"] = body.language
    store.save(SESSIONS)
    return {"enabled": tg["enabled"], "language": tg["language"]}


@app.post("/api/telegram/test")
async def tg_test(body: ToggleReq):
    session = get_session(body.session_id)
    if not reminders.is_linked(session):
        raise HTTPException(409, "Connect Telegram first.")
    if body.language:
        session["telegram"]["language"] = body.language
    now = reminders.now_local().strftime("%H:%M")
    slot, _ = schedule.next_slot(session["schedule"], now)
    if not slot:
        raise HTTPException(409, "There are no medicines to remind about.")
    try:
        sent = await reminders.send_reminder(session, slot, test=True, channels=("telegram",))
    except telegram.TelegramError as exc:
        raise HTTPException(502, f"Telegram: {exc}") from exc
    return sent


# ---------- notifications on the phone itself (web push) ----------

@app.get("/api/push/config")
async def push_config():
    return {"enabled": push.enabled(), "key": config.VAPID_PUBLIC_KEY if push.enabled() else ""}


class PushSub(BaseModel):
    session_id: str
    subscription: dict
    language: str = "hi-IN"


def _push_state(session: dict) -> dict:
    p = session.get("push") or {}
    return {
        "configured": push.enabled(), "devices": len(p.get("subs", [])), "enabled": bool(p.get("enabled")),
        "language": p.get("language", "hi-IN"), "times": [s["time"] for s in session["schedule"]],
        "blocked": bool(session["pending"]),
    }


@app.post("/api/push/subscribe")
async def push_subscribe(body: PushSub):
    if not push.enabled():
        raise HTTPException(400, "Phone notifications are not set up on this server yet (VAPID keys).")
    session = get_session(body.session_id)
    if session["pending"]:
        raise HTTPException(409, "Confirm the highlighted items first, so reminders use the right times.")
    if not push.valid_subscription(body.subscription):
        raise HTTPException(400, "That phone did not give a usable notification address.")
    p = session.setdefault("push", {})
    subs = [x for x in p.get("subs", []) if x["endpoint"] != body.subscription["endpoint"]]
    subs.append({"endpoint": body.subscription["endpoint"], "keys": body.subscription["keys"]})
    p.update(subs=subs, enabled=True, language=body.language)
    reminders._skip_past(session, reminders.now_local())
    store.save(SESSIONS)
    return _push_state(session)


class PushRef(BaseModel):
    session_id: str
    endpoint: str = ""


@app.post("/api/push/status")
async def push_status(body: PushRef):
    session = get_session(body.session_id)
    here = any(x["endpoint"] == body.endpoint for x in (session.get("push") or {}).get("subs", []))
    return {**_push_state(session), "here": here}


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(body: PushRef):
    session = get_session(body.session_id)
    p = session.setdefault("push", {})
    p["subs"] = [x for x in p.get("subs", []) if body.endpoint and x["endpoint"] != body.endpoint]
    store.save(SESSIONS)
    return _push_state(session)


class PushSettings(BaseModel):
    session_id: str
    language: str


@app.post("/api/push/settings")
async def push_settings(body: PushSettings):
    session = get_session(body.session_id)
    if (session.get("push") or {}).get("subs"):
        session["push"]["language"] = body.language
        store.save(SESSIONS)
    return _push_state(session)


@app.post("/api/push/test")
async def push_test(body: PushSettings):
    session = get_session(body.session_id)
    if not (session.get("push") or {}).get("subs"):
        raise HTTPException(409, "Turn on phone notifications first.")
    session["push"]["language"] = body.language
    slot, _ = schedule.next_slot(session["schedule"], reminders.now_local().strftime("%H:%M"))
    if not slot:
        raise HTTPException(409, "There are no medicines to remind about.")
    try:
        return await reminders.send_reminder(session, slot, test=True, channels=("push",))
    except push.PushError as exc:
        raise HTTPException(502, f"Notification service: {exc}") from exc


class DueReq(BaseModel):
    session_id: str
    slot_key: str
    language: str = "hi-IN"


@app.post("/api/reminder/speak")
async def reminder_speak(body: DueReq):
    """What the page plays when someone taps a notification: the same words, spoken by Timbre."""
    session = get_session(body.session_id)
    slot = next((s for s in session["schedule"] if s["key"] == body.slot_key), None)
    if not slot:
        raise HTTPException(404, "That reminder is no longer on your timetable.")
    text = await reminders.reminder_text(session, slot, body.language)
    audio = None
    if gnani.enabled:
        try:
            audio = base64.b64encode(await gnani.synthesize(text, body.language)).decode()
        except GnaniError:
            pass
    return {"text": text, "label": slot["label"], "audio_b64": audio, "taken": bool(session["adherence"].get(slot["key"]))}


@app.get("/sw.js")
async def service_worker():
    # Served from the root so it can handle notifications for the whole site.
    return FileResponse(WEB / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
