"""Thin client for Gnani's speech APIs: Prisma (speech to text) and Timbre (text to speech).

Endpoints and field names follow the public docs at docs.gnani.ai. The docs show an
example error saying only one request may be active at a time, so calls are serialised.
"""
import asyncio

import httpx

from . import config


class GnaniError(Exception):
    pass


class Gnani:
    def __init__(self) -> None:
        self._lock = asyncio.Semaphore(1)

    @property
    def enabled(self) -> bool:
        return bool(config.GNANI_API_KEY)

    def _headers(self) -> dict:
        return {"X-API-Key-ID": config.GNANI_API_KEY}

    @staticmethod
    def _check(resp: httpx.Response) -> None:
        if resp.status_code == 401:
            raise GnaniError("Gnani rejected the API key (401). Check GNANI_API_KEY.")
        if resp.status_code in (402, 403):
            raise GnaniError(f"Gnani says no credit or access ({resp.status_code}).")
        if resp.status_code == 429:
            raise GnaniError("Gnani rate limit hit (429). Try again in a moment.")
        if resp.status_code >= 400:
            raise GnaniError(f"Gnani error {resp.status_code}: {resp.text[:160]}")

    async def transcribe(self, audio: bytes, lang: str = "hi-IN", filename: str = "speech.wav") -> dict:
        """Speech to text. REST accepts audio up to 30 seconds."""
        if not self.enabled:
            raise GnaniError("Voice is off: GNANI_API_KEY is not set.")
        # Give Prisma the chosen language plus English as hints (it accepts 1 to 4).
        hint = "en-IN,hi-IN" if lang == "en-IN" else f"{lang},en-IN"
        data = {"model": config.STT_MODEL, "language_code": hint, "format": "codemix"}
        files = {"audio_file": (filename, audio, "audio/wav")}
        async with self._lock:
            async with httpx.AsyncClient(timeout=40) as client:
                resp = await client.post(
                    f"{config.GNANI_BASE_URL}/stt/v3", headers=self._headers(), data=data, files=files
                )
        self._check(resp)
        body = resp.json()
        return {
            "transcript": (body.get("transcript") or "").strip(),
            "language": body.get("detected_language_code") or lang,
        }

    async def synthesize(self, text: str, lang: str = "hi-IN") -> bytes:
        """Text to speech. Returns WAV bytes (24 kHz, mono, 16-bit)."""
        if not self.enabled:
            raise GnaniError("Voice is off: GNANI_API_KEY is not set.")
        body = {
            "text": text[:800],  # keep credits in check
            "voice": config.TTS_VOICE,
            "model": config.TTS_MODEL,
            "language": lang,
            "speed": 1.0,
            "audio_config": {
                "sample_rate": 24000,
                "num_channels": 1,
                "sample_width": 2,
                "encoding": "linear_pcm",
                "container": "wav",
            },
        }
        headers = {**self._headers(), "Content-Type": "application/json"}
        async with self._lock:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{config.GNANI_BASE_URL}/api/v1/tts/inference", headers=headers, json=body
                )
        self._check(resp)
        return resp.content


gnani = Gnani()
