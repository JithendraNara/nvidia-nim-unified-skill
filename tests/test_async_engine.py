"""Tests for async execution engine in nim_router."""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from nim_router import (
    async_invoke_batch,
    async_invoke_request,
    plan_task,
    build_request,
    load_json,
    AIOHTTP_AVAILABLE,
)


ROOT = Path(__file__).parent.parent
CATALOG_PATH = ROOT / "references" / "nim-capabilities.json"


@pytest.fixture
def catalog():
    """Load the capabilities catalog."""
    return load_json(CATALOG_PATH)


@pytest.mark.asyncio
async def test_async_invoke_request_success():
    """Test successful async request invocation."""
    if not AIOHTTP_AVAILABLE:
        pytest.skip("aiohttp not available")
    
    request_plan = {
        "method": "POST",
        "url": "https://httpbin.org/post",
        "headers": {"Content-Type": "application/json"},
        "body": {"test": "data"}
    }
    
    result = await async_invoke_request(request_plan, timeout=30)
    assert result["status"] == 200
    assert "response" in result


@pytest.mark.asyncio
async def test_async_invoke_batch_empty():
    """Test async batch with empty list."""
    if not AIOHTTP_AVAILABLE:
        pytest.skip("aiohttp not available")
    
    results = await async_invoke_batch([])
    assert results == []


@pytest.mark.asyncio
async def test_async_invoke_batch_single():
    """Test async batch with single request."""
    if not AIOHTTP_AVAILABLE:
        pytest.skip("aiohttp not available")
    
    request_plan = {
        "method": "POST",
        "url": "https://httpbin.org/post",
        "headers": {"Content-Type": "application/json"},
        "body": {"test": "data"}
    }
    
    results = await async_invoke_batch([request_plan])
    assert len(results) == 1
    assert results[0]["status"] == 200


@pytest.mark.asyncio
async def test_async_invoke_batch_multiple():
    """Test async batch with multiple requests runs in parallel."""
    if not AIOHTTP_AVAILABLE:
        pytest.skip("aiohttp not available")
    
    # Create requests with a slight delay
    request_plan = {
        "method": "POST",
        "url": "https://httpbin.org/delay/1",
        "headers": {"Content-Type": "application/json"},
        "body": {"test": "data"}
    }
    
    # With 3 requests that each take 1 second, sequential would take ~3s
    # Parallel should take ~1s
    start = time.time()
    results = await async_invoke_batch([request_plan, request_plan, request_plan])
    elapsed = time.time() - start
    
    assert len(results) == 3
    # Should be faster than 2.5s (parallel) not slower than 2.9s (sequential)
    assert elapsed < 2.5, f"Batch took {elapsed}s, expected < 2.5s for parallel execution"


@pytest.mark.asyncio
async def test_async_errors_dont_block():
    """Test that async errors in one request don't block others (VAL-ASYNC-002)."""
    if not AIOHTTP_AVAILABLE:
        pytest.skip("aiohttp not available")
    
    good_request = {
        "method": "POST",
        "url": "https://httpbin.org/status/200",
        "headers": {"Content-Type": "application/json"},
        "body": {"test": "data"}
    }
    
    bad_request = {
        "method": "POST",
        "url": "https://httpbin.org/status/500",
        "headers": {"Content-Type": "application/json"},
        "body": {"test": "data"}
    }
    
    # One good, one bad - the good should still succeed
    results = await async_invoke_batch([good_request, bad_request, good_request])
    
    assert len(results) == 3
    # Results should contain the successful ones and the error one
    statuses = [r["status"] for r in results]
    assert 200 in statuses
    assert 500 in statuses


def test_plan_command_ocr(catalog):
    """Test plan command returns correct capability for text extraction (VAL-ROUTING-001)."""
    result = plan_task("extract text from invoice", catalog, {"image_urls": []})
    
    assert result["primary_capability"] == "ocr"
    assert "ocr" in result["workflow"]


def test_plan_command_workflow(catalog):
    """Test plan command returns correct workflow for mixed task (VAL-ROUTING-002)."""
    result = plan_task(
        "extract text from image and rank passages by relevance",
        catalog,
        {"image_urls": ["http://example.com/img.png"], "query_text": "test", "passages": ["content"]}
    )
    
    assert result["workflow_id"] == "ocr_then_rerank"
    assert "ocr" in result["workflow"]
    assert "rerank" in result["workflow"]


def test_plan_command_multiple_flags(catalog):
    """Test plan with multiple flags detected."""
    result = plan_task(
        "extract text and rank passages",
        catalog,
        {"image_urls": [], "query_text": "test", "passages": ["content"]}
    )
    
    assert result["workflow_id"] == "ocr_then_rerank"
    assert result["flags"]["wants_text"] is True
    assert result["flags"]["wants_rank"] is True


class TestBuildRequest:
    """Tests for build-request command (VAL-CLI-002, VAL-ROUTING-003, VAL-ROUTING-004)."""
    
    def test_build_request_ocr(self, catalog, tmp_path):
        """Test build-request generates valid OCR request."""
        # Create a minimal config
        config = {"ocr": {"url": "https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v1"}}
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))
        
        class Args:
            capability = "ocr"
            image_url = ["data:image/png;base64,iVBORw0KGgo="]
            merge_level = ["paragraph"]
            confidence_threshold = None
            nms_threshold = None
            config = str(config_path)
        
        request = build_request("ocr", Args(), catalog, config)
        
        assert request["capability"] == "ocr"
        assert request["method"] == "POST"
        assert "body" in request
        assert request["body"]["input"][0]["type"] == "image_url"
        assert request["body"]["input"][0]["url"].startswith("data:image/")
    
    def test_build_request_rerank(self, catalog, tmp_path):
        """Test build-request generates valid rerank request."""
        config = {"rerank": {"url": "https://ai.api.nvidia.com/v1/rerank"}}
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))
        
        class Args:
            capability = "rerank"
            image_url = []
            query_text = "test query"
            passage = ["passage 1", "passage 2"]
            model = None
            truncate = None
            config = str(config_path)
        
        request = build_request("rerank", Args(), catalog, config)
        
        assert request["capability"] == "rerank"
        assert request["body"]["model"] is not None
        assert request["body"]["query"]["text"] == "test query"
        assert len(request["body"]["passages"]) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
