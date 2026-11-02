# -*- coding: utf-8 -*-
"""对外地址推断中间件：把"用户实际访问的站点地址"记下来，供后台任务复用。

解决的具体问题：媒体改写发生在任务引擎的后台线程里，那里没有 `Request`。
上一轮的做法是要求用户必须手工配 `PUBLIC_BASE_URL`，否则静默跳过改写——
用户看到的是"图片没被转存"，但没有任何提示。

推断优先级（从可信到不可信）：
  1. `X-Forwarded-Proto` + `X-Forwarded-Host`：只在对端是**受信代理**时采信
     （复用 `api.auth` 的 TRUSTED_PROXIES 判定，防 Host 头注入）
  2. 请求自身的 `Host`：仅当它不像回环地址时采信
  3. 都不满足（本机 127.0.0.1 访问）：不设置，保持"未知"

为什么第 3 条不设置 127.0.0.1：平台侧抓不到用户的 localhost，写进正文只会
让图片全裂。桌面版单机发布场景本来就需要用户提供可公网访问的地址。
"""

from starlette.middleware.base import BaseHTTPMiddleware

from service.publishing import media


def _is_loopback_host(host: str) -> bool:
    h = (host or "").split(":")[0].strip("[]").lower()
    return h in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


class PublicBaseMiddleware(BaseHTTPMiddleware):
    """记录请求对应的对外基地址（只读请求头，不改响应）。"""

    async def dispatch(self, request, call_next):
        try:
            self._observe(request)
        except Exception:      # aqg: top-level boundary（推断失败绝不能阻断请求）
            pass
        return await call_next(request)

    @staticmethod
    def _observe(request) -> None:
        from api.auth import _trusted_proxies
        from ipaddress import ip_address, ip_network

        peer = request.client.host if request.client else ""
        trusted_proxy = False
        proxies = _trusted_proxies()
        if peer and proxies:
            try:
                addr = ip_address(peer)
                trusted_proxy = any(
                    addr in ip_network(n, strict=False) for n in proxies)
            except (ValueError, TypeError):
                trusted_proxy = False

        if trusted_proxy:
            proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
            host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
            if proto in ("http", "https") and host and not _is_loopback_host(host):
                media.set_runtime_base(f"{proto}://{host}")
                return

        host = request.headers.get("host", "").strip()
        if not host or _is_loopback_host(host):
            return
        scheme = request.url.scheme or "http"
        media.set_runtime_base(f"{scheme}://{host}")
