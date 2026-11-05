"""Hand-off agent: passes the hospital details to the claim-appeal agent (Insurithm AI).

Set INSURITHM_URL to send it for real. Without it the payload is only prepared, so the
demo can still show what would be handed over.
"""
import httpx

from .. import config
from ..models import Discharge


def payload(data: Discharge) -> dict:
    return {
        "source": "carewise",
        "patient": data.patient_name,
        "diagnosis": data.diagnosis,
        "medicines": [f"{m.name} {m.dose}".strip() for m in data.medicines],
        "follow_up": data.follow_up.date,
    }


async def send(data: Discharge) -> tuple[bool, dict]:
    body = payload(data)
    if not config.INSURITHM_URL:
        return False, body
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(config.INSURITHM_URL, json=body)
    resp.raise_for_status()
    return True, body


def compose(sent: bool) -> tuple[str, str]:
    if sent:
        return ("I have passed your hospital details to the claim agent. It will help you with the insurance claim.",
                "मैंने आपके अस्पताल की जानकारी बीमा क्लेम वाले एजेंट को दे दी है। वह क्लेम में आपकी मदद करेगा।")
    return ("I have prepared your hospital details for the claim agent. Connect it to send them.",
            "मैंने आपके अस्पताल की जानकारी बीमा क्लेम एजेंट के लिए तैयार कर ली है। भेजने के लिए उसे जोड़ें।")
