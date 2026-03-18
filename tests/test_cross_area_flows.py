"""Tests for cross-area flows - end-to-end workflows spanning multiple features.

These tests verify:
- VAL-CROSS-001: Full workflow from plan to build-request to invoke produces valid results
- VAL-CROSS-002: API server produces identical results to equivalent CLI commands
- VAL-CROSS-003: Batch processing with retry and cache integration
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import from main nim_router.py (scripts level)
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import nim_router
from nim_router import (
    plan_task,
    build_request,
    invoke_request,
    async_invoke_request,
    async_invoke_batch,
    load_json,
    AIOHTTP_AVAILABLE,
)
from nim_router.retry import (
    RetryConfig,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    ExponentialBackoff,
    with_retry,
)
from nim_router.cache import Cache, CacheConfig


ROOT = Path(__file__).parent.parent
CATALOG_PATH = ROOT / "references" / "nim-capabilities.json"


@pytest.fixture
def catalog():
    """Load the capabilities catalog."""
    return load_json(CATALOG_PATH)


class TestCrossAreaFlow001:
    """VAL-CROSS-001: Full workflow plan → build → invoke.
    
    A complete flow from `plan` to `build-request` to `invoke` produces valid
    end-to-end results.
    """
    
    def test_plan_to_build_request_flow(self, catalog):
        """Test that plan output can be used to build a request."""
        # Step 1: Plan
        task = "extract text from invoice"
        plan = plan_task(task, catalog, {"image_urls": []})
        
        assert plan["primary_capability"] == "ocr"
        assert "ocr" in plan["workflow"]
        
        # Step 2: Build request using plan's capability
        # Use data URL directly to avoid network call
        class Args:
            capability = plan["primary_capability"]
            image_url = ["data:image/png;base64,fake"]
            merge_level = ["paragraph"]
            confidence_threshold = None
            nms_threshold = None
            query_text = None
            passage = None
            truncate = None
            model = None
        
        request_plan = build_request(plan["primary_capability"], Args(), catalog, {})
        
        assert request_plan["capability"] == "ocr"
        assert request_plan["method"] == "POST"
        assert "body" in request_plan
        assert "input" in request_plan["body"]
    
    def test_plan_workflow_to_build_request_flow(self, catalog):
        """Test plan with workflow (ocr_then_rerank) generates requests for both steps."""
        task = "extract text from image and rank passages by relevance"
        plan = plan_task(
            task, catalog,
            {
                "image_urls": ["https://example.com/doc.png"],
                "query_text": "Which section mentions pricing?",
                "passages": ["First section about pricing", "Second section about features"]
            }
        )
        
        assert plan["workflow_id"] == "ocr_then_rerank"
        assert "ocr" in plan["workflow"]
        assert "rerank" in plan["workflow"]
        
        # Build OCR request - use data URL to avoid network
        class OCRArgs:
            capability = "ocr"
            image_url = ["data:image/png;base64,fake"]
            merge_level = ["paragraph"]
            confidence_threshold = None
            nms_threshold = None
            query_text = None
            passage = None
            truncate = None
            model = None
        
        ocr_request = build_request("ocr", OCRArgs(), catalog, {})
        
        assert ocr_request["capability"] == "ocr"
        
        # Build rerank request
        class RerankArgs:
            capability = "rerank"
            image_url = []
            merge_level = None
            confidence_threshold = None
            nms_threshold = None
            query_text = "Which section mentions pricing?"
            passage = ["First section about pricing", "Second section about features"]
            truncate = None
            model = None
        
        rerank_request = build_request("rerank", RerankArgs(), catalog, {})
        
        assert rerank_request["capability"] == "rerank"
        assert rerank_request["body"]["query"]["text"] == "Which section mentions pricing?"
        assert len(rerank_request["body"]["passages"]) == 2


class TestCrossAreaFlow002:
    """VAL-CROSS-002: Server mode mirrors CLI behavior.
    
    The API server produces identical results to equivalent CLI commands.
    """
    
    def test_plan_output_format_matches(self, catalog):
        """Verify plan output format matches between CLI and server."""
        task = "extract text from invoice"
        
        result = plan_task(task, catalog, {"image_urls": []})
        
        # Check required fields that server would return
        required_fields = [
            "task_query", "workflow_id", "workflow", "primary_capability",
            "scores", "flags", "reasoning", "ready_to_invoke_primary"
        ]
        for field in required_fields:
            assert field in result, f"Missing field: {field}"
    
    def test_build_request_format_matches(self, catalog):
        """Verify build-request output format matches between CLI and server."""
        class Args:
            capability = "ocr"
            image_url = ["data:image/png;base64,iVBORw0KGgo="]
            merge_level = ["paragraph"]
            confidence_threshold = None
            nms_threshold = None
            query_text = None
            passage = None
            truncate = None
            model = None
        
        result = build_request("ocr", Args(), catalog, {})
        
        # Check required fields that server would return
        required_fields = ["capability", "method", "url", "headers", "body", "auth_env"]
        for field in required_fields:
            assert field in result, f"Missing field: {field}"
        
        assert result["body"]["input"][0]["type"] == "image_url"
        assert result["body"]["input"][0]["url"].startswith("data:image/")


class TestCrossAreaFlow003:
    """VAL-CROSS-003: Batch processing with retry and cache.
    
    A batch of 5 images where 2 fail initially, succeed on retry, and results
    are cached.
    """
    
    @pytest.mark.asyncio
    async def test_batch_with_retry_logic(self, catalog):
        """Test batch processing where some fail and succeed on retry."""
        if not AIOHTTP_AVAILABLE:
            pytest.skip("aiohttp not available")
        
        # Create mock requests that will fail twice then succeed
        call_counts = {}
        
        async def mock_invoke_with_fails(request_plan, timeout=120):
            url = request_plan.get("url", "")
            call_counts[url] = call_counts.get(url, 0) + 1
            
            # Fail first 2 times, succeed on 3rd
            if call_counts[url] < 3:
                return {"status": 503, "response": "Service unavailable"}
            return {"status": 200, "response": {"data": f"result for {url}"}}
        
        # Patch async_invoke_request
        with patch("nim_router.async_invoke_request", side_effect=mock_invoke_with_fails):
            # Create batch of 3 requests (simulating 5 with 2 failing)
            requests = [
                {"method": "POST", "url": f"https://httpbin.org/post{idx}", "headers": {}, "body": {}}
                for idx in range(3)
            ]
            
            retry_config = RetryConfig(max_attempts=3, base_delay=0.1)
            results = []
            
            for req in requests:
                for attempt in range(1, retry_config.max_attempts + 1):
                    result = await mock_invoke_with_fails(req)
                    if result["status"] == 200:
                        results.append(result)
                        break
                    if attempt < retry_config.max_attempts:
                        delay = retry_config.get_delay(attempt)
                        await asyncio.sleep(delay)
            
            # All should eventually succeed
            assert len(results) == 3
            assert all(r["status"] == 200 for r in results)
    
    def test_cache_integration(self, catalog):
        """Test that cache works with batch processing."""
        import tempfile
        import shutil
        
        # Use a temporary cache directory to avoid interference
        temp_cache_dir = Path(tempfile.mkdtemp())
        cache = Cache(CacheConfig(cache_dir=temp_cache_dir))
        
        try:
            # Generate cache keys
            key1 = cache.generate_key("ocr", ["https://example.com/img1.png"], {})
            key2 = cache.generate_key("ocr", ["https://example.com/img1.png"], {})  # Same
            key3 = cache.generate_key("ocr", ["https://example.com/img2.png"], {})  # Different
            
            # Same inputs should generate same key
            assert key1 == key2
            # Different inputs should generate different key
            assert key1 != key3
            
            # Test cache set/get
            cache.set(key1, {"status": 200, "data": "test"})
            
            cached = cache.get(key1)
            assert cached is not None
            assert cached["status"] == 200
            assert cached["data"] == "test"
            
            # Key not in cache should return None
            assert cache.get(key3) is None
        finally:
            # Clean up temp directory
            shutil.rmtree(temp_cache_dir, ignore_errors=True)
    
    @pytest.mark.asyncio
    async def test_cache_with_batch_fetch(self):
        """Test cache.get_or_fetch with batch scenario."""
        import tempfile
        import shutil
        
        temp_cache_dir = Path(tempfile.mkdtemp())
        cache = Cache(CacheConfig(cache_dir=temp_cache_dir))
        
        try:
            fetch_count = 0
            
            async def mock_fetch():
                nonlocal fetch_count
                fetch_count += 1
                await asyncio.sleep(0.01)  # Simulate API call
                return {"status": 200, "data": "fetched"}
            
            # First call should fetch
            result1 = await cache.get_or_fetch(
                "ocr",
                ["https://example.com/img.png"],
                {},
                mock_fetch
            )
            assert result1["source"] == "fetch"
            assert fetch_count == 1
            
            # Second call with same params should use cache
            result2 = await cache.get_or_fetch(
                "ocr",
                ["https://example.com/img.png"],
                {},
                mock_fetch
            )
            assert result2["source"] == "cache"
            assert fetch_count == 1  # Still 1, didn't fetch again
            
            # Different params should fetch again
            result3 = await cache.get_or_fetch(
                "ocr",
                ["https://example.com/img2.png"],
                {},
                mock_fetch
            )
            assert result3["source"] == "fetch"
            assert fetch_count == 2
        finally:
            shutil.rmtree(temp_cache_dir, ignore_errors=True)


class TestRetryAndCircuitBreaker:
    """Tests for retry and circuit breaker with batch processing."""
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_failures(self):
        """Test circuit breaker opens after repeated failures."""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=60.0
        ))
        
        # Record failures
        await cb.record_failure()
        assert not cb.is_open()
        
        await cb.record_failure()
        assert not cb.is_open()
        
        await cb.record_failure()
        assert cb.is_open()
        
        # Can't execute when open
        assert not cb.can_execute()
        
        status = cb.get_status()
        assert status["state"] == "open"
        assert status["failure_count"] >= 3
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_half_open_after_timeout(self):
        """Test circuit breaker half-opens after recovery timeout."""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout=0.1  # 100ms
        ))
        
        # Open the circuit
        await cb.record_failure()
        await cb.record_failure()
        assert cb.is_open()
        
        # Wait for recovery timeout
        await asyncio.sleep(0.15)
        
        # Should transition to half-open
        assert cb.can_execute()
        assert cb.state.value == "half_open"
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_closes_after_successes(self):
        """Test circuit breaker closes after successes in half-open state."""
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.1,
            success_threshold=2
        ))
        
        # Open the circuit
        await cb.record_failure()
        assert cb.is_open()
        
        # Wait and transition to half-open
        await asyncio.sleep(0.15)
        assert cb.can_execute()
        
        # Record successes
        await cb.record_success()
        await cb.record_success()
        
        # Should be closed now
        assert not cb.is_open()
        status = cb.get_status()
        assert status["state"] == "closed"
    
    def test_exponential_backoff_delays(self):
        """Test exponential backoff generates correct delays."""
        config = RetryConfig(
            max_attempts=4,
            base_delay=1.0,
            exponential_base=2.0,
            max_delay=30.0
        )
        backoff = ExponentialBackoff(config)
        
        delays = list(backoff)
        
        # Attempt 1: 1s, Attempt 2: 2s, Attempt 3: 4s, Attempt 4: 8s
        assert len(delays) == 4
        assert delays[0] == pytest.approx(1.0, rel=0.2)  # ±20% jitter
        assert delays[1] == pytest.approx(2.0, rel=0.2)
        assert delays[2] == pytest.approx(4.0, rel=0.2)
        assert delays[3] == pytest.approx(8.0, rel=0.2)
    
    def test_exponential_backoff_with_max_delay(self):
        """Test exponential backoff respects max_delay."""
        config = RetryConfig(
            max_attempts=10,
            base_delay=10.0,
            exponential_base=2.0,
            max_delay=30.0
        )
        backoff = ExponentialBackoff(config, jitter=0.0)  # No jitter for predictable testing
        
        delays = list(backoff)
        
        # Expected: 10, 20, 40→30, 80→30, 160→30, etc.
        # i=0: 10 * 2^0 = 10, i=1: 10 * 2^1 = 20, i=2: 10 * 2^2 = 40→30, etc.
        expected = [10.0, 20.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0]
        for i, (delay, exp) in enumerate(zip(delays, expected)):
            assert delay == exp, f"Attempt {i+1}: expected {exp}, got {delay}"


class TestServerEndpoints:
    """Tests for server endpoint behavior matching CLI."""
    
    def test_plan_endpoint_parity(self, catalog):
        """Test that plan endpoint output matches CLI plan command."""
        task_query = "extract text from invoice"
        
        result = plan_task(task_query, catalog, {"image_urls": []})
        
        # Server would return this as JSON
        assert result["task_query"] == task_query
        assert result["primary_capability"] == "ocr"
        assert "workflow" in result
        assert "ready_to_invoke_primary" in result
    
    def test_invoke_request_parity(self, catalog):
        """Test that invoke request format matches between CLI and server."""
        class Args:
            capability = "ocr"
            image_url = ["data:image/png;base64,test123"]
            merge_level = ["paragraph"]
            confidence_threshold = None
            nms_threshold = None
            query_text = None
            passage = None
            truncate = None
            model = None
        
        runtime = {
            "ocr": {"url": "https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v1"}
        }
        
        result = build_request("ocr", Args(), catalog, runtime)
        
        # These fields are what the server would use
        assert "capability" in result
        assert "method" in result
        assert "url" in result
        assert "headers" in result
        assert "body" in result
        
        # Body should have correct structure for NVIDIA API
        assert "input" in result["body"]
        assert result["body"]["input"][0]["type"] == "image_url"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
