from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProcessMessageResult:
    """NLU outcome for messaging channels; may include optional briefing audio."""

    text: str
    audio_bytes: Optional[bytes] = None
    audio_mime_type: str = "audio/mpeg"

    def __str__(self) -> str:
        return self.text
