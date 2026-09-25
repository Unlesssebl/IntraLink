"""Cryptographically secure Active Directory password generation with Zero-Plaintext Policy."""

import secrets
import string
from typing import Set

from pydantic import SecretStr

# Characters excluded due to visual ambiguity (e.g. 0, O, o, 1, l, I)
AMBIGUOUS_CHARS: Set[str] = {"0", "O", "o", "1", "l", "I", "i"}

# Clean character pools
UPPERCASE_POOL = "".join(c for c in string.ascii_uppercase if c not in AMBIGUOUS_CHARS)
LOWERCASE_POOL = "".join(c for c in string.ascii_lowercase if c not in AMBIGUOUS_CHARS)
DIGITS_POOL = "".join(c for c in string.digits if c not in AMBIGUOUS_CHARS)
SPECIALS_POOL = "!@#$%^&*()_+=-"
FULL_POOL = UPPERCASE_POOL + LOWERCASE_POOL + DIGITS_POOL + SPECIALS_POOL


class SecretPassword(SecretStr):
    """Pydantic SecretStr wrapper enforcing Zero-Plaintext Policy.

    Never exposes the raw plaintext in string representations or logs.
    Access plaintext solely via `.get_secret_value()`.
    """

    def __repr__(self) -> str:
        return "SecretPassword('***REDACTED***')"

    def __str__(self) -> str:
        return "***REDACTED***"


def mask_password(raw_value: str) -> str:
    """Mask a password string for safe logging and ticket notes."""
    return "***REDACTED***"


def generate_secure_password(length: int = 14) -> SecretPassword:
    """Generate cryptographically strong password conforming to AD complexity requirements.

    Constraints:
      - Length >= 12
      - Zero visually ambiguous characters (0, O, 1, l, I)
      - At least one uppercase letter
      - At least one lowercase letter
      - At least one digit
      - At least one special symbol
      - Returns SecretPassword (protected against log leaks)
    """
    target_len = max(12, length)

    # Ensure required AD complexity categories
    chars = [
        secrets.choice(UPPERCASE_POOL),
        secrets.choice(LOWERCASE_POOL),
        secrets.choice(DIGITS_POOL),
        secrets.choice(SPECIALS_POOL),
    ]

    # Fill remaining characters from full unambiguous pool
    remaining_len = target_len - len(chars)
    for _ in range(remaining_len):
        chars.append(secrets.choice(FULL_POOL))

    # Cryptographically secure shuffle
    rng = secrets.SystemRandom()
    rng.shuffle(chars)

    plaintext = "".join(chars)
    return SecretPassword(plaintext)
