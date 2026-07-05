import os

from dotenv import load_dotenv

load_dotenv()

TTS_API_KEY = (os.getenv("TTS_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
TTS_BASE_URL = (os.getenv("TTS_BASE_URL") or "https://api.openai.com/v1").strip()
TTS_MODEL = (os.getenv("TTS_MODEL") or "tts-1").strip()
TTS_VOICE = (os.getenv("TTS_VOICE") or "alloy").strip()
BRIEFING_AUDIO_ENABLED = os.getenv("BRIEFING_AUDIO_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def tts_is_configured() -> bool:
    return bool(TTS_API_KEY) and BRIEFING_AUDIO_ENABLED
