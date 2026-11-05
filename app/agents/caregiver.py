"""Caregiver agent: a short update the family can read at a glance."""


def summary(session: dict, next_label: str) -> dict:
    schedule = session.get("schedule", [])
    taken_keys = {k for k, v in session.get("adherence", {}).items() if v}
    total = sum(len(s["items"]) for s in schedule)
    taken = sum(len(s["items"]) for s in schedule if s["key"] in taken_keys)
    return {"taken": taken, "total": total, "next": next_label, "alert": session.get("alert", "")}


def compose(session: dict, next_label: str) -> tuple[str, str]:
    s = summary(session, next_label)
    d = session["data"]
    name = d.patient_name or "Patient"
    date = d.follow_up.date or "not set"
    alert_en = f"Warning: {s['alert']}." if s["alert"] else "No warning signs reported."
    alert_hi = f"चेतावनी: {s['alert']}।" if s["alert"] else "कोई चेतावनी संकेत नहीं बताया गया।"
    en = (f"Update for family: {name} has taken {s['taken']} of {s['total']} doses today. {alert_en} "
          f"Next: {s['next']}. Follow-up visit: {date}.")
    hi = (f"परिवार के लिए खबर: {name} ने आज {s['total']} में से {s['taken']} खुराक ली हैं। {alert_hi} "
          f"अगली दवा: {s['next']}। अगली मुलाक़ात: {date}।")
    return en, hi
