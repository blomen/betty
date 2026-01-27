"""
Tests for circuit breaker functionality.
"""

import time
import pytest
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from src.pipeline.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitStatus
)


def test_circuit_status_properties():
    """Test CircuitStatus property methods."""
    status = CircuitStatus(provider_id="test")

    assert status.is_closed
    assert not status.is_open
    assert not status.is_half_open

    status.state = CircuitState.OPEN
    assert not status.is_closed
    assert status.is_open
    assert not status.is_half_open

    status.state = CircuitState.HALF_OPEN
    assert not status.is_closed
    assert not status.is_open
    assert status.is_half_open


def test_circuit_breaker_init():
    """Test CircuitBreaker initialization."""
    cb = CircuitBreaker(
        failure_threshold=3,
        recovery_timeout_seconds=60,
        half_open_max_attempts=2
    )

    assert cb.failure_threshold == 3
    assert cb.recovery_timeout_seconds == 60
    assert cb.half_open_max_attempts == 2


def test_circuit_breaker_closed_state():
    """Test circuit breaker in CLOSED state (normal operation)."""
    cb = CircuitBreaker(failure_threshold=3)

    # Initially closed, calls allowed
    assert cb.call("provider1")
    assert cb.call("provider1")

    # Check status
    status = cb.get_status("provider1")
    assert status.is_closed
    assert status.failure_count == 0


def test_circuit_breaker_records_success():
    """Test recording successful calls."""
    cb = CircuitBreaker(failure_threshold=3)

    # Record some failures
    cb.record_failure("provider1")
    cb.record_failure("provider1")
    assert cb.get_status("provider1").failure_count == 2

    # Record success - should reset failure count
    cb.record_success("provider1")
    status = cb.get_status("provider1")
    assert status.failure_count == 0
    assert status.success_count == 1
    assert status.is_closed


def test_circuit_breaker_opens_after_threshold():
    """Test circuit opens after failure threshold."""
    cb = CircuitBreaker(failure_threshold=3)

    # Record failures
    cb.record_failure("provider1")
    assert cb.get_status("provider1").is_closed

    cb.record_failure("provider1")
    assert cb.get_status("provider1").is_closed

    cb.record_failure("provider1")
    # Should transition to OPEN
    status = cb.get_status("provider1")
    assert status.is_open
    assert status.failure_count == 3
    assert status.opened_at is not None


def test_circuit_breaker_blocks_when_open():
    """Test circuit blocks calls when OPEN."""
    cb = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout_seconds=10
    )

    # Open the circuit
    cb.record_failure("provider1")
    cb.record_failure("provider1")
    assert cb.get_status("provider1").is_open

    # Calls should be blocked
    assert not cb.call("provider1")
    assert not cb.call("provider1")


def test_circuit_breaker_recovery_timeout():
    """Test circuit transitions to HALF_OPEN after recovery timeout."""
    cb = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout_seconds=1  # 1 second for testing
    )

    # Open the circuit
    cb.record_failure("provider1")
    cb.record_failure("provider1")
    assert cb.get_status("provider1").is_open

    # Wait for recovery timeout
    time.sleep(1.1)

    # Next call should transition to HALF_OPEN and allow call
    assert cb.call("provider1")
    status = cb.get_status("provider1")
    assert status.is_half_open
    assert status.half_open_attempts == 1


def test_circuit_breaker_half_open_limits_attempts():
    """Test circuit in HALF_OPEN state limits attempts."""
    cb = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout_seconds=1,
        half_open_max_attempts=2
    )

    # Open the circuit
    cb.record_failure("provider1")
    cb.record_failure("provider1")

    # Wait and transition to HALF_OPEN
    time.sleep(1.1)
    assert cb.call("provider1")  # Attempt 1
    assert cb.call("provider1")  # Attempt 2

    # Third attempt should be blocked
    assert not cb.call("provider1")


def test_circuit_breaker_half_open_to_closed():
    """Test circuit transitions from HALF_OPEN to CLOSED on success."""
    cb = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout_seconds=1
    )

    # Open the circuit
    cb.record_failure("provider1")
    cb.record_failure("provider1")

    # Wait and transition to HALF_OPEN
    time.sleep(1.1)
    cb.call("provider1")
    assert cb.get_status("provider1").is_half_open

    # Record success - should close circuit
    cb.record_success("provider1")
    status = cb.get_status("provider1")
    assert status.is_closed
    assert status.failure_count == 0
    assert status.half_open_attempts == 0


def test_circuit_breaker_half_open_to_open():
    """Test circuit transitions from HALF_OPEN back to OPEN on failure."""
    cb = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout_seconds=1
    )

    # Open the circuit
    cb.record_failure("provider1")
    cb.record_failure("provider1")

    # Wait and transition to HALF_OPEN
    time.sleep(1.1)
    cb.call("provider1")
    assert cb.get_status("provider1").is_half_open

    # Record failure - should reopen circuit
    cb.record_failure("provider1")
    status = cb.get_status("provider1")
    assert status.is_open
    assert status.opened_at is not None
    assert status.half_open_attempts == 0


def test_circuit_breaker_manual_reset():
    """Test manual circuit reset."""
    cb = CircuitBreaker(failure_threshold=2)

    # Open the circuit
    cb.record_failure("provider1")
    cb.record_failure("provider1")
    assert cb.get_status("provider1").is_open

    # Manual reset
    cb.reset("provider1")
    status = cb.get_status("provider1")
    assert status.is_closed
    assert status.failure_count == 0
    assert status.success_count == 0
    assert status.opened_at is None


def test_circuit_breaker_multiple_providers():
    """Test circuit breaker with multiple providers."""
    cb = CircuitBreaker(failure_threshold=2)

    # Fail provider1
    cb.record_failure("provider1")
    cb.record_failure("provider1")

    # provider1 should be open
    assert cb.get_status("provider1").is_open

    # provider2 should still be closed
    assert cb.get_status("provider2").is_closed
    assert cb.call("provider2")


def test_circuit_breaker_get_all_statuses():
    """Test getting all circuit statuses."""
    cb = CircuitBreaker(failure_threshold=2)

    # Create some circuits
    cb.record_failure("provider1")
    cb.record_success("provider2")
    cb.record_failure("provider3")
    cb.record_failure("provider3")

    statuses = cb.get_all_statuses()
    assert len(statuses) == 3
    assert "provider1" in statuses
    assert "provider2" in statuses
    assert "provider3" in statuses

    assert statuses["provider1"].is_closed
    assert statuses["provider2"].is_closed
    assert statuses["provider3"].is_open


def test_circuit_breaker_is_open_helper():
    """Test is_open helper method."""
    cb = CircuitBreaker(failure_threshold=2)

    assert not cb.is_open("provider1")

    cb.record_failure("provider1")
    cb.record_failure("provider1")

    assert cb.is_open("provider1")
    assert not cb.is_open("provider2")


def test_circuit_breaker_failure_while_open():
    """Test recording failure while circuit is already open."""
    cb = CircuitBreaker(failure_threshold=2)

    # Open the circuit
    cb.record_failure("provider1")
    cb.record_failure("provider1")
    assert cb.get_status("provider1").is_open
    assert cb.get_status("provider1").failure_count == 2

    # Record another failure while open
    cb.record_failure("provider1")
    status = cb.get_status("provider1")
    assert status.is_open
    assert status.failure_count == 3


def test_circuit_breaker_thread_safety():
    """Test circuit breaker thread safety (basic check)."""
    import threading

    cb = CircuitBreaker(failure_threshold=10)

    def record_failures():
        for _ in range(5):
            cb.record_failure("provider1")

    def record_successes():
        for _ in range(3):
            cb.record_success("provider1")

    # Run in parallel
    t1 = threading.Thread(target=record_failures)
    t2 = threading.Thread(target=record_successes)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    status = cb.get_status("provider1")
    # With threshold=10, should still be closed
    assert status.is_closed
    # Failure count should be 0 (reset by successes) or some value < 10
    assert status.failure_count < 10


def test_circuit_breaker_timestamps():
    """Test circuit breaker tracks timestamps correctly."""
    cb = CircuitBreaker(failure_threshold=2)

    start = time.time()

    cb.record_success("provider1")
    status = cb.get_status("provider1")
    assert status.last_success_time is not None
    assert status.last_success_time >= start

    time.sleep(0.1)

    cb.record_failure("provider1")
    cb.record_failure("provider1")
    status = cb.get_status("provider1")
    assert status.last_failure_time is not None
    assert status.last_failure_time > status.last_success_time
    assert status.opened_at is not None
    assert status.opened_at >= start
