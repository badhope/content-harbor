# -*- coding: utf-8 -*-
"""Telegraph 适配器（telegra.ph 免登录即时发布，纯 API）。

Telegraph 是 Telegram 团队提供的极简发布平台：无需注册账号，
首次调 createAccount 即获得 access_token，之后可创建/编辑页面。
适合"快速发布一篇 Markdown"或"生成预览链接分享"场景。

凭据放 config.json（token 可留空，首发布会自动创建账号并回写）：
    "platforms": { "telegraph": { "token": "", "author_name": "Harbour" } }

依赖：telegraph (PyPI, MIT) —— 对 API 的完整封装，避免手写 HTTP 转换。
"""

import json
from pathlib import Path

from service.publishing.db import config_file

from service.publishing.adapters.base import PlatformAdapter, PlatformError, register

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = config_file()


def _creds():
    if not CONFIG_FILE.exists():
        return {"token": "", "author_name": "Harbour"}
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    c = (cfg.get("platforms") or {}).get("telegraph") or {}
    return {"token": str(c.get("token") or ""), "author_name": str(c.get("author_name") or "Harbour")}


def _save_token(token: str) -> None:
    """首发布自动创建账号后回写 token，后续编辑/列表依赖它。"""
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
        cfg.setdefault("platforms", {}).setdefault("telegraph", {})["token"] = token
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _md_to_telegraph_nodes(md_text: str) -> list:
    """Markdown → Telegraph Node 数组（ Telegraph API 要求的格式）。

    最小实现：段落 + 代码块 + 图片。复杂格式（表格等）降级为纯文本段落。
    """
    from service.publishing.adapters.base import md_to_html
    html = md_to_html(md_text)
    # Telegraph 接受 Node 数组；最简单方式：整篇作为一个 html 字符串节点
    # Telegraph 内部会解析 HTML（只支持部分标签，但 p/img/pre/code 等常用标签都在）
    return [{"tag": "p", "children": [line]} for line in html.split("\n") if line.strip()]


@register
class TelegraphAdapter(PlatformAdapter):
    id = "telegraph"
    name = "Telegraph"
    needs_browser = False

    home_url = "https://telegra.ph/"

    def _client(self):
        from telegraph import Telegraph
        c = _creds()
        tg = Telegraph(c["author_name"])
        token = c["token"]
        if token:
            tg.set_access_token(token)
        else:
            tg.create_account(short_name="Harbour")
            _save_token(tg.get_access_token())
        return tg

    def check_auth(self, page=None) -> bool:
        try:
            tg = self._client()
            return bool(tg.get_page_list(1)["pages"])
        except Exception:
            return False

    def publish(self, page, article, options=None):
        options = options or {}
        tg = self._client()
        nodes = _md_to_telegraph_nodes(article.get("content_md", ""))
        result = tg.create_page(
            title=article.get("title", "untitled"),
            content=nodes,
            author_name=_creds()["author_name"],
        )
        return {"post_id": result["path"],
                "post_url": result["url"],
                "edit_url": result["url"],
                "draft_only": False}

    def update(self, page, pub, article):
        path = pub.get("post_id") or ""
        if not path:
            raise PlatformError("telegraph 原地更新需要 post_id (path)")
        tg = self._client()
        nodes = _md_to_telegraph_nodes(article.get("content_md", ""))
        tg.edit_page(
            path=path,
            title=article.get("title", "untitled"),
            content=nodes,
        )
        return True

    def list_articles(self, page=None, limit=50):
        tg = self._client()
        result = tg.get_page_list(min(limit, 200))
        out = []
        for pg in result["pages"]:
            out.append({
                "post_id": pg["path"],
                "title": pg.get("title", ""),
                "url": pg.get("url", ""),
                "edit_url": pg.get("url", ""),
                "status": "published",
                "stats": {"views": pg.get("views", 0)},
            })
        return out
