# -*- coding: utf-8 -*-
"""51CTO 博客适配器：走 MetaWeblog XML-RPC 标准协议，**不需要浏览器**。

为什么做它：51CTO 是老牌技术博客平台，开放了标准写入协议（xmlrpc.php），
用账号密码直发，**不用扫码、不用管登录态过期**——正是"免扫码内容平台"里
除博客园之外的另一条通道。

开通：不需要额外设置（51CTO 一直支持 MetaWeblog 客户端直发）。
配置写进 config.json：
{
  "platforms": {
    "51cto": {
      "endpoint": "https://你的用户名.blog.51cto.com/xmlrpc.php",
      "username": "51CTO 用户名",
      "password": "51CTO 登录密码"
    }
  }
}

坑（文档里没写，踩过才知道）：
  1. endpoint 里的"你的用户名"是博客地址名（https://<这段>.blog.51cto.com/），
     不是昵称、不是手机号。填错表现是 500/401，极具迷惑性。
  2. 密码就是**登录密码**（51CTO 没像博客园那样改成令牌）。
  3. 内容按 HTML 传（51CTO 的 xmlrpc 对 Markdown 支持不稳），发布前转 HTML。
"""

import json
import xmlrpc.client
from datetime import datetime
from pathlib import Path

from service.publishing.db import config_file

from service.publishing.adapters.base import PlatformAdapter, PlatformError, register

# 本文件在 core/adapters/ 下，往上三级才是项目根
ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = config_file()


def _creds():
    if not CONFIG_FILE.exists():
        raise PlatformError("没有 config.json，51CTO 需要配 endpoint/username/password")
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    c = (cfg.get("platforms") or {}).get("51cto") or {}
    if not c.get("endpoint") and c.get("username"):
        # 没写 endpoint 时按标准规则自动拼，省一步配置
        c["endpoint"] = f"https://{c['username']}.blog.51cto.com/xmlrpc.php"
    for k in ("endpoint", "username", "password"):
        if not c.get(k):
            raise PlatformError(f"config.json 里 platforms.51cto.{k} 没填。"
                                "51CTO 博客设置里能看到你的博客地址名，密码用登录密码。")
    # 占位符守卫：中文说明还是模板值
    for k in ("endpoint", "username", "password"):
        v = str(c.get(k, ""))
        if any("\u4e00" <= ch <= "\u9fff" for ch in v):
            raise PlatformError(
                f"config.json 里 platforms.51cto.{k} 还是占位符，没换成真实值。")
    return c


def _client():
    c = _creds()
    return xmlrpc.client.ServerProxy(c["endpoint"], allow_none=True), c


@register
class WuyiCTOAdapter(PlatformAdapter):
    id = "51cto"
    name = "51CTO博客"
    needs_browser = False          # 纯协议，不开浏览器
    login_url = "https://www.51cto.com/"

    selector_version = "2026-09"
    key_selectors = {'editor': '.CodeMirror, .ql-editor', 'title_input': "input[placeholder*='标题']"}
    home_url = "https://blog.51cto.com/"
    list_url = "https://blog.51cto.com/my/posts"
    new_url = "https://blog.51cto.com/my/post/edit"

    # ---------------- 登录态 ----------------

    def check_auth(self, page=None) -> bool:
        try:
            s, c = _client()
            blogs = s.blogger.getUsersBlogs("", c["username"], c["password"])
            return bool(blogs)
        except xmlrpc.client.ProtocolError as e:
            raise PlatformError(
                f"MetaWeblog 返回 {e.errcode}。三种可能：\n"
                f"  1) endpoint 里的用户名不对（现在用的：{_creds().get('endpoint','')}）\n"
                f"  2) 密码不是登录密码（51CTO 用登录密码即可，不是令牌）\n"
                f"  3) 用户名/密码错了：去 https://www.51cto.com 登录验证一下")
        except PlatformError:
            raise          # 配置缺失这类自身错误，原样透传
        except Exception as e:
            raise PlatformError(f"连不上 MetaWeblog: {type(e).__name__} {e}")

    def whoami(self):
        """排查用：把 endpoint 和拿到的博客信息打出来（不含密码）。"""
        s, c = _client()
        blogs = s.blogger.getUsersBlogs("", c["username"], c["password"])
        return {"endpoint": c["endpoint"], "username": c["username"], "blogs": blogs}

    def _blogid(self, s, c):
        blogs = s.blogger.getUsersBlogs("", c["username"], c["password"])
        if not blogs:
            raise PlatformError("拿不到 blogid，检查 MetaWeblog 凭据")
        return blogs[0]["blogid"]

    # ---------------- 列表 ----------------

    def list_articles(self, page=None, limit=50):
        s, c = _client()
        bid = self._blogid(s, c)
        posts = s.metaWeblog.getRecentPosts(bid, c["username"], c["password"], limit)
        out = []
        for p in posts:
            pid = str(p.get("postid", ""))
            out.append({
                "post_id": pid,
                "title": p.get("title", ""),
                "url": p.get("link", ""),
                "edit_url": f"https://blog.51cto.com/my/post/edit/{pid}",
                "status": "published",
                "stats": {},
            })
        return out

    # ---------------- 发布 ----------------

    def publish(self, page, article, options=None):
        options = options or {}
        s, c = _client()
        bid = self._blogid(s, c)
        tags = [t.strip() for t in (article.get("tags") or "").split(",") if t.strip()]
        # 51CTO 的 xmlrpc 按 HTML 渲染，Markdown 原文会变成一行纯文本。
        # 优先用文章自带的 html，没有就做一个最小转换（标题/段落/代码块/换行）
        html = (article.get("content_html") or _md_to_html(article.get("content_md", "")))
        struct = {
            "title": article["title"],
            "description": html,
            "categories": tags,
            "dateCreated": xmlrpc.client.DateTime(datetime.now()),
        }
        publish_flag = not options.get("draft_only")
        pid = s.metaWeblog.newPost(bid, c["username"], c["password"], struct, publish_flag)
        pid = str(pid)
        return {"post_id": pid,
                "post_url": f"https://blog.51cto.com/{_creds()['username']}/p/{pid}.html",
                "edit_url": f"https://blog.51cto.com/my/post/edit/{pid}",
                "draft_only": bool(options.get("draft_only"))}

    # ---------------- 原地更新 ----------------

    def update(self, page, pub, article):
        s, c = _client()
        pid = pub.get("post_id")
        if not pid:
            raise PlatformError("没有 post_id，更新不了")
        tags = [t.strip() for t in (article.get("tags") or "").split(",") if t.strip()]
        html = (article.get("content_html") or _md_to_html(article.get("content_md", "")))
        struct = {
            "title": article["title"],
            "description": html,
            "categories": tags,
            "dateCreated": xmlrpc.client.DateTime(datetime.now()),
        }
        # MetaWeblog 的 editPost 是全额覆盖，天然就是"原地更新"
        ok = s.metaWeblog.editPost(pid, c["username"], c["password"], struct, True)
        if not ok:
            raise PlatformError(f"editPost 返回 {ok}")
        return True


def _md_to_html(md):
    """最小 Markdown→HTML 转换：够 51CTO 渲染用，不引第三方依赖。

    覆盖：标题(#)、代码块(```)、无序列表(-)、行内代码(`)、空行分段。
    """
    if not md:
        return ""
    lines = md.split("\n")
    out, in_code, in_list = [], False, False
    for line in lines:
        s = line.rstrip()
        if s.startswith("```"):
            out.append("<pre><code>" if not in_code else "</code></pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(s.replace("&", "&amp;").replace("<", "&lt;"))
            continue
        if s.startswith("# "):
            out.append(f"<h2>{s[2:]}</h2>")
        elif s.startswith("## "):
            out.append(f"<h3>{s[3:]}</h3>")
        elif s.startswith("### "):
            out.append(f"<h4>{s[4:]}</h4>")
        elif s.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{s[2:]}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            if s.strip():
                seg = s.replace("`", "<code>", 1)
                if "`" in seg:
                    seg = seg.replace("`", "</code>", 1)
                out.append(f"<p>{seg}</p>")
    if in_code:
        out.append("</code></pre>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)
