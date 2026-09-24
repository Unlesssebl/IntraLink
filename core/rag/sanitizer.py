"""PII and sensitive data sanitizer for RAG Knowledge Base.

Masks confidential data (passwords, tokens, emails, phone numbers) and truncates
large error stacktraces / log dumps before vectorization and database insertion.
"""

import collections
import html
import math
import re
from typing import List, NamedTuple

# HTML tag removal regex
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Shannon entropy thresholds for high-entropy secrets (API keys, raw tokens, random passwords)
_MIN_ENTROPY_TOKEN_LEN = 20
_SHANNON_ENTROPY_THRESHOLD = 3.8
_HIGH_ENTROPY_CANDIDATE = re.compile(r"\b[A-Za-z0-9+/=_\-]{20,128}\b")
_UUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def calculate_shannon_entropy(data: str) -> float:
    """Calculate Shannon information entropy for a string (bits per symbol)."""
    if not data:
        return 0.0
    entropy = 0.0
    length = len(data)
    counts = collections.Counter(data)
    for count in counts.values():
        p = count / length
        entropy -= p * math.log2(p)
    return entropy

# Credential patterns
_PASSWORD_PATTERN = re.compile(
    r"(?i)\b(пароль|pass|password|pwd|secret)\s*[:=]\s*([^\s,;]+)",
    re.IGNORECASE,
)
_TOKEN_KEYWORD_PATTERN = re.compile(
    r"(?i)\b(токен|token|api_key|apikey|secret_key)\s*[:=]\s*([^\s,;]+)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(
    r"\bBearer\s+([a-zA-Z0-9_\-\.]{15,})\b",
    re.IGNORECASE,
)
_JWT_PATTERN = re.compile(
    r"\beyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\b"
)

# Email pattern
_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

# Phone pattern: matches Russian mobile/landline numbers (+7 or 8 followed by 10 digits with optional separators)
_PHONE_PATTERN = re.compile(
    r"(?:(?<=\s)|(?<=^)|(?<=[^\w\+]))(?:\+7|8)[\s\-(]?\(?(\d{3})\)?[\s\-]?(\d{3})[\s\-]?(\d{2})[\s\-]?(\d{2})\b"
)

# Stacktrace / error dump indicator
_STACKTRACE_PATTERN = re.compile(
    r"(?i)(?:Traceback \(most recent call last\):|Exception in thread|Caused by:|at [a-zA-Z0-9_$.]+\([a-zA-Z0-9_$.]+:\d+\))"
)


class SanitizationResult(NamedTuple):
    sanitized_text: str
    detected_types: List[str]
    is_truncated: bool


class PIISanitizer:
    """Sanitizer for personal identifiable information (PII) and secret credentials."""

    def __init__(self, max_length: int = 2000) -> None:
        self.max_length = max_length

    def clean_html(self, text: str) -> str:
        """Strip HTML tags and unescape HTML entities."""
        if not text:
            return ""
        # Replace line-breaking HTML tags with newlines
        clean = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", text)
        clean = _HTML_TAG_RE.sub(" ", clean)
        clean = html.unescape(clean)
        clean = clean.replace("\xa0", " ")
        # Normalize multiple spaces and extra empty lines
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in clean.splitlines()]
        clean = "\n".join(line for line in lines if line)
        return clean.strip()


    def truncate_logs(self, text: str, max_chars: int = 2000) -> tuple[str, bool]:
        """Truncate error log dumps and stacktraces exceeding max_chars."""
        if not text:
            return "", False

        if len(text) <= max_chars:
            return text, False

        # If it contains an error stacktrace or excessive dump, cut cleanly
        truncated_suffix = "\n... [TRUNCATED LOG DUMP]"
        cutoff = max_chars - len(truncated_suffix)
        return text[:cutoff].rstrip() + truncated_suffix, True

    def sanitize(self, text: str) -> SanitizationResult:
        """Perform full PII sanitization and dump truncation on input text."""
        if not text:
            return SanitizationResult(sanitized_text="", detected_types=[], is_truncated=False)

        detected_types: List[str] = []

        # 1. Clean HTML
        clean = self.clean_html(text)

        # 2. Mask passwords
        def _replace_password(match: re.Match[str]) -> str:
            prefix = match.group(1)
            if "PASSWORD" not in detected_types:
                detected_types.append("PASSWORD")
            return f"{prefix}: [PASSWORD]"

        clean = _PASSWORD_PATTERN.sub(_replace_password, clean)

        # 3. Mask tokens and API keys
        def _replace_token_keyword(match: re.Match[str]) -> str:
            prefix = match.group(1)
            if "TOKEN" not in detected_types:
                detected_types.append("TOKEN")
            return f"{prefix}: [TOKEN]"

        clean = _TOKEN_KEYWORD_PATTERN.sub(_replace_token_keyword, clean)

        if _BEARER_PATTERN.search(clean):
            if "TOKEN" not in detected_types:
                detected_types.append("TOKEN")
            clean = _BEARER_PATTERN.sub("Bearer [TOKEN]", clean)

        if _JWT_PATTERN.search(clean):
            if "TOKEN" not in detected_types:
                detected_types.append("TOKEN")
            clean = _JWT_PATTERN.sub("[TOKEN]", clean)

        # 3b. Mask standalone high-entropy secrets (raw tokens, API keys, random passwords)
        def _replace_high_entropy(match: re.Match[str]) -> str:
            token = match.group(0)
            if _UUID_PATTERN.match(token):
                return token
            entropy = calculate_shannon_entropy(token)
            if entropy >= _SHANNON_ENTROPY_THRESHOLD:
                if "TOKEN" not in detected_types:
                    detected_types.append("TOKEN")
                return "[TOKEN]"
            return token

        clean = _HIGH_ENTROPY_CANDIDATE.sub(_replace_high_entropy, clean)


        # 4. Mask emails
        if _EMAIL_PATTERN.search(clean):
            if "EMAIL" not in detected_types:
                detected_types.append("EMAIL")
            clean = _EMAIL_PATTERN.sub("[EMAIL]", clean)

        # 5. Mask phone numbers
        if _PHONE_PATTERN.search(clean):
            if "PHONE" not in detected_types:
                detected_types.append("PHONE")
            clean = _PHONE_PATTERN.sub("[PHONE]", clean)

        # 6. Truncate logs if oversized
        clean, was_truncated = self.truncate_logs(clean, max_chars=self.max_length)
        if was_truncated:
            if "LOG_DUMP" not in detected_types:
                detected_types.append("LOG_DUMP")

        return SanitizationResult(
            sanitized_text=clean,
            detected_types=detected_types,
            is_truncated=was_truncated,
        )


_default_sanitizer = PIISanitizer()


def sanitize_text(text: str, max_length: int = 2000) -> str:
    """Convenience helper to sanitize text using default sanitizer."""
    if max_length == 2000:
        return _default_sanitizer.sanitize(text).sanitized_text
    return PIISanitizer(max_length=max_length).sanitize(text).sanitized_text


def truncate_log_dump(text: str, max_chars: int = 2000) -> str:
    """Convenience helper to truncate log dumps and stacktraces."""
    res, _ = _default_sanitizer.truncate_logs(text, max_chars=max_chars)
    return res
