"""Core tone and urgency intent detection for tickets and dialogues."""

import re

# Emotional / Urgency markers (UI Mood Tag: is_tense)
TENSE_PATTERNS = [
    re.compile(r"\b(?:срочно|срочнейше|срочный|горит|горят|отчет\s+горит|работа\s+стоит|встала\s+работа|не\s+могу\s+работать|критично|asap|sos)\b", re.IGNORECASE),
    re.compile(r"\b(?:сколько\s+можно\s+ждать|доколе|когда\s+уже|почему\s+так\s+долго|возмутительно|безобразие|караул|беспредел)\b", re.IGNORECASE),
    re.compile(r"\b(?:повторно\s+пишу|третий\s+раз\s+пишу|шеф\s+ругается|начальство\s+требует)\b", re.IGNORECASE),
    re.compile(r"!{2,}"),
]


def detect_tense_tone(text: str | None) -> tuple[bool, str | None]:
    """Determine whether the message indicates high urgency, distress or frustration.

    Returns:
        (is_tense: bool, tense_reason: Optional[str])
    """
    if not text:
        return False, None
    clean_text = text.strip()
    if not clean_text:
        return False, None

    for pattern in TENSE_PATTERNS:
        match = pattern.search(clean_text)
        if match:
            matched_str = match.group(0)
            return True, f"Обнаружен маркер срочности/напряженности: '{matched_str}'"

    return False, None
