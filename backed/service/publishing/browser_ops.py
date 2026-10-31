# -*- coding: utf-8 -*-
"""内置浏览器操控面：带登录态抓取 / 截图——"随便操控"的 API 化。

定位：Harbour 的内置浏览器（Patchright 驱动系统 Edge，profile 持久化登录态）
不应该只服务发布链路。把 fetch / screenshot 暴露成能力后：
  - 爬虫场景：带平台登录态抓登录墙后的内容（trafilatura 纯 HTTP 做不到）
  - AI 场景：MCP 工具 browser_fetch / browser_screenshot 直接可用
  - 排查场景：截图留证，配合 dump_dom 定位页面结构变更

实现模式与 ``_account_ops.assist_open`` 一致：per-key 互斥（整个操作期间持有）
→ 释放池内旧实例 → 新开 BuiltinBrowser（复用 profile 登录态）→ 操作 → 关闭。
needs_browser=False 的纯协议平台（cnblogs 等）同样强制开浏览器——操控面不
依赖适配器通道。
"""

import time
from urllib.parse import urlparse

from service.publishing.db import data_root


def validate_url(url: str) -> str:
    """scheme 白名单 + SSRF 黑名单（与 accounts._check_url_safety 同口径）。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"仅支持 http/https 链接: {url[:100]}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("url 必须含有效域名")
    bad_hosts = ("localhost", "metadata.google.internal", "169.254.169.254")
    if any(host == b or host.endswith(b) for b in bad_hosts):
        raise ValueError(f"url 主机 {host} 在 SSRF 黑名单中")
    return url


def browser_fetch(hub, platform, url, account="default", timeout=60,
                  want_html=False):
    """带平台登录态抓取页面：返回标题 + 正文 + 可选 HTML。

    正文抽取用 trafilatura（与 import_url 同引擎），拿不到时退化整个
    body 文本——操控面保证"有东西回来"，抽取质量是另一层的事。
    """
    def _op():
        from service.publishing.browser import BuiltinBrowser
        validate_url(url)
        br = None
        try:
            with hub._browser_pool._busy_for(platform, account):
                hub._drop(platform, account)
                br = BuiltinBrowser(platform, account, headless=False)
                page = br.start().new_page()
                page.goto(url, wait_until="domcontentloaded",
                          timeout=timeout * 1000)
                time.sleep(1.5)   # 等 JS 首屏渲染（domcontentloaded 不含异步内容）
                html = page.content()
                title = page.title() or ""
                import trafilatura
                text = trafilatura.extract(html) or ""
                if not text.strip():
                    text = page.inner_text("body")[:20000]
                out = {"url": page.url, "title": title,
                       "text": text[:50000], "html_len": len(html)}
                if want_html:
                    out["html"] = html[:300000]
                return out
        finally:
            if br is not None:
                try:
                    br.close()
                except Exception:
                    pass
    from service.publishing.browser import browser_thread_run
    return browser_thread_run(_op)


def browser_screenshot(hub, platform, url, account="default", full_page=False,
                       timeout=60):
    """带登录态打开页面并截图，落盘 data/exports/screenshots/。"""
    def _op():
        from service.publishing.browser import BuiltinBrowser
        validate_url(url)
        br = None
        try:
            with hub._browser_pool._busy_for(platform, account):
                hub._drop(platform, account)
                br = BuiltinBrowser(platform, account, headless=False)
                page = br.start().new_page()
                page.goto(url, wait_until="domcontentloaded",
                          timeout=timeout * 1000)
                time.sleep(1.5)
                out_dir = data_root() / "exports" / "screenshots"
                out_dir.mkdir(parents=True, exist_ok=True)
                name = f"{time.strftime('%Y%m%d_%H%M%S')}_{platform}.png"
                path = out_dir / name
                page.screenshot(full_page=full_page, path=str(path))
                return {"path": str(path), "url": page.url,
                        "bytes": path.stat().st_size}
        finally:
            if br is not None:
                try:
                    br.close()
                except Exception:
                    pass
    from service.publishing.browser import browser_thread_run
    return browser_thread_run(_op)
