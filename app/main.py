"""Carewise API. Run with: uvicorn app.main:app --reload"""
import base64
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, llm, orchestrator
from .agents import document, schedule, verifier
from .gnani import GnaniError, gnani
from .trace import Trace

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SAMPLE = ROOT / "app" / "samples" / "discharge_sample.txt"

app = FastAPI(title="Carewise")
SESSIONS: dict[str, dict] = {}  # in memory: fine for a demo, swap for a database later


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
        "data": data, "source": source, "flags": flags, "stats": stats,
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
    return {"adherence": session["adherence"]}


@app.post("/api/ask")
async def ask(
    session_id: str = Form(...),
    language: str = Form("hi-IN"),
    now: str = Form("09:00"),
    text: str = Form(""),
    audio: UploadFile | None = File(None),
    speak: bool = Form(True),
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

    result = await orchestrator.handle(session, transcript, language, now, trace)

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


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
