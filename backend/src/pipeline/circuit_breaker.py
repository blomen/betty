"""
Circuit Breaker Pattern

State machine for provider health management.
Prevents cascading failures by temporarily disabling degraded providers.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation, requests allowed
    OPEN = "open"          # Provider degraded, requests blocked
    HALF_OPEN = "half_open"  # Testing recovery, limited requests


@dataclass
class CircuitStatus:
    """Circuit breaker status for a provider."""
    provider_id: str
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    opened_at: Optional[float] = None
    half_open_attempts: int = 0

    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self.state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (blocking requests)."""
        return self.state == CircuitState.OPEN

    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing recovery)."""
        return self.state == CircuitState.HALF_OPEN


class CircuitBreaker:
    """
    Circuit breaker for provider health management.

    State Transitions:
    - CLOSED -> OPEN: After failure_threshold consecutive failures
    - OPEN -> HALF_OPEN: After recovery_timeout_seconds
    - HALF_OPEN -> CLOSED: On successful call
    - HALF_OPEN -> OPEN: On failure

    Thread-safe for concurrent provider calls.

    Note on threading.Lock vs asyncio.Lock:
    We use threading.Lock here intentionally. While this is called from async
    contexts, the operations are fast in-memory dict updates (microseconds).
    Using asyncio.Lock would require making all methods async, complicating
    the API. The lock is held for such a short time that event loop blocking
    is negligible.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_seconds: int = 300,
        half_open_max_attempts: int = 3
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Consecutive failures before opening circuit
            recovery_timeout_seconds: Time before attempting recovery
            half_open_max_attempts: Max attempts in half-open state
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.half_open_max_attempts = half_open_max_attempts

        self._lock = Lock()
        self._circuits: Dict[str, CircuitStatus] = {}

    def _get_or_create_circuit(self, provider_id: str) -> CircuitStatus:
        """Get or create circuit status for provider."""
        if provider_id not in self._circuits:
            self._circuits[provider_id] = CircuitStatus(provider_id=provider_id)
        return self._circuits[provider_id]

    def call(self, provider_id: str) -> bool:
        """
        Check if call is allowed for provider.

        Args:
            provider_id: Provider identifier

        Returns:
            True if call allowed, False if circuit is open
        """
        with self._lock:
            circuit = self._get_or_create_circuit(provider_id)

            # CLOSED: Always allow
            if circuit.is_closed:
                return True

            # OPEN: Check if recovery timeout has elapsed
            if circuit.is_open:
                if circuit.opened_at is None:
                    # Shouldn't happen, but allow call
                    return True

                elapsed = time.time() - circuit.opened_at
                if elapsed >= self.recovery_timeout_seconds:
                    # Transition to HALF_OPEN and count this as first attempt
                    circuit.state = CircuitState.HALF_OPEN
                    circuit.half_open_attempts = 1
                    logger.info(
                        f"[CircuitBreaker] {provider_id}: OPEN -> HALF_OPEN "
                        f"(recovery timeout elapsed: {elapsed:.1f}s)"
                    )
                    return True
                else:
                    # Still in recovery period
                    logger.debug(
                        f"[CircuitBreaker] {provider_id}: Call blocked (OPEN), "
                        f"recovery in {self.recovery_timeout_seconds - elapsed:.1f}s"
                    )
                    return False

            # HALF_OPEN: Allow limited attempts
            if circuit.is_half_open:
                if circuit.half_open_attempts >= self.half_open_max_attempts:
                    logger.debug(
                        f"[CircuitBreaker] {provider_id}: Call blocked (HALF_OPEN), "
                        f"max attempts reached ({self.half_open_max_attempts})"
                    )
                    return False

                circuit.half_open_attempts += 1
                return True

            return False

    def record_success(self, provider_id: str):
        """
        Record successful call.

        Args:
            provider_id: Provider identifier
        """
        with self._lock:
            circuit = self._get_or_create_circuit(provider_id)
            circuit.last_success_time = time.time()
            circuit.success_count += 1

            # CLOSED: Reset failure count
            if circuit.is_closed:
                if circuit.failure_count > 0:
                    logger.debug(
                        f"[CircuitBreaker] {provider_id}: Success, "
                        f"resetting failure count ({circuit.failure_count} -> 0)"
                    )
                    circuit.failure_count = 0

            # HALF_OPEN -> CLOSED: Recovery successful
            elif circuit.is_half_open:
                circuit.state = CircuitState.CLOSED
                circuit.failure_count = 0
                circuit.half_open_attempts = 0
                logger.info(
                    f"[CircuitBreaker] {provider_id}: HALF_OPEN -> CLOSED "
                    f"(recovery successful after {circuit.success_count} successes)"
                )

    def record_failure(self, provider_id: str):
        """
        Record failed call.

        Args:
            provider_id: Provider identifier
        """
        with self._lock:
            circuit = self._get_or_create_circuit(provider_id)
            circuit.last_failure_time = time.time()
            circuit.failure_count += 1

            # CLOSED: Check if threshold reached
            if circuit.is_closed:
                if circuit.failure_count >= self.failure_threshold:
                    circuit.state = CircuitState.OPEN
                    circuit.opened_at = time.time()
                    logger.warning(
                        f"[CircuitBreaker] {provider_id}: CLOSED -> OPEN "
                        f"(failure threshold reached: {circuit.failure_count}/{self.failure_threshold})"
                    )
                else:
                    logger.debug(
                        f"[CircuitBreaker] {provider_id}: Failure recorded "
                        f"({circuit.failure_count}/{self.failure_threshold})"
                    )

            # HALF_OPEN -> OPEN: Recovery failed
            elif circuit.is_half_open:
                circuit.state = CircuitState.OPEN
                circuit.opened_at = time.time()
                circuit.half_open_attempts = 0
                logger.warning(
                    f"[CircuitBreaker] {provider_id}: HALF_OPEN -> OPEN "
                    f"(recovery failed, failure count: {circuit.failure_count})"
                )

            # Already OPEN: Just increment counter
            elif circuit.is_open:
                logger.debug(
                    f"[CircuitBreaker] {provider_id}: Failure while OPEN "
                    f"(count: {circuit.failure_count})"
                )

    def reset(self, provider_id: str):
        """
        Manually reset circuit to CLOSED state.

        Args:
            provider_id: Provider identifier
        """
        with self._lock:
            circuit = self._get_or_create_circuit(provider_id)
            old_state = circuit.state

            circuit.state = CircuitState.CLOSED
            circuit.failure_count = 0
            circuit.success_count = 0
            circuit.half_open_attempts = 0
            circuit.opened_at = None

            logger.info(
                f"[CircuitBreaker] {provider_id}: {old_state.value} -> CLOSED (manual reset)"
            )

    def get_status(self, provider_id: str) -> CircuitStatus:
        """
        Get circuit status for provider.

        Args:
            provider_id: Provider identifier

        Returns:
            CircuitStatus instance
        """
        with self._lock:
            return self._get_or_create_circuit(provider_id)

    def get_all_statuses(self) -> Dict[str, CircuitStatus]:
        """
        Get all circuit statuses.

        Returns:
            Dictionary of provider_id -> CircuitStatus
        """
        with self._lock:
            return dict(self._circuits)

    def is_open(self, provider_id: str) -> bool:
        """
        Check if circuit is currently open for provider.

        Args:
            provider_id: Provider identifier

        Returns:
            True if circuit is open, False otherwise
        """
        with self._lock:
            circuit = self._get_or_create_circuit(provider_id)
            return circuit.is_open
