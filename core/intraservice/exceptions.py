"""Domain exceptions for IntraService API communication."""


class IntraServiceError(Exception):
    """Base exception for all IntraService API interactions."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class IntraServiceAuthError(IntraServiceError):
    """Raised when authentication against IntraService fails (HTTP 401/403)."""

    pass


class IntraServiceNotFoundError(IntraServiceError):
    """Raised when a requested resource is not found (HTTP 404)."""

    pass


class IntraServiceValidationError(IntraServiceError):
    """Raised when request payload or parameters are invalid (HTTP 400)."""

    pass


class IntraServiceServerError(IntraServiceError):
    """Raised when IntraService returns a server error (HTTP 5xx)."""

    pass


class CircuitBreakerOpenError(IntraServiceError):
    """Raised when the Circuit Breaker is OPEN and calls are temporarily blocked."""

    def __init__(self, cooldown_remaining: float) -> None:
        super().__init__(
            f"Circuit Breaker is OPEN. IntraService API is recovering. Remaining cooldown: {cooldown_remaining:.1f}s",
            status_code=503,
        )
        self.cooldown_remaining = cooldown_remaining
