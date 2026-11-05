import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.agents import checkin, document, schedule, verifier
from app.main import app
from app.orchestrator import classify
from app.trace import Trace

SAMPLE = (Path(__file__).resolve().parent.parent / "app" / "samples" / "discharge_sample.txt").read_text()


def read():
    return document.parse_rules(SAMPLE)


def test_reads_four_medicines():
    d = read()
    assert [m.name for m in d.medicines] == ["Metformin", "Amlodipine", "Cefixime", "Pantoprazole"]
    assert d.medicines[0].times_per_day == 2 and d.medicines[0].food == "after"
    assert d.medicines[3].food == "before" and d.medicines[3].duration_days == 10
    assert d.follow_up.date == "11-Nov-2026"
    assert "fever above 101 F" in d.warning_signs
    assert "breathlessness" in d.warning_signs


def test_verifier_passes_clean_extraction():
    flags, stats = verifier.run(read(), SAMPLE)
    assert flags == [] and stats["confidence"] == 1.0


def test_verifier_catches_invented_dose():
    d = read()
    d.medicines[0].dose = "850 mg"  # not in the document
    flags, _ = verifier.run(d, SAMPLE)
    assert any(f.field == "dose" and "Metformin" in f.value for f in flags)


def test_schedule_slots():
    sched = schedule.build(read())
    assert [s["time"] for s in sched] == ["07:30", "08:00", "21:00"]
    assert [i["medicine"] for i in sched[0]["items"]] == ["Pantoprazole"]
    slot, tomorrow = schedule.next_slot(sched, "09:00")
    assert slot["time"] == "21:00" and not tomorrow
    slot, tomorrow = schedule.next_slot(sched, "22:30")
    assert slot["time"] == "07:30" and tomorrow


def test_checkin_matches_and_respects_numbers():
    signs = read().warning_signs
    assert any("fever" in s for s in checkin.evaluate("mujhe bukhar hai", signs))
    assert checkin.evaluate("fever 99", signs) == []  # below the doctor's limit
    assert any("fever" in s for s in checkin.evaluate("fever 103", signs))
    assert checkin.evaluate("my knee hurts", signs) == []


def test_intent_routing():
    assert classify("kal kaun si goli leni hai?") == "next_dose"
    assert classify("explain my summary") == "explain"
    assert classify("मुझे बुखार है") == "symptoms"
    assert classify("dawai le li") == "took_dose"
    assert classify("insurance ne claim reject kar diya") == "insurance"
    assert classify("update my family") == "family"
    assert classify("hello") == "unknown"


def test_full_flow_through_api():
    c = TestClient(app)
    r = c.post("/api/document", data={"text": SAMPLE})
    assert r.status_code == 200
    body = r.json()
    sid = body["session_id"]
    assert body["pending"] == [] and len(body["schedule"]) == 3
    assert [e["agent"] for e in body["trace"]][:3] == ["document", "verifier", "schedule"]

    r = c.post("/api/ask", data={"session_id": sid, "text": "kal kaun si goli leni hai", "language": "en-IN", "now": "09:00"})
    assert r.status_code == 200 and "21:00" in r.json()["reply"]

    r = c.post("/api/ask", data={"session_id": sid, "text": "mujhe bukhar hai", "language": "hi-IN", "now": "09:00"})
    assert r.json()["alert"] is True

    r = c.post("/api/ask", data={"session_id": sid, "text": "update my family", "language": "en-IN", "now": "09:00"})
    assert "Warning" in r.json()["reply"]


def test_gate_blocks_schedule_until_confirmed():
    c = TestClient(app)
    messy = SAMPLE.replace("Tab Metformin 500 mg - one tablet twice daily after meals (morning and night) - 30 days",
                           "Tab Metformin 500 mg - after meals")
    sid = c.post("/api/document", data={"text": messy}).json()
    assert sid["pending"], "an unclear frequency should be flagged"
    r = c.post("/api/ask", data={"session_id": sid["session_id"], "text": "next medicine", "language": "en-IN"})
    assert r.json()["intent"] == "gated"
    c.post("/api/confirm", json={"session_id": sid["session_id"]})
    r = c.post("/api/ask", data={"session_id": sid["session_id"], "text": "next medicine", "language": "en-IN"})
    assert r.json()["intent"] == "next_dose"


def test_quick_question_works_in_any_language():
    c = TestClient(app)
    sid = c.post("/api/document", data={"text": SAMPLE}).json()["session_id"]
    # A Tamil chip: no keyword in the text could match, so the intent has to be sent directly.
    r = c.post("/api/ask", data={"session_id": sid, "text": "எனக்கு காய்ச்சல் உள்ளது", "language": "ta-IN",
                                  "now": "09:00", "intent": "symptoms", "hint": "I have a fever"})
    body = r.json()
    assert body["intent"] == "symptoms" and body["alert"] is True
    # Without the hint or a model, the same Tamil text is not guessed at: it shows the help menu.
    r = c.post("/api/ask", data={"session_id": sid, "text": "எனக்கு காய்ச்சல் உள்ளது", "language": "en-IN", "now": "09:00"})
    assert r.json()["intent"] == "unknown"
