"""Gemini wrapper used for two jobs only: reading a photo of a document and translating.

Everything else in the app works without it, so the key is optional.
"""
import base64
import json
import re

import httpx

from . import config


class LLMError(Exception):
    pass


def enabled() -> bool:
    return bool(config.GEMINI_API_KEY)


async def generate(prompt: str, *, json_mode: bool = False, image: tuple[str, bytes] | None = None) -> str:
    if not enabled():
        raise LLMError("No GEMINI_API_KEY set.")
    parts: list[dict] = [{"text": prompt}]
    if image:
        mime, data = image
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
    payload: dict = {"contents": [{"parts": parts}], "generationConfig": {"temperature": 0.1}}
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers={"x-goog-api-key": config.GEMINI_API_KEY}, json=payload)
    if resp.status_code >= 400:
        raise LLMError(f"Gemini error {resp.status_code}: {resp.text[:160]}")
    try:
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise LLMError("Gemini returned no text.") from exc


def parse_json(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMError("Model did not return valid JSON.") from exc
