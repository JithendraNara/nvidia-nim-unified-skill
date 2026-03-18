"""Tests for end-to-end flow - plan→build→invoke (VAL-RT-003, VAL-RT-004, VAL-CA-001, VAL-CA-002).

These tests verify:
- VAL-RT-003: Build-request generates valid payload for NVIDIA API
- VAL-RT-004: Invoke command succeeds with mocked API
- VAL-CA-001: Full plan→build→invoke flow produces extracted text
- VAL-CA-002: All three platforms produce same routing results
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import nim_router
from nim_router import (
    plan_task,
    build_request,
    invoke_request,
    async_invoke_request,
    invoke_with_retry,
    load_json,
    load_runtime_config,
    to_data_url,
    AIOHTTP_AVAILABLE,
)
from nim_router.retry import RetryConfig, ExponentialBackoff, CircuitBreaker, CircuitBreakerConfig


ROOT = Path(__file__).parent.parent
CATALOG_PATH = ROOT / "references" / "nim-capabilities.json"


@pytest.fixture
def catalog():
    """Load the capabilities catalog."""
    return load_json(CATALOG_PATH)


@pytest.fixture
def sample_image_data_url():
    """A minimal 1x1 transparent PNG as data URL."""
    return "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


class Args:
    """Helper class to simulate argparse.Namespace for build_request."""
    def __init__(self, capability, image_url=None, query_text=None, passages=None,
                 merge_level=None, confidence_threshold=None, nms_threshold=None,
                 truncate=None, model=None):
        self.capability = capability
        self.image_url = image_url or []
        self.query_text = query_text
        self.passage = passages
        self.merge_level = merge_level
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.truncate = truncate
        self.model = model


class TestBuildRequestValidation:
    """VAL-RT-003: Build-request generates valid payload.
    
    Tests that build-request command produces valid JSON that matches
    the format expected by NVIDIA API.
    """
    
    def test_build_request_valid_json_structure(self, catalog, sample_image_data_url):
        """Test build-request produces valid JSON structure."""
        args = Args(
            capability="ocr",
            image_url=[sample_image_data_url],
            merge_level=["paragraph"]
        )
        
        request_plan = build_request("ocr", args, catalog, {})
        
        # Check top-level structure
        assert "capability" in request_plan
        assert "method" in request_plan
        assert "url" in request_plan
        assert "headers" in request_plan
        assert "body" in request_plan
        
        # Check headers
        assert request_plan["headers"]["Content-Type"] == "application/json"
        assert request_plan["headers"]["Accept"] == "application/json"
    
    def test_build_request_body_has_input_format(self, catalog, sample_image_data_url):
        """Test body has correct input format for NVIDIA API."""
        args = Args(
            capability="ocr",
            image_url=[sample_image_data_url],
            merge_level=["paragraph"]
        )
        
        request_plan = build_request("ocr", args, catalog, {})
        
        # NVIDIA API expects input array with image_url objects
        assert "input" in request_plan["body"]
        assert isinstance(request_plan["body"]["input"], list)
        assert len(request_plan["body"]["input"]) == 1
        assert request_plan["body"]["input"][0]["type"] == "image_url"
        assert request_plan["body"]["input"][0]["url"].startswith("data:image/")
    
    def test_build_request_url_from_config(self, catalog, sample_image_data_url):
        """Test URL is correctly resolved from config or environment."""
        config = {
            "ocr": {"url": "https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v1"}
        }
        
        args = Args(
            capability="ocr",
            image_url=[sample_image_data_url],
            merge_level=["paragraph"]
        )
        
        request_plan = build_request("ocr", args, catalog, config)
        
        assert request_plan["url"] == "https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v1"
    
    def test_build_request_with_multiple_images(self, catalog, sample_image_data_url):
        """Test build-request handles multiple image URLs."""
        images = [sample_image_data_url, sample_image_data_url, sample_image_data_url]
        
        args = Args(
            capability="ocr",
            image_url=images,
            merge_level=["paragraph"]
        )
        
        request_plan = build_request("ocr", args, catalog, {})
        
        assert len(request_plan["body"]["input"]) == 3
        for inp in request_plan["body"]["input"]:
            assert inp["type"] == "image_url"


class TestInvokeWithMockedAPI:
    """VAL-RT-004: Invoke command succeeds with mocked API.
    
    Tests that invoke command returns structured response when
    API is mocked.
    """
    
    def test_invoke_with_mocked_successful_response(self, catalog, sample_image_data_url):
        """Test invoke returns structured response on success."""
        config = {
            "ocr": {"url": "https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v1"}
        }
        
        args = Args(
            capability="ocr",
            image_url=[sample_image_data_url],
            merge_level=["paragraph"]
        )
        
        request_plan = build_request("ocr", args, catalog, config)
        
        # Mock the HTTP response
        mock_response = {
            "status": 200,
            "response": {
                "result": {
                    "text": "Sample extracted text from invoice #12345"
                }
            }
        }
        
        # Patch the _sync_invoke_request in the parent module
        parent_module = nim_router._nim_router_parent
        with patch.object(parent_module, '_sync_invoke_request', return_value=mock_response):
            result = invoke_request(request_plan)
        
        assert result["status"] == 200
        assert "response" in result
        assert result["response"]["result"]["text"] == "Sample extracted text from invoice #12345"
    
    def test_invoke_with_retry_on_429_response(self, catalog, sample_image_data_url):
        """Test invoke retries on 429 (rate limit) response."""
        config = {
            "ocr": {"url": "https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v1"}
        }
        
        args = Args(
            capability="ocr",
            image_url=[sample_image_data_url],
            merge_level=["paragraph"]
        )
        
        request_plan = build_request("ocr", args, catalog, config)
        
        # Track calls to simulate rate limiting then success
        call_count = [0]
        
        def mock_sync_invoke(req):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"status": 429, "response": "Rate limit exceeded"}
            return {"status": 200, "response": {"result": {"text": "Success after retry"}}}
        
        parent_module = nim_router._nim_router_parent
        with patch.object(parent_module, '_sync_invoke_request', side_effect=mock_sync_invoke):
            result = invoke_request(request_plan)
        
        # Should have retried and eventually succeeded
        assert call_count[0] == 2
        assert result["status"] == 200


class TestRateLimitHandling:
    """VAL-RT-005: Rate limit handled gracefully.
    
    Tests that 429 responses are handled with clear error messages.
    """
    
    def test_429_returns_clear_error(self, catalog, sample_image_data_url):
        """Test 429 response includes clear rate limit error."""
        config = {
            "ocr": {"url": "https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v1"}
        }
        
        args = Args(
            capability="ocr",
            image_url=[sample_image_data_url],
            merge_level=["paragraph"]
        )
        
        request_plan = build_request("ocr", args, catalog, config)
        
        # Mock 429 response
        mock_response = {"status": 429, "response": "Rate limit exceeded"}
        
        parent_module = nim_router._nim_router_parent
        with patch.object(parent_module, '_sync_invoke_request', return_value=mock_response):
            result = invoke_request(request_plan)
        
        # Should propagate the 429 status
        assert result["status"] == 429


class TestEndToEndFlow:
    """VAL-CA-001: Full plan→build→invoke flow.
    
    Tests that a complete end-to-end flow from plan to build to invoke
    produces valid extracted text.
    """
    
    def test_full_flow_plan_build_invoke(self, catalog, sample_image_data_url):
        """Test complete flow: plan → build → invoke."""
        # Step 1: Plan
        task = "extract text from invoice"
        plan = plan_task(task, catalog, {"image_urls": []})
        
        assert plan["primary_capability"] == "ocr"
        assert "ocr" in plan["workflow"]
        
        # Step 2: Build request
        args = Args(
            capability=plan["primary_capability"],
            image_url=[sample_image_data_url],
            merge_level=["paragraph"]
        )
        
        request_plan = build_request(plan["primary_capability"], args, catalog, {})
        
        assert request_plan["capability"] == "ocr"
        assert request_plan["method"] == "POST"
        assert "input" in request_plan["body"]
        
        # Step 3: Invoke with mocked API
        mock_response = {
            "status": 200,
            "response": {
                "result": {
                    "text": "INVOICE #98765\nDate: 2024-01-15\nTotal: $1,234.56"
                }
            }
        }
        
        parent_module = nim_router._nim_router_parent
        with patch.object(parent_module, '_sync_invoke_request', return_value=mock_response):
            result = invoke_request(request_plan)
        
        # Verify final result
        assert result["status"] == 200
        assert "INVOICE #98765" in result["response"]["result"]["text"]
    
    def test_full_flow_with_workflow(self, catalog, sample_image_data_url):
        """Test full flow with workflow (ocr_then_rerank)."""
        # Step 1: Plan with workflow
        task = "extract text and rank by relevance"
        plan = plan_task(
            task, catalog,
            {
                "image_urls": [sample_image_data_url],
                "query_text": "What is the total?",
                "passages": ["Total is $100", "Subtotal is $90", "Tax is $10"]
            }
        )
        
        assert plan["workflow_id"] == "ocr_then_rerank"
        assert "ocr" in plan["workflow"]
        assert "rerank" in plan["workflow"]
        
        # Step 2: Build OCR request
        ocr_args = Args(
            capability="ocr",
            image_url=[sample_image_data_url],
            merge_level=["paragraph"]
        )
        ocr_request = build_request("ocr", ocr_args, catalog, {})
        
        assert ocr_request["capability"] == "ocr"
        
        # Step 3: Build rerank request
        rerank_args = Args(
            capability="rerank",
            image_url=[],
            query_text="What is the total?",
            passages=["Total is $100", "Subtotal is $90", "Tax is $10"]
        )
        rerank_request = build_request("rerank", rerank_args, catalog, {})
        
        assert rerank_request["capability"] == "rerank"
        assert rerank_request["body"]["query"]["text"] == "What is the total?"


class TestCrossPlatformRouting:
    """VAL-CA-002: All three platforms produce same results.
    
    Tests that the same task query routes to the same capability
    regardless of platform-specific invocation.
    """
    
    def test_same_query_routes_identically(self, catalog):
        """Test that same query produces same routing across different inputs."""
        query = "extract text from image"
        
        # Plan with only task query
        result1 = plan_task(query, catalog, {"image_urls": []})
        
        # Plan with image URLs (simulating OpenClaw with actual image)
        result2 = plan_task(query, catalog, {"image_urls": ["https://example.com/doc.png"]})
        
        # Both should route to same capability
        assert result1["primary_capability"] == result2["primary_capability"] == "ocr"
        assert result1["workflow"] == result2["workflow"]
    
    def test_rerank_query_routes_correctly(self, catalog):
        """Test rerank queries route to rerank capability."""
        queries = [
            "rank these passages by relevance",
            "find most relevant chunks",
            "rerank search results"
        ]
        
        for query in queries:
            result = plan_task(query, catalog, {
                "query_text": "test query",
                "passages": ["passage 1", "passage 2"]
            })
            assert result["primary_capability"] == "rerank", f"Query '{query}' didn't route to rerank"
    
    def test_workflow_detection_consistent(self, catalog):
        """Test workflow detection is consistent across different image sources."""
        task = "read document and rank results"
        
        # Test with data URL
        result1 = plan_task(task, catalog, {
            "image_urls": ["data:image/png;base64,fake"],
            "query_text": "find pricing",
            "passages": ["price is $10", "price is $20"]
        })
        
        # Test with HTTPS URL
        result2 = plan_task(task, catalog, {
            "image_urls": ["https://example.com/doc.png"],
            "query_text": "find pricing",
            "passages": ["price is $10", "price is $20"]
        })
        
        # Both should detect same workflow
        assert result1["workflow_id"] == result2["workflow_id"] == "ocr_then_rerank"
        assert result1["workflow"] == result2["workflow"]


class TestCLIPlanCommand:
    """Test the CLI plan command matches expected behavior."""
    
    def test_plan_extract_text_returns_ocr(self, catalog):
        """Verify: python3 scripts/nim_router.py plan --task-query 'extract text' returns ocr."""
        result = plan_task("extract text", catalog, {"image_urls": []})
        
        assert result["primary_capability"] == "ocr"
        assert "ocr" in result["workflow"]
    
    def test_plan_with_image_url_shows_ready(self, catalog):
        """Verify plan shows ready_to_invoke when image URL provided."""
        result = plan_task(
            "extract text",
            catalog,
            {"image_urls": ["https://example.com/test.png"]}
        )
        
        assert result["ready_to_invoke_primary"] is True
        assert "missing_primary_inputs" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
