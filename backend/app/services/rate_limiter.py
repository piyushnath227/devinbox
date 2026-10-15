"""Failure-count rate limiter that is correct across processes.

Backed by Redis when REDIS_URL is set (shared by every uvicorn worker and app
replica) and falls back to a per-process dict otherwise. Redis errors never
lock people out or crash a request: the limiter degrades to the in-memory
counter instead.
"""

import time
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()


class FailureRateLimiter:
    def __init__(self, name: str, limit: int, window_seconds: int, redis_client: Any = None):
        self.name, self.limit, self.window = name, limit, window_seconds
        self._redis = redis_client
        self._local: Dict[str, List[float]] = {}

    def _rkey(self, key: str) -> str:
        return f"devinbox:rl:{self.name}:{key}"

    # -- in-memory fallback ---------------------------------------------
    def _local_count(self, key: str) -> int:
        now = time.time()
        hits = self._local.setdefault(key, [])
        hits[:] = [t for t in hits if now - t < self.window]
        return len(hits)

    def is_limited(self, key: str) -> bool:
        if self._redis is not None:
            try:
                return int(self._redis.get(self._rkey(key)) or 0) >= self.limit
            except Exception as e:
                logger.warning("rate_limiter_redis_error", error=str(e))
        return self._local_count(key) >= self.limit

    def record_failure(self, key: str) -> None:
        if self._redis is not None:
            try:
                rkey = self._rkey(key)
                if self._redis.incr(rkey) == 1:
                    self._redis.expire(rkey, self.window)  # fixed window starts at first failure
                return
            except Exception as e:
                logger.warning("rate_limiter_redis_error", error=str(e))
        self._local.setdefault(key, []).append(time.time())

    def reset(self, key: str) -> None:
        self._local.pop(key, None)
        if self._redis is not None:
            try:
                self._redis.delete(self._rkey(key))
            except Exception:
                pass


def redis_client_from_settings(settings) -> Optional[Any]:
    if not getattr(settings, "REDIS_URL", ""):
        return None
    try:
        import redis
        return redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    except Exception as e:
        logger.warning("rate_limiter_redis_unavailable", error=str(e))
        return None
