from __future__ import annotations

import re

_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)
_DELETE_HINT = re.compile(
    r'^\s*(?:to remove an item|say\s+"delete|using the number from the list)',
    re.IGNORECASE,
)


def briefing_text_for_speech(text: str) -> str:
    """Strip emojis and on-screen-only hints so briefing text reads well aloud."""
    lines: list[str] = []
    for raw_line in (text or "").splitlines():
        line = _EMOJI_PATTERN.sub("", raw_line).strip()
        line = re.sub(r"\s*—\s*", ", ", line)
        line = re.sub(r"\s{2,}", " ", line).strip()
        if not line or _DELETE_HINT.search(line):
            continue
        lines.append(line)

    if not lines:
        return _EMOJI_PATTERN.sub("", text or "").strip()

    return ". ".join(lines)
