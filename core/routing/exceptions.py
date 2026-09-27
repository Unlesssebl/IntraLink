"""Exceptions and degradation codes for Evidence-Based Routing Cascade."""


class RoutingError(Exception):
    """Base exception for routing cascade domain and persistence errors."""


class RoutingPersistenceError(RoutingError):
    """Raised when storing or retrieving a routing decision fails."""


class RoutingProviderError(RoutingError):
    """Base exception for candidate provider execution errors."""

    code: str = "provider_error"


class ProviderUnavailableError(RoutingProviderError):
    """Raised when an external service (e.g. embedding gateway) is completely unreachable."""

    code: str = "provider_unavailable"


class ProviderTimeoutError(RoutingProviderError):
    """Raised when candidate provider operations exceed allowable deadlines."""

    code: str = "provider_timeout"


class ProviderInvalidResultError(RoutingProviderError):
    """Raised when candidate provider produces contradictory or unparseable evidence."""

    code: str = "provider_invalid_result"
