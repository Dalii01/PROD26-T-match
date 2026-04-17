from __future__ import annotations

import os
import time
from typing import Callable

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)

_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
_redis = None
_LATENCY_LIST_KEY = "metrics:latencies"
_LATENCY_LIST_MAX = 500
_TOTAL_REQUESTS_KEY = "metrics:total_requests"


def _get_redis():
    global _redis
    if _redis is None:
        try:
            import redis.asyncio as aioredis

            _redis = aioredis.from_url(_REDIS_URL, decode_responses=True)
        except Exception:
            pass
    return _redis


async def _record_latency_redis(latency_ms: float, path: str) -> None:
    if path in ("/health", "/metrics"):
        return
    r = _get_redis()
    if r is None:
        return
    try:
        pipe = r.pipeline()
        pipe.lpush(_LATENCY_LIST_KEY, str(round(latency_ms, 2)))
        pipe.ltrim(_LATENCY_LIST_KEY, 0, _LATENCY_LIST_MAX - 1)
        pipe.incr(_TOTAL_REQUESTS_KEY)
        await pipe.execute()
    except Exception:
        pass


async def get_latency_metrics() -> dict:
    """Читает из Redis avg_latency_ms, p95_latency_ms, total_requests для /health."""
    r = _get_redis()
    if r is None:
        return {}
    try:
        pipe = r.pipeline()
        pipe.get(_TOTAL_REQUESTS_KEY)
        pipe.lrange(_LATENCY_LIST_KEY, 0, -1)
        total_raw, latencies_raw = await pipe.execute()
        total_requests = int(total_raw or 0)
        if not latencies_raw:
            return {
                "total_requests": total_requests,
                "avg_latency_ms": None,
                "p95_latency_ms": None,
            }
        latencies = sorted([float(x) for x in latencies_raw])
        n = len(latencies)
        avg_ms = round(sum(latencies) / n, 2)
        p95_idx = int(n * 0.95) - 1 if n else 0
        p95_ms = round(latencies[max(0, p95_idx)], 2)
        return {
            "total_requests": total_requests,
            "avg_latency_ms": avg_ms,
            "p95_latency_ms": p95_ms,
        }
    except Exception:
        return {}


def _get_route_path(request: Request) -> str:
    route = request.scope.get("route")
    if route and hasattr(route, "path"):
        return str(route.path)
    return request.url.path


async def metrics_middleware(request: Request, call_next: Callable) -> Response:
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration = time.perf_counter() - start
        path = _get_route_path(request)
        REQUEST_COUNT.labels(
            method=request.method, path=path, status=str(status_code)
        ).inc()
        REQUEST_LATENCY.labels(method=request.method, path=path).observe(duration)
        latency_ms = duration * 1000
        await _record_latency_redis(latency_ms, path)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
