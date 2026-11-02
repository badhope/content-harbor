# -*- coding: utf-8 -*-
"""博客园适配器：走 MetaWeblog XML-RPC 标准协议，**不需要浏览器**。

为什么单独做它：博客园是少数开放了标准写入协议的老牌平台，
用 xmlrpc 发/改/列都比模拟浏览器稳一个数量级，还不用管登录态过期。

本适配器是薄壳：核心逻辑全部复用 ``service.publishing.tools.metaweblog.MetaWeblogClient``
（博客园/博客园类 MetaWeblog 协议平台的标准实现），本类只做凭据接入和平台特有适配。

开通：博客园 → 管理 → 设置 → 其他设置 → 勾选「允许 MetaWeblog 博客客户端访问」。
页面会给出三样东西，一个都不能猜：
  MetaWeblog 访问地址  https://rpc.cnblogs.com/metaweblog/<博客地址名>
  MetaWeblog 登录名    注意：不是邮箱，也不是昵称
  MetaWeblog 访问令牌  注意：2025 起**密码登录已取消**，填登录密码必挂

配置写进 config.json：
{
  "platforms": {
    "cnblogs": {
      "endpoint": "https://rpc.cnblogs.com/metaweblog/你的博客地址名",
      "username": "MetaWeblog 登录名",
      "token": "MetaWeblog 访问令牌"
    }
  }
}

坑（文档里没写，踩过才知道）：
  1. endpoint 里那段是**博客地址名**（https://www.cnblogs.com/<这段>/），
     不是数字用户 ID。填错的表现是 500，不是 404，极具迷惑性。
  2. 密码必须是**访问令牌**，登录密码会被拒："密码登录已取消，请在密码框中输入访问令牌"。
  3. categories 里**必须带 "[Markdown]"**，否则博客园把内容当 HTML 渲染，
     Markdown 全变成一行纯文本。
  4. 没勾「允许 MetaWeblog 博客客户端访问」同样是 500。
"""

import json
from pathlib import Path

from service.publishing.db import config_file

from service.publishing.adapters.base import PlatformAdapter, PlatformError, register
from service.publishing.tools.metaweblog import MetaWeblogClient

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = config_file()
MARKDOWN_FLAG = "[Markdown]"


def _load_creds():
    """从 config.json 读博客园凭据，带占位符守卫。"""
    if not CONFIG_FILE.exists():
        raise PlatformError("没有 config.json，博客园需要配 endpoint/username/token")
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    c = (cfg.get("platforms") or {}).get("cnblogs") or {}
    for k in ("endpoint", "username"):
        if not c.get(k):
            raise PlatformError(f"config.json 里 platforms.cnblogs.{k} 没填")
    if not (c.get("token") or c.get("password")):
        raise PlatformError(
            "config.json 里 platforms.cnblogs 缺 token。"
            "博客园已取消密码登录，必须填「MetaWeblog 访问令牌」")
    c["secret"] = c.get("token") or c.get("password")
    c["using_token"] = bool(c.get("token"))
    # 占位符守卫
    for k in ("endpoint", "username", "secret"):
        v = str(c.get(k, ""))
        if any("\u4e00" <= ch <= "\u9fff" for ch in v):
            raise PlatformError(
                f"config.json 里 platforms.cnblogs.{k} 还是占位符，没换成真实值。")
    return c


def _client() -> MetaWeblogClient:
    """构造 MetaWeblogClient（复用成熟工具层）。"""
    c = _load_creds()
    return MetaWeblogClient(
        endpoint=c["endpoint"], username=c["username"], password=c["secret"])


@register
class CnblogsAdapter(PlatformAdapter):
    id = "cnblogs"
    name = "博客园"
    needs_browser = False          # 纯协议，不开浏览器
    login_url = "https://account.cnblogs.com/signin"

    selector_version = "2026-09"
    key_selectors = {'editor': '.CodeMirror', 'title_input': "input[name='Title'], #post_title"}
    home_url = "https://i.cnblogs.com/posts"
    list_url = "https://i.cnblogs.com/posts"
    new_url = "https://i.cnblogs.com/posts/edit"

    # ---------------- 登录态 ----------------

    def check_auth(self, page=None) -> bool:
        try:
            cli = _client()
            posts = cli.get_recent_posts(count=1)
            return bool(posts) or True  # 能连上就算 auth OK（空博客无文章）
        except PlatformError:
            raise
        except Exception as e:
            raise PlatformError(
                f"MetaWeblog 连接失败：{type(e).__name__} {e}\n"
                f"三种可能：\n"
                f"  1) endpoint 里的博客名不对\n"
                f"  2) 密码不是 MetaWeblog 令牌（博客园要单独设置，不是登录密码）\n"
                f"  3) 没开通 MetaWeblog：设置 → 其他设置 → 允许 MetaWeblog 访问")

    def whoami(self):
        """排查用：把 endpoint 和拿到的博客信息打出来。"""
        cli = _client()
        return {"endpoint": cli.endpoint, "username": cli.username,
                "recent_posts": len(cli.get_recent_posts(count=10))}

    # ---------------- 列表 ----------------

    def list_articles(self, page=None, limit=50):
        cli = _client()
        posts = cli.get_recent_posts(count=limit)
        out = []
        for p in posts:
            pid = str(p.get("post_id") or p.get("postid") or "")
            out.append({
                "post_id": pid,
                "title": p.get("title", ""),
                "url": p.get("link", ""),
                "edit_url": f"https://i.cnblogs.com/posts/edit;postId={pid}" if pid else "",
                "status": "published",
                "stats": {},
            })
        return out

    # ---------------- 发布 ----------------

    def publish(self, page, article, options=None):
        options = options or {}
        cli = _client()
        tags = [t.strip() for t in (article.get("tags") or "").split(",") if t.strip()]
        categories = [MARKDOWN_FLAG] + tags
        publish_flag = not options.get("draft_only")
        pid = cli.new_post(
            title=article["title"],
            description=article.get("content_md", ""),
            categories=categories,
            mt_keywords=article.get("tags") or "",
            publish=publish_flag)
        pid = str(pid)
        return {"post_id": pid,
                "post_url": cli.get_post_url(pid) or f"https://www.cnblogs.com/p/{pid}.html",
                "edit_url": f"https://i.cnblogs.com/posts/edit;postId={pid}",
                "draft_only": bool(options.get("draft_only"))}

    # ---------------- 原地更新 ----------------

    def update(self, page, pub, article):
        cli = _client()
        pid = pub.get("post_id")
        if not pid:
            raise PlatformError("没有 post_id，更新不了")
        tags = [t.strip() for t in (article.get("tags") or "").split(",") if t.strip()]
        ok = cli.edit_post(
            post_id=pid,
            title=article["title"],
            description=article.get("content_md", ""),
            categories=[MARKDOWN_FLAG] + tags,
            mt_keywords=article.get("tags") or "")
        if not ok:
            raise PlatformError(f"editPost 返回 {ok}")
        return True
