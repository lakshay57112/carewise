"""Phone notifications through the browser's own push service (no app to install, no account).

A phone subscribes once from the Carewise page. Later the server sends a small encrypted message
to Google's (or Mozilla's, or Apple's) push service, which wakes the phone's browser and shows the
notification even when the page is closed.
"""
import asyncio
import logging
from urllib.parse import urlparse

from pywebpush import WebPushException, webpush
from py_vapid import Vapid

from . import config

log = logging.getLogger("carewise.push")

# The server will post to whatever address a subscription names, so only real push services are accepted.
PUSH_HOSTS = (".googleapis.com", ".mozilla.com", ".push.apple.com", ".notify.windows.com")


class PushError(Exception):
    pass


class PushGone(PushError):
    """The phone cancelled or the subscription expired. Forget it."""


def enabled() -> bool:
    return bool(config.VAPID_PUBLIC_KEY and config.VAPID_PRIVATE_KEY)


def valid_subscription(sub) -> bool:
    if not isinstance(sub, dict):
        return False
    keys = sub.get("keys") or {}
    url = urlparse(str(sub.get("endpoint", "")))
    host = url.hostname or ""
    return (url.scheme == "https" and any(host.endswith(h) for h in PUSH_HOSTS)
            and bool(keys.get("p256dh")) and bool(keys.get("auth")))


def _send_blocking(sub: dict, payload: str) -> None:
    try:
        webpush(
            subscription_info=sub, data=payload,
            vapid_private_key=Vapid.from_raw(config.VAPID_PRIVATE_KEY.encode()),
            vapid_claims={"sub": config.VAPID_SUBJECT}, ttl=3600,
        )
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            raise PushGone(str(exc)) from exc
        raise PushError(str(exc)) from exc


async def send(sub: dict, payload: str) -> None:
    await asyncio.to_thread(_send_blocking, sub, payload)
