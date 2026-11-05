"""Schedule agent: turns the medicine list into a day timetable and answers 'what's next?'."""
from ..models import Discharge

SLOTS = {
    "pre": ("Before breakfast", "07:30"),
    "morning": ("Morning", "08:00"),
    "afternoon": ("Afternoon", "14:00"),
    "evening": ("Evening", "18:00"),
    "night": ("Night", "21:00"),
}
DEFAULTS = {
    0: ["morning"],
    1: ["morning"],
    2: ["morning", "night"],
    3: ["morning", "afternoon", "night"],
    4: ["morning", "afternoon", "evening", "night"],
}


def build(data: Discharge) -> list[dict]:
    slots: dict[str, dict] = {}
    for med in data.medicines:
        names = list(med.timing)
        if not names or (med.times_per_day and len(names) != med.times_per_day):
            names = DEFAULTS.get(med.times_per_day, ["morning"])
        for name in names:
            key = name
            if med.food == "before" and name == "morning":
                key = "pre"
            label, time = SLOTS[key]
            slot = slots.setdefault(key, {"key": key, "label": label, "time": time, "items": []})
            note = {"before": "before food", "after": "after food"}.get(med.food, "")
            slot["items"].append({"medicine": med.name, "dose": med.dose, "note": note})
    return sorted(slots.values(), key=lambda s: s["time"])


def next_slot(schedule: list[dict], now: str) -> tuple[dict | None, bool]:
    """Returns (slot, is_tomorrow). `now` is 'HH:MM'."""
    if not schedule:
        return None, False
    for slot in schedule:
        if slot["time"] >= now:
            return slot, False
    return schedule[0], True


def current_slot(schedule: list[dict], now: str) -> dict | None:
    """The latest slot at or before `now`, else the first one of the day."""
    if not schedule:
        return None
    due = [s for s in schedule if s["time"] <= now]
    return due[-1] if due else schedule[0]
