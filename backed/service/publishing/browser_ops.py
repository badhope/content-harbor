# -*- coding: utf-8 -*-
"""内置浏览器操控面：带登录态抓取 / 截图——"随便操控"的 API 化。

定位：Harbour 的内置浏览器（Patchright 驱动系统 Edge，profile 持久化登录态）
不应该只服务发布链路。把 fetch / screenshot 暴露成能力后：
  - 爬虫场景：带平台登录态抓登录墙后的内容（trafilatura 纯 HTTP 做不到）
  - AI 场景：MCP 工具 browser_fetch / browser_screenshot 直接可用
  - 排查场景：截图留证，配合 dump_dom 定位页面结构变更

安全边界（DNS 重绑定）
--------------------
浏览器自身不能在 socket 层做连接钉扎，所以这里改用 Chromium 的
``--host-resolver-rules``：先在本进程解析并校验全部 A/AAAA，全部为公网地址后，
把该主机名**映射到已校验的 IP** 再启动浏览器。浏览器渲染进程的 DNS 与
套接字都由这层规则约束，攻击者无法在"校验"和"连接"之间换掉解析结果。
配合 ``--host-resolver-rules`` 的 ``MAP * ~NOTFOUND EXCLUDE <host>``，其余
主机名一律拒绝解析，连重定向到内网也会在浏览器内直接失败。

实现模式与 ``_account_ops.assist_open`` 一致：per-key 互斥（整个操作期间持有）
→ 释放池内旧实例 → 新开 BuiltinBrowser（复用 profile 登录态）→ 操作 → 关闭。
needs_browser=False 的纯协议平台（cnblogs 等）同样强制开浏览器——操控面不
依赖适配器通道。
"""

import time
from urllib.parse import urlparse

from service.publishing.db import data_root
from service.publishing.url_safety import (
    URLSafetyError,
    resolve_public_ips,
    validate_timeout,
    validate_url as _validate_url,
)


def validate_url(url: str) -> str:
    """校验 scheme，并拒绝 DNS 解析到非公网地址的目标。"""
    try:
        return _validate_url(url)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


def _pinned_resolver_rules(url: str) -> list[str]:
    """Build a Chromium arg that pins ``url``'s host to its validated public IPs.

    Chromium accepts several MAP rules inside a single ``--host-resolver-rules``
    value; passing the flag twice would let the last one win, so everything is
    emitted as one comma-separated rule set.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("url 必须含有效域名")
    addresses = resolve_public_ips(host)
    if not addresses:
        raise ValueError(f"url 主机 {host} 无公网地址")
    mapping = ",".join(str(ip) for ip in addresses)
    return ["--host-resolver-rules=MAP " + host + " " + mapping]


def _guard_requests(page, pinned_host: str) -> None:
    """Abort every request whose host does not resolve to a public address.

    The primary target is already pinned by ``--host-resolver-rules``; this
    guard closes the same hole for sub-resources and cross-host redirects,
    which no launch flag can enumerate up front.
    """
    from urllib.parse import urlparse as _parse

    allowed_cache: dict[str, bool] = {}

    def _allowed(hostname: str) -> bool:
        if not hostname:
            return False
        cached = allowed_cache.get(hostname)
        if cached is not None:
            return cached
        try:
            resolve_public_ips(hostname)
            allowed = True
        except (URLSafetyError, ValueError):
            allowed = False
        allowed_cache[hostname] = allowed
        return allowed

    def _handler(route):
        request = route.request
        parsed = _parse(request.url)
        if parsed.scheme not in ("http", "https"):
            # data:/blob:/about: are page-internal and carry no network risk
            return route.continue_()
        hostname = (parsed.hostname or "").lower()
        if _allowed(hostname):
            return route.continue_()
        return route.abort("blockedbyclient")

    page.route("**/*", _handler)



def _run_with_pinned_browser(hub, platform, account, url, timeout, action):
    """Open a browser whose DNS for ``url`` is pinned, then run ``action``."""
    from service.publishing.browser import BuiltinBrowser, browser_thread_run

    resolved = _pinned_resolver_rules(url)
    pinned_host = (urlparse(url).hostname or "").lower()
    opts = {"extra_args": resolved}

    def _op():
        br = None
        try:
            with hub._browser_pool._busy_for(platform, account):
                hub._drop(platform, account)
                br = BuiltinBrowser(platform, account, headless=False, **opts)
                page = br.start().new_page()
                _guard_requests(page, pinned_host)
                page.goto(url, wait_until="domcontentloaded",
                          timeout=int(timeout * 1000))
                # 重定向后落点必须仍在允许集合内（route 已拦截，这里再兜一层）
                final_host = (urlparse(page.url).hostname or "").lower()
                if final_host != pinned_host:
                    resolve_public_ips(final_host)
                time.sleep(1.5)   # 等 JS 首屏渲染（domcontentloaded 不含异步内容）
                return action(br, page)
        finally:
            if br is not None:
                try:
                    br.close()
                except Exception:   # aqg: top-level boundary（清理失败不掩盖主异常）
                    pass

    return browser_thread_run(_op, _run_timeout=int(timeout) + 120)


def browser_fetch(hub, platform, url, account="default", timeout=60,
                  want_html=False):
    """带平台登录态抓取页面：返回标题 + 正文 + 可选 HTML。"""
    timeout = validate_timeout(timeout, name="timeout")
    validate_url(url)

    def _extract(br, page):
        import trafilatura
        html = page.content()
        title = page.title() or ""
        text = trafilatura.extract(html) or ""
        if not text.strip():
            text = page.inner_text("body")[:20000]
        out = {"url": page.url, "title": title,
               "text": text[:50000], "html_len": len(html)}
        if want_html:
            out["html"] = html[:300000]
        return out

    return _run_with_pinned_browser(hub, platform, account, url, timeout, _extract)


def browser_screenshot(hub, platform, url, account="default", full_page=False,
                       timeout=60):
    """带登录态打开页面并截图，落盘 data/exports/screenshots/。"""
    timeout = validate_timeout(timeout, name="timeout")
    validate_url(url)

    def _shot(br, page):
        out_dir = data_root() / "exports" / "screenshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"{time.strftime('%Y%m%d_%H%M%S')}_{platform}.png"
        path = out_dir / name
        page.screenshot(full_page=full_page, path=str(path))
        return {"path": str(path), "url": page.url,
                "bytes": path.stat().st_size}

    return _run_with_pinned_browser(hub, platform, account, url, timeout, _shot)


__all__ = [
    "browser_fetch",
    "browser_screenshot",
    "validate_url",
    "URLSafetyError",
]
