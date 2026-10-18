"""In-process sliding-window rate limiter, keyed by client IP.

No Redis needed for a single-process portfolio site. Each key keeps a deque
of call timestamps; a call is refused when the window already holds
`max_calls`. IPs live in memory only and are never written anywhere.
Buckets that have gone quiet are pruned once per window, so the dict
doesn't grow with every visitor the process has ever seen.
"""
from __future__ import annotations

import time
from collections import deque


class RateLimiter:
    def __init__(self, max_calls: int, window_seconds: float) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._buckets: dict[str, deque[float]] = {}
        self._last_prune = time.monotonic()

    def limited(self, key: str) -> bool:
        """True if `key` has used up its window. Records the call otherwise."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        if now - self._last_prune > self.window_seconds:
            self._prune(cutoff)
            self._last_prune = now
        bucket = self._buckets.setdefault(key, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_calls:
            return True
        bucket.append(now)
        return False

    def _prune(self, cutoff: float) -> None:
        for key in [k for k, b in self._buckets.items() if not b or b[-1] < cutoff]:
            del self._buckets[key]

    def clear(self) -> None:
        self._buckets.clear()

    def __len__(self) -> int:
        return len(self._buckets)
