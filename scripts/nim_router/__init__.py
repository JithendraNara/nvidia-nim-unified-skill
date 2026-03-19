"""NVIDIA NIM Unified Router - Extended Features Package.

This package extends the base nim_router module with:
- Retry logic with exponential backoff and circuit breaker
- Content-addressable caching
- FastAPI server
- Async execution support
"""

import sys
from pathlib import Path
import importlib.util

# Load the parent nim_router.py module
_parent_path = Path(__file__).parent.parent / "nim_router.py"
_spec = importlib.util.spec_from_file_location("_nim_router_parent", _parent_path)
_nim_router_parent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_nim_router_parent)

# Re-export parent module's exports
__all__ = [
    # From parent nim_router.py
    "plan_task",
    "build_request",
    "invoke_request",
    "async_invoke_request", 
    "async_invoke_batch",
    "load_json",
    "AIOHTTP_AVAILABLE",
    "to_data_url",
    "build_parser",
    "main",
    # From this package - retry module
    "RetryConfig",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitOpenError",
    "ExponentialBackoff",
    "with_retry",
    # From this package - cache module
    "Cache",
    "CacheConfig",
    # From this package - rate limiter module
    "RateLimiter",
    "RateLimitConfig",
    "RateLimitExceededError",
    "RateLimitSource",
    "parse_rate_limit_config",
    "get_rate_limiter",
    "initialize_rate_limiter",
    "close_rate_limiter",
    # From this package - chunker module
    "Chunk",
    "SemanticUnit",
    "semantic_chunk_text",
    "format_semantic_chunks_json",
    "format_semantic_chunks_markdown",
    "format_semantic_chunks_text",
    "count_tokens",
    "identify_semantic_units",
]

# Copy parent module's exports to this namespace
globals().update({
    name: getattr(_nim_router_parent, name) 
    for name in dir(_nim_router_parent) 
    if not name.startswith('_')
})

# Import extended features
from nim_router.retry import RetryConfig, CircuitBreaker, CircuitBreakerConfig, CircuitOpenError, ExponentialBackoff, with_retry
from nim_router.cache import Cache, CacheConfig
from nim_router.rate_limiter import (
    RateLimiter,
    RateLimitConfig,
    RateLimitExceededError,
    RateLimitSource,
    parse_rate_limit_config,
    get_rate_limiter,
    initialize_rate_limiter,
    close_rate_limiter,
)
from nim_router.chunker import (
    Chunk,
    SemanticUnit,
    semantic_chunk_text,
    format_semantic_chunks_json,
    format_semantic_chunks_markdown,
    format_semantic_chunks_text,
    count_tokens,
    identify_semantic_units,
)
