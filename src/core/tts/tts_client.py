from __future__ import annotations

import logging
import re
from typing import Optional

import openai

from core.tts.config import TTS_API_KEY, TTS_BASE_URL, TTS_MODEL, TTS_VOICE, tts_is_configured
from core.tts.speech_text import briefing_text_for_speech

logger = logging.getLogger(__name__)

_MAX_CHARS_PER_REQUEST = 4000


class TTSClient:
    """OpenAI-compatible text-to-speech client for briefing audio."""

    def __init__(self) -> None:
        self._client: Optional[openai.OpenAI] = None

    @property
    def is_available(self) -> bool:
        return tts_is_configured()

    def _get_client(self) -> openai.OpenAI:
        if self._client is None:
            if not TTS_API_KEY:
                raise ValueError("TTS_API_KEY or OPENAI_API_KEY is not configured")
            self._client = openai.OpenAI(api_key=TTS_API_KEY, base_url=TTS_BASE_URL)
        return self._client

    def synthesize_briefing(self, briefing_text: str) -> Optional[bytes]:
        if not self.is_available:
            return None

        speakable = briefing_text_for_speech(briefing_text)
        if not speakable:
            return None

        try:
            chunks = self._split_text(speakable)
            audio_parts: list[bytes] = []
            client = self._get_client()
            for chunk in chunks:
                response = client.audio.speech.create(
                    model=TTS_MODEL,
                    voice=TTS_VOICE,
                    input=chunk,
                    response_format="mp3",
                )
                audio_parts.append(response.content)

            if not audio_parts:
                return None
            if len(audio_parts) == 1:
                return audio_parts[0]
            return b"".join(audio_parts)
        except Exception as exc:
            logger.error("Briefing TTS synthesis failed: %s", exc, exc_info=True)
            return None

    @staticmethod
    def _split_text(text: str) -> list[str]:
        if len(text) <= _MAX_CHARS_PER_REQUEST:
            return [text]

        parts: list[str] = []
        current = ""
        for sentence in _split_sentences(text):
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) <= _MAX_CHARS_PER_REQUEST:
                current = candidate
                continue
            if current:
                parts.append(current)
            while len(sentence) > _MAX_CHARS_PER_REQUEST:
                parts.append(sentence[:_MAX_CHARS_PER_REQUEST])
                sentence = sentence[_MAX_CHARS_PER_REQUEST:]
            current = sentence
        if current:
            parts.append(current)
        return parts or [text[:_MAX_CHARS_PER_REQUEST]]


def _split_sentences(text: str) -> list[str]:
    segments = re.split(r"(?<=[.!?])\s+", text.strip())
    return [segment.strip() for segment in segments if segment.strip()]
