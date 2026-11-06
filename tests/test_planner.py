import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, llm, orchestrator, planner
from app.agents import document, schedule
from app.main import app
from app.state import SESSIONS
from app.trace import Trace

SAMPLE = (Path(__file__).resolve().parent.parent / "app" / "samples" / "discharge_sample.txt").read_text()


def new_session(pending=False):
    data = document.parse_rules(SAMPLE)
    return {"sid": "t1", "data": data, "schedule": schedule.build(data), "adherence": {}, "alert": "",
            "pending": ["x"] if pending else [], "flags": [], "stats": {}}


class FakeModel:
    """Stands in for Gemini: returns one canned plan, and records the prompt it was shown."""

    def __init__(self, monkeypatch, plan=None, fail=False):
        self.prompt = ""
        monkeypatch.setattr(config, "GEMINI_API_KEY", "k")

        async def generate(prompt, json_mode=False, image=None):
            self.prompt = prompt
            if fail:
                raise llm.LLMError("down")
            return json.dumps(plan)

        monkeypatch.setattr(llm, "generate", generate)


def run(session, text, now="09:30", lang="en-IN", **kw):
    trace = Trace()
    return asyncio.run(orchestrator.handle(session, text, lang, now, trace, **kw)), trace


def test_a_two_part_request_becomes_two_steps(monkeypatch):
    FakeModel(monkeypatch, {"english": "I missed my morning medicine and tell my family", "steps": [
        {"tool": "missed_dose", "slot": "morning", "why": "dose was missed"},
        {"tool": "family", "slot": "", "why": "they asked to tell family"}]})
    s = new_session()
    out, trace = run(s, "subah ki dawai chhoot gayi, ghar walon ko bata do")
    assert out["intent"] == "missed_dose+family"
    assert "marked your morning medicines as missed" in out["reply"] and "Update for family" in out["reply"]
    assert s["adherence"]["morning"] is False and s["alert"] == "morning medicines missed"
    agents = [e["agent"] for e in trace.events]
    assert agents[:2] == ["orchestrator", "planner"] and "caregiver" in agents and agents[-1] == "explainer"


def test_missed_dose_never_tells_the_person_to_double_up(monkeypatch):
    FakeModel(monkeypatch, {"english": "x", "steps": [{"tool": "missed_dose", "slot": "", "why": ""}]})
    out, _ = run(new_session(), "i forgot", now="09:00")
    assert "do not double the next dose" in out["reply"] and "ask your doctor or pharmacist" in out["reply"]


def test_nothing_is_missed_before_the_first_dose(monkeypatch):
    FakeModel(monkeypatch, {"english": "x", "steps": [{"tool": "missed_dose", "slot": "", "why": ""}]})
    s = new_session()
    out, _ = run(s, "i forgot", now="06:00")
    assert "nothing is missed" in out["reply"] and s["adherence"] == {}


def test_plan_is_cleaned(monkeypatch):
    s = new_session()
    plan = planner.clean({"english": "e", "steps": [
        {"tool": "delete_everything", "slot": ""},                 # not on the menu
        {"tool": "took_dose", "slot": "moonrise"},                 # a slot that does not exist
        {"tool": "took_dose", "slot": "moonrise"},                 # a repeat
        {"tool": "diet"}, {"tool": "family"}, {"tool": "explain"},  # past the cap of three
    ]}, s)
    assert [x["intent"] for x in plan["steps"]] == ["took_dose", "diet", "family"]
    assert plan["steps"][0]["slot"] == ""
    with pytest.raises(llm.LLMError):
        planner.clean({"steps": [{"tool": "nope"}]}, s)


def test_the_model_sees_the_day_but_not_the_documents_text(monkeypatch):
    fm = FakeModel(monkeypatch, {"english": "x", "steps": [{"tool": "next_dose"}]})
    run(new_session(), "agli dawai?")
    assert "morning 08:00 (not taken)" in fm.prompt and "Ramesh" not in fm.prompt and "Metformin" not in fm.prompt


@pytest.mark.parametrize("fail", [True, False])
def test_bad_model_falls_back_to_keywords(monkeypatch, fail):
    FakeModel(monkeypatch, {"steps": [{"tool": "nonsense"}]}, fail=fail)
    out, trace = run(new_session(), "what should i eat")
    assert out["intent"] == "diet" and "Diet" in out["reply"]
    assert not any(e["agent"] == "planner" for e in trace.events)


def test_chips_skip_the_planner(monkeypatch):
    fm = FakeModel(monkeypatch, {"english": "x", "steps": [{"tool": "family"}]})
    out, _ = run(new_session(), "What should I eat?", forced="diet")
    assert out["intent"] == "diet" and fm.prompt == ""


def test_planner_can_be_switched_off(monkeypatch):
    fm = FakeModel(monkeypatch, {"english": "x", "steps": [{"tool": "family"}]})
    monkeypatch.setattr(config, "PLANNER", False)
    out, _ = run(new_session(), "what should i eat")
    assert out["intent"] == "diet" and fm.prompt == ""


def test_no_key_means_keyword_routing(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    out, trace = run(new_session(), "agli dawai kaun si hai")
    assert out["intent"] == "next_dose" and not any(e["agent"] == "planner" for e in trace.events)


def test_the_verifier_gate_still_holds_a_whole_plan(monkeypatch):
    FakeModel(monkeypatch, {"english": "x", "steps": [{"tool": "took_dose"}, {"tool": "family"}]})
    s = new_session(pending=True)
    out, trace = run(s, "took it, tell family")
    assert out["intent"] == "gated" and s["adherence"] == {}
    assert any(e["agent"] == "verifier" for e in trace.events)


def test_symptoms_use_the_models_english_gist(monkeypatch):
    FakeModel(monkeypatch, {"english": "I have a high fever and I am breathless", "steps": [{"tool": "symptoms"}]})
    s = new_session()
    out, _ = run(s, "মোর জ্বর আর শ্বাসকষ্ট")  # Bengali: the keyword lists don't cover it
    assert out["alert"] is True and s["alert"]
    assert "warning sign" in out["reply"]


def test_took_dose_with_a_named_slot(monkeypatch):
    FakeModel(monkeypatch, {"english": "x", "steps": [{"tool": "took_dose", "slot": "night"}]})
    s = new_session()
    run(s, "raat ki le li", now="09:00")
    assert s["adherence"] == {"night": True}


def test_keyword_router_knows_missed_doses():
    assert orchestrator.classify("i forgot my medicine") == "missed_dose"
    assert orchestrator.classify("dawai chhoot gayi") == "missed_dose"


def test_ask_endpoint_shows_the_plan_in_the_trace(monkeypatch):
    FakeModel(monkeypatch, {"english": "x", "steps": [{"tool": "diet", "why": "asked about food"}]})
    SESSIONS.clear()
    c = TestClient(app)
    sid = c.post("/api/document", data={"text": SAMPLE}).json()["session_id"]
    r = c.post("/api/ask", data={"session_id": sid, "language": "en-IN", "text": "khane mein kya lun", "speak": "false"}).json()
    assert r["intent"] == "diet"
    assert [e["agent"] for e in r["trace"]][:2] == ["orchestrator", "planner"]
