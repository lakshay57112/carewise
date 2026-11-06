import asyncio
import datetime as dt
import io
import wave
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import config, reminders, store, telegram
from app.audio import wav_to_mp3
from app.gnani import gnani
from app.main import app
from app.state import LINK_CODES, SESSIONS

SAMPLE = (Path(__file__).resolve().parent.parent / "app" / "samples" / "discharge_sample.txt").read_text()
IST = ZoneInfo("Asia/Kolkata")


def at(h, m):
    return dt.datetime(2026, 11, 6, h, m, tzinfo=IST)


def tiny_wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x01" * 24000)  # one second
    return buf.getvalue()


class FakeTelegram:
    """Stands in for the Telegram API and records what would have been sent."""

    def __init__(self, monkeypatch):
        self.texts, self.voices, self.answers, self.cleared = [], [], [], []
        monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "test-token")
        monkeypatch.setattr(config, "DATA_DIR", str(Path("/tmp") / "carewise-test-data"))

        async def username():
            return "carewise_test_bot"

        async def send_text(chat_id, text, buttons=None):
            self.texts.append((chat_id, text))

        async def send_voice(chat_id, audio, caption, buttons=None):
            self.voices.append((chat_id, audio, caption, buttons))

        async def answer(cb_id, text):
            self.answers.append(text)

        async def clear(chat_id, message_id):
            self.cleared.append((chat_id, message_id))

        monkeypatch.setattr(telegram, "bot_username", username)
        monkeypatch.setattr(telegram, "send_text", send_text)
        monkeypatch.setattr(telegram, "send_voice", send_voice)
        monkeypatch.setattr(telegram, "answer_callback", answer)
        monkeypatch.setattr(telegram, "clear_buttons", clear)


@pytest.fixture
def tg(monkeypatch):
    SESSIONS.clear()
    LINK_CODES.clear()
    return FakeTelegram(monkeypatch)


def make_session(client):
    return client.post("/api/document", data={"text": SAMPLE}).json()["session_id"]


def link(client, sid, chat_id=555):
    code = client.post("/api/telegram/link", json={"session_id": sid, "language": "en-IN"}).json()["link"].split("start=")[1]
    asyncio.run(reminders.handle_update({"message": {"chat": {"id": chat_id}, "text": f"/start {code}"}}))
    return code


def test_wav_converts_to_mp3():
    mp3 = wav_to_mp3(tiny_wav())
    assert mp3 and len(mp3) > 100
    assert wav_to_mp3(b"not audio") is None


def test_link_flow_and_status(tg):
    c = TestClient(app)
    sid = make_session(c)
    r = c.post("/api/telegram/link", json={"session_id": sid})
    assert r.status_code == 200 and r.json()["link"].startswith("https://t.me/carewise_test_bot?start=")
    assert c.get("/api/telegram/status", params={"session_id": sid}).json()["linked"] is False
    link(c, sid)
    status = c.get("/api/telegram/status", params={"session_id": sid}).json()
    assert status["linked"] and status["enabled"] and status["times"] == ["07:30", "08:00", "21:00"]
    assert any("Connected" in t for _, t in tg.texts)


def test_link_code_is_single_use_and_unknown_code_is_refused(tg):
    c = TestClient(app)
    sid = make_session(c)
    code = link(c, sid)
    asyncio.run(reminders.handle_update({"message": {"chat": {"id": 999}, "text": f"/start {code}"}}))
    assert not reminders.is_linked(SESSIONS[sid]) or SESSIONS[sid]["telegram"]["chat_id"] == 555
    asyncio.run(reminders.handle_update({"message": {"chat": {"id": 999}, "text": "/start nope"}}))
    assert "Connect Telegram" in tg.texts[-1][1]


def test_link_blocked_until_flags_confirmed(tg):
    c = TestClient(app)
    messy = SAMPLE.replace("Tab Metformin 500 mg - one tablet twice daily after meals (morning and night) - 30 days",
                           "Tab Metformin 500 mg - after meals")
    sid = c.post("/api/document", data={"text": messy}).json()["session_id"]
    assert c.post("/api/telegram/link", json={"session_id": sid}).status_code == 409


def test_due_slots_window_and_once_only(tg):
    c = TestClient(app)
    sid = make_session(c)
    link(c, sid)
    s = SESSIONS[sid]
    s["sent"] = []  # pretend nothing has gone out yet today
    s["day"] = "2026-11-06"
    assert [x["time"] for x in reminders.due_slots(s, at(8, 3))] == ["08:00"]      # 07:30 is 33 min ago: too late
    assert [x["time"] for x in reminders.due_slots(s, at(7, 31))] == ["07:30"]
    assert reminders.due_slots(s, at(12, 0)) == []                                  # nothing at noon
    s["sent"].append("morning")
    assert reminders.due_slots(s, at(8, 3)) == []                                   # already sent
    assert [x["time"] for x in reminders.due_slots(s, at(21, 0))] == ["21:00"]


def test_new_day_resets_taken_and_sent(tg):
    c = TestClient(app)
    sid = make_session(c)
    link(c, sid)
    s = SESSIONS[sid]
    s.update(day="2026-11-05", sent=["morning"], adherence={"morning": True}, alert="fever")
    reminders.due_slots(s, at(6, 0))
    assert s["sent"] == [] and s["adherence"] == {} and s["alert"] == ""


def test_linking_late_does_not_replay_the_morning(tg, monkeypatch):
    c = TestClient(app)
    sid = make_session(c)
    monkeypatch.setattr(reminders, "now_local", lambda: at(9, 0))
    link(c, sid)
    assert set(SESSIONS[sid]["sent"]) == {"pre", "morning"}
    assert [x["time"] for x in reminders.due_slots(SESSIONS[sid], at(21, 2))] == ["21:00"]


def test_tick_sends_a_voice_message_with_a_taken_button(tg, monkeypatch):
    monkeypatch.setattr(config, "GNANI_API_KEY", "test-key")

    async def fake_tts(text, lang="hi-IN"):
        fake_tts.heard = (text, lang)
        return tiny_wav()

    monkeypatch.setattr(gnani, "synthesize", fake_tts)
    c = TestClient(app)
    sid = make_session(c)
    monkeypatch.setattr(reminders, "now_local", lambda: at(6, 0))
    link(c, sid)
    SESSIONS[sid]["sent"] = []
    assert asyncio.run(reminders.tick(at(21, 1))) == 1
    chat, audio, caption, buttons = tg.voices[0]
    assert chat == 555
    assert audio[:3] == b"ID3" or audio[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")  # starts like an MP3
    assert "night medicine" in caption and "Metformin 500 mg" in caption
    assert buttons[0][0]["callback_data"] == f"taken:{sid}:night"
    assert fake_tts.heard[1] == "en-IN"
    assert asyncio.run(reminders.tick(at(21, 2))) == 0  # once only


def test_voice_falls_back_to_text_when_gnani_is_down(tg, monkeypatch):
    from app.gnani import GnaniError
    monkeypatch.setattr(config, "GNANI_API_KEY", "test-key")

    async def broken(text, lang="hi-IN"):
        raise GnaniError("no credit")

    monkeypatch.setattr(gnani, "synthesize", broken)
    c = TestClient(app)
    sid = make_session(c)
    link(c, sid)
    SESSIONS[sid]["sent"] = []
    assert asyncio.run(reminders.tick(at(8, 1))) == 1
    assert tg.voices == [] and "morning medicine" in tg.texts[-1][1]


def test_taken_button_marks_dose_and_only_for_the_linked_chat(tg):
    c = TestClient(app)
    sid = make_session(c)
    link(c, sid, chat_id=555)
    cb = lambda chat: {"callback_query": {"id": "1", "data": f"taken:{sid}:morning", "message": {"chat": {"id": chat}, "message_id": 7}}}
    asyncio.run(reminders.handle_update(cb(999)))           # a stranger's chat
    assert SESSIONS[sid]["adherence"].get("morning") is None
    asyncio.run(reminders.handle_update(cb(555)))           # the linked chat
    assert SESSIONS[sid]["adherence"]["morning"] is True
    assert tg.answers[-1] == "Marked as taken." and tg.cleared == [(555, 7)]
    assert c.get("/api/state", params={"session_id": sid}).json()["adherence"]["morning"] is True


def test_typing_to_the_bot_uses_the_same_agents(tg):
    c = TestClient(app)
    sid = make_session(c)
    link(c, sid)
    asyncio.run(reminders.handle_update({"message": {"chat": {"id": 555}, "text": "what should i eat"}}))
    assert "Diet" in tg.texts[-1][1]
    asyncio.run(reminders.handle_update({"message": {"chat": {"id": 555}, "text": "/stop"}}))
    assert SESSIONS[sid]["telegram"]["enabled"] is False
    assert reminders.due_slots(SESSIONS[sid], at(8, 1)) == []


def test_reminders_survive_a_restart(tg):
    c = TestClient(app)
    sid = make_session(c)
    link(c, sid)
    store.save(SESSIONS)
    SESSIONS.clear()
    assert store.load_into(SESSIONS) == 1
    assert SESSIONS[sid]["telegram"]["chat_id"] == 555 and len(SESSIONS[sid]["schedule"]) == 3


def test_test_endpoint_and_settings(tg):
    c = TestClient(app)
    sid = make_session(c)
    assert c.post("/api/telegram/test", json={"session_id": sid}).status_code == 409  # not linked yet
    link(c, sid)
    r = c.post("/api/telegram/test", json={"session_id": sid, "language": "hi-IN"})
    assert r.status_code == 200 and "टेस्ट" in tg.texts[-1][1]
    assert c.post("/api/telegram/settings", json={"session_id": sid, "enabled": False}).json()["enabled"] is False


def test_reminder_text_in_hindi_and_english():
    from app.agents import explainer
    slot = {"key": "afternoon", "label": "Afternoon", "time": "14:00",
            "items": [{"medicine": "Metformin", "dose": "500 mg", "note": "after food"}]}
    en, hi = explainer.compose("reminder", {"slot": slot})
    assert en == "Time to take your afternoon medicine: Metformin 500 mg after food."
    assert hi == "दोपहर की दवा लेने का समय हो गया है: Metformin 500 mg खाने के बाद।"
