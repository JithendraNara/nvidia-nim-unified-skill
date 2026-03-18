"""Retry logic with exponential backoff and circuit breaker pattern."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar
from enum import Enum

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation, requests pass through
    OPEN = "open"          # Failing fast, no requests allowed
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    max_attempts: int = 3
    base_delay: float = 1.0  # seconds
    max_delay: float = 30.0  # seconds
    exponential_base: float = 2.0
    retryable_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)
    
    def get_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt with exponential backoff."""
        delay = self.base_delay * (self.exponential_base ** (attempt - 1))
        return min(delay, self.max_delay)


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5  # Open after this many consecutive failures
    recovery_timeout: float = 60.0  # Seconds to wait before trying again
    success_threshold: int = 2  # Close after this many successes in half-open


class CircuitBreaker:
    """Circuit breaker implementation to fail fast on repeated failures.
    
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: After failures exceed threshold, immediately reject requests
    - HALF_OPEN: After recovery timeout, allow one test request
    
    Usage:
        cb = CircuitBreaker("ocr", CircuitBreakerConfig())
        if cb.can_execute():
            try:
                result = await some_operation()
                cb.record_success()
            except Exception:
                cb.record_failure()
                raise
        else:
            raise CircuitOpenError("Circuit is open for capability: ocr")
    """
    
    def __init__(
        self,
        capability: str,
        config: CircuitBreakerConfig | None = None
    ):
        self.capability = capability
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._lock = asyncio.Lock()
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state, checking for timeout transition."""
        if self._state == CircuitState.OPEN:
            if self._last_failure_time:
                elapsed = time.time() - self._last_failure_time
                if elapsed >= self.config.recovery_timeout:
                    return CircuitState.HALF_OPEN
        return self._state
    
    def is_open(self) -> bool:
        """Check if circuit is open (rejecting requests)."""
        return self.state == CircuitState.OPEN
    
    def can_execute(self) -> bool:
        """Check if a request can be executed."""
        current_state = self.state
        return current_state != CircuitState.OPEN
    
    async def record_success(self) -> None:
        """Record a successful request."""
        async with self._lock:
            # Use self.state property to handle any timeout-based transitions
            current_state = self.state
            if current_state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self._close_circuit()
            elif current_state == CircuitState.CLOSED:
                self._failure_count = 0
                self._success_count = 0
    
    async def record_failure(self) -> None:
        """Record a failed request."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            # Use self.state property to handle any timeout-based transitions
            current_state = self.state
            if current_state == CircuitState.HALF_OPEN:
                self._open_circuit()
            elif current_state == CircuitState.CLOSED and self._failure_count >= self.config.failure_threshold:
                self._open_circuit()
    
    def _open_circuit(self) -> None:
        """Open the circuit (fail-fast mode)."""
        self._state = CircuitState.OPEN
        self._success_count = 0
    
    def _close_circuit(self) -> None:
        """Close the circuit (normal operation)."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
    
    def get_status(self) -> dict[str, Any]:
        """Get circuit breaker status for observability."""
        return {
            "capability": self.capability,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure_time": self._last_failure_time,
        }


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open."""
    def __init__(self, capability: str, retry_after: float | None = None):
        self.capability = capability
        self.retry_after = retry_after
        msg = f"Circuit breaker is open for capability: {capability}"
        if retry_after:
            msg += f". Retry after {retry_after:.1f} seconds"
        super().__init__(msg)


class ExponentialBackoff:
    """Generator for exponential backoff delays with jitter."""
    
    def __init__(
        self,
        config: RetryConfig | None = None,
        jitter: float = 0.1
    ):
        self.config = config or RetryConfig()
        self.jitter = jitter
    
    def get_delay(self, attempt: int) -> float:
        """Get delay for attempt with optional jitter."""
        import random
        delay = self.config.get_delay(attempt)
        # Add jitter (±10% by default)
        jitter_range = delay * self.jitter
        delay += random.uniform(-jitter_range, jitter_range)
        return max(0, delay)
    
    def __iter__(self):
        """Iterate over delays for retry attempts."""
        for attempt in range(1, self.config.max_attempts + 1):
            yield self.get_delay(attempt)


async def with_retry(
    operation: Callable[..., Any],
    config: RetryConfig | None = None,
    circuit_breaker: CircuitBreaker | None = None,
    *args,
    **kwargs
) -> Any:
    """Execute operation with retry and circuit breaker.
    
    Args:
        operation: Async callable to execute
        config: Retry configuration
        circuit_breaker: Optional circuit breaker
        *args, **kwargs: Arguments to pass to operation
        
    Returns:
        Result of operation
        
    Raises:
        CircuitOpenError: If circuit is open
        Exception: If all retries exhausted
    """
    import asyncio
    
    retry_config = config or RetryConfig()
    backoff = ExponentialBackoff(retry_config)
    
    last_exception = None
    
    for attempt in range(1, retry_config.max_attempts + 1):
        # Check circuit breaker
        if circuit_breaker and not circuit_breaker.can_execute():
            raise CircuitOpenError(circuit_breaker.capability)
        
        try:
            result = await operation(*args, **kwargs)
            if circuit_breaker:
                await circuit_breaker.record_success()
            return result
            
        except Exception as exc:
            last_exception = exc
            
            # Check if status is retryable
            should_retry = False
            if hasattr(exc, 'status'):
                if exc.status in retry_config.retryable_statuses:
                    should_retry = True
            elif hasattr(exc, 'code'):
                if exc.code in retry_config.retryable_statuses:
                    should_retry = True
            
            if not should_retry:
                raise
            
            if circuit_breaker:
                await circuit_breaker.record_failure()
            
            # Don't wait after last attempt
            if attempt < retry_config.max_attempts:
                delay = next(backoff)
                await asyncio.sleep(delay)
    
    raise last_exception
