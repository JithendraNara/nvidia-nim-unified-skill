"""FastAPI server for NVIDIA NIM Unified Router.

This module provides REST API endpoints that mirror the CLI functionality.
Start with: python3 -m nim_router.server
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any

try:
    import fastapi
    from fastapi import FastAPI, HTTPException, Header, Request
    from fastapi.responses import JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

# Add scripts directory to path for imports
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from nim_router import (
    plan_task,
    build_request,
    invoke_request,
    async_invoke_request,
    async_invoke_batch,
    load_json,
    AIOHTTP_AVAILABLE,
)
from nim_router.retry import RetryConfig, CircuitBreaker, CircuitBreakerConfig, CircuitOpenError
from nim_router.cache import Cache, CacheConfig


# Pydantic models for request/response
class PlanRequest(BaseModel):
    task_query: str = Field(..., description="Free-form task description")
    image_url: list[str] | None = Field(default=None, description="Image URLs")
    query_text: str | None = Field(default=None, description="Query text for rerank")
    passage: list[str] | None = Field(default=None, description="Passages for rerank")


class BuildRequestRequest(BaseModel):
    capability: str = Field(..., description="Capability name")
    image_url: list[str] | None = Field(default=None, description="Image URLs")
    merge_level: list[str] | None = Field(default=None, description="Merge levels")
    confidence_threshold: float | None = Field(default=None, description="Confidence threshold")
    nms_threshold: float | None = Field(default=None, description="NMS threshold")
    query_text: str | None = Field(default=None, description="Query text for rerank")
    passage: list[str] | None = Field(default=None, description="Passages for rerank")
    truncate: str | None = Field(default=None, description="Truncate option")
    model: str | None = Field(default=None, description="Model name")


class InvokeRequest(BaseModel):
    capability: str = Field(..., description="Capability name")
    image_url: list[str] | None = Field(default=None, description="Image URLs")
    query_text: str | None = Field(default=None, description="Query text for rerank")
    passage: list[str] | None = Field(default=None, description="Passages for rerank")
    merge_level: list[str] | None = Field(default=None, description="Merge levels")
    confidence_threshold: float | None = Field(default=None, description="Confidence threshold")
    nms_threshold: float | None = Field(default=None, description="NMS threshold")
    truncate: str | None = Field(default=None, description="Truncate option")
    model: str | None = Field(default=None, description="Model name")
    async_mode: bool = Field(default=False, description="Enable async parallel execution")


class HealthResponse(BaseModel):
    status: str
    version: str
    async_enabled: bool


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    if not FASTAPI_AVAILABLE:
        raise RuntimeError("FastAPI is required. Install with: pip install fastapi uvicorn")
    
    app = FastAPI(
        title="NVIDIA NIM Unified Router",
        description="REST API for NVIDIA NIM capabilities routing",
        version="1.0.0",
    )
    
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Initialize shared resources
    catalog_path = ROOT / "references" / "nim-capabilities.json"
    catalog = load_json(catalog_path)
    cache = Cache(CacheConfig())
    circuit_breakers: dict[str, CircuitBreaker] = {}
    
    def get_circuit_breaker(capability: str) -> CircuitBreaker:
        if capability not in circuit_breakers:
            circuit_breakers[capability] = CircuitBreaker(
                capability,
                CircuitBreakerConfig()
            )
        return circuit_breakers[capability]
    
    @app.get("/health", response_model=HealthResponse)
    async def health_check():
        """Health check endpoint."""
        return HealthResponse(
            status="healthy",
            version="1.0.0",
            async_enabled=AIOHTTP_AVAILABLE
        )
    
    @app.post("/plan")
    async def plan_endpoint(request: PlanRequest) -> dict[str, Any]:
        """Plan endpoint - mirrors CLI plan command."""
        result = plan_task(
            request.task_query,
            catalog,
            {
                "image_urls": request.image_url or [],
                "query_text": request.query_text,
                "passages": request.passage or []
            }
        )
        return result
    
    @app.post("/build-request")
    async def build_request_endpoint(request: BuildRequestRequest) -> dict[str, Any]:
        """Build request endpoint - mirrors CLI build-request command."""
        # Build args object from request
        class Args:
            capability = request.capability
            image_url = request.image_url
            merge_level = request.merge_level
            confidence_threshold = request.confidence_threshold
            nms_threshold = request.nms_threshold
            query_text = request.query_text
            passage = request.passage
            truncate = request.truncate
            model = request.model
        
        runtime = {}  # Would normally load from config
        result = build_request(request.capability, Args(), catalog, runtime)
        return result
    
    @app.post("/invoke")
    async def invoke_endpoint(request: InvokeRequest) -> dict[str, Any]:
        """Invoke endpoint - mirrors CLI invoke command."""
        # Build args object from request
        class Args:
            capability = request.capability
            image_url = request.image_url or []
            query_text = request.query_text
            passage = request.passage
            merge_level = request.merge_level
            confidence_threshold = request.confidence_threshold
            nms_threshold = request.nms_threshold
            truncate = request.truncate
            model = request.model
        
        runtime = {}
        request_plan = build_request(request.capability, Args(), catalog, runtime)
        
        # Check circuit breaker
        cb = get_circuit_breaker(request.capability)
        if not cb.can_execute():
            status = cb.get_status()
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "circuit_breaker_open",
                    "capability": request.capability,
                    "state": status["state"],
                    "message": f"Circuit breaker is open for {request.capability}"
                }
            )
        
        # Handle async mode for batch
        if request.async_mode and request.capability != "rerank" and len(request.image_url or []) > 1:
            # Generate cache key
            cache_key = cache.generate_key(
                request.capability,
                request.image_url,
                {"query_text": request.query_text, "passage": request.passage}
            )
            
            # Check cache first
            cached = cache.get(cache_key)
            if cached:
                return {
                    "source": "cache",
                    "cache_key": cache_key,
                    "result": cached
                }
            
            # Build batch requests
            requests = []
            for url in request.image_url:
                req = dict(request_plan)
                req["body"] = dict(req["body"])
                req["body"]["input"] = [{"type": "image_url", "url": url}]
                requests.append(req)
            
            # Invoke batch
            results = await async_invoke_batch(requests)
            
            # Cache results
            cache.set(cache_key, {"results": results, "request_count": len(results)})
            
            return {
                "source": "fetch",
                "cache_key": cache_key,
                "result": {
                    "results": results,
                    "request_count": len(results)
                }
            }
        
        # Default sync invocation
        result = invoke_request(request_plan)
        
        # Record success/failure in circuit breaker
        if result.get("status", 0) >= 200 and result.get("status", 0) < 300:
            await cb.record_success()
        else:
            await cb.record_failure()
        
        return result
    
    @app.get("/cache/stats")
    async def cache_stats():
        """Get cache statistics."""
        return cache.get_stats()
    
    @app.delete("/cache")
    async def cache_clear():
        """Clear the cache."""
        count = cache.clear()
        return {"cleared": count}
    
    @app.get("/circuit-breakers")
    async def circuit_breaker_status():
        """Get status of all circuit breakers."""
        return {
            capability: cb.get_status()
            for capability, cb in circuit_breakers.items()
        }
    
    @app.get("/openapi.json")
    async def openapi_spec():
        """Return OpenAPI specification."""
        return app.openapi()
    
    return app


def main():
    """Main entry point for the server."""
    parser = argparse.ArgumentParser(description="NVIDIA NIM Unified Router API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=3100, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    
    args = parser.parse_args()
    
    if not FASTAPI_AVAILABLE:
        print("Error: FastAPI is required. Install with: pip install fastapi uvicorn")
        sys.exit(1)
    
    import uvicorn
    
    app = create_app()
    
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload
    )


if __name__ == "__main__":
    main()
