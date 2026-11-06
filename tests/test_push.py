import asyncio
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import config, push, reminders, store
from app.gnani import gnani
from app.main import app
from app.state import SESSIONS
from app.vapid import make_pair

SAMPLE = (Path(__file__).resolve().parent.parent / "app" / "samples" / "discharge_sample.txt").read_text()
IST = ZoneInfo("Asia/Kolkata")
SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc123", "keys": {"p256dh": "BPk", "auth": "xyz"}}


def at(h, m):
    return dt.datetime(2026, 11, 6, h, m, tzinfo=IST)


class FakePush:
    """Records what would have gone to the phone's push service."""

    def __init__(self, monkeypatch):
        pub, priv = make_pair()
        monkeypatch.setattr(config, "VAPID_PUBLIC_KEY", pub)
        monkeypatch.setattr(config, "VAPID_PRIVATE_KEY", priv)
        monkeypatch.setattr(config, "DATA_DIR", str(Path("/tmp") / "carewise-test-push"))
        self.sent, self.gone = [], set()

        async def send(sub, payload):
            if sub["endpoint"] in self.gone:
                raise push.PushGone("410")
            self.sent.append((sub["endpoint"], json.loads(payload)))

        monkeypatch.setattr(push, "send", send)


@pytest.fixture
def fp(monkeypatch):
    SESSIONS.clear()
    return FakePush(monkeypatch)


def session(client):
    return client.post("/api/document", data={"text": SAMPLE}).json()["session_id"]


def subscribe(client, sid, sub=SUB, lang="en-IN"):
    return client.post("/api/push/subscribe", json={"session_id": sid, "subscription": sub, "language": lang})


def test_vapid_pair_has_the_right_shape():
    pub, priv = make_pair()
    assert len(pub) == 87 and pub.startswith("B") and len(priv) == 43


def test_real_vapid_signing_accepts_the_key(monkeypatch):
    """The key we generate must be one pywebpush can actually sign with (no network involved)."""
    from py_vapid import Vapid
    _, priv = make_pair()
    v = Vapid.from_raw(priv.encode())
    assert v.sign({"sub": "mailto:a@b.co", "aud": "https://fcm.googleapis.com"})["Authorization"].startswith("vapid ")


def test_config_and_off_by_default(monkeypatch):
    monkeypatch.setattr(config, "VAPID_PUBLIC_KEY", "")
    c = TestClient(app)
    assert c.get("/api/push/config").json() == {"enabled": False, "key": ""}
    assert c.get("/api/health").json()["push"] is False
    assert c.post("/api/push/subscribe", json={"session_id": "x", "subscription": SUB}).status_code == 400


def test_subscribe_status_and_unsubscribe(fp):
    c = TestClient(app)
    assert c.get("/api/push/config").json()["enabled"] is True
    sid = session(c)
    r = subscribe(c, sid)
    assert r.status_code == 200 and r.json()["devices"] == 1 and r.json()["enabled"]
    assert subscribe(c, sid).json()["devices"] == 1  # the same phone twice is one phone
    st = c.post("/api/push/status", json={"session_id": sid, "endpoint": SUB["endpoint"]}).json()
    assert st["here"] and st["times"] == ["07:30", "08:00", "21:00"]
    assert c.post("/api/push/status", json={"session_id": sid, "endpoint": "https://fcm.googleapis.com/other"}).json()["here"] is False
    assert c.post("/api/push/unsubscribe", json={"session_id": sid, "endpoint": SUB["endpoint"]}).json()["devices"] == 0


def test_only_real_push_services_are_accepted(fp):
    c = TestClient(app)
    sid = session(c)
    for endpoint in ("http://fcm.googleapis.com/x", "https://evil.example.com/x", "https://127.0.0.1/x",
                     "https://fcm.googleapis.com.evil.com/x"):
        r = subscribe(c, sid, {"endpoint": endpoint, "keys": SUB["keys"]})
        assert r.status_code == 400, endpoint
    assert subscribe(c, sid, {"endpoint": SUB["endpoint"], "keys": {}}).status_code == 400
    assert subscribe(c, sid, {"endpoint": "https://updates.push.services.mozilla.com/wpush/v2/x", "keys": SUB["keys"]}).status_code == 200


def test_subscribe_blocked_until_flags_confirmed(fp):
    c = TestClient(app)
    messy = SAMPLE.replace("Tab Metformin 500 mg - one tablet twice daily after meals (morning and night) - 30 days",
                           "Tab Metformin 500 mg - after meals")
    sid = c.post("/api/document", data={"text": messy}).json()["session_id"]
    assert subscribe(c, sid).status_code == 409


def test_a_due_dose_sends_a_notification_once(fp, monkeypatch):
    c = TestClient(app)
    sid = session(c)
    monkeypatch.setattr(reminders, "now_local", lambda: at(6, 0))
    subscribe(c, sid)
    assert asyncio.run(reminders.tick(at(21, 1))) == 1
    endpoint, payload = fp.sent[0]
    assert endpoint == SUB["endpoint"]
    assert payload["title"] == "Carewise" and "night medicine" in payload["body"] and "Metformin 500 mg" in payload["body"]
    assert payload["sid"] == sid and payload["slot"] == "night" and payload["test"] is False
    assert asyncio.run(reminders.tick(at(21, 2))) == 0  # once only


def test_subscribing_late_does_not_replay_the_morning(fp, monkeypatch):
    c = TestClient(app)
    sid = session(c)
    monkeypatch.setattr(reminders, "now_local", lambda: at(9, 0))
    subscribe(c, sid)
    assert set(SESSIONS[sid]["sent"]) == {"pre", "morning"}
    assert asyncio.run(reminders.tick(at(9, 1))) == 0
    assert asyncio.run(reminders.tick(at(21, 1))) == 1


def test_notification_follows_the_chosen_language(fp, monkeypatch):
    c = TestClient(app)
    sid = session(c)
    monkeypatch.setattr(reminders, "now_local", lambda: at(6, 0))
    subscribe(c, sid, lang="hi-IN")
    asyncio.run(reminders.tick(at(21, 1)))
    assert "रात की दवा" in fp.sent[0][1]["body"] and fp.sent[0][1]["lang"] == "hi-IN"
    assert c.post("/api/push/settings", json={"session_id": sid, "language": "en-IN"}).json()["language"] == "en-IN"


def test_a_cancelled_phone_is_dropped_and_the_other_still_gets_it(fp, monkeypatch):
    c = TestClient(app)
    sid = session(c)
    monkeypatch.setattr(reminders, "now_local", lambda: at(6, 0))
    other = {"endpoint": "https://fcm.googleapis.com/fcm/send/second", "keys": SUB["keys"]}
    subscribe(c, sid); subscribe(c, sid, other)
    fp.gone.add(SUB["endpoint"])
    assert asyncio.run(reminders.tick(at(21, 1))) == 1
    assert [e for e, _ in fp.sent] == [other["endpoint"]]
    assert [x["endpoint"] for x in SESSIONS[sid]["push"]["subs"]] == [other["endpoint"]]


def test_test_notification_has_no_taken_risk_and_says_so(fp):
    c = TestClient(app)
    sid = session(c)
    assert c.post("/api/push/test", json={"session_id": sid, "language": "en-IN"}).status_code == 409
    subscribe(c, sid)
    r = c.post("/api/push/test", json={"session_id": sid, "language": "en-IN"})
    assert r.status_code == 200 and r.json()["pushed"] == 1
    assert fp.sent[-1][1]["test"] is True and fp.sent[-1][1]["body"].startswith("This is a test reminder.")


def test_turning_reminders_off_stops_the_push(fp, monkeypatch):
    c = TestClient(app)
    sid = session(c)
    monkeypatch.setattr(reminders, "now_local", lambda: at(6, 0))
    subscribe(c, sid)
    SESSIONS[sid]["push"]["enabled"] = False
    assert asyncio.run(reminders.tick(at(21, 1))) == 0


def test_phone_subscriptions_survive_a_restart(fp):
    c = TestClient(app)
    sid = session(c)
    subscribe(c, sid)
    SESSIONS.clear()
    assert store.load_into(SESSIONS) == 1
    assert SESSIONS[sid]["push"]["subs"][0]["endpoint"] == SUB["endpoint"]
    assert SESSIONS[sid]["telegram"] == {} and reminders.push_active(SESSIONS[sid])


def test_tapping_a_notification_speaks_the_reminder(fp, monkeypatch):
    monkeypatch.setattr(config, "GNANI_API_KEY", "k")

    async def tts(text, lang="hi-IN"):
        tts.heard = (text, lang)
        return b"RIFFfake"

    monkeypatch.setattr(gnani, "synthesize", tts)
    c = TestClient(app)
    sid = session(c)
    r = c.post("/api/reminder/speak", json={"session_id": sid, "slot_key": "night", "language": "en-IN"}).json()
    assert "night medicine" in r["text"] and r["label"] == "Night" and r["audio_b64"] and r["taken"] is False
    assert tts.heard[1] == "en-IN"
    c.post("/api/dose", json={"session_id": sid, "slot_key": "night", "taken": True})
    assert c.post("/api/reminder/speak", json={"session_id": sid, "slot_key": "night"}).json()["taken"] is True
    assert c.post("/api/reminder/speak", json={"session_id": sid, "slot_key": "nope"}).status_code == 404


def test_both_channels_fire_and_one_failing_does_not_block_the_other(fp, monkeypatch):
    from app import telegram
    c = TestClient(app)
    sid = session(c)
    monkeypatch.setattr(reminders, "now_local", lambda: at(6, 0))
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "t")
    texts = []

    async def broken(*a, **k):
        raise telegram.TelegramError("blocked by user")

    monkeypatch.setattr(telegram, "send_text", broken)
    subscribe(c, sid)
    SESSIONS[sid]["telegram"] = {"chat_id": 5, "enabled": True, "language": "en-IN"}
    assert asyncio.run(reminders.tick(at(21, 1))) == 1  # push got through, so it counts
    assert len(fp.sent) == 1
