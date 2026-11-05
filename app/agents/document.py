"""Document agent: turns a discharge summary (text or a photo) into structured data.

With a Gemini key it reads photos and extracts with a model. Without one it falls
back to a rule-based reader that handles the usual numbered medicine list.
"""
import re

from .. import llm
from ..models import Discharge, FollowUp, Medicine
from ..trace import Trace

SCHEMA_HELP = """Return JSON with exactly these keys:
{
  "patient_name": str,
  "diagnosis": str,
  "medicines": [{"name": str, "dose": str, "instructions": str, "times_per_day": int,
                 "timing": [one or more of "morning","afternoon","evening","night"],
                 "food": "before" | "after" | "any", "duration_days": int or null}],
  "diet": [str], "activity": [str],
  "follow_up": {"date": str, "note": str},
  "warning_signs": [str]
}
Copy values from the document only. If something is not written, use "" / [] / 0 / null. Do not guess doses."""


# ---------- rule-based reader ----------

MED_RE = re.compile(
    r"^\s*\d+[.)]\s*(?:(?:Tab|Cap|Syp|Syrup|Inj|Tablet|Capsule)\.?\s+)?"
    r"(?P<name>[A-Za-z][A-Za-z\-]+(?:\s[A-Za-z\-]+)?)\s+"
    r"(?P<dose>\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu))\b\s*[-–—:,]?\s*(?P<rest>.*)$",
    re.I,
)
DATE_RE = re.compile(r"\d{1,2}[-/ ](?:[A-Za-z]{3,9}|\d{1,2})[-/ ]\d{2,4}")


def _field(text: str, label: str) -> str:
    m = re.search(rf"^\s*{label}\s*:\s*(.+)$", text, re.I | re.M)
    return m.group(1).strip() if m else ""


def _sentences(value: str) -> list[str]:
    return [s.strip(" .") for s in re.split(r"\.\s+|\.$", value) if s.strip(" .")]


def _parse_med(m: re.Match) -> Medicine:
    rest = m.group("rest")
    low = rest.lower()
    if re.search(r"twice|\bbd\b|\bbid\b|two times", low):
        per_day = 2
    elif re.search(r"thrice|three times|\btds\b|\btid\b", low):
        per_day = 3
    elif re.search(r"four times|\bqid\b", low):
        per_day = 4
    elif re.search(r"once|\bod\b|daily|a day", low):
        per_day = 1
    else:
        per_day = 0
    timing = [t for t in ("morning", "afternoon", "evening", "night") if t in low]
    if "bedtime" in low and "night" not in timing:
        timing.append("night")
    if "breakfast" in low and "morning" not in timing:
        timing.insert(0, "morning")
    food = "any"
    if re.search(r"before (?:breakfast|food|meals?|eating)", low):
        food = "before"
    elif re.search(r"after (?:breakfast|food|meals?|lunch|dinner|eating)", low):
        food = "after"
    duration = None
    d = re.search(r"(\d+)\s*days?", low)
    w = re.search(r"(\d+)\s*weeks?", low)
    if d:
        duration = int(d.group(1))
    elif w:
        duration = int(w.group(1)) * 7
    return Medicine(
        name=m.group("name").strip().title(),
        dose=re.sub(r"\s+", " ", m.group("dose")).strip(),
        instructions=rest.strip(" -"),
        times_per_day=per_day,
        timing=timing,
        food=food,
        duration_days=duration,
    )


def parse_rules(text: str) -> Discharge:
    meds = [_parse_med(m) for line in text.splitlines() if (m := MED_RE.match(line))]
    patient = re.split(r"\s{2,}|\t", _field(text, "Patient"))[0] if _field(text, "Patient") else ""
    follow_raw = _field(text, "Follow-?up")
    date = DATE_RE.search(follow_raw)
    warning = []
    w = re.search(r"return (?:to (?:the )?hospital )?(?:immediately )?(?:if|when)\s*:?\s*(.+)", text, re.I | re.S)
    if w:
        block = w.group(1).split("\n\n")[0].strip().rstrip(".")
        warning = [re.sub(r"^or\s+", "", s.strip(), flags=re.I) for s in re.split(r",|;", block) if s.strip()]
    return Discharge(
        patient_name=patient,
        diagnosis=_field(text, "Diagnosis"),
        medicines=meds,
        diet=_sentences(_field(text, "Diet")),
        activity=_sentences(_field(text, "Activity")),
        follow_up=FollowUp(date=date.group(0) if date else "", note=follow_raw),
        warning_signs=warning,
        extractor="rules",
    )


# ---------- model-based reader ----------

async def ocr_image(image: tuple[str, bytes]) -> str:
    prompt = (
        "Transcribe this medical document exactly as written, line by line. "
        "Do not summarise, correct or add anything. If a word is unreadable write [unclear]."
    )
    return await llm.generate(prompt, image=image)


async def extract_llm(text: str) -> Discharge:
    prompt = f"Extract the discharge summary below into structured data.\n{SCHEMA_HELP}\n\nDOCUMENT:\n{text}"
    raw = await llm.generate(prompt, json_mode=True)
    data = llm.parse_json(raw)
    data["extractor"] = "gemini"
    return Discharge.model_validate(data)


async def run(trace: Trace, text: str = "", image: tuple[str, bytes] | None = None) -> tuple[Discharge, str]:
    """Returns the structured data and the plain text it was read from."""
    source = text.strip()
    if image and not source:
        if not llm.enabled():
            raise ValueError("Reading photos needs GEMINI_API_KEY. Paste the text instead, or add a key.")
        with trace.step("document", "Reading the photo") as s:
            source = await ocr_image(image)
            s.detail = f"{len(source.split())} words read"
    if not source:
        raise ValueError("Nothing to read. Paste the summary or upload a photo.")

    if llm.enabled():
        try:
            with trace.step("document", "Extracting medicines, diet and warning signs") as s:
                data = await extract_llm(source)
                s.detail = f"{len(data.medicines)} medicines found"
            return data, source
        except Exception as exc:  # fall back to the rules reader
            trace.note("document", "Model reader failed, using the basic reader", str(exc)[:120], "warn")

    with trace.step("document", "Extracting medicines, diet and warning signs") as s:
        data = parse_rules(source)
        s.detail = f"{len(data.medicines)} medicines found (basic reader)"
    return data, source
