"""Caching layer for NVIDIA NIM Router responses."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import os


@dataclass
class CacheConfig:
    """Configuration for cache behavior."""
    cache_dir: Path = field(default_factory=lambda: Path.home() / ".cache" / "nim-router")
    default_ttl: float = 3600.0  # 1 hour in seconds
    enabled: bool = True


class Cache:
    """Content-addressable cache for API responses.
    
    Cache key is generated from: capability + image_url(s) + params hash
    
    Usage:
        cache = Cache(CacheConfig())
        
        # Check cache before API call
        cache_key = cache.generate_key("ocr", ["https://example.com/img.png"], {})
        if cached := cache.get(cache_key):
            return cached
            
        # After API call
        cache.set(cache_key, result)
    """
    
    def __init__(self, config: CacheConfig | None = None):
        self.config = config or CacheConfig()
        self._memory_cache: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()
        
        # Ensure cache directory exists
        if self.config.enabled:
            self.config.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def generate_key(
        self,
        capability: str,
        image_urls: list[str] | None = None,
        params: dict[str, Any] | None = None
    ) -> str:
        """Generate cache key from request parameters.
        
        Args:
            capability: The capability name (e.g., "ocr")
            image_urls: List of image URLs
            params: Additional parameters
            
        Returns:
            SHA256 hash as cache key
        """
        # Normalize inputs for consistent hashing
        key_parts = [capability]
        
        if image_urls:
            # Sort URLs for consistency
            sorted_urls = sorted(image_urls)
            key_parts.extend(sorted_urls)
        
        if params:
            # Sort params keys for consistency
            sorted_params = json.dumps(params, sort_keys=True)
            key_parts.append(sorted_params)
        
        key_string = "|".join(str(p) for p in key_parts)
        return hashlib.sha256(key_string.encode()).hexdigest()
    
    def get(self, key: str) -> Any | None:
        """Get cached value if exists and not expired.
        
        Args:
            key: Cache key from generate_key()
            
        Returns:
            Cached value or None if not found/expired
        """
        if not self.config.enabled:
            return None
        
        # Check memory cache first
        if key in self._memory_cache:
            value, expiry = self._memory_cache[key]
            if time.time() < expiry:
                return value
            else:
                del self._memory_cache[key]
        
        # Check disk cache
        cache_file = self.config.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    entry = json.load(f)
                
                expiry = entry.get("_expiry", 0)
                if time.time() < expiry:
                    # Promote to memory cache
                    self._memory_cache[key] = (entry["_value"], expiry)
                    return entry["_value"]
                else:
                    # Expired, remove
                    cache_file.unlink(missing_ok=True)
            except (json.JSONDecodeError, IOError):
                pass
        
        return None
    
    def set(
        self,
        key: str,
        value: Any,
        ttl: float | None = None
    ) -> None:
        """Store value in cache.
        
        Args:
            key: Cache key
            value: Value to cache (must be JSON serializable)
            ttl: Time-to-live in seconds (uses default if not specified)
        """
        if not self.config.enabled:
            return
        
        ttl = ttl if ttl is not None else self.config.default_ttl
        expiry = time.time() + ttl
        
        # Store in memory cache
        self._memory_cache[key] = (value, expiry)
        
        # Store in disk cache
        cache_file = self.config.cache_dir / f"{key}.json"
        entry = {
            "_value": value,
            "_expiry": expiry,
            "_cached_at": time.time()
        }
        
        try:
            with open(cache_file, 'w') as f:
                json.dump(entry, f)
        except (IOError, TypeError):
            # If serialization fails, at least memory cache works
            pass
    
    def invalidate(self, key: str) -> bool:
        """Remove key from cache.
        
        Args:
            key: Cache key to invalidate
            
        Returns:
            True if key was found and removed
        """
        # Remove from memory
        if key in self._memory_cache:
            del self._memory_cache[key]
        
        # Remove from disk
        cache_file = self.config.cache_dir / f"{key}.json"
        if cache_file.exists():
            cache_file.unlink(missing_ok=True)
            return True
        
        return False
    
    def clear(self) -> int:
        """Clear all cache entries.
        
        Returns:
            Number of entries cleared
        """
        count = 0
        
        # Clear memory cache
        count += len(self._memory_cache)
        self._memory_cache.clear()
        
        # Clear disk cache
        if self.config.cache_dir.exists():
            for cache_file in self.config.cache_dir.glob("*.json"):
                cache_file.unlink(missing_ok=True)
                count += 1
        
        return count
    
    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Dict with cache stats (size, hit rate approximation, etc.)
        """
        disk_entries = 0
        if self.config.cache_dir.exists():
            disk_entries = len(list(self.config.cache_dir.glob("*.json")))
        
        return {
            "enabled": self.config.enabled,
            "memory_entries": len(self._memory_cache),
            "disk_entries": disk_entries,
            "cache_dir": str(self.config.cache_dir),
            "default_ttl": self.config.default_ttl,
        }
    
    async def get_or_fetch(
        self,
        capability: str,
        image_urls: list[str] | None,
        params: dict[str, Any] | None,
        fetch_fn: callable,
        ttl: float | None = None
    ) -> Any:
        """Get from cache or fetch if not present.
        
        This is a convenience method that combines cache check and fetch.
        
        Args:
            capability: Capability name
            image_urls: Image URLs for cache key
            params: Additional params for cache key
            fetch_fn: Async function to call if cache miss
            ttl: Optional TTL override
            
        Returns:
            Cached or freshly fetched value
        """
        key = self.generate_key(capability, image_urls, params)
        
        # Try cache first
        cached = self.get(key)
        if cached is not None:
            return {"source": "cache", "data": cached, "cache_key": key}
        
        # Fetch fresh
        result = await fetch_fn()
        
        # Cache the result
        self.set(key, result, ttl)
        
        return {"source": "fetch", "data": result, "cache_key": key}
