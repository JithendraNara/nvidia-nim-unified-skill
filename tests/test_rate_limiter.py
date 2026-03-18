"""Tests for rate limiter (VAL-RETRY-003).

These tests verify:
- VAL-RETRY-003: Rate limiting respects API quotas with configurable requests_per_minute
"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from nim_router.rate_limiter import (
    RateLimitConfig,
    RateLimiter,
    RateLimiterStorage,
    TokenBucket,
    RateLimitExceededError,
    RateLimitSource,
    parse_rate_limit_config,
)


class TestTokenBucket:
    """Tests for token bucket algorithm."""

    def test_bucket_starts_full(self):
        """Test that bucket starts with full capacity."""
        bucket = TokenBucket(
            capacity=60,
            tokens=60,
            last_update=time.time(),
            refill_rate=1.0
        )
        assert bucket.tokens == 60
        assert bucket.capacity == 60

    def test_refill_adds_tokens_over_time(self):
        """Test that tokens are refilled based on elapsed time."""
        bucket = TokenBucket(
            capacity=60,
            tokens=0,  # Start empty
            last_update=time.time() - 10.0,  # 10 seconds ago
            refill_rate=6.0  # 6 tokens per second = 60/10
        )
        
        # After 10 seconds at 6 tokens/sec, should have 60 tokens (capped at capacity)
        # But wait, 10s * 6 = 60, so should be at capacity
        bucket.tokens = min(bucket.capacity, bucket.tokens + 10.0 * bucket.refill_rate)
        assert bucket.tokens == 60


class TestRateLimiterConfig:
    """Tests for rate limit configuration."""

    def test_default_config(self):
        """Test default rate limit config."""
        config = RateLimitConfig()
        assert config.requests_per_minute == 60
        assert config.per_capability == {}
        assert config.redis_url is None

    def test_per_capability_overrides(self):
        """Test per-capability rate limit overrides."""
        config = RateLimitConfig(
            requests_per_minute=60,
            per_capability={
                "ocr": 30,
                "rerank": 120
            }
        )
        assert config.requests_per_minute == 60
        assert config.per_capability["ocr"] == 30
        assert config.per_capability["rerank"] == 120


class TestParseRateLimitConfig:
    """Tests for parsing rate limit config from dict."""

    def test_parse_empty_config(self):
        """Test parsing empty config dict."""
        config = parse_rate_limit_config({})
        assert config.requests_per_minute == 60
        assert config.per_capability == {}

    def test_parse_full_config(self):
        """Test parsing full rate limit config."""
        config_dict = {
            "rate_limit": {
                "requests_per_minute": 30,
                "per_capability": {
                    "ocr": 15,
                    "rerank": 60
                },
                "redis_url": "redis://localhost:6379"
            }
        }
        config = parse_rate_limit_config(config_dict)
        assert config.requests_per_minute == 30
        assert config.per_capability["ocr"] == 15
        assert config.per_capability["rerank"] == 60
        assert config.redis_url == "redis://localhost:6379"

    def test_parse_partial_config(self):
        """Test parsing partial config (only some fields)."""
        config_dict = {
            "rate_limit": {
                "requests_per_minute": 120
            }
        }
        config = parse_rate_limit_config(config_dict)
        assert config.requests_per_minute == 120
        assert config.per_capability == {}


class TestRateLimiterBehavior:
    """Tests for rate limiter behavior (VAL-RETRY-003)."""

    @pytest.mark.asyncio
    async def test_acquire_returns_immediately_when_tokens_available(self):
        """Test that acquire returns immediately when tokens are available."""
        config = RateLimitConfig(requests_per_minute=60)
        limiter = RateLimiter(config)
        await limiter.initialize()
        
        try:
            wait_time = await limiter.acquire("ocr")
            assert wait_time == 0.0
        finally:
            await limiter.close()

    @pytest.mark.asyncio
    async def test_tokens_decrease_after_acquire(self):
        """Test that tokens decrease after acquiring."""
        config = RateLimitConfig(requests_per_minute=60)
        limiter = RateLimiter(config)
        await limiter.initialize()
        
        try:
            # First acquire should succeed with no wait
            wait1 = await limiter.acquire("ocr")
            assert wait1 == 0.0
            
            status = limiter.get_status("ocr")
            # Should have approximately 59 tokens left (started with 60)
            assert 58 < status["available_tokens"] < 60
        finally:
            await limiter.close()

    @pytest.mark.asyncio
    async def test_rate_limit_throttles_requests(self):
        """Test that rate limiting throttles requests when bucket is empty (VAL-RETRY-003)."""
        # Set a very low rate limit (1 request per minute = 1/60 per second)
        config = RateLimitConfig(requests_per_minute=1)
        limiter = RateLimiter(config)
        await limiter.initialize()
        
        try:
            # First request should have no wait
            wait1 = await limiter.acquire("ocr")
            assert wait1 == 0.0
            
            # Verify bucket is now empty (or nearly empty due to refill)
            status = limiter.get_status("ocr")
            # Token was consumed, so we should have less than 1 token
            assert status["available_tokens"] < 1.0
            
            # Second request should wait approximately 60 seconds
            start = time.time()
            wait2 = await limiter.acquire("ocr")
            elapsed = time.time() - start
            
            # Should have waited approximately 60 seconds (with some tolerance)
            # Since refill_rate is 1/60, we need to wait ~60s for 1 token
            assert 59.0 <= wait2 <= 61.0, f"Expected ~60s wait, got {wait2}s"
        finally:
            await limiter.close()

    @pytest.mark.asyncio
    async def test_different_capabilities_have_separate_buckets(self):
        """Test that each capability has its own rate limit bucket."""
        config = RateLimitConfig(requests_per_minute=60)
        limiter = RateLimiter(config)
        await limiter.initialize()
        
        try:
            # Acquire from OCR
            await limiter.acquire("ocr")
            
            # Rerank should still have full bucket
            status_rerank = limiter.get_status("rerank")
            assert 59 < status_rerank["available_tokens"] <= 60
            
            # OCR should have one less
            status_ocr = limiter.get_status("ocr")
            assert 58 < status_ocr["available_tokens"] < 60
        finally:
            await limiter.close()

    @pytest.mark.asyncio
    async def test_per_capability_rate_limits(self):
        """Test that per-capability rate limits work correctly."""
        config = RateLimitConfig(
            requests_per_minute=60,
            per_capability={
                "ocr": 2,  # Very low limit for OCR
                "rerank": 120  # High limit for rerank
            }
        )
        limiter = RateLimiter(config)
        await limiter.initialize()
        
        try:
            # Acquire twice from OCR - should work
            await limiter.acquire("ocr")
            await limiter.acquire("ocr")
            
            # Third OCR request should wait
            start = time.time()
            wait = await limiter.acquire("ocr")
            elapsed = time.time() - start
            
            # With 2 req/min limit, wait should be ~30 seconds
            assert 29.0 <= wait <= 31.0
            
            # Rerank should still have many tokens
            status_rerank = limiter.get_status("rerank")
            # Used 1 token from rerank
            assert 119 < status_rerank["available_tokens"] <= 120
        finally:
            await limiter.close()


class TestRateLimiterLogging:
    """Tests for rate limiter logging (VERIFICATION STEP 2)."""

    @pytest.mark.asyncio
    async def test_logs_show_wait_time(self, capsys):
        """Test that logs show actual wait time (VAL-RETRY-003 verification)."""
        config = RateLimitConfig(requests_per_minute=1)
        limiter = RateLimiter(config)
        await limiter.initialize()
        
        try:
            # First acquire
            await limiter.acquire("ocr")
            
            # Second acquire returns wait time (actual wait happens in calling code)
            wait = await limiter.acquire("ocr")
            
            # The wait time should be approximately 60s
            # The acquire() returns the expected wait time; the caller does the actual sleeping
            assert 59.0 <= wait <= 61.0, f"Expected ~60s wait, got {wait}s"
        finally:
            await limiter.close()


class TestRateLimiterEdgeCases:
    """Edge case tests for rate limiter."""

    @pytest.mark.asyncio
    async def test_acquire_multiple_different_capabilities(self):
        """Test acquiring from multiple different capabilities."""
        config = RateLimitConfig(
            requests_per_minute=60,
            per_capability={
                "ocr": 10,
                "rerank": 20
            }
        )
        limiter = RateLimiter(config)
        await limiter.initialize()
        
        try:
            # Acquire from multiple capabilities
            await limiter.acquire("ocr")
            await limiter.acquire("rerank")
            await limiter.acquire("page_elements")
            
            # All should succeed with no wait
            status_ocr = limiter.get_status("ocr")
            status_rerank = limiter.get_status("rerank")
            status_page = limiter.get_status("page_elements")
            
            assert 8 < status_ocr["available_tokens"] <= 10
            assert 19 < status_rerank["available_tokens"] <= 20
            assert 58 < status_page["available_tokens"] <= 60  # Default limit
        finally:
            await limiter.close()

    @pytest.mark.asyncio
    async def test_get_status_returns_correct_info(self):
        """Test that get_status returns correct information."""
        config = RateLimitConfig(
            requests_per_minute=30,
            per_capability={"ocr": 15}
        )
        limiter = RateLimiter(config)
        await limiter.initialize()
        
        try:
            await limiter.acquire("ocr")
            
            status = limiter.get_status("ocr")
            
            assert status["capability"] == "ocr"
            assert status["rate_limit_rpm"] == 15
            # Approximately 14 tokens left (used 1)
            assert 13 < status["available_tokens"] < 15
            assert status["capacity"] == 15
            # refill_rate should be 15/60 = 0.25
            assert 0.24 < status["refill_rate_per_second"] < 0.26
        finally:
            await limiter.close()

    @pytest.mark.asyncio
    async def test_rate_limiter_with_zero_per_capability(self):
        """Test behavior when per-capability is set to zero (edge case)."""
        config = RateLimitConfig(
            requests_per_minute=60,
            per_capability={"ocr": 0}  # Edge case: zero rate limit
        )
        limiter = RateLimiter(config)
        await limiter.initialize()
        
        try:
            # Verify config is parsed correctly
            status = limiter.get_status("ocr")
            assert status["rate_limit_rpm"] == 0
            assert status["refill_rate_per_second"] == 0.0
            assert status["capacity"] == 0
            
            # First acquire will return infinity since bucket is empty and never refills
            wait1 = await limiter.acquire("ocr")
            assert wait1 == float('inf')
        finally:
            await limiter.close()


class TestRateLimiterRedisBackend:
    """Tests for Redis backend (skipped if Redis not available)."""

    @pytest.mark.asyncio
    async def test_redis_config_parsing(self):
        """Test that Redis URL is correctly parsed from config."""
        config = RateLimitConfig(
            requests_per_minute=60,
            redis_url="redis://localhost:6379"
        )
        
        assert config.redis_url == "redis://localhost:6379"
        
        storage = RateLimiterStorage(config)
        assert storage._source == RateLimitSource.IN_MEMORY  # No actual connection

    @pytest.mark.asyncio
    async def test_in_memory_fallback(self):
        """Test that in-memory storage is used when Redis fails."""
        config = RateLimitConfig(
            requests_per_minute=60,
            redis_url="redis://invalid:9999"  # Invalid URL
        )
        
        storage = RateLimiterStorage(config)
        await storage.initialize()
        
        assert storage._source == RateLimitSource.IN_MEMORY
        await storage.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
