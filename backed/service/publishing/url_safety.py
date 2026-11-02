# -*- coding: utf-8 -*-
"""Shared validation for network targets crossing the publishing boundary."""

import http.client
import ipaddress
import socket
import ssl
import threading
import time
from urllib.parse import urlparse


_fetch_slots = threading.BoundedSemaphore(16)


ALLOWED_SCHEMES = frozenset(("http", "https"))
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 600.0


class URLSafetyError(ValueError):
    """Raised when a target URL is not safe to access."""


def validate_timeout(value, *, default=None, name="timeout") -> float:
    """Return a bounded timeout, rejecting malformed, zero, negative, or huge values."""
    if value is None:
        value = default
    if value is None:
        raise URLSafetyError(f"{name} 必须设置")
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise URLSafetyError(f"{name} 必须是数字")
    if timeout != timeout or timeout < MIN_TIMEOUT or timeout > MAX_TIMEOUT:
        raise URLSafetyError(f"{name} 必须在 {MIN_TIMEOUT:g}-{MAX_TIMEOUT:g} 秒之间")
    return timeout


_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _is_public_ip(ip: ipaddress._BaseAddress) -> bool:
    """Only globally routable addresses are acceptable targets."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT:
        return False
    return bool(ip.is_global) and not any((
        ip.is_private, ip.is_loopback, ip.is_link_local,
        ip.is_reserved, ip.is_multicast, ip.is_unspecified,
    ))


def resolve_public_ips(host: str) -> tuple[ipaddress._BaseAddress, ...]:
    """Resolve a host and require every returned address to be globally routable."""
    if not host:
        raise URLSafetyError("url 必须含有效域名")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_ip(literal):
            raise URLSafetyError(f"url 主机 {host} 不是公网地址")
        return (literal,)

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror) as exc:
        raise URLSafetyError(f"url 主机 {host} 无法解析") from exc
    addresses = []
    seen = set()
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError):
            raise URLSafetyError(f"url 主机 {host} 解析出非法地址")
        if not _is_public_ip(ip):
            raise URLSafetyError(f"url 主机 {host} 解析到非公网地址 {ip}")
        if ip not in seen:
            addresses.append(ip)
            seen.add(ip)
    if not addresses:
        raise URLSafetyError(f"url 主机 {host} 无公网地址")
    return tuple(addresses)


def validate_url(url: str) -> str:
    """Require an absolute HTTP(S) URL whose DNS results are all public."""
    if not isinstance(url, str) or not url.strip():
        raise URLSafetyError("url 必须是非空字符串")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise URLSafetyError(f"仅支持 http/https 链接: {url[:100]}")
    if parsed.username is not None or parsed.password is not None:
        raise URLSafetyError("url 不允许携带用户凭据")
    host = parsed.hostname
    if not host:
        raise URLSafetyError("url 必须含有效域名")
    resolve_public_ips(host)
    return url


def validate_redirect_url(current_url: str, location: str) -> str:
    from urllib.parse import urljoin
    return validate_url(urljoin(current_url, location))


def fetch_pinned(url: str, timeout: float = 30.0, max_bytes: int = 10 * 1024 * 1024,
                 max_redirects: int = 5, headers: dict | None = None) -> tuple[bytes, str]:
    """GET a public URL while pinning the connection to the validated IP.

    DNS is resolved once, every address is checked, then the socket connects to
    that literal IP while Host/SNI/certificate validation keep the original name.
    This closes the resolve-then-connect DNS rebinding window for this helper.
    """
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        raise URLSafetyError("抓取超时参数无效")
    if not 0 < timeout <= MAX_TIMEOUT:
        raise URLSafetyError("抓取超时参数无效")
    deadline = time.monotonic() + timeout
    if not _fetch_slots.acquire(blocking=False):
        raise URLSafetyError("抓取并发已达安全上限")
    done = threading.Event()
    cancelled = threading.Event()
    result = []

    def run():
        try:
            result.append((True, _fetch_pinned(url, deadline, max_bytes, max_redirects,
                                               headers, cancelled)))
        except BaseException as exc:
            result.append((False, exc))
        finally:
            _fetch_slots.release()
            done.set()

    try:
        threading.Thread(target=run, daemon=True).start()
    except BaseException:
        _fetch_slots.release()
        raise
    if not done.wait(max(0, deadline - time.monotonic())):
        # 调用方已经超时返回，工作线程必须停止一切网络动作：
        # 否则"超时"只是假象——请求仍会在后台发出去，调用方却以为没发生。
        cancelled.set()
        raise URLSafetyError("抓取超时")
    if not result[0][0]:
        raise result[0][1]
    if time.monotonic() >= deadline:
        raise URLSafetyError("抓取超时")
    return result[0][1]


def _fetch_pinned(url, deadline, max_bytes, max_redirects, headers, cancelled=None):
    current = url
    def _abort_if_cancelled():
        """调用方已放弃等待：立即停止，绝不把请求发出去。"""
        if cancelled is not None and cancelled.is_set():
            raise URLSafetyError("抓取已取消")
        if time.monotonic() >= deadline:
            raise URLSafetyError("抓取超时")

    for _ in range(max_redirects + 1):
        validate_url(current)
        _abort_if_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise URLSafetyError("抓取超时")
        parsed = urlparse(current)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = resolve_public_ips(host)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise URLSafetyError("抓取超时")
        _abort_if_cancelled()
        chosen = str(addresses[0])
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        request_headers = {"Host": parsed.netloc, **(headers or {})}

        if parsed.scheme == "https":
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
            conn = http.client.HTTPSConnection(chosen, port, timeout=remaining,
                                               context=context)
            # 连接钉在已校验 IP 上，证书仍按原始域名校验
            sock = socket.create_connection((chosen, port), timeout=remaining)
            try:
                conn.sock = context.wrap_socket(sock, server_hostname=host)
            except Exception:
                sock.close()
                raise
        else:
            conn = http.client.HTTPConnection(chosen, port, timeout=remaining)
            conn.connect()
        try:
            if time.monotonic() >= deadline:
                raise URLSafetyError("抓取超时")
            conn.request("GET", path, headers=request_headers)
            if time.monotonic() >= deadline:
                raise URLSafetyError("抓取超时")
            response = conn.getresponse()
            if time.monotonic() >= deadline:
                raise URLSafetyError("抓取超时")
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("location")
                if not location:
                    raise URLSafetyError("重定向缺少目标")
                current = validate_redirect_url(current, location)
                continue
            if response.status != 200:
                raise URLSafetyError(f"HTTP {response.status}")
            chunks: list[bytes] = []
            total = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise URLSafetyError("抓取超时")
                if conn.sock is not None:
                    conn.sock.settimeout(remaining)
                chunk = response.read1(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise URLSafetyError("响应内容超过安全大小上限")
                chunks.append(chunk)
            return b"".join(chunks), current
        finally:
            conn.close()
    raise URLSafetyError("重定向次数过多")
