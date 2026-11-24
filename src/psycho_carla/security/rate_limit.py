"""
Rate limiting — token buckets per client key.

Used by the HTTP layer when ``security.rate_limit_enabled`` is on: each
client (by IP, or by API token when present) gets
``security.rate_limit_rpm`` requests per minute with a burst allowance of
``security.rate_limit_burst``.

Layer: security.
"""

from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, rate_per_minute: int, burst: int) -> None:
        self.capacity = max(1, burst)
        self.refill_per_second = max(1, rate_per_minute) / 60.0
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def allow(self, cost: float = 1.0) -> bool:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.updated
            self.updated = now
            self.tokens = min(
                float(self.capacity),
                self.tokens + elapsed * self.refill_per_second,
            )
            if self.tokens >= cost:
                self.tokens -= cost
                return True
            return False


class RateLimiter:
    """One token bucket per key, created on demand."""

    def __init__(self, rate_per_minute: int = 120, burst: int = 20) -> None:
        self.rate_per_minute = rate_per_minute
        self.burst = burst
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(self.rate_per_minute, self.burst)
                self._buckets[key] = bucket
        return bucket.allow()

    def reset(self, key: str = "") -> None:
        with self._lock:
            if key:
                self._buckets.pop(key, None)
            else:
                self._buckets.clear()


__all__ = ["RateLimiter", "TokenBucket"]
