"""Check-in agent: compares what the person says with the warning signs the doctor wrote.

It never diagnoses. It only says whether a symptom lines up with the doctor's own list.
"""
import re

# concept -> words a person might say (English, Hinglish, Hindi)
SAID = {
    "fever": ["fever", "bukhar", "बुखार", "temperature", "tapman"],
    "vomit": ["vomit", "ulti", "उल्टी"],
    "chest": ["chest", "seene", "सीने", "छाती", "chhati"],
    "breath": ["breath", "saans", "सांस", "साँस"],
    "swelling": ["swelling", "swollen", "soojan", "सूजन"],
    "sugar": ["sugar", "शुगर", "glucose"],
    "urine": ["urine", "peshab", "पेशाब", "jalan", "जलन"],
    "blood": ["blood", "khoon", "खून"],
}
# concept -> word to look for inside the doctor's warning sign
IN_SIGN = {
    "fever": "fever", "vomit": "vomit", "chest": "chest", "breath": "breath",
    "swelling": "swelling", "sugar": "sugar", "urine": "urine", "blood": "blood",
}
ABOVE = re.compile(r"(?:above|over|more than|greater than|>)\s*(\d+(?:\.\d+)?)", re.I)
BELOW = re.compile(r"(?:below|under|less than|<)\s*(\d+(?:\.\d+)?)", re.I)


def _concepts(text: str) -> set[str]:
    low = text.lower()
    return {c for c, words in SAID.items() if any(w in low for w in words)}


def _threshold_hit(sign: str, numbers: list[float]) -> bool:
    """If the sign has limits and the person gave a number, check the number against them."""
    limits = [("above", float(m)) for m in ABOVE.findall(sign)] + [("below", float(m)) for m in BELOW.findall(sign)]
    if not limits or not numbers:
        return True  # no way to compare: stay on the safe side
    return any((n > v) if kind == "above" else (n < v) for kind, v in limits for n in numbers)


def evaluate(text: str, warning_signs: list[str]) -> list[str]:
    said = _concepts(text)
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    matched = []
    for sign in warning_signs:
        low = sign.lower()
        if any(IN_SIGN[c] in low for c in said) and _threshold_hit(sign, numbers):
            matched.append(sign)
    return matched
