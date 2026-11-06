"""Keeps linked reminder sessions on disk so a restart doesn't forget them.

On a host with a throwaway disk (Render's free plan) this only survives a restart, not a
redeploy. For real use, point DATA_DIR at a persistent disk or move this to a database.
"""
import json
from pathlib import Path

from . import config
from .models import Discharge


def _file() -> Path:
    return Path(config.DATA_DIR) / "linked.json"


def save(sessions: dict) -> None:
    out = {}
    for sid, s in sessions.items():
        if (s.get("telegram") or {}).get("chat_id") or (s.get("push") or {}).get("subs"):
            out[sid] = {
                "data": s["data"].model_dump(), "schedule": s["schedule"], "adherence": s["adherence"],
                "alert": s["alert"], "telegram": s.get("telegram") or {}, "push": s.get("push") or {},
                "day": s.get("day", ""), "sent": s.get("sent", []),
            }
    try:
        _file().parent.mkdir(parents=True, exist_ok=True)
        _file().write_text(json.dumps(out))
    except OSError:
        pass  # reminders still work this run; they just won't survive a restart


def load_into(sessions: dict) -> int:
    try:
        raw = json.loads(_file().read_text())
    except (OSError, ValueError):
        return 0
    for sid, s in raw.items():
        sessions[sid] = {
            "sid": sid, "data": Discharge.model_validate(s["data"]), "source": "", "flags": [], "stats": {},
            "pending": [], "schedule": s["schedule"], "adherence": s.get("adherence", {}),
            "alert": s.get("alert", ""), "telegram": s.get("telegram") or {}, "push": s.get("push") or {},
            "day": s.get("day", ""), "sent": s.get("sent", []),
        }
    return len(raw)
