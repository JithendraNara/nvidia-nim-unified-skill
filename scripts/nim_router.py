#!/usr/bin/env python3
"""Unified planner and request builder for NVIDIA NIM OCR/layout/rerank tasks."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

# Import retry and circuit breaker from the extended package
from nim_router.retry import (
    RetryConfig,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    CircuitState,
    ExponentialBackoff,
)

# Import rate limiter from the extended package
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


# Circuit breaker registry - one per capability
# This persists state across invocations for the same capability
_circuit_breakers: dict[str, CircuitBreaker] = {}
_circuit_breakers_lock = asyncio.Lock() if AIOHTTP_AVAILABLE else None

# Default retry configuration for transient errors
_default_retry_config = RetryConfig(
    max_attempts=3,
    base_delay=1.0,
    exponential_base=2.0,
    max_delay=30.0,
    retryable_statuses=(429, 500, 502, 503, 504)
)

# Default circuit breaker configuration
_default_circuit_breaker_config = CircuitBreakerConfig(
    failure_threshold=5,  # Open after 5 consecutive failures
    recovery_timeout=60.0,  # Wait 60 seconds before trying again
    success_threshold=2  # Close after 2 successes in half-open
)


def get_circuit_breaker(capability: str) -> CircuitBreaker:
    """Get or create a circuit breaker for the given capability.
    
    Circuit breakers are shared across invocations to maintain state.
    """
    if capability not in _circuit_breakers:
        _circuit_breakers[capability] = CircuitBreaker(
            capability,
            _default_circuit_breaker_config
        )
    return _circuit_breakers[capability]


def invoke_with_retry(request_plan: dict[str, Any]) -> dict[str, Any]:
    """Invoke a request with retry and circuit breaker protection.
    
    Uses exponential backoff (1s, 2s, 4s) for retries on transient errors.
    Circuit breaker opens after 5 consecutive failures.
    
    Args:
        request_plan: The request plan dict with url, headers, body, method
        
    Returns:
        Dict with status and response
        
    Raises:
        CircuitOpenError: If circuit breaker is open
    """
    capability = request_plan.get("capability", "unknown")
    cb = get_circuit_breaker(capability)
    backoff = ExponentialBackoff(_default_retry_config, jitter=0.1)
    
    last_error = None
    
    for attempt in range(1, _default_retry_config.max_attempts + 1):
        # Check circuit breaker before attempting
        if not cb.can_execute():
            status = cb.get_status()
            raise CircuitOpenError(
                capability,
                retry_after=_default_circuit_breaker_config.recovery_timeout
            )
        
        try:
            result = _sync_invoke_request(request_plan)
            
            # Check if we got a retryable error
            if result.get("status") in _default_retry_config.retryable_statuses:
                last_error = Exception(f"HTTP {result.get('status')}: {result.get('response')}")
                
                # Record failure in circuit breaker
                if AIOHTTP_AVAILABLE:
                    asyncio.run(cb.record_failure())
                else:
                    # Synchronous fallback
                    import asyncio as async_lib
                    async_lib.run(cb.record_failure())
                
                # Don't wait after last attempt
                if attempt < _default_retry_config.max_attempts:
                    delay = next(iter(backoff))
                    print(f"[retry] Attempt {attempt} failed with status {result.get('status')}, "
                          f"retrying in {delay:.1f}s...", file=sys.stderr)
                    time.sleep(delay)
                    continue
            
            # Success or non-retryable error - record success if we had failures
            if attempt > 1:  # We had some retries
                if AIOHTTP_AVAILABLE:
                    asyncio.run(cb.record_success())
                else:
                    import asyncio as async_lib
                    async_lib.run(cb.record_success())
            
            return result
            
        except urllib.error.HTTPError as exc:
            last_error = exc
            status_code = exc.code
            
            if status_code in _default_retry_config.retryable_statuses:
                # Record failure
                if AIOHTTP_AVAILABLE:
                    asyncio.run(cb.record_failure())
                else:
                    import asyncio as async_lib
                    async_lib.run(cb.record_failure())
                
                if attempt < _default_retry_config.max_attempts:
                    delay = next(iter(backoff))
                    print(f"[retry] Attempt {attempt} failed with HTTP {status_code}, "
                          f"retrying in {delay:.1f}s...", file=sys.stderr)
                    time.sleep(delay)
                    continue
            else:
                # Non-retryable error
                return {
                    "status": status_code,
                    "response": str(exc)
                }
    
    # All retries exhausted
    return {
        "status": last_error.code if hasattr(last_error, 'code') else 0,
        "response": f"All retries exhausted: {last_error}"
    }


def _sync_invoke_request(request_plan: dict[str, Any]) -> dict[str, Any]:
    """Internal synchronous HTTP request without retry logic.
    
    Args:
        request_plan: The request plan dict
        
    Returns:
        Dict with status and response
    """
    body_bytes = json.dumps(request_plan["body"]).encode("utf-8")
    request = urllib.request.Request(
        request_plan["url"],
        data=body_bytes,
        method=request_plan["method"],
        headers=request_plan["headers"]
    )
    try:
        with urllib.request.urlopen(request) as response:
            text = response.read().decode("utf-8")
            try:
                parsed: Any = json.loads(text)
            except json.JSONDecodeError:
                parsed = text
            return {
                "status": response.status,
                "response": parsed
            }
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text
        return {
            "status": exc.code,
            "response": parsed
        }


ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "references" / "nim-capabilities.json"


def load_json(path: Path) -> dict[str, Any]:
    with path.open() as handle:
        return json.load(handle)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def keyword_score(query: str, keywords: list[str]) -> int:
    normalized = normalize_text(query)
    # Split query into individual tokens for flexible matching
    query_tokens = set(normalized.split())
    score = 0
    for keyword in keywords:
        token = normalize_text(keyword)
        if not token:
            continue
        tokens = token.split()
        if len(tokens) == 1:
            # Single token: exact substring match (case-insensitive)
            if tokens[0] in normalized:
                score += 1
        else:
            # Multi-token: check if ALL tokens appear in query (not necessarily consecutively)
            if all(t in query_tokens for t in tokens):
                score += len(tokens)
    return score


def detect_flags(query: str) -> dict[str, bool]:
    normalized = normalize_text(query)
    # Also create token set for flexible matching
    query_tokens = set(normalized.split())
    phrase_sets = {
        "wants_text": [
            "ocr",
            "extract text",
            "read text",
            "transcribe image",
            "document text",
            "invoice text",
            "receipt text",
            # Single-word triggers for implicit routing
            "read",
            "scan",
            "text",
            "document",
        ],
        "wants_table": [
            "table",
            "cell",
            "row",
            "column",
            "grid",
            "table structure",
        ],
        "wants_chart": [
            "chart",
            "graph",
            "legend",
            "axis",
            "xlabel",
            "ylabel",
            "value label",
        ],
        "wants_layout": [
            "layout",
            "page elements",
            "paragraph",
            "header",
            "footer",
            "title block",
            "segment page",
        ],
        "wants_rank": [
            "rerank",
            "rank passages",
            "relevance",
            "most relevant",
            "best chunk",
            "search results",
            # Single-word triggers for implicit routing
            "rank",
            "relevant",
            "passage",
            "chunks",
        ]
    }
    result = {}
    for flag, phrases in phrase_sets.items():
        if flag in ("wants_text", "wants_rank"):
            # For text and rank flags, also check single-word tokens
            # against single-word phrases in the list
            single_word_phrases = [p for p in phrases if " " not in p]
            multi_word_phrases = [p for p in phrases if " " in p]
            
            # Check multi-word phrases first (must be consecutive)
            multi_match = any(phrase in normalized for phrase in multi_word_phrases)
            # Check single-word phrases (any token match)
            single_match = any(phrase in query_tokens for phrase in single_word_phrases)
            
            result[flag] = multi_match or single_match
        else:
            result[flag] = any(phrase in normalized for phrase in phrases)
    return result


def select_workflow(query: str, flags: dict[str, bool], inputs: dict[str, Any]) -> tuple[str | None, list[str], list[str]]:
    reasons: list[str] = []
    if flags["wants_rank"] and (flags["wants_text"] or bool(inputs.get("image_urls"))):
        reasons.append("Task mixes text extraction and relevance ranking.")
        return "ocr_then_rerank", ["ocr", "rerank"], reasons
    if flags["wants_text"] and flags["wants_table"]:
        reasons.append("Task asks for text extraction with table preservation.")
        return "layout_aware_table_extraction", ["page_elements", "table_structure", "ocr"], reasons
    if flags["wants_text"] and flags["wants_chart"]:
        reasons.append("Task asks for text extraction with chart-aware understanding.")
        return "chart_aware_extraction", ["page_elements", "graphic_elements", "ocr"], reasons
    if flags["wants_layout"] and flags["wants_table"]:
        reasons.append("Task asks for layout and table structure together.")
        return None, ["page_elements", "table_structure"], reasons
    if flags["wants_layout"] and flags["wants_chart"]:
        reasons.append("Task asks for layout and chart annotations together.")
        return None, ["page_elements", "graphic_elements"], reasons
    return None, [], reasons


def select_capability(query: str, catalog: dict[str, Any]) -> tuple[str, dict[str, int]]:
    scores: dict[str, int] = {}
    for name, capability in catalog["capabilities"].items():
        scores[name] = keyword_score(query, capability.get("keywords", []))
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        if "http" in query.lower() or "image" in query.lower() or "document" in query.lower():
            return "ocr", scores
        return "rerank", scores
    return best, scores


def plan_task(task_query: str, catalog: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    flags = detect_flags(task_query)
    workflow_id, workflow, reasons = select_workflow(task_query, flags, inputs)
    if workflow:
        primary = workflow[-1] if workflow[-1] == "rerank" else workflow[0]
    else:
        primary, scores = select_capability(task_query, catalog)
        workflow = [primary]
        scores = scores
    if not workflow_id:
        primary, scores = select_capability(task_query, catalog)
        if workflow == [primary]:
            reasons.append(f"Highest keyword score matched `{primary}`.")
    else:
        _, scores = select_capability(task_query, catalog)
    required_inputs = catalog["capabilities"][workflow[0]]["request"]["required_inputs"]
    missing_inputs = []
    if "image_urls" in required_inputs and not inputs.get("image_urls"):
        missing_inputs.append("image_url")
    if "query_text" in required_inputs and not inputs.get("query_text"):
        missing_inputs.append("query_text")
    if "passages" in required_inputs and not inputs.get("passages"):
        missing_inputs.append("passage")
    return {
        "task_query": task_query,
        "workflow_id": workflow_id,
        "workflow": workflow,
        "primary_capability": primary,
        "scores": scores,
        "flags": flags,
        "reasoning": reasons,
        "ready_to_invoke_primary": not missing_inputs,
        "missing_primary_inputs": missing_inputs
    }


def load_runtime_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    return load_json(Path(path))


def guess_image_media_type(source: str, response_headers: Any | None = None) -> str:
    if response_headers is not None:
        content_type = response_headers.get_content_type()
        if content_type in {"image/png", "image/jpeg", "image/jpg"}:
            return "image/jpeg" if content_type == "image/jpg" else content_type
    guessed, _ = mimetypes.guess_type(source)
    if guessed in {"image/png", "image/jpeg", "image/jpg"}:
        return "image/jpeg" if guessed == "image/jpg" else guessed
    raise SystemExit(
        f"Unsupported image type for `{source}`. NVIDIA managed CV endpoints require png/jpeg/jpg data URLs."
    )


def to_data_url(source: str) -> str:
    if source.startswith("data:image/"):
        return source

    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in {"http", "https"}:
        with urllib.request.urlopen(source, timeout=120) as response:
            raw = response.read()
            media_type = guess_image_media_type(source, response.headers)
        return f"data:{media_type};base64,{base64.b64encode(raw).decode()}"

    path = Path(source).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.exists():
        raw = path.read_bytes()
        media_type = guess_image_media_type(str(path))
        return f"data:{media_type};base64,{base64.b64encode(raw).decode()}"

    raise SystemExit(
        f"Could not resolve image source `{source}`. Provide an https URL, a local file path, or a data:image/... URL."
    )


def join_url(base_or_url: str, path: str) -> str:
    if base_or_url.endswith(path):
        return base_or_url
    if base_or_url.endswith("/"):
        base_or_url = base_or_url[:-1]
    return f"{base_or_url}{path}"


def resolve_capability_config(capability_name: str, capability: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    override = runtime.get(capability_name, {})
    url = override.get("url")
    if not url:
        fixed = capability.get("fixed_url")
        if fixed:
          url = fixed
        else:
            env_name = capability.get("endpoint_env")
            if env_name:
                env_value = os.getenv(env_name)
                if env_value:
                    url = join_url(env_value, capability["path"])
    if not url:
        raise SystemExit(f"Missing endpoint URL for `{capability_name}`.")

    auth_env = override.get("bearer_env") or capability.get("auth_env")
    token = override.get("bearer_token")
    if not token and auth_env:
        token = os.getenv(auth_env)

    return {
        "url": url,
        "token": token,
        "auth_env": auth_env
    }


def build_body(capability_name: str, capability: dict[str, Any], args: argparse.Namespace, runtime: dict[str, Any]) -> dict[str, Any]:
    defaults = capability["request"].get("defaults", {})
    if capability_name == "rerank":
        passages = [{"text": passage} for passage in (args.passage or [])]
        if not args.query_text or not passages:
            raise SystemExit("`rerank` requires --query-text and at least one --passage.")
        model = args.model or runtime.get("rerank", {}).get("model") or defaults["model"]
        return {
            "model": model,
            "query": {"text": args.query_text},
            "passages": passages,
            "truncate": args.truncate or defaults["truncate"]
        }

    if capability_name == "embed":
        texts = args.text or []
        if not texts:
            raise SystemExit("`embed` requires at least one --text argument.")
        model = args.model or defaults["model"]
        input_type = args.input_type or defaults.get("input_type", "passage")
        return {
            "input": texts,
            "model": model,
            "input_type": input_type,
            "encoding_format": defaults.get("encoding_format", "float"),
            "truncate": args.truncate or defaults.get("truncate", "END")
        }

    image_urls = args.image_url or []
    if not image_urls:
        raise SystemExit(f"`{capability_name}` requires at least one --image-url.")
    managed_input_mode = capability["request"].get("image_input_mode")
    image_inputs = image_urls
    if managed_input_mode == "data_url":
        image_inputs = [to_data_url(source) for source in image_urls]
    body: dict[str, Any] = {
        "input": [{"type": "image_url", "url": url} for url in image_inputs]
    }
    if capability_name == "ocr":
        merge_levels = args.merge_level or defaults.get("merge_levels")
        if merge_levels:
            body["merge_levels"] = merge_levels
        return body

    body["confidence_threshold"] = (
        args.confidence_threshold
        if args.confidence_threshold is not None
        else defaults.get("confidence_threshold")
    )
    body["nms_threshold"] = (
        args.nms_threshold
        if args.nms_threshold is not None
        else defaults.get("nms_threshold")
    )
    return body


def build_request(capability_name: str, args: argparse.Namespace, catalog: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    capability = catalog["capabilities"][capability_name]
    resolved = resolve_capability_config(capability_name, capability, runtime)
    body = build_body(capability_name, capability, args, runtime)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    if resolved["token"]:
        headers["Authorization"] = f"Bearer {resolved['token']}"
    return {
        "capability": capability_name,
        "method": capability["method"],
        "url": resolved["url"],
        "headers": headers,
        "body": body,
        "auth_env": resolved["auth_env"]
    }


def invoke_request(request_plan: dict[str, Any]) -> dict[str, Any]:
    """Invoke a request with retry and circuit breaker protection.
    
    This is the main entry point for sync invocation. It delegates to
    invoke_with_retry which handles the retry logic with exponential backoff
    and circuit breaker protection.
    
    Rate limiting is applied before retry logic using token bucket algorithm.
    
    Args:
        request_plan: The request plan dict with url, headers, body, method
        
    Returns:
        Dict with status and response
    """
    capability = request_plan.get("capability", "unknown")
    
    # Apply rate limiting before making the request
    limiter = get_rate_limiter()
    try:
        # acquire() is async, so we need to run it in an event loop for sync mode
        wait_time = asyncio.run(limiter.acquire(capability))
        if wait_time > 0:
            print(f"[rate-limit] Rate limited for {capability}, waiting {wait_time:.2f}s...", file=sys.stderr)
            # For sync mode, we need to block
            import time as time_module
            time_module.sleep(wait_time)
    except Exception as e:
        print(f"[rate-limit] Rate limiter error: {e}", file=sys.stderr)
    
    try:
        return invoke_with_retry(request_plan)
    except CircuitOpenError as exc:
        return {
            "status": 503,
            "response": f"Circuit breaker open for {exc.capability}: {exc}"
        }
    except RateLimitExceededError as exc:
        return {
            "status": 429,
            "response": f"Rate limit exceeded for {exc.capability}: {exc}"
        }


async def async_invoke_request(request_plan: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    """Invoke a request asynchronously with retry and circuit breaker.
    
    Uses exponential backoff (1s, 2s, 4s) for retries on transient errors.
    Circuit breaker opens after 5 consecutive failures per capability.
    
    Args:
        request_plan: The request plan dict with url, headers, body, method
        timeout: Request timeout in seconds
        
    Returns:
        Dict with status and response
    """
    if not AIOHTTP_AVAILABLE:
        raise SystemExit(
            "aiohttp is required for async mode. "
            "Install it with: pip install aiohttp"
        )
    
    capability = request_plan.get("capability", "unknown")
    
    # Apply rate limiting before making the request
    limiter = get_rate_limiter()
    try:
        wait_time = await limiter.acquire(capability)
        if wait_time > 0:
            print(f"[rate-limit] Rate limited for {capability}, waiting {wait_time:.2f}s...", file=sys.stderr)
            await asyncio.sleep(wait_time)
    except Exception as e:
        print(f"[rate-limit] Rate limiter error: {e}", file=sys.stderr)
    
    cb = get_circuit_breaker(capability)
    backoff = ExponentialBackoff(_default_retry_config, jitter=0.1)
    
    last_error = None
    
    for attempt in range(1, _default_retry_config.max_attempts + 1):
        # Check circuit breaker before attempting
        if not cb.can_execute():
            status = cb.get_status()
            raise CircuitOpenError(
                capability,
                retry_after=_default_circuit_breaker_config.recovery_timeout
            )
        
        try:
            result = await _async_invoke_single(request_plan, timeout)
            
            # Check if we got a retryable error
            if result.get("status") in _default_retry_config.retryable_statuses:
                last_error = Exception(f"HTTP {result.get('status')}: {result.get('response')}")
                
                # Record failure in circuit breaker
                await cb.record_failure()
                
                # Don't wait after last attempt
                if attempt < _default_retry_config.max_attempts:
                    delay = next(iter(backoff))
                    print(f"[retry] Attempt {attempt} failed with status {result.get('status')}, "
                          f"retrying in {delay:.1f}s...", file=sys.stderr)
                    await asyncio.sleep(delay)
                    continue
            
            # Success or non-retryable error - record success if we had retries
            if attempt > 1:  # We had some retries
                await cb.record_success()
            
            return result
            
        except aiohttp.ClientError as exc:
            last_error = exc
            
            # Record failure
            await cb.record_failure()
            
            if attempt < _default_retry_config.max_attempts:
                delay = next(iter(backoff))
                print(f"[retry] Attempt {attempt} failed with error {type(exc).__name__}, "
                      f"retrying in {delay:.1f}s...", file=sys.stderr)
                await asyncio.sleep(delay)
                continue
            else:
                return {
                    "status": 0,
                    "response": f"All retries exhausted: {last_error}"
                }
    
    # All retries exhausted
    return {
        "status": 0,
        "response": f"All retries exhausted: {last_error}"
    }


async def _async_invoke_single(request_plan: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    """Internal async HTTP request without retry logic.
    
    Args:
        request_plan: The request plan dict
        timeout: Request timeout in seconds
        
    Returns:
        Dict with status and response
    """
    body_bytes = json.dumps(request_plan["body"]).encode("utf-8")
    headers = request_plan["headers"]
    
    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    
    try:
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.request(
                method=request_plan["method"],
                url=request_plan["url"],
                headers=headers,
                data=body_bytes
            ) as response:
                text = await response.text()
                try:
                    parsed: Any = json.loads(text)
                except json.JSONDecodeError:
                    parsed = text
                return {
                    "status": response.status,
                    "response": parsed
                }
    except aiohttp.ClientError as exc:
        return {
            "status": 0,
            "response": str(exc)
        }


async def async_invoke_batch(
    requests: list[dict[str, Any]],
    fail_fast: bool = False
) -> list[dict[str, Any]]:
    """Invoke multiple requests concurrently with retry and circuit breaker.
    
    Each request is protected by its capability's circuit breaker.
    Uses exponential backoff for retries on transient errors.
    
    Args:
        requests: List of request plans
        fail_fast: If True, raise exception on first error (not used for individual errors)
        
    Returns:
        List of results in same order as requests
    """
    if not requests:
        return []
    
    async def safe_invoke(req: dict[str, Any]) -> dict[str, Any]:
        try:
            return await async_invoke_request(req)
        except CircuitOpenError as exc:
            return {
                "status": 503,
                "response": f"Circuit breaker open for {exc.capability}: {exc}"
            }
        except Exception as exc:
            return {
                "status": 0,
                "response": f"Async invocation error: {str(exc)}"
            }
    
    tasks = [safe_invoke(req) for req in requests]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    processed_results = []
    for result in results:
        if isinstance(result, Exception):
            processed_results.append({
                "status": 0,
                "response": f"Task error: {str(result)}"
            })
        else:
            processed_results.append(result)
    
    return processed_results


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified planner and request builder for NVIDIA NIM tasks.")
    parser.add_argument("--catalog", default=str(CATALOG_PATH), help="Path to nim-capabilities.json")
    parser.add_argument(
        "--async",
        dest="async_mode",
        action="store_true",
        default=False,
        help="Enable async parallel execution for batch image URLs"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Plan a capability or workflow from a free-form task.")
    plan_parser.add_argument("--task-query", required=True)
    plan_parser.add_argument("--image-url", action="append", default=[])
    plan_parser.add_argument("--query-text")
    plan_parser.add_argument("--passage", action="append", default=[])

    for name in ("build-request", "invoke"):
        command_parser = subparsers.add_parser(name, help=f"{name} for a specific capability")
        command_parser.add_argument("--capability", choices=["ocr", "page_elements", "table_structure", "graphic_elements", "rerank", "embed"], required=True)
        command_parser.add_argument("--config", help="Optional runtime config JSON")
        command_parser.add_argument("--image-url", "--image-source", dest="image_url", action="append")
        command_parser.add_argument("--merge-level", action="append", choices=["word", "sentence", "paragraph"])
        command_parser.add_argument("--confidence-threshold", type=float)
        command_parser.add_argument("--nms-threshold", type=float)
        command_parser.add_argument("--query-text")
        command_parser.add_argument("--passage", action="append")
        command_parser.add_argument("--truncate", choices=["NONE", "START", "END"])
        command_parser.add_argument("--model")
        command_parser.add_argument("--text", action="append", help="Text to embed (can be specified multiple times)")
        command_parser.add_argument("--input-type", choices=["passage", "query"], help="Input type for embedding (passage or query)")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    catalog = load_json(Path(args.catalog))

    if args.command == "plan":
        plan = plan_task(
            args.task_query,
            catalog,
            {
                "image_urls": args.image_url,
                "query_text": args.query_text,
                "passages": args.passage
            }
        )
        print_json(plan)
        return

    runtime = load_runtime_config(args.config)
    request_plan = build_request(args.capability, args, catalog, runtime)
    if args.command == "build-request":
        print_json(request_plan)
        return
    
    # Initialize rate limiter from config if present
    rate_limit_config = None
    if runtime and "rate_limit" in runtime:
        rate_limit_config = parse_rate_limit_config(runtime)
        asyncio.run(initialize_rate_limiter(rate_limit_config))
    
    try:
        # Handle async mode for invoke command
        if getattr(args, 'async_mode', False) and args.capability != "rerank":
            image_urls = args.image_url or []
            if len(image_urls) > 1:
                # Build separate requests for each image URL
                requests = []
                for idx, url in enumerate(image_urls):
                    single_request = build_single_image_request(
                        request_plan, url, idx
                    )
                    requests.append(single_request)
                
                # Invoke all requests concurrently
                start_time = time.time()
                results = asyncio.run(async_invoke_batch(requests))
                elapsed = time.time() - start_time
                
                print_json({
                    "request": request_plan,
                    "batch_requests": requests,
                    "results": results,
                    "timing": {
                        "elapsed_seconds": elapsed,
                        "mode": "async_parallel"
                    }
                })
                return

        # Default sync invocation
        result = invoke_request(request_plan)
        print_json({
            "request": request_plan,
            "result": result
        })
    finally:
        # Cleanup rate limiter
        if rate_limit_config:
            asyncio.run(close_rate_limiter())


def build_single_image_request(
    base_request: dict[str, Any],
    image_url: str,
    index: int
) -> dict[str, Any]:
    """Build a request for a single image URL from a batch request.
    
    Args:
        base_request: The original batch request plan
        image_url: The single image URL to invoke
        index: Index of this image in the batch
        
    Returns:
        Request plan for a single image
    """
    import copy
    req = copy.deepcopy(base_request)
    
    # Find the image in the body input array and keep only this one
    # The body has format: {"input": [{"type": "image_url", "url": ...}]}
    # For async, we need to build a separate request for each image
    body = req["body"]
    
    # Check if it's data_url mode (image embedded) or regular URL
    if image_url.startswith("data:image/"):
        body["input"] = [{"type": "image_url", "url": image_url}]
    else:
        body["input"] = [{"type": "image_url", "url": image_url}]
    
    return req


if __name__ == "__main__":
    main()
