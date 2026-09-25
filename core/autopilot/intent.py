"""Core tone and urgency intent detection for tickets and dialogues (re-exported from dialogue.py)."""

from core.autopilot.dialogue import TENSE_PATTERNS, detect_tense_tone

__all__ = ["TENSE_PATTERNS", "detect_tense_tone"]
