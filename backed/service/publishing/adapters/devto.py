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
    readback_mode = "api"

    home_url = "https://dev.to/"
    login_url = "https://dev.to/enter"
    list_url = "https://dev.to/dashboard"

    def read_back(self, pub: dict, article: dict = None) -> dict:
        """回读：GET /api/articles/{id} 拿平台侧真实标题与正文。

        这是结果级证据——比 publish 返回的 201 更硬，因为它是**再查一次**平台上
        现在有什么。draft 文章用 /articles/{id} 也能读到（作者本人 token）。
        """
        pid = (pub or {}).get("post_id") or ""
        if not pid:
            return {"supported": True, "found": False, "detail": "缺 post_id，无法回读"}
        try:
            r = requests.get(f"{API_BASE}/articles/{pid}", headers=_headers(),
                             timeout=TIMEOUT)
        except Exception as e:
            return {"supported": True, "found": False,
                    "detail": f"回读请求失败：{type(e).__name__}: {str(e)[:120]}"}
        if r.status_code == 404:
            return {"supported": True, "found": False, "detail": "平台上找不到这篇文章（可能已删除）"}
        if r.status_code in (401, 403):
            return {"supported": True, "found": False, "detail": f"回读无权限（HTTP {r.status_code}）"}
        if r.status_code != 200:
            return {"supported": True, "found": False,
                    "detail": f"回读失败 HTTP {r.status_code}: {r.text[:120]}"}
        a = r.json() or {}
        return {"supported": True, "found": True,
                "title": a.get("title", ""),
                "body": a.get("body_markdown") or a.get("body_html") or "",
                "url": a.get("url", ""),
                "published": bool(a.get("published")),
                "detail": ""}

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
