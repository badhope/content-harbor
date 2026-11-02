"""速率限制中间件（纯内存令牌桶，无第三方限流库依赖）

设计说明：
- 不依赖 slowapi / redis 即可工作，默认内存令牌桶。
- 可选 Redis 后端（通过 settings.cache 配置），失败自动降级到内存。
- 提供与旧接口兼容的导出：RateLimitMiddleware / RateLimiter /
  init_rate_limiter / close_rate_limiter / get_limiter。
"""

import time
from typing import Optional, Dict, Any, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from config import settings
from utils.logger import log_security_event
from utils.response_code import ResponseCode


def _trusted_networks():
    """受信代理网段：本机回环 + TRUSTED_NETS 追加。与 api/hub.py 的判定同源。"""
    import os
    from ipaddress import ip_network
    nets = ["127.0.0.0/8", "::1/128"]
    extra = os.environ.get("TRUSTED_NETS", "").strip()
    if extra:
        nets += [n.strip() for n in extra.split(",") if n.strip()]
    parsed = []
    for n in nets:
        try:
            parsed.append(ip_network(n, strict=False))
        except ValueError:
            continue          # 配错的网段直接忽略，不让限流整体失效
    return parsed


def _peer_is_trusted(peer: str) -> bool:
    """对端（直连来源）是否受信代理。解析不出 IP 一律不信。"""
    from ipaddress import ip_address
    if not peer:
        return False
    try:
        addr = ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in _trusted_networks())


class _TokenBucket:
    """单个键的令牌桶"""

    __slots__ = ("capacity", "tokens", "refill_rate", "last_refill")

    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity
        self.tokens = float(capacity)
        self.refill_rate = refill_rate  # 每秒补充令牌数
        self.last_refill = time.monotonic()

    def consume(self, now: float, cost: float = 1.0) -> bool:
        # 补充令牌
        delta = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + delta * self.refill_rate)
        self.last_refill = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False

    def remaining(self) -> int:
        return int(self.tokens)


class InMemoryRateLimiter:
    """内存令牌桶限流器（线程安全）

    桶表有上限，防止任意来源换 IP 让 ``_buckets`` 无限增长。

    淘汰顺序刻意不是"最久未用"——那会让轮换来源的请求把自己还没花完的桶挤掉、
    拿回满额令牌，等于把限流洗白。这里优先淘汰**已经回满**的桶（它的状态本来
    就等价于新建），只有热桶占满且超过 2× 上限时才按最久未用硬淘汰。

    ``max_buckets <= 0`` 表示显式关闭上限（需要运维自己承担内存），不做静默改值。

    ``_EVICT_SAMPLE``: 淘汰时只扫描最旧的 N 个桶，避免每次新建桶都 O(n) 遍历 1 万条。
    """

    _EVICT_SAMPLE = 64

    def __init__(self, max_buckets: int = 10000):
        import threading
        self._lock = threading.Lock()
        self._buckets: Dict[str, _TokenBucket] = {}
        self._max_buckets = int(max_buckets)
        self._capacity = getattr(settings, "rate_limit_burst", 20) or 20
        self._refill = (
            getattr(settings, "rate_limit_requests_per_minute", 60) or 60
        ) / 60.0  # 每秒补充

    def _evict_one(self, now: float) -> None:
        """表满时淘汰一个桶，保持严格上限。

        淘汰优先级：**剩余令牌最多者优先**（最接近回满，限流状态最不重要）。
        被限流的来源（令牌已耗尽）因此最不容易被挤掉——轮换新来源冲刷桶表
        也洗不白自己的配额。为控制开销只抽样最旧的 64 个桶（抽样成本有界），
        抽到已回满的直接淘汰。
        """
        if self._max_buckets <= 0 or len(self._buckets) < self._max_buckets:
            return
        victim_key, victim_tokens, first = None, -1.0, None
        for i, (key, b) in enumerate(self._buckets.items()):
            if i >= self._EVICT_SAMPLE:
                break
            if first is None:
                first = key
            tokens = b.tokens + (now - b.last_refill) * b.refill_rate
            if tokens >= b.capacity:      # 已回满：直接淘汰，不必再找
                victim_key = key
                break
            if tokens > victim_tokens:
                victim_key, victim_tokens = key, tokens
        del self._buckets[victim_key if victim_key is not None else first]

    def is_allowed(self, key: str) -> Tuple[bool, Dict[str, Any]]:
        with self._lock:
            now = time.monotonic()   # 取时钟必须在锁内：锁外取值会让等待期间 delta 为负、倒扣令牌
            bucket = self._buckets.pop(key, None)   # pop 后再插回 = 按最近使用排序
            if bucket is None:
                self._evict_one(now)
                bucket = _TokenBucket(self._capacity, self._refill)
            self._buckets[key] = bucket
            allowed = bucket.consume(now)
            return allowed, {
                "limit": self._capacity,
                "remaining": bucket.remaining(),
                "reset": int(now + 60),
                "retry_after": 0 if allowed else 1,
            }

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


# 全局限流器实例
_limiter = InMemoryRateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """速率限制中间件"""

    def __init__(self, app, calls_per_minute: int = 60, burst: int = 10):
        super().__init__(app)
        self.calls_per_minute = calls_per_minute
        self.burst = burst

    async def dispatch(self, request: Request, call_next) -> Response:
        # 健康检查 / 文档路径跳过限流
        if self._should_skip(request):
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        key = f"rate_limit:{client_ip}"

        allowed, stats = _limiter.is_allowed(key)

        if not allowed:
            log_security_event(
                "RATE_LIMIT_EXCEEDED",
                details={
                    "client_ip": client_ip,
                    "path": str(request.url.path),
                    "method": request.method,
                    "user_agent": request.headers.get("user-agent"),
                    "stats": stats,
                },
            )
            payload = {
                "success": False,
                "code": ResponseCode.TOO_MANY_REQUESTS.code,
                "message": ResponseCode.TOO_MANY_REQUESTS.message,
                "data": None,
            }
            return JSONResponse(
                status_code=429,
                content=payload,
                headers={
                    "X-RateLimit-Limit": str(stats["limit"]),
                    "X-RateLimit-Remaining": "0",
                    "Retry-After": str(stats["retry_after"]),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(stats["limit"])
        response.headers["X-RateLimit-Remaining"] = str(stats["remaining"])
        return response

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """取真实客户端 IP 作为限流键。

        X-Forwarded-For / X-Real-IP 由客户端自由填写，只在请求来自受信代理时
        才采信——否则任何人每请求换一个 XFF 就能拿到新令牌桶，限流形同虚设。
        受信判定复用 api/hub.py 的既有约定：本机回环 + TRUSTED_NETS 网段。
        """
        peer = request.client.host if request.client else ""
        if _peer_is_trusted(peer):
            forwarded_for = request.headers.get("X-Forwarded-For")
            if forwarded_for:
                # 多跳时取最左侧——那是链路最靠近客户端的那一跳
                return forwarded_for.split(",")[0].strip()
            real_ip = request.headers.get("X-Real-IP")
            if real_ip:
                return real_ip
        return peer or "unknown"

    @staticmethod
    def _should_skip(request: Request) -> bool:
        skip_paths = (
            "/health",
            "/ping",
            "/metrics",
            "/docs",
            "/redoc",
            "/openapi.json",
        )
        return any(request.url.path.startswith(p) for p in skip_paths)


# ================ 兼容接口 ================

class RateLimiter:
    """兼容旧接口的速率限制器类"""

    def __init__(self):
        self.manager = _limiter

    def is_allowed(
        self,
        key: str,
        limit: int = 60,
        window: int = 60,
        burst: Optional[int] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """兼容旧接口——全局 _limiter 使用模块级默认配置。
        
        注：传入的 limit/window/burst 被忽略，如需多档限速请使用
        middleware/rate_limiter.py InMemoryRateLimiter 直接扩展。
        """
        return _limiter.is_allowed(key)


def get_limiter() -> InMemoryRateLimiter:
    """获取限流器实例"""
    return _limiter


async def init_rate_limiter() -> None:
    """初始化速率限制器（内存模式无需异步初始化，保留接口兼容）"""
    # 内存模式无需初始化；未来接入 Redis 时在此建立连接
    return None


async def close_rate_limiter() -> None:
    """关闭速率限制器资源"""
    return None
