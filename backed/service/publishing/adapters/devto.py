# -*- coding: utf-8 -*-
"""Dev.to 适配器（Forem 官方 REST API，纯协议免浏览器）。

为什么选它：完全开放的官方 API（`api-key` 头认证），Markdown 原生存储，
创建/更新/列表三个动作都是一等端点——是所有候选平台里接入成本最低、
可靠性最高的一个。中文技术创作者做双语分发时的标准出口。

凭据放 config.json：
    "platforms": { "devto": { "api_key": "xxx" } }
或环境变量 DEVTO_API_KEY（优先级更高，便于 CI/容器）。

API 参考（developers.forem.com/api/v1）：
  POST /api/articles            {article: {title, body_markdown, published, tags(≤4)}}
  PUT  /api/articles/{id}       同上（更新）
  GET  /api/articles/me/all     自己全部文章（未发布在前）
限流：官方未给确定数值；遇到 429 时任务引擎的平台冷却（ratelimit.py）会兜住。
"""

import json
import os
import re
from pathlib import Path

from service.publishing.db import config_file

import requests

from service.publishing.adapters.base import PlatformAdapter, PlatformError, register

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = config_file()
API_BASE = "https://dev.to/api"
TIMEOUT = 30
MAX_TAGS = 4

# dev.to 标签规则：小写字母数字，2-30 字符
_TAG_RE = re.compile(r"^[a-z0-9]{2,30}$")


def _creds():
    env = os.environ.get("DEVTO_API_KEY", "").strip()
    if env:
        return {"api_key": env}
    if not CONFIG_FILE.exists():
        raise PlatformError("没有 config.json，devto 需要配 api_key（或设 DEVTO_API_KEY）")
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    c = (cfg.get("platforms") or {}).get("devto") or {}
    if not c.get("api_key"):
        raise PlatformError(
            "config.json 里 platforms.devto.api_key 没填。"
            "去 dev.to/settings/extensions 生成 API Key，或设 DEVTO_API_KEY 环境变量。")
    v = str(c["api_key"])
    if any("\u4e00" <= ch <= "\u9fff" for ch in v):
        raise PlatformError("platforms.devto.api_key 还是占位符，没换成真实值。")
    return {"api_key": v}


def _headers():
    return {"api-key": _creds()["api_key"], "Content-Type": "application/json"}


def _clean_tags(tags: str) -> list:
    """dev.to 标签限制：≤4 个、小写、[a-z0-9]。不合规则的不硬塞（422 会被任务引擎当成重试错误空转）。"""
    out = []
    for t in (tags or "").split(","):
        t = t.strip().lower().replace(" ", "")
        if _TAG_RE.match(t) and t not in out:
            out.append(t)
        if len(out) >= MAX_TAGS:
            break
    return out


@register
class DevToAdapter(PlatformAdapter):
    id = "devto"
    name = "Dev.to"
    needs_browser = False

    home_url = "https://dev.to/"
    login_url = "https://dev.to/enter"
    list_url = "https://dev.to/dashboard"

    def check_auth(self, page=None) -> bool:
        try:
            r = requests.get(f"{API_BASE}/articles/me/all?per_page=1",
                             headers=_headers(), timeout=TIMEOUT)
        except Exception:
            return False
        if r.status_code == 401:
            return False
        if r.status_code == 429:
            # 限流不是"凭据无效"：交由上层按可重试错误处理，但 check 语义上
            # 只能如实报告未知——返回 True 会让账号状态骗人，返回 False 会
            # 提示重登。折中：让 429 抛 PlatformError 带状态码（任务引擎分诊）。
            raise PlatformError("HTTP 429: rate limited (devto check)")
        return r.status_code == 200

    def list_articles(self, page=None, limit=50):
        r = requests.get(f"{API_BASE}/articles/me/all",
                         headers=_headers(),
                         params={"per_page": max(1, min(limit, 1000))},
                         timeout=TIMEOUT)
        if r.status_code == 429:
            raise PlatformError("HTTP 429: rate limited (devto list)")
        if r.status_code == 401:
            raise PlatformError("devto api_key 无效")
        if r.status_code != 200:
            raise PlatformError(f"GET /articles/me/all -> {r.status_code}")
        out = []
        for a in (r.json() or [])[:limit]:
            pid = str(a.get("id", ""))
            out.append({
                "post_id": pid,
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "edit_url": f"https://dev.to/{a.get('path', '')}/edit" if a.get("path") else "",
                "status": "published" if a.get("published") else "draft",
                "stats": {"reactions": a.get("positive_reactions_count", 0),
                          "comments": a.get("comments_count", 0)},
            })
        return out

    def publish(self, page, article, options=None):
        options = options or {}
        body = {
            "article": {
                "title": article.get("title", ""),
                "body_markdown": article.get("content_md", ""),
                "published": not options.get("draft_only"),
                "tags": _clean_tags(article.get("tags") or ""),
            }
        }
        if article.get("summary"):
            body["article"]["description"] = article["summary"][:140]
        if article.get("cover"):
            body["article"]["main_image"] = article["cover"]
        r = requests.post(f"{API_BASE}/articles", headers=_headers(),
                          json=body, timeout=TIMEOUT)
        if r.status_code == 429:
            raise PlatformError(f"HTTP 429: rate limited (devto publish) {r.headers.get('retry-after', '')}")
        if r.status_code == 401:
            raise PlatformError("devto api_key 无效")
        if r.status_code != 201:
            raise PlatformError(f"POST /articles 失败({r.status_code}): {r.text[:200]}")
        a = r.json()
        return {"post_id": str(a.get("id", "")),
                "post_url": a.get("url", ""),
                "edit_url": f"https://dev.to/{a.get('path', '')}/edit",
                "draft_only": bool(options.get("draft_only"))}

    def update(self, page, pub, article):
        pid = pub.get("post_id")
        if not pid:
            raise PlatformError("devto 原地更新需要 post_id")
        body = {"article": {
            "title": article.get("title", ""),
            "body_markdown": article.get("content_md", ""),
        }}
        tags = _clean_tags(article.get("tags") or "")
        if tags:
            body["article"]["tags"] = tags
        r = requests.put(f"{API_BASE}/articles/{pid}", headers=_headers(),
                         json=body, timeout=TIMEOUT)
        if r.status_code == 429:
            raise PlatformError(f"HTTP 429: rate limited (devto update) {r.headers.get('retry-after', '')}")
        if r.status_code == 404:
            raise PlatformError(f"devto 文章 {pid} 不存在（可能已删除）")
        if r.status_code != 200:
            raise PlatformError(f"PUT /articles/{pid} 失败({r.status_code}): {r.text[:200]}")
        return True
