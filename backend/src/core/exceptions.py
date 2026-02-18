"""
BankrollBBQ Exception Hierarchy

Structured exceptions for distinguishing retryable vs fatal errors.
"""


class BankrollBBQError(Exception):
    """Base exception for all BankrollBBQ errors."""

    def __init__(self, message: str, provider_id: str | None = None):
        super().__init__(message)
        self.provider_id = provider_id
        self.message = message


class RetryableError(BankrollBBQError):
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


class FatalError(BankrollBBQError):
    """
    Errors that should not be retried (bad config, auth failed).

    Use for:
    - Invalid configuration
    - Authentication failures (401, 403)
    - Invalid API responses
    - Provider permanently unavailable
    """

    pass


class ConfigurationError(FatalError):
    """Invalid or missing configuration."""

    pass


class AuthenticationError(FatalError):
    """Authentication or authorization failed."""

    pass


class DataQualityError(BankrollBBQError):
    """
    Data quality issues (suspicious arbs, bad odds).

    Use for:
    - Arbitrage opportunities with suspiciously high profit (>10%)
    - Odds values outside reasonable range
    - Mismatched events detected
    - Market type parsing failures
    """

    def __init__(
        self,
        message: str,
        provider_id: str | None = None,
        event_id: str | None = None,
        metric_value: float | None = None,
    ):
        super().__init__(message, provider_id)
        self.event_id = event_id
        self.metric_value = metric_value


class ExtractionError(BankrollBBQError):
    """Error during data extraction from provider."""

    def __init__(
        self,
        message: str,
        provider_id: str | None = None,
        sport: str | None = None,
    ):
        super().__init__(message, provider_id)
        self.sport = sport


class MatchingError(BankrollBBQError):
    """Error during event matching or normalization."""

    def __init__(
        self,
        message: str,
        provider_id: str | None = None,
        event_id: str | None = None,
        home_team: str | None = None,
        away_team: str | None = None,
    ):
        super().__init__(message, provider_id)
        self.event_id = event_id
        self.home_team = home_team
        self.away_team = away_team
