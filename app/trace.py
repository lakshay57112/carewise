"""A tiny recorder so the UI can show which agent did what, and how long it took."""
import time
from contextlib import contextmanager


class Step:
    def __init__(self, agent: str, title: str):
        self.agent = agent
        self.title = title
        self.detail = ""
        self.status = "done"
        self.ms = 0


class Trace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    @contextmanager
    def step(self, agent: str, title: str):
        s = Step(agent, title)
        start = time.perf_counter()
        try:
            yield s
        except Exception as exc:  # record the failure, then let the caller decide
            s.status = "error"
            s.detail = str(exc)[:200]
            raise
        finally:
            s.ms = int((time.perf_counter() - start) * 1000)
            self.events.append(
                {"agent": s.agent, "title": s.title, "detail": s.detail, "status": s.status, "ms": s.ms}
            )

    def note(self, agent: str, title: str, detail: str = "", status: str = "done") -> None:
        self.events.append({"agent": agent, "title": title, "detail": detail, "status": status, "ms": 0})
