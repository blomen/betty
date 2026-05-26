"""
Betty Exception Hierarchy

Structured exceptions for distinguishing retryable vs fatal errors.
"""


class BettyError(Exception):
    """Base exception for all Betty errors."""

    def __init__(self, message: str, provider_id: str | None = None):
        super().__init__(message)
        self.provider_id = provider_id
        self.message = message


class RetryableError(BettyError):
    """
    Errors that can be retried (timeouts, rate limits, transient failures).

    Use for:
    - Network timeouts
    - 429 rate limit responses
    - 5xx server errors
    - Temporary connection failures
    """

    def __init__(
        self,
        message: str,
        provider_id: str | None = None,
        retry_after: int | None = None,
    ):
        super().__init__(message, provider_id)
        self.retry_after = retry_after  # Seconds to wait before retry


class RateLimitError(RetryableError):
    """HTTP 429 rate limit exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        provider_id: str | None = None,
        retry_after: int | None = None,
    ):
        super().__init__(message, provider_id, retry_after)


class TimeoutError(RetryableError):
    """Request timeout."""

    def __init__(
        self,
        message: str = "Request timed out",
        provider_id: str | None = None,
    ):
        super().__init__(message, provider_id)


class FatalError(BettyError):
    """
    Errors that should not be retried (bad config, auth failed).

    Use for:
    - Invalid configuration
    - Authentication failures (401, 403)
    - Invalid API responses
    - Provider permanently unavailable
    """

    pass
