# Architecture

Architectural decisions for NVIDIA NIM Unified Skill enhancement.

## Design Principles

1. **Backward Compatibility First** - All enhancements preserve the original CLI interface
2. **Skill Interface Preserved** - SKILL.md remains the primary agent-facing document
3. **Modular Enhancement** - New features in separate modules under `nim_router/`
4. **Async by Default** - Use asyncio for concurrent operations

## Module Structure

```
nim_router.py          # Original single-file router (unchanged interface)
nim_router/
  __init__.py          # Package init, exports from nim_router.py
  async_engine.py      # Async execution with asyncio
  retry.py             # Retry logic + circuit breaker
  rate_limiter.py      # Token bucket rate limiting
  cache.py             # Content-addressable caching
  semantic.py          # Embedding-based routing
  fallback.py          # Fallback chain execution
  server.py            # FastAPI REST server
  formatters.py        # Output formatters (markdown, csv, json-ld)
  observability.py     # Structured logging + metrics
```

## Capability Routing

1. **Keyword matching** (existing) - Scores against capability keywords
2. **Semantic similarity** (new) - Embedding cosine similarity for ambiguous cases
3. **Confidence threshold** - 0.5: below = suggest alternatives

## Retry Strategy

- Exponential backoff: 1s, 2s, 4s (max 3 retries)
- Circuit breaker: 5 failures → 60s cooldown
- Rate limiting: configurable per-capability RPM

## Caching

- Key: SHA256(capability + image_url + params_hash)
- Storage: ~/.cache/nim-router/
- TTL: 1 hour default, configurable
