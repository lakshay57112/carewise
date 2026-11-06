"""Shared in-memory state. Fine for a demo: swap for a database when this grows up."""

SESSIONS: dict[str, dict] = {}
LINK_CODES: dict[str, str] = {}  # one-time Telegram link code -> session id
