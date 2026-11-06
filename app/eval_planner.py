"""Scores how well requests are routed to the right agents.  Run:  python -m app.eval_planner

Always scores the keyword router. With GEMINI_API_KEY set it also scores the model planner on the
same requests, so you can report both numbers. The requests are written by hand (English, Hinglish,
Hindi and a few other languages) and are a small sample, not a benchmark.
"""
import asyncio
from pathlib import Path

from . import config, llm, orchestrator, planner
from .agents import document, schedule

SAMPLE = (Path(__file__).resolve().parent / "samples" / "discharge_sample.txt").read_text()

# (what the person says, the agents that should run, in order)
CASES = [
    ("what is my next medicine", ["next_dose"]),
    ("agli dawai kab leni hai", ["next_dose"]),
    ("मेरी अगली दवा कौन सी है", ["next_dose"]),
    ("I took my morning tablets", ["took_dose"]),
    ("maine dawai le li", ["took_dose"]),
    ("I forgot my morning medicine", ["missed_dose"]),
    ("subah ki dawai chhoot gayi, ghar walon ko bata do", ["missed_dose", "family"]),
    ("I missed my night dose and I feel dizzy", ["missed_dose", "symptoms"]),
    ("mujhe bukhar hai aur saans phool rahi hai", ["symptoms"]),
    ("what should I eat and when is my follow up", ["diet", "followup"]),
    ("khane mein kya parhez karna hai", ["diet"]),
    ("explain my report", ["explain"]),
    ("meri report samjhao", ["explain"]),
    ("tell my family how I am doing", ["family"]),
    ("my insurance claim got rejected", ["insurance"]),
    ("bima company ne cashless mana kar diya", ["insurance"]),
    ("মোর জ্বর হয়েছে", ["symptoms"]),
    ("எனக்கு காய்ச்சல் இருக்கிறது", ["symptoms"]),
    ("I took my medicine, update my family", ["took_dose", "family"]),
    ("show me the whole timetable", ["schedule"]),
]


def _session() -> dict:
    data = document.parse_rules(SAMPLE)
    return {"sid": "eval", "data": data, "schedule": schedule.build(data), "adherence": {}, "alert": "", "pending": []}


def _score(name: str, got: list[list[str]]) -> None:
    exact = sum(g == want for g, (_, want) in zip(got, CASES))
    first = sum(bool(g) and g[0] == want[0] for g, (_, want) in zip(got, CASES))
    print(f"{name:<16} exact plan {exact}/{len(CASES)}   first agent right {first}/{len(CASES)}")
    for g, (text, want) in zip(got, CASES):
        if g != want:
            print(f"   miss: {text!r}\n         wanted {want}, got {g}")


async def main() -> None:
    keyword = [[orchestrator.classify(t)] for t, _ in CASES]
    _score("keyword router", keyword)
    if not llm.enabled():
        print("\nSet GEMINI_API_KEY to score the model planner on the same requests.")
        return
    got = []
    for text, _ in CASES:
        try:
            plan = await planner.make_plan(text, _session(), "09:30", "hi-IN")
            got.append([x["intent"] for x in plan["steps"]])
        except Exception as exc:
            got.append([f"error: {exc}"[:40]])
    print()
    _score(f"planner ({config.GEMINI_MODEL})", got)


if __name__ == "__main__":
    asyncio.run(main())
