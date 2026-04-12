"""Simple in-memory rate limiter middleware for FastAPI.

Uses a sliding window counter per client IP.
"""

import time
import threading
from collections import defaultdict
from typing import Dict, Tuple

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RateLimiter:
    """Thread-safe sliding window rate limiter."""

    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: Dict[str, list] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, client_id: str) -> Tuple[bool, int]:
        """Check if a request is allowed.

        Returns:
            (allowed, remaining_requests)
        """
        now = time.time()
        cutoff = now - self.window_seconds

        with self._lock:
            # Clean old entries
            self._requests[client_id] = [
                t for t in self._requests[client_id] if t > cutoff
            ]
            count = len(self._requests[client_id])

            if count >= self.max_requests:
                return False, 0

            self._requests[client_id].append(now)
            return True, self.max_requests - count - 1

    def get_stats(self) -> dict:
        """Get rate limiter stats."""
        with self._lock:
            return {
                "tracked_clients": len(self._requests),
                "max_requests": self.max_requests,
                "window_seconds": self.window_seconds,
            }


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that applies rate limiting per client IP."""

    def __init__(self, app, max_requests: int = 100, window_seconds: int = 60):
        super().__init__(app)
        self.limiter = RateLimiter(max_requests, window_seconds)

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks and WebSocket
        if request.url.path in ("/", "/api/v1/system/health") or request.url.path.startswith("/ws"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        allowed, remaining = self.limiter.is_allowed(client_ip)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests",
                    "retry_after": self.limiter.window_seconds,
                },
                headers={
                    "Retry-After": str(self.limiter.window_seconds),
                    "X-RateLimit-Limit": str(self.limiter.max_requests),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.limiter.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
