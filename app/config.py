"""Settings. Reads a local .env file if there is one, then the real environment."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()

# Gnani voice models (speech in, speech out)
GNANI_API_KEY = os.getenv("GNANI_API_KEY", "")
GNANI_BASE_URL = os.getenv("GNANI_BASE_URL", "https://api.vachana.ai")
STT_MODEL = os.getenv("GNANI_STT_MODEL", "prisma-v3.0")
TTS_MODEL = os.getenv("GNANI_TTS_MODEL", "timbre-v3.0")
TTS_VOICE = os.getenv("GNANI_TTS_VOICE", "Shivani")

# Reasoning + reading photos of documents. Optional: the app falls back to a
# rule-based reader when this is empty.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Optional hand-off to the claim-appeal agent (Insurithm AI)
INSURITHM_URL = os.getenv("INSURITHM_URL", "")

# Languages both Prisma (speech to text) and Timbre (text to speech) list
LANGUAGES = {
    "hi-IN": "हिन्दी",
    "en-IN": "English",
    "bn-IN": "বাংলা",
    "gu-IN": "ગુજરાતી",
    "kn-IN": "ಕನ್ನಡ",
    "ml-IN": "മലയാളം",
    "mr-IN": "मराठी",
    "pa-IN": "ਪੰਜਾਬੀ",
    "ta-IN": "தமிழ்",
    "te-IN": "తెలుగు",
}
