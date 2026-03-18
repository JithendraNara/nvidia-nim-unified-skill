"""Token bucket rate limiter with per-capability limits.

This module implements rate limiting using the token bucket algorithm:
- Each capability has its own bucket
- Tokens are added at a constant rate (requests_per_minute / 60 per second)
- Each request consumes one token
- If no tokens available, wait until one becomes available

Supports:
- Per-capability rate limits
- In-memory storage (default)
- Optional Redis backend for distributed scenarios
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
from enum import Enum

# Optional Redis support
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None


class RateLimitSource(Enum):
    """Source of rate limit configuration."""
    IN_MEMORY = "in_memory"
    REDIS = "redis"


@dataclass
class RateLimitConfig:
    """Configuration for rate limiter behavior."""
    # Default requests per minute for all capabilities
    requests_per_minute: int = 60
    # Per-capability overrides (capability_name -> requests_per_minute)
    per_capability: dict[str, int] = field(default_factory=dict)
    # Enable Redis backend for distributed rate limiting
    redis_url: str | None = None
    # Redis key prefix
    redis_prefix: str = "nim_router:ratelimit:"
    # TTL for Redis keys (seconds)
    redis_ttl: int = 3600


@dataclass
class TokenBucket:
    """Token bucket for rate limiting.
    
    Attributes:
        capacity: Maximum tokens in bucket
        tokens: Current tokens available
        last_update: Last time tokens were added (timestamp)
        refill_rate: Tokens added per second
    """
    capacity: float
    tokens: float
    last_update: float
    refill_rate: float  # tokens per second


class RateLimiterStorage:
    """Storage backend for rate limiter state.
    
    Provides an abstraction layer that can use either:
    - In-memory storage (default, single process)
    - Redis (distributed, multiple processes/instances)
    """
    
    def __init__(self, config: RateLimitConfig):
        self.config = config
        self._in_memory: dict[str, TokenBucket] = {}
        self._redis_client: redis.Redis | None = None
        self._source = RateLimitSource.IN_MEMORY
    
    async def initialize(self) -> None:
        """Initialize the storage backend."""
        if self.config.redis_url and REDIS_AVAILABLE:
            try:
                self._redis_client = redis.from_url(
                    self.config.redis_url,
                    encoding="utf-8",
                    decode_responses=True
                )
                # Test connection
                await self._redis_client.ping()
                self._source = RateLimitSource.REDIS
                print(f"[rate-limiter] Using Redis backend at {self.config.redis_url}")
            except Exception as exc:
                print(f"[rate-limiter] Failed to connect to Redis: {exc}, using in-memory storage")
                self._source = RateLimitSource.IN_MEMORY
        else:
            if self.config.redis_url and not REDIS_AVAILABLE:
                print(f"[rate-limiter] Redis not available, using in-memory storage")
            self._source = RateLimitSource.IN_MEMORY
    
    async def close(self) -> None:
        """Close the storage backend."""
        if self._redis_client:
            await self._redis_client.close()
    
    def get_bucket(self, capability: str) -> TokenBucket:
        """Get token bucket for a capability (in-memory)."""
        if capability not in self._in_memory:
            # Get rate limit for this capability
            rpm = self.config.per_capability.get(capability, self.config.requests_per_minute)
            capacity = rpm / 60.0  # Convert to per-second rate
            self._in_memory[capability] = TokenBucket(
                capacity=rpm,  # Bucket holds up to rpm tokens
                tokens=rpm,    # Start with full bucket
                last_update=time.time(),
                refill_rate=capacity  # tokens per second
            )
        return self._in_memory[capability]
    
    async def get_bucket_redis(self, capability: str) -> TokenBucket:
        """Get token bucket for a capability (Redis)."""
        if not self._redis_client:
            raise RuntimeError("Redis not initialized")
        
        key = f"{self.config.redis_prefix}{capability}"
        data = await self._redis_client.hgetall(key)
        
        # Get rate limit for this capability
        rpm = self.config.per_capability.get(capability, self.config.requests_per_minute)
        capacity = rpm
        
        if not data:
            # Initialize new bucket
            bucket = TokenBucket(
                capacity=rpm,
                tokens=rpm,
                last_update=time.time(),
                refill_rate=rpm / 60.0
            )
            await self._save_bucket_redis(key, bucket)
            return bucket
        
        # Parse existing bucket
        bucket = TokenBucket(
            capacity=float(data.get("capacity", str(rpm))),
            tokens=float(data.get("tokens", str(rpm))),
            last_update=float(data.get("last_update", str(time.time()))),
            refill_rate=float(data.get("refill_rate", str(rpm / 60.0)))
        )
        
        return bucket
    
    async def _save_bucket_redis(self, key: str, bucket: TokenBucket) -> None:
        """Save bucket state to Redis."""
        if not self._redis_client:
            raise RuntimeError("Redis not initialized")
        
        await self._redis_client.hset(key, mapping={
            "capacity": str(bucket.capacity),
            "tokens": str(bucket.tokens),
            "last_update": str(bucket.last_update),
            "refill_rate": str(bucket.refill_rate)
        })
        await self._redis_client.expire(key, self.config.redis_ttl)
    
    async def save_bucket(self, capability: str, bucket: TokenBucket) -> None:
        """Save token bucket for a capability."""
        if self._source == RateLimitSource.REDIS:
            key = f"{self.config.redis_prefix}{capability}"
            await self._save_bucket_redis(key, bucket)
        else:
            self._in_memory[capability] = bucket


class RateLimiter:
    """Token bucket rate limiter for API requests.
    
    Implements the token bucket algorithm to limit requests per capability:
    - Each capability has its own token bucket
    - Tokens are added at a constant rate (requests_per_minute / 60 per second)
    - Each request consumes one token
    - If no tokens available, wait until one becomes available
    
    Usage:
        config = RateLimitConfig(requests_per_minute=60)
        limiter = RateLimiter(config)
        await limiter.initialize()
        
        # Before making a request
        wait_time = await limiter.acquire("ocr")
        if wait_time > 0:
            print(f"Rate limited, waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)
        
        # Make the request...
        await limiter.record_request("ocr")
        
        await limiter.close()
    """
    
    def __init__(self, config: RateLimitConfig | None = None):
        self.config = config or RateLimitConfig()
        self._storage = RateLimiterStorage(self.config)
        self._lock = asyncio.Lock()
    
    async def initialize(self) -> None:
        """Initialize the rate limiter."""
        await self._storage.initialize()
    
    async def close(self) -> None:
        """Close the rate limiter and cleanup resources."""
        await self._storage.close()
    
    def _get_rate_limit(self, capability: str) -> int:
        """Get the rate limit for a specific capability."""
        return self.config.per_capability.get(
            capability,
            self.config.requests_per_minute
        )
    
    def _refill_bucket(self, bucket: TokenBucket) -> None:
        """Refill tokens in bucket based on elapsed time.
        
        Args:
            bucket: Token bucket to refill
        """
        now = time.time()
        elapsed = now - bucket.last_update
        
        # Add tokens based on elapsed time
        new_tokens = elapsed * bucket.refill_rate
        bucket.tokens = min(bucket.capacity, bucket.tokens + new_tokens)
        bucket.last_update = now
    
    async def acquire(self, capability: str) -> float:
        """Acquire a token for the given capability.
        
        If no token is available, waits until one becomes available.
        
        Args:
            capability: The capability name (e.g., "ocr", "rerank")
            
        Returns:
            Actual wait time in seconds before token was acquired
        """
        async with self._lock:
            # Get or create bucket
            if self._storage._source == RateLimitSource.REDIS:
                bucket = await self._storage.get_bucket_redis(capability)
            else:
                bucket = self._storage.get_bucket(capability)
            
            # Refill tokens based on elapsed time
            self._refill_bucket(bucket)
            
            if bucket.tokens >= 1.0:
                # Token available, consume it
                bucket.tokens -= 1.0
                await self._storage.save_bucket(capability, bucket)
                return 0.0
            
            # Calculate wait time for next token
            tokens_needed = 1.0 - bucket.tokens
            
            # Handle zero refill rate (no new tokens ever)
            if bucket.refill_rate <= 0:
                # Will never get new tokens, wait forever
                await self._storage.save_bucket(capability, bucket)
                return float('inf')
            
            wait_time = tokens_needed / bucket.refill_rate
            
            # Update bucket state (will have 1 token after wait_time)
            bucket.tokens = 0.0
            bucket.last_update = time.time() - (tokens_needed / bucket.refill_rate) + (1.0 / bucket.refill_rate)
            await self._storage.save_bucket(capability, bucket)
            
            return wait_time
    
    async def try_acquire(self, capability: str) -> tuple[bool, float]:
        """Try to acquire a token without blocking.
        
        Args:
            capability: The capability name
            
        Returns:
            Tuple of (acquired: bool, wait_time: float)
            - acquired: True if token was acquired
            - wait_time: 0.0 if acquired, otherwise estimated wait time
        """
        async with self._lock:
            if self._storage._source == RateLimitSource.REDIS:
                bucket = await self._storage.get_bucket_redis(capability)
            else:
                bucket = self._storage.get_bucket(capability)
            
            self._refill_bucket(bucket)
            
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                await self._storage.save_bucket(capability, bucket)
                return True, 0.0
            
            tokens_needed = 1.0 - bucket.tokens
            
            # Handle zero refill rate (no new tokens ever)
            if bucket.refill_rate <= 0:
                return False, float('inf')
            
            wait_time = tokens_needed / bucket.refill_rate
            return False, wait_time
    
    async def record_request(self, capability: str) -> None:
        """Record that a request was made for a capability.
        
        This is called after a successful request to update rate limiting.
        
        Args:
            capability: The capability name
        """
        # Token was already consumed in acquire(), nothing to do here
        pass
    
    def get_status(self, capability: str) -> dict[str, Any]:
        """Get rate limiter status for a capability.
        
        Args:
            capability: The capability name
            
        Returns:
            Dict with rate limit status
        """
        bucket = self._storage.get_bucket(capability)
        self._refill_bucket(bucket)
        
        rate_limit = self._get_rate_limit(capability)
        
        return {
            "capability": capability,
            "rate_limit_rpm": rate_limit,
            "available_tokens": bucket.tokens,
            "capacity": bucket.capacity,
            "refill_rate_per_second": bucket.refill_rate,
            "source": self._storage._source.value
        }
    
    def get_all_status(self) -> dict[str, dict[str, Any]]:
        """Get rate limiter status for all tracked capabilities.
        
        Returns:
            Dict mapping capability name to status
        """
        status = {}
        for capability in self._storage._in_memory.keys():
            status[capability] = self.get_status(capability)
        return status


class RateLimitExceededError(Exception):
    """Raised when rate limit is exceeded and cannot wait."""
    
    def __init__(self, capability: str, wait_time: float, retry_after: float | None = None):
        self.capability = capability
        self.wait_time = wait_time
        self.retry_after = retry_after
        msg = f"Rate limit exceeded for {capability}"
        if wait_time > 0:
            msg += f", wait {wait_time:.2f}s"
        if retry_after:
            msg += f", retry after {retry_after:.1f}s"
        super().__init__(msg)


async def with_rate_limit(
    capability: str,
    limiter: RateLimiter,
    operation: Any,
    *args,
    **kwargs
) -> Any:
    """Execute operation with rate limiting.
    
    Args:
        capability: The capability name
        limiter: RateLimiter instance
        operation: Async callable to execute
        *args, **kwargs: Arguments to pass to operation
        
    Returns:
        Result of operation
        
    Raises:
        RateLimitExceededError: If rate limit cannot be satisfied
    """
    wait_time = await limiter.acquire(capability)
    
    if wait_time > 0:
        print(f"[rate-limit] Rate limited for {capability}, waiting {wait_time:.2f}s...")
        await asyncio.sleep(wait_time)
    
    return await operation(*args, **kwargs)


# Global rate limiter instance
_rate_limiter: RateLimiter | None = None
_rate_limiter_config: RateLimitConfig | None = None


def get_rate_limiter(config: RateLimitConfig | None = None) -> RateLimiter:
    """Get or create the global rate limiter instance.
    
    Args:
        config: Optional rate limit configuration
        
    Returns:
        RateLimiter instance
    """
    global _rate_limiter, _rate_limiter_config
    
    if config is not None and config != _rate_limiter_config:
        _rate_limiter_config = config
        _rate_limiter = RateLimiter(config)
    
    if _rate_limiter is None:
        _rate_limiter_config = config or RateLimitConfig()
        _rate_limiter = RateLimiter(_rate_limiter_config)
    
    return _rate_limiter


async def initialize_rate_limiter(config: RateLimitConfig | None = None) -> RateLimiter:
    """Initialize the global rate limiter.
    
    Args:
        config: Optional rate limit configuration
        
    Returns:
        Initialized RateLimiter instance
    """
    limiter = get_rate_limiter(config)
    await limiter.initialize()
    return limiter


async def close_rate_limiter() -> None:
    """Close the global rate limiter and cleanup resources."""
    global _rate_limiter
    
    if _rate_limiter:
        await _rate_limiter.close()
        _rate_limiter = None


def parse_rate_limit_config(config_dict: dict[str, Any]) -> RateLimitConfig:
    """Parse rate limit configuration from config dict.
    
    Supports:
    - requests_per_minute: Global default
    - per_capability: Dict of capability -> requests_per_minute
    - redis_url: Optional Redis backend URL
    
    Example config:
        {
            "rate_limit": {
                "requests_per_minute": 60,
                "per_capability": {
                    "ocr": 30,
                    "rerank": 120
                },
                "redis_url": "redis://localhost:6379"
            }
        }
    
    Args:
        config_dict: Configuration dict from config.json
        
    Returns:
        RateLimitConfig instance
    """
    rate_limit_config = config_dict.get("rate_limit", {})
    
    requests_per_minute = rate_limit_config.get(
        "requests_per_minute",
        60  # Default to 60 req/min
    )
    
    per_capability = rate_limit_config.get("per_capability", {})
    
    redis_url = rate_limit_config.get("redis_url")
    
    return RateLimitConfig(
        requests_per_minute=requests_per_minute,
        per_capability=per_capability,
        redis_url=redis_url
    )
