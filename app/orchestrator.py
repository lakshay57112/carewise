"""Orchestrator: works out what the person wants and hands it to the right agents.

Two ways in. With a model key set, the planner (planner.py) reads the request and chooses which
agents to run and in what order, so "I missed my morning dose, tell my family" becomes two steps.
Without a key, or if the model fails, routing falls back to keywords: instant, free and
predictable. Either way the agents do the work and the fixed templates write the reply, so the
model never writes medical content.
"""
from . import config, llm, planner
from .agents import caregiver, checkin, explainer, handoff, schedule
from .trace import Trace

KEYWORDS: dict[str, list[str]] = {
    "took_dose": ["le li", "le liya", "kha li", "kha liya", "taken", "i took", "took it", "ले ली", "खा ली", "ले लिया", "खा लिया"],
    "missed_dose": ["missed", "forgot", "forget", "bhool", "bhul", "chhoot", "छूट", "भूल"],
    "insurance": ["insurance", "claim", "bima", "reject", "cashless", "tpa", "बीमा", "क्लेम"],
    "family": ["family", "ghar wale", "ghar walon", "parivar", "relatives", "परिवार", "घर वालों", "घरवालों"],
    "symptoms": ["fever", "bukhar", "vomit", "ulti", "chest", "seene", "saans", "breath", "swelling", "soojan",
                 "sugar", "urine", "peshab", "chakkar", "dizzy", "tabiyat", "feeling", "dard", "pain",
                 "तबीयत", "बुखार", "उल्टी", "सीने", "सांस", "साँस", "सूजन", "शुगर", "पेशाब", "चक्कर", "दर्द"],
    "next_dose": ["next", "agli", "agla", "अगली", "अगला", "kaun si", "kaun sa", "कौन सी", "कौन सा", "kab leni",
                  "kab lena", "कब लेनी", "कब लेना", "abhi", "now", "aaj", "kal", "आज", "कल", "due"],
    "schedule": ["schedule", "timetable", "time table", "all medicines", "poora", "sab dawai", "routine",
                 "kitne baje", "सारी", "सभी दवाइयां", "समय सारणी", "कितने बजे"],
    "diet": ["diet", "eat", "food", "khana", "khane", "parhez", "avoid", "walk", "activity", "exercise",
             "परहेज", "खाना", "खाने"],
    "followup": ["follow", "appointment", "dobara", "kab dikhana", "review", "next visit", "opd", "दिखाना", "अपॉइंटमेंट"],
    "explain": ["explain", "samjha", "samjhao", "kya hua", "bimari", "diagnosis", "summary", "what happened",
                "समझा", "बीमारी", "क्या हुआ"],
}
WEAK = {"next_dose": ["goli", "dawai", "dawa", "medicine", "tablet", "गोली", "दवा", "दवाई"]}
GATED = {"schedule", "next_dose", "took_dose", "missed_dose"}


def classify(text: str) -> str:
    low = text.lower()
    best, best_score = "unknown", 0
    for intent, words in KEYWORDS.items():
        score = sum(2 for w in words if w in low) + sum(1 for w in WEAK.get(intent, []) if w in low)
        if score > best_score:  # earlier intents win ties
            best, best_score = intent, score
    return best


INTENTS = set(KEYWORDS) | {"unknown"}


async def classify_llm(text: str) -> tuple[str, str]:
    """For languages the keyword lists don't cover. Returns (intent, english_gist)."""
    prompt = (
        "A patient wrote or said this in an Indian language. Pick the one label that fits best from: "
        f"{', '.join(sorted(INTENTS))}. Also give a short English translation. "
        'Reply as JSON: {"intent": "...", "english": "..."}.\n\n' + text
    )
    data = llm.parse_json(await llm.generate(prompt, json_mode=True))
    label = str(data.get("intent", "unknown"))
    return (label if label in INTENTS else "unknown"), str(data.get("english", ""))


async def _act(intent: str, session: dict, text: str, english: str, now: str, trace: Trace,
               extra: dict, slot_key: str = "") -> dict:
    """Runs the agent behind one intent and returns what the explainer needs."""
    data = session["data"]
    sched = session["schedule"]
    ctx: dict = {"data": data, "schedule": sched}
    chosen = next((x for x in sched if x["key"] == slot_key), None)

    if intent == "next_dose":
        with trace.step("schedule", "Finding the next dose") as s:
            ctx["slot"], ctx["tomorrow"] = schedule.next_slot(sched, now)
            s.detail = f"{ctx['slot']['label']} {ctx['slot']['time']}" if ctx["slot"] else "no schedule"
    elif intent == "took_dose":
        with trace.step("schedule", "Logging the dose as taken") as s:
            slot = chosen or schedule.current_slot(sched, now)
            ctx["slot"] = slot
            if slot:
                session["adherence"][slot["key"]] = True
                s.detail = f"{slot['label']} marked taken"
                extra["adherence"] = session["adherence"]
    elif intent == "missed_dose":
        with trace.step("schedule", "Logging the dose as missed") as s:
            slot = chosen or schedule.last_due(sched, now)
            ctx["slot"] = slot
            ctx["next"], ctx["tomorrow"] = schedule.next_slot(sched, now)
            if slot:
                session["adherence"][slot["key"]] = False
                if not session.get("alert"):
                    session["alert"] = f"{slot['label'].lower()} medicines missed"
                extra["adherence"] = session["adherence"]
                s.detail = f"{slot['label']} marked missed"
            else:
                s.detail = "no dose was due yet"
    elif intent == "symptoms":
        with trace.step("check-in", "Comparing with the doctor's warning signs") as s:
            ctx["matched"] = checkin.evaluate(f"{text} {english}", data.warning_signs)
            s.detail = f"{len(ctx['matched'])} possible match" + ("es" if len(ctx["matched"]) != 1 else "")
        if ctx["matched"]:
            session["alert"] = ctx["matched"][0]
            extra["alert"] = True
            with trace.step("caregiver", "Alerting the family") as s:
                s.detail = "alert added to the family update"
    elif intent == "family":
        with trace.step("caregiver", "Writing the family update") as s:
            nxt, _ = schedule.next_slot(sched, now)
            label = f"{nxt['label']} {nxt['time']}" if nxt else "none"
            ctx["family_en"], ctx["family_hi"] = caregiver.compose(session, label)
            s.detail = "update ready"
    elif intent == "insurance":
        with trace.step("hand-off", "Passing details to the claim agent (Insurithm AI)") as s:
            sent, body = await handoff.send(data)
            ctx["insurance_en"], ctx["insurance_hi"] = handoff.compose(sent)
            extra["handoff"] = body
            s.detail = "sent" if sent else "prepared, not sent (INSURITHM_URL not set)"
    return ctx


async def handle(session: dict, text: str, lang: str, now: str, trace: Trace,
                 forced: str = "", hint: str = "") -> dict:
    english = hint  # an English version of what was said, used for symptom matching
    steps: list[dict] = []  # what to run, in order: [{"intent", "slot"}]
    planned = False
    with trace.step("orchestrator", "Understanding the request") as s:
        if forced in INTENTS and forced != "unknown":
            steps = [{"intent": forced, "slot": ""}]
            s.detail = f"intent: {forced} (quick question)"
        else:
            if llm.enabled() and config.PLANNER:
                try:
                    plan = await planner.make_plan(text, session, now, lang)
                    steps, english = plan["steps"], plan["english"] or english
                    planned = True
                    s.detail = "planned: " + " → ".join(x["intent"] for x in steps)
                except Exception:
                    steps = []  # no plan; the keyword router below takes over
            if not steps:
                intent = classify(text)
                s.detail = f"intent: {intent}"
                if intent == "unknown" and llm.enabled():
                    try:
                        intent, english = await classify_llm(text)
                        s.detail = f"intent: {intent} (read by model)"
                    except Exception:
                        pass  # stay on 'unknown': it shows the help menu, which is safe
                steps = [{"intent": intent, "slot": ""}]

    if planned:
        for x in steps:
            trace.note("planner", f"Step: {x['intent']}", x["why"])
    extra: dict = {}
    parts: list[tuple[str, str]] = []
    if any(x["intent"] in GATED for x in steps) and session["pending"]:
        trace.note("verifier", "Held back until flagged items are confirmed", f"{len(session['pending'])} pending", "warn")
        steps = [{"intent": "gated", "slot": ""}]
    for x in steps:
        ctx = await _act(x["intent"], session, text, english, now, trace, extra, x.get("slot", ""))
        parts.append(explainer.compose(x["intent"], ctx))

    with trace.step("explainer", "Writing the reply in plain words") as s:
        en = " ".join(p[0] for p in parts)
        hi = " ".join(p[1] for p in parts)
        reply, note = await explainer.localize(en, hi, lang)
        s.detail = lang
        if note:
            extra["note"] = note

    return {"intent": steps[0]["intent"] if len(steps) == 1 else "+".join(x["intent"] for x in steps),
            "reply": reply, **extra}
