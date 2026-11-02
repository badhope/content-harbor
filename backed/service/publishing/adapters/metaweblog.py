# -*- coding: utf-8 -*-
"""自建博客通用适配器（WordPress / Typecho 等 MetaWeblog 标准协议）。

用户自己搭的博客（WordPress、Typecho、Halo 等）都实现 MetaWeblog XML-RPC，
用户名 + 应用密码即可直发，**不用扫码、不用开浏览器**，是最省事的发布通道。

凭据放 config.json：
    "platforms": { "metaweblog": { "endpoint": "https://你的域名/xmlrpc.php",
                                    "username": "登录名",
                                    "password": "应用密码或登录密码" } }

与博客园适配器同协议，但自建站没有博客园的 [Markdown] 分类 hack：
Typecho 原生支持 Markdown 渲染，WordPress 装了 Markdown 插件也能渲染，
所以这里直接传 Markdown 原文（比转 HTML 更保真）。
"""

import json
import xmlrpc.client
from datetime import datetime
from pathlib import Path

from service.publishing.db import config_file

from service.publishing.adapters.base import PlatformAdapter, PlatformError, register

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = config_file()

# WordPress / Typecho 都认这套字段名，个别实现（Halo）可能忽略，不影响发布
BLOG_ID = "0"


def _creds():
    if not CONFIG_FILE.exists():
        raise PlatformError("没有 config.json，自建博客需要配 endpoint/username/password")
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    c = (cfg.get("platforms") or {}).get("metaweblog") or {}
    for k in ("endpoint", "username", "password"):
        if not c.get(k):
            raise PlatformError(
                f"config.json 里 platforms.metaweblog.{k} 没填。"
                "去 WordPress 用户→个人资料→应用程序密码 生成一个，"
                "Typecho 直接用登录密码即可。")
    # 占位符守卫：中文说明还是模板值
    for k in ("endpoint", "username", "password"):
        v = str(c.get(k, ""))
        if any("\u4e00" <= ch <= "\u9fff" for ch in v):
            raise PlatformError(
                f"config.json 里 platforms.metaweblog.{k} 还是占位符，没换成真实值。")
    return c


def _client():
    c = _creds()
    return xmlrpc.client.ServerProxy(c["endpoint"], allow_none=True), c


class MetaWeblogAdapter(PlatformAdapter):
    id = "metaweblog"
    name = "自建博客"
    needs_browser = False          # 纯 XML-RPC，不开浏览器

    home_url = None
    login_url = None
    key_selectors = {}

    # ---------------- 登录态 ----------------

    def check_auth(self, page=None) -> bool:
        s, c = _client()
        try:
            blogs = s.blogger.getUsersBlogs("", c["username"], c["password"])
            return bool(blogs)
        except Exception:
            return False

    def whoami(self):
        s, c = _client()
        blogs = s.blogger.getUsersBlogs("", c["username"], c["password"])
        return {"endpoint": c["endpoint"], "username": c["username"], "blogs": blogs}

    def _blogid(self, s, c):
        blogs = s.blogger.getUsersBlogs("", c["username"], c["password"])
        if not blogs:
            raise PlatformError("MetaWeblog 没返回博客，检查 endpoint 和账号")
        return blogs[0].get("blogid") or BLOG_ID

    # ---------------- 列表 ----------------

    def list_articles(self, page=None, limit=50):
        s, c = _client()
        bid = self._blogid(s, c)
        posts = s.metaWeblog.getRecentPosts(bid, c["username"], c["password"], limit)
        out = []
        for p in posts or []:
            pid = str(p.get("postid", ""))
            if not pid:
                continue
            out.append({
                "post_id": pid,
                "title": p.get("title", ""),
                "url": p.get("link", ""),
                "edit_url": p.get("link", ""),
                "status": "published" if p.get("post_status") != "draft" else "draft",
                "stats": {},
            })
        return out

    # ---------------- 发布 ----------------

    def publish(self, page, article, options=None):
        options = options or {}
        s, c = _client()
        bid = self._blogid(s, c)
        tags = [t.strip() for t in (article.get("tags") or "").split(",") if t.strip()]
        struct = {
            "title": article["title"],
            "description": article.get("content_md", ""),
            "mt_keywords": tags,
            "dateCreated": xmlrpc.client.DateTime(datetime.now()),
        }
        publish_flag = not options.get("draft_only")
        pid = str(s.metaWeblog.newPost(bid, c["username"], c["password"],
                                       struct, publish_flag))
        return {"post_id": pid,
                "post_url": f"{c['endpoint']}?p={pid}",
                "edit_url": f"{c['endpoint']}?p={pid}&action=edit",
                "draft_only": bool(options.get("draft_only"))}

    # ---------------- 原地更新 ----------------

    def update(self, page, pub, article):
        s, c = _client()
        pid = pub.get("post_id")
        if not pid:
            raise PlatformError("没有 post_id，更新不了")
        tags = [t.strip() for t in (article.get("tags") or "").split(",") if t.strip()]
        struct = {
            "title": article["title"],
            "description": article.get("content_md", ""),
            "mt_keywords": tags,
        }
        ok = s.metaWeblog.editPost(pid, c["username"], c["password"], struct, True)
        if not ok:
            raise PlatformError(f"editPost 返回 {ok}")
        return True


register(MetaWeblogAdapter)
