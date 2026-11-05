"""Explainer agent: writes the reply in plain words, in the person's language.

Hindi and English come from fixed templates, so they never drift from the facts and
cost nothing. Other languages are translated by the model when a key is set.
"""
from .. import config, llm

HI_LABEL = {
    "Before breakfast": "नाश्ते से पहले",
    "Morning": "सुबह",
    "Afternoon": "दोपहर",
    "Evening": "शाम",
    "Night": "रात",
}
DISCLAIMER_EN = "This is a plain-language summary, not medical advice. Please confirm with your doctor."
DISCLAIMER_HI = "यह सिर्फ़ आसान भाषा में समझाने के लिए है, चिकित्सा सलाह नहीं। कृपया अपने डॉक्टर से पुष्टि कर लें।"


def _items(slot: dict) -> str:
    return ", ".join(f"{i['medicine']} {i['dose']}".strip() for i in slot["items"])


def _slot_en(slot: dict) -> str:
    return f"{slot['label']} ({slot['time']}): {_items(slot)}"


def _slot_hi(slot: dict) -> str:
    return f"{HI_LABEL.get(slot['label'], slot['label'])} {slot['time']} बजे: {_items(slot)}"


def compose(intent: str, ctx: dict) -> tuple[str, str]:
    """Returns (english, hindi) text for the intent."""
    d = ctx.get("data")
    if intent == "explain":
        n = len(d.medicines)
        date = d.follow_up.date or "the follow-up date"
        en = (f"You were treated for {d.diagnosis or 'the condition in your summary'}. You are going home with "
              f"{n} medicine{'s' if n != 1 else ''}. Take them as per your schedule, follow the diet advice, "
              f"and see the doctor on {date}. {DISCLAIMER_EN}")
        hi = (f"आपका इलाज {d.diagnosis or 'आपकी रिपोर्ट में लिखी बीमारी'} के लिए हुआ। घर जाने पर आपको {n} दवाइयां लेनी हैं। "
              f"उन्हें समय-सारणी के हिसाब से लें, खाने-पीने की सलाह मानें, और {date} को डॉक्टर को दिखाएं। {DISCLAIMER_HI}")
        return en, hi
    if intent == "schedule":
        sched = ctx["schedule"]
        return ("Here is your day. " + " ".join(_slot_en(s) + "." for s in sched),
                "आपका दिन ऐसा रहेगा। " + " ".join(_slot_hi(s) + "।" for s in sched))
    if intent == "next_dose":
        slot, tomorrow = ctx["slot"], ctx["tomorrow"]
        if not slot:
            return "I could not find any medicines in your schedule.", "आपकी समय-सारणी में कोई दवा नहीं मिली।"
        if tomorrow:
            return (f"Today's medicines are done. Tomorrow, {_slot_en(slot)}.",
                    f"आज की दवाइयां हो चुकी हैं। कल, {_slot_hi(slot)}।")
        return f"Your next medicines: {_slot_en(slot)}.", f"आपकी अगली दवाइयां: {_slot_hi(slot)}।"
    if intent == "took_dose":
        slot = ctx["slot"]
        return (f"Noted. I marked the {slot['label'].lower()} medicines as taken.",
                f"ठीक है। {HI_LABEL.get(slot['label'], slot['label'])} की दवाइयां ली हुई दर्ज कर दीं।")
    if intent == "diet":
        diet = "; ".join(d.diet) or "no diet advice was written"
        act = "; ".join(d.activity)
        return (f"Diet: {diet}. " + (f"Activity: {act}. " if act else "") + "Follow what your doctor wrote.",
                f"खाने के बारे में डॉक्टर ने लिखा है: {diet}। " + (f"चलने-फिरने के बारे में: {act}। " if act else ""))
    if intent == "followup":
        fu = d.follow_up
        if not fu.date and not fu.note:
            return "No follow-up visit was written in your summary.", "आपकी रिपोर्ट में अगली मुलाक़ात नहीं लिखी है।"
        return (f"Your follow-up is on {fu.date or 'a date not written'}. {fu.note}".strip(),
                f"आपकी अगली मुलाक़ात {fu.date or 'तारीख़ नहीं लिखी'} को है। {fu.note}".strip())
    if intent == "symptoms":
        matched = ctx["matched"]
        if matched:
            sign = matched[0]
            return (f"What you described may match a warning sign your doctor wrote: \"{sign}\". "
                    "Please contact your doctor or go to the hospital now. I am alerting your family.",
                    f"आपने जो बताया वह डॉक्टर के लिखे चेतावनी संकेत जैसा हो सकता है: \"{sign}\"। "
                    "कृपया अभी डॉक्टर से संपर्क करें या अस्पताल जाएं। मैं आपके परिवार को बता रही हूँ।")
        return ("That does not match the warning signs on your summary. Keep taking your medicines and rest. "
                "If you feel worse, contact your doctor.",
                "यह आपकी रिपोर्ट के चेतावनी संकेतों से नहीं मिलता। दवाइयां लेते रहें और आराम करें। "
                "तबीयत बिगड़े तो डॉक्टर से संपर्क करें।")
    if intent == "family":
        return ctx["family_en"], ctx["family_hi"]
    if intent == "insurance":
        return ctx["insurance_en"], ctx["insurance_hi"]
    if intent == "gated":
        return ("Some items still need your confirmation. Please check the highlighted ones first, so I do not give you a wrong schedule.",
                "कुछ बातों की आपकी पुष्टि बाकी है। पहले चिह्नित बातें देख लें, ताकि मैं गलत समय-सारणी न बताऊं।")
    return ("I can explain your summary, tell you your medicine times, what to eat, your next visit, check a symptom, "
            "update your family, or start an insurance claim. What would you like?",
            "मैं आपकी रिपोर्ट समझा सकती हूँ, दवाइयों का समय, खाने की सलाह, अगली मुलाक़ात बता सकती हूँ, लक्षण जांच सकती हूँ, "
            "परिवार को खबर दे सकती हूँ, या बीमा क्लेम शुरू कर सकती हूँ। आप क्या जानना चाहेंगे?")


async def localize(en: str, hi: str, lang: str) -> tuple[str, str]:
    """Pick the right language. Returns (text, note)."""
    if lang == "hi-IN":
        return hi, ""
    if lang == "en-IN":
        return en, ""
    name = config.LANGUAGES.get(lang, lang)
    if llm.enabled():
        try:
            prompt = (f"Translate this to {name} in simple everyday words. Keep medicine names, doses and times "
                      f"unchanged. Output only the translation.\n\n{en}")
            return (await llm.generate(prompt)).strip(), ""
        except Exception:
            pass
    return en, f"Showing English. Add GEMINI_API_KEY to get replies in {name}."
