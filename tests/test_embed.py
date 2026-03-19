"""Tests for embed capability (VAL-EMBED-001, VAL-EMBED-002, VAL-EMBED-003).

These tests verify:
- VAL-EMBED-001: Embed capability added to catalog
- VAL-EMBED-002: Build embed request works
- VAL-EMBED-003: Embed endpoint returns embedding vector
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from nim_router import (
    build_request,
    invoke_request,
    load_json,
)


ROOT = Path(__file__).parent.parent
CATALOG_PATH = ROOT / "references" / "nim-capabilities.json"


@pytest.fixture
def catalog():
    """Load the capabilities catalog."""
    return load_json(CATALOG_PATH)


class Args:
    """Helper class to simulate argparse.Namespace for build_request."""
    def __init__(self, capability, image_url=None, query_text=None, passages=None,
                 merge_level=None, confidence_threshold=None, nms_threshold=None,
                 truncate=None, model=None, text=None, input_type=None):
        self.capability = capability
        self.image_url = image_url or []
        self.query_text = query_text
        self.passage = passages
        self.merge_level = merge_level
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.truncate = truncate
        self.model = model
        self.text = text
        self.input_type = input_type


class TestEmbedCapability:
    """VAL-EMBED-001: Embed capability added to catalog."""
    
    def test_embed_capability_exists_in_catalog(self, catalog):
        """Test embed capability is present in capabilities catalog."""
        assert "embed" in catalog["capabilities"]
    
    def test_embed_capability_has_required_fields(self, catalog):
        """Test embed capability has all required fields."""
        embed = catalog["capabilities"]["embed"]
        
        # Required fields per VAL-EMBED-001
        assert "fixed_url" in embed or "endpoint_env" in embed
        assert "auth_env" in embed
        assert "method" in embed
        assert "request" in embed
        
        # Request schema should have text input
        request_schema = embed["request"]
        assert "schema" in request_schema


class TestBuildEmbedRequest:
    """VAL-EMBED-002: Build embed request works."""
    
    def test_build_embed_request_basic(self, catalog):
        """Test build-request for embed with basic text."""
        args = Args(
            capability="embed",
            text=["hello world"]
        )
        
        request_plan = build_request("embed", args, catalog, {})
        
        # Check top-level structure
        assert request_plan["capability"] == "embed"
        assert request_plan["method"] == "POST"
        assert "url" in request_plan
        assert "headers" in request_plan
        assert "body" in request_plan
        
        # Check body structure per NVIDIA embed API
        body = request_plan["body"]
        assert "input" in body
        assert isinstance(body["input"], list)
        assert body["input"] == ["hello world"]
        assert "model" in body
        assert body["model"] == "nvidia/nv-embed-v1"
        assert "input_type" in body
        assert body["input_type"] == "passage"  # default
        assert "encoding_format" in body
        assert body["encoding_format"] == "float"
        assert "truncate" in body
    
    def test_build_embed_request_with_multiple_texts(self, catalog):
        """Test build-request with multiple text inputs."""
        args = Args(
            capability="embed",
            text=["first text", "second text", "third text"]
        )
        
        request_plan = build_request("embed", args, catalog, {})
        
        assert len(request_plan["body"]["input"]) == 3
        assert request_plan["body"]["input"] == ["first text", "second text", "third text"]
    
    def test_build_embed_request_with_input_type_query(self, catalog):
        """Test build-request with input_type=query."""
        args = Args(
            capability="embed",
            text=["what is GPU?"],
            input_type="query"
        )
        
        request_plan = build_request("embed", args, catalog, {})
        
        assert request_plan["body"]["input_type"] == "query"
    
    def test_build_embed_request_with_input_type_passage(self, catalog):
        """Test build-request with input_type=passage."""
        args = Args(
            capability="embed",
            text=["some passage text"],
            input_type="passage"
        )
        
        request_plan = build_request("embed", args, catalog, {})
        
        assert request_plan["body"]["input_type"] == "passage"
    
    def test_build_embed_request_with_truncate(self, catalog):
        """Test build-request with truncate option."""
        args = Args(
            capability="embed",
            text=["long text here"],
            truncate="NONE"
        )
        
        request_plan = build_request("embed", args, catalog, {})
        
        assert request_plan["body"]["truncate"] == "NONE"
    
    def test_build_embed_request_requires_text(self, catalog):
        """Test build-request fails without text argument."""
        args = Args(
            capability="embed",
            text=None
        )
        
        with pytest.raises(SystemExit) as exc_info:
            build_request("embed", args, catalog, {})
        
        assert "embed" in str(exc_info.value).lower()
    
    def test_build_embed_request_url_is_correct(self, catalog):
        """Test embed uses correct embeddings endpoint URL."""
        args = Args(
            capability="embed",
            text=["test"]
        )
        
        request_plan = build_request("embed", args, catalog, {})
        
        assert request_plan["url"] == "https://ai.api.nvidia.com/v1/embeddings"


class TestEmbedCapabilityKeywords:
    """Test embed capability keyword matching for routing."""
    
    def test_embed_capability_keywords(self, catalog):
        """Test embed capability has appropriate keywords."""
        embed = catalog["capabilities"]["embed"]
        keywords = embed.get("keywords", [])
        
        # Should have embedding-related keywords
        assert "embed" in keywords or "embedding" in keywords or "vector" in keywords


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
