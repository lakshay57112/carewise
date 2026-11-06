"""Planner: the model decides WHICH agents to run and in what order. It never writes the answer.

It sees the person's message, the time and the day's timetable, and picks up to three steps from a
fixed menu. Every step is then carried out by an ordinary agent and spoken from a fixed template,
so the facts about medicines, doses and warning signs never pass through the model's imagination.
Anything unexpected (a bad tool name, broken JSON) is dropped or raises, and the orchestrator
falls back to keyword routing.
"""
from . import llm

MAX_STEPS = 3

# The menu the model chooses from. Names are the orchestrator's intents.
TOOLS = {
    "next_dose": "Tell the person which medicines are next.",
    "schedule": "Read out the whole day's timetable.",
    "took_dose": "Log that the person took a dose (optional slot).",
    "missed_dose": "Log that the person missed a dose (optional slot).",
    "symptoms": "Compare how the person feels with the warning signs their doctor wrote.",
    "diet": "What to eat, avoid and do, as the doctor wrote it.",
    "followup": "When the next doctor visit is.",
    "explain": "Explain the discharge summary in plain words.",
    "family": "Write an update for the family.",
    "insurance": "Hand the hospital details to the insurance claim agent.",
}


def _prompt(text: str, session: dict, now: str, lang: str) -> str:
    sched = session["schedule"]
    day = "; ".join(
        f"{s['key']} {s['time']} ({'taken' if session['adherence'].get(s['key']) else 'not taken'})" for s in sched
    ) or "no medicines"
    tools = "\n".join(f"- {k}: {v}" for k, v in TOOLS.items())
    return (
        "You route requests for a discharge-summary assistant. You do not answer; you only choose which tools run.\n"
        f"Tools:\n{tools}\n\n"
        f"Time now: {now}. Today's timetable: {day}.\n"
        f"The person wrote or said this (language code {lang}, may be Hinglish): {text!r}\n\n"
        f"Choose between 1 and {MAX_STEPS} tools, in the order they should run. If one request has two parts "
        "(for example a missed dose and telling the family), use one tool for each. For took_dose or missed_dose, "
        "add \"slot\" with a timetable key only if the person named a time of day. Never choose a tool the person "
        "did not ask for. If you cannot tell, choose a single tool: explain.\n"
        'Reply as JSON: {"english": "<short English translation of the message>", "steps": '
        '[{"tool": "<name>", "slot": "<key or empty>", "why": "<six words or fewer>"}]}'
    )


def clean(raw: dict, session: dict) -> dict:
    """Keeps only steps that are on the menu and real. Raises if nothing usable is left."""
    keys = {s["key"] for s in session["schedule"]}
    steps, seen = [], set()
    for st in raw.get("steps") or []:
        tool = str(st.get("tool", "")).strip()
        slot = str(st.get("slot", "") or "").strip()
        if tool not in TOOLS or (tool, slot) in seen:
            continue
        seen.add((tool, slot))
        steps.append({"intent": tool, "slot": slot if slot in keys else "", "why": str(st.get("why", ""))[:60]})
        if len(steps) == MAX_STEPS:
            break
    if not steps:
        raise llm.LLMError("The plan had no usable steps.")
    return {"steps": steps, "english": str(raw.get("english", ""))[:300]}


async def make_plan(text: str, session: dict, now: str, lang: str) -> dict:
    raw = llm.parse_json(await llm.generate(_prompt(text, session, now, lang), json_mode=True))
    return clean(raw, session)
