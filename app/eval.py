"""Scores the document reader on the sample summaries in tests/fixtures.

Run:  python -m app.eval
It uses whichever reader is active (the model if GEMINI_API_KEY is set, otherwise the
basic rules reader), so you can compare the two honestly.
"""
import asyncio
from pathlib import Path

from .agents import document, verifier
from .trace import Trace

FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# name, dose, times per day, days
EXPECTED = {
    "diabetes.txt": {
        "meds": [("Metformin", "500 mg", 2, 30), ("Amlodipine", "5 mg", 1, 30),
                 ("Cefixime", "200 mg", 2, 5), ("Pantoprazole", "40 mg", 1, 10)],
        "date": "11-Nov-2026", "signs": 7,
    },
    "pneumonia.txt": {
        "meds": [("Azithromycin", "500 mg", 1, 3), ("Cetirizine", "10 mg", 1, 5), ("Paracetamol", "650 mg", 3, 3)],
        "date": "12/11/2026", "signs": 3,
    },
    "appendix.txt": {
        "meds": [("Ibuprofen", "400 mg", 2, 7), ("Omeprazole", "20 mg", 1, 14)],
        "date": "15-Nov-2026", "signs": 4,
    },
}


async def main() -> None:
    right = total = 0
    for name, exp in EXPECTED.items():
        text = (FIX / name).read_text()
        data, source = await document.run(Trace(), text=text)
        got = {m.name.lower(): m for m in data.medicines}
        for n, dose, per_day, days in exp["meds"]:
            m = got.get(n.lower())
            for field, ok in (("name", m is not None),
                              ("dose", bool(m) and m.dose.lower() == dose),
                              ("per day", bool(m) and m.times_per_day == per_day),
                              ("days", bool(m) and m.duration_days == days)):
                total += 1
                right += ok
                if not ok:
                    print(f"  miss  {name}: {n} {field}")
        for field, ok in (("follow-up date", data.follow_up.date == exp["date"]),
                          ("warning signs", len(data.warning_signs) == exp["signs"])):
            total += 1
            right += ok
            if not ok:
                print(f"  miss  {name}: {field} (got {data.follow_up.date!r} / {len(data.warning_signs)} signs)")
        flags, stats = verifier.run(data, source)
        print(f"{name:16} reader={data.extractor:7} verifier flags={len(flags)} confidence={stats['confidence']}")
    print(f"\nField accuracy: {right}/{total} = {right / total:.0%}")


if __name__ == "__main__":
    asyncio.run(main())
