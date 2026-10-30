# -*- coding: utf-8 -*-
"""微信公众号适配器（官方草稿箱 API，纯协议免浏览器）。

中文创作者最大的流量池，且是**官方 API**（不需要浏览器逆向）：
  1. GET  /cgi-bin/token?grant_type=client_credential&appid=&secret=  → access_token
  2. POST /cgi-bin/draft/add?access_token=   → 草稿箱（content 是 HTML）
  3. POST /cgi-bin/freepublish/submit?access_token=   → 发布（认证服务号）

为什么默认只进草稿箱：
  - freepublish 需要认证服务号权限，个人订阅号普遍没有；
  - 草稿箱在公众号后台可见、可手动点发布——正好贴合本项目"人工兜底"
    的一贯语义（draft_only=True 或 pending_human）。

凭据放 config.json（或环境变量 WECHAT_APPID / WECHAT_SECRET 优先）：
    "platforms": { "wechat": { "appid": "wx...", "secret": "..." } }

图片防盗链：微信正文里 <img src> 必须是微信素材库地址，外链图不显示。
所以发布前把正文图片经本项目的 media 库上传到微信素材接口（有 appid 即可），
换回 wx 链接再入库。上传失败的单图保留原地址并降级（不让一张图阻断发布）。

注意事项（来自官方文档与实测）：
  - access_token 有效期 7200s，每日获取次数有限 → 本地缓存 + 失败重取一次；
  - token 接口需要把服务器 IP 加白名单，否则 errcode 40164（错误信息会带 IP）；
  - content 支持的 HTML 标签/样式是白名单子集，复杂样式会被过滤（不影响发布）。
"""

import json
import os
import re
import time
from pathlib import Path

import requests

from service.publishing.adapters.base import PlatformAdapter, PlatformError, register

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "config.json"
API = "https://api.weixin.qq.com"
TIMEOUT = 30

_TOKEN_CACHE = {"key": None, "token": "", "expire": 0.0}
_TOKEN_LOCK = __import__("threading").Lock()

# 微信正文允许保留的标签（其余剔除，保留内部文本）。刻意保守：宁少勿炸。
_ALLOWED_TAG_NAMES = ("p", "br", "strong", "b", "em", "i", "u", "s", "h1", "h2", "h3",
                      "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "code", "pre",
                      "table", "thead", "tbody", "tr", "th", "td", "section", "span",
                      "img", "figure", "figcaption", "hr", "a")
_ALLOWED_TAG_RE = re.compile(
    r"</?(?:" + "|".join(_ALLOWED_TAG_NAMES) + r")\b[^>]*>", re.I)


def _creds():
    appid = os.environ.get("WECHAT_APPID", "").strip()
    secret = os.environ.get("WECHAT_SECRET", "").strip()
    if not (appid and secret) and CONFIG_FILE.exists():
        try:
            c = ((json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                  .get("platforms") or {}).get("wechat") or {})
            appid = appid or str(c.get("appid") or "").strip()
            secret = secret or str(c.get("secret") or "").strip()
        except Exception:
            pass
    if not (appid and secret):
        raise PlatformError(
            "wechat 需要 appid + secret（设 WECHAT_APPID/WECHAT_SECRET 或填 config.json）。"
            "公众号后台 → 设置与开发 → 基本配置 里获取；IP 白名单要包含本机出口 IP。")
    return {"appid": appid, "secret": secret}


def _err(resp_json: dict, action: str) -> PlatformError:
    code = resp_json.get("errcode")
    msg = resp_json.get("errmsg", "")
    hints = {
        40164: "IP 不在公众号白名单：后台 → 基本配置 → IP 白名单 加入本机出口 IP",
        40013: "appid 无效",
        40125: "secret 无效（或已被重置）",
        48001: "该账号没有此 API 权限（个人订阅号没有草稿箱/发布权限）",
        45009: "接口调用次数超限（今日配额用完）",
    }
    hint = hints.get(code, "")
    return PlatformError(f"微信 {action} 失败 errcode={code}: {msg}" + (f"（{hint}）" if hint else ""))


def _get_token(force: bool = False) -> str:
    """access_token 本地缓存（提前 5 分钟过期），41001 时上层会 force 重取一次。"""
    c = _creds()
    key = f"{c['appid']}:{c['secret'][:6]}"
    with _TOKEN_LOCK:
        if not force and _TOKEN_CACHE["key"] == key and _TOKEN_CACHE["expire"] > time.time() + 300:
            return _TOKEN_CACHE["token"]
        r = requests.get(f"{API}/cgi-bin/token", timeout=TIMEOUT, params={
            "grant_type": "client_credential", "appid": c["appid"], "secret": c["secret"]})
        data = r.json() if r.status_code == 200 else {}
        if data.get("access_token"):
            _TOKEN_CACHE.update({"key": key, "token": data["access_token"],
                                 "expire": time.time() + int(data.get("expires_in", 7200))})
            return _TOKEN_CACHE["token"]
        raise _err(data, "获取 access_token")


def _post(action_path: str, payload: dict, action: str) -> dict:
    """带一次 40001/41001（token 失效）重试的 POST。"""
    for force in (False, True):
        token = _get_token(force=force)
        r = requests.post(f"{API}{action_path}", timeout=TIMEOUT,
                          params={"access_token": token}, json=payload)
        data = r.json() if r.status_code == 200 else {}
        if data.get("errcode") in (40001, 41001, 42001) and not force:
            continue            # token 失效：强制刷新重试一次
        if data.get("errcode"):
            raise _err(data, action)
        return data
    raise PlatformError(f"微信 {action} 失败：token 重试后仍无效")


def _sanitize_html(html: str) -> str:
    """剔除非白名单标签（保留内部文本），微信会拒绝含未知标签的草稿。

    两步：先整块删 script/style，再把其余非白名单标签剥壳留文本。
    """
    text = re.sub(r"<(script|style)\b[^>]*>[\s\S]*?</\1>", "", html or "", flags=re.I)
    out, pos = [], 0
    for m in re.finditer(r"<[a-zA-Z/][^>]*>", text):
        if _ALLOWED_TAG_RE.fullmatch(m.group(0)):
            continue
        out.append(text[pos:m.start()])
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


@register
class WeChatAdapter(PlatformAdapter):
    id = "wechat"
    name = "微信公众号"
    needs_browser = False

    home_url = "https://mp.weixin.qq.com/"
    login_url = "https://mp.weixin.qq.com/"

    def check_auth(self, page=None) -> bool:
        try:
            _get_token()
            return True
        except PlatformError:
            return False

    def _upload_image(self, src: str) -> str:
        """把一张图片传进微信永久素材库，返回微信侧 URL。失败返回原地址。"""
        from service.publishing import media
        try:
            if re.match(r"^https?://", src, re.I):
                data, mime = media.fetch_bytes(src)
            else:
                data = Path(src).read_bytes()
                mime = "image/png"
        except Exception:
            return src
        try:
            token = _get_token()
            r = requests.post(f"{API}/cgi-bin/material/add_material",
                              params={"access_token": token, "type": "image"},
                              files={"media": ("img.png", data, mime)},
                              timeout=TIMEOUT)
            data = r.json() if r.status_code == 200 else {}
            url = data.get("url")
            if url:
                return url
        except Exception:
            pass
        return src

    def _prepare_content(self, article) -> str:
        """Markdown → HTML，并把图片换成微信素材地址。"""
        from service.publishing.adapters.base import md_to_html
        html = md_to_html(article.get("content_md", ""))
        # 逐图替换：media.fetch_bytes 已带 SSRF 防护
        for m in list(re.finditer(r'<img[^>]*?src="([^"]+)"', html, re.I)):
            src = m.group(1)
            if "mmbiz.qpic.cn" in src:
                continue
            wx = self._upload_image(src)
            if wx != src:
                html = html.replace(src, wx)
        return _sanitize_html(html)

    def publish(self, page, article, options=None):
        options = options or {}
        content = self._prepare_content(article)
        payload = {"articles": [{
            "title": (article.get("title") or "未命名")[:64],
            "author": "",
            "digest": (article.get("summary") or article.get("title") or "")[:120],
            "content": content,
            "need_open_comment": 1,
            "only_fans_comment": 0,
        }]}
        data = _post("/cgi-bin/draft/add", payload, "存草稿")
        media_id = data.get("media_id", "")

        # 有认证服务号权限且未要求草稿时，尝试群发；个人订阅号 48001 → 保持草稿
        if not options.get("draft_only"):
            try:
                _post("/cgi-bin/freepublish/submit", {"media_id": media_id}, "发布")
            except PlatformError as e:
                if "48001" in str(e):
                    return {"post_id": media_id, "post_url": "",
                            "edit_url": "https://mp.weixin.qq.com/",
                            "draft_only": True,
                            "warning": "账号无发布权限（个人订阅号），已存草稿箱，去后台点发布"}
                raise
            return {"post_id": media_id, "post_url": "",
                    "edit_url": "https://mp.weixin.qq.com/",
                    "draft_only": False}
        return {"post_id": media_id, "post_url": "",
                "edit_url": "https://mp.weixin.qq.com/",
                "draft_only": True}

    def update(self, page, pub, article):
        """草稿箱官方 API 支持 delete + add；原地更新语义 = 删旧草稿再存新版。

        新草稿 media_id 会随 publish 落库（publish_single 的 upsert 会更新
        post_id），这里不需要拿返回值。
        """
        old = (pub or {}).get("post_id") or ""
        if old:
            try:
                _post("/cgi-bin/draft/delete", {"media_id": old}, "删旧草稿")
            except PlatformError as e:
                if "40007" not in str(e):        # 40007=media_id 不存在，可忽略
                    raise
        self.publish(page, article, options={"draft_only": True})
        return True

    def list_articles(self, page=None, limit=50):
        """草稿箱列表（官方 /draft/batchget）。"""
        token = _get_token()
        resp = requests.post(f"{API}/cgi-bin/draft/batchget", timeout=TIMEOUT,
                             params={"access_token": token},
                             json={"offset": 0, "count": min(limit, 20), "no_content": 1})
        data = resp.json() if resp.status_code == 200 else {}
        if data.get("errcode"):
            raise _err(data, "草稿列表")
        out = []
        for item in (data.get("item") or []):
            media_id = item.get("media_id", "")
            articles = item.get("content", {}).get("news_item") or []
            title = (articles[0].get("title") if articles else "") or "(无标题)"
            out.append({"post_id": media_id, "title": title, "url": "",
                        "edit_url": "https://mp.weixin.qq.com/",
                        "status": "draft", "stats": {}})
        return out
