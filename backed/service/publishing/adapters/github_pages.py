# -*- coding: utf-8 -*-
"""GitHub Pages / Jekyll 仓库适配器（官方 Contents API，纯协议免浏览器）。

面向技术创作者的"最后一公里"：把文章以 Markdown + front-matter 直接 commit
到自己的 GitHub 仓库（Jekyll/Hugo/Astro 源仓库），由 GitHub Pages/Actions
负责构建发布。这是**真正的原地更新**——同 path 再 PUT 一次就是一次 commit。

凭据放 config.json（或环境变量 GITHUB_TOKEN 优先）：
    "platforms": { "github": { "token": "ghp_xxx",
                                "owner": "用户名", "repo": "用户名.github.io",
                                "branch": "main",
                                "path_prefix": "_posts" } }

API 流程（docs.github.com Contents API，实测确认字段）：
  GET  /repos/{o}/{r}/contents/{path}?ref={branch}   → 存在则拿 sha
  PUT  /repos/{o}/{r}/contents/{path}                → {message, content(b64), sha?, branch}
  201=新建 / 200=更新；响应含 commit.html_url 与 content.html_url
限制：API 单文件 ≤1MB（文章场景绰绰有余）。
"""

import base64
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from service.publishing.db import config_file

import requests

from service.publishing.adapters.base import PlatformAdapter, PlatformError, register

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = config_file()
API = "https://api.github.com"
TIMEOUT = 30
MAX_BYTES = 900_000          # 官方上限 1MB，留余量给 base64 膨胀

_SLUG_RE = re.compile(r"[^\w\-]+")


def _creds():
    env = os.environ.get("GITHUB_TOKEN", "").strip()
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = (json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                   .get("platforms") or {}).get("github") or {}
        except Exception:
            cfg = {}
    token = env or str(cfg.get("token") or "").strip()
    owner = str(cfg.get("owner") or "").strip()
    repo = str(cfg.get("repo") or "").strip()
    if not token:
        raise PlatformError("github 需要 token（设 GITHUB_TOKEN 或 config.json platforms.github.token）。"
                            "去 GitHub Settings → Developer settings → PAT 生成，勾 repo 权限。")
    if not owner or not repo:
        raise PlatformError("config.json 里 platforms.github.owner / repo 没填（如 owner='你的用户名'，repo='你的用户名.github.io'）。")
    for k, v in (("owner", owner), ("repo", repo)):
        if any("\u4e00" <= ch <= "\u9fff" for ch in v):
            raise PlatformError(f"platforms.github.{k} 还是占位符。")
    return {"token": token, "owner": owner, "repo": repo,
            "branch": str(cfg.get("branch") or "main").strip() or "main",
            "path_prefix": str(cfg.get("path_prefix") or "_posts").strip("/"),
            "site_base": str(cfg.get("site_base") or "").strip("/")}


def _headers():
    return {"Authorization": f"Bearer {_creds()['token']}",
            "Accept": "application/vnd.github+json"}


def _slug(title: str) -> str:
    """标题转文件名安全 slug；中文保留（Jekyll/Hugo 都支持中文文件名）。"""
    s = _SLUG_RE.sub("-", (title or "post").strip().lower()).strip("-")
    return (s or "post")[:60]


def _utc_now() -> datetime:
    """发布用的时间一律用 UTC。

    真机事故（2026-11-07 首次真实发布验证）：本机在 UTC+8，`datetime.now()`
    写出 `date: 2026-11-07T13:43:39`，而 GitHub Pages 构建机在 UTC 05:43。
    Jekyll 默认 `future: false`，**未来日期的文章会被静默丢弃**——commit 成功、
    API 回读成功（文件确实在仓库里），但站点上 404。仓库层验证完全看不出来。
    统一用 UTC 才能让 front-matter 与本机时区无关。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _file_path(article) -> str:
    c = _creds()
    name = f"{_utc_now().strftime('%Y-%m-%d')}-{_slug(article.get('title'))}.md"
    return f"{c['path_prefix']}/{name}" if c["path_prefix"] else name


def _front_matter(article) -> str:
    meta = {
        "title": article.get("title", ""),
        # 带 +0000 显式声明时区：只写裸时间戳时 Jekyll 按站点时区解释，
        # 站点时区不是 UTC 时又会绕回"未来文章"问题。
        "date": _utc_now().strftime("%Y-%m-%d %H:%M:%S +0000"),
        "tags": [t.strip() for t in (article.get("tags") or "").split(",") if t.strip()],
    }
    if article.get("summary"):
        meta["description"] = article["summary"][:200]
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(v)}]")
        else:
            lines.append(f'{k}: "{str(v).replace(chr(34), chr(39))}"')
    lines.append("---")
    return "\n".join(lines)


def _site_url(article) -> str:
    """尽力拼出文章的站点 URL（Jekyll 默认 /:year/:month/:day/:slug）。"""
    c = _creds()
    if not c["site_base"]:
        return ""
    now = _utc_now()
    slug = _slug(article.get("title"))
    return f"https://{c['site_base']}/{now.year}/{now.month:02d}/{now.day:02d}/{slug}.html"


def _split_front_matter(text: str):
    """把 `---` 包裹的 YAML front-matter 与正文分开。没有 front-matter 时返回 ("", text)。"""
    t = text or ""
    if not t.startswith("---"):
        return "", t
    end = t.find("\n---", 3)
    if end < 0:
        return "", t
    head = t[3:end]
    body = t[end + 4:]
    return head, body.lstrip("\n")


def _front_title(text: str) -> str:
    """从 front-matter 里取 title；没有就把正文首个非空行当标题。"""
    head, body = _split_front_matter(text)
    for line in head.splitlines():
        line = line.strip()
        if line.lower().startswith("title:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line:
            return line
    return ""


def _strip_front_matter(text: str) -> str:
    return _split_front_matter(text)[1]


def _pages_build_state(owner: str, repo: str) -> dict:
    """查 GitHub Pages 最近一次构建的真实状态。

    为什么不能只靠探 URL：Pages 构建是**异步**的，刚 commit 完的几十秒里
    站点必然 404——那是"还在构建"，不是"发布失败"。盲探 URL 会把正常的
    异步等待误判成失败，逼用户去查一个并不存在的问题。
    `/pages/builds/latest` 直接给出 building / built / errored，比猜准。

    返回 {"known": bool, "status": str, "commit": str, "detail": str}；
    查不到（未启用 Pages、无权限、网络问题）时 known=False，调用方据此降级。
    """
    try:
        r = requests.get(f"{API}/repos/{owner}/{repo}/pages/builds/latest",
                         headers=_headers(), timeout=15)
        if r.status_code != 200:
            return {"known": False, "detail": f"查不到构建状态（HTTP {r.status_code}）"}
        d = r.json() or {}
        return {"known": True, "status": str(d.get("status") or ""),
                "commit": str(d.get("commit") or ""),
                "detail": f"Pages 构建状态：{d.get('status')}"}
    except Exception as e:
        return {"known": False, "detail": f"查构建状态失败：{type(e).__name__}"}


def _site_check(url: str, attempts: int = 3, wait: float = 5.0,
                owner: str = "", repo: str = "") -> dict:
    """站点层探活：把发布时算出的公开 URL 真访问一次。

    为什么必须做：2026-11-07 首次真机发布，仓库层全绿（commit 在、API 回读
    通过、list_articles 能看到），但 Pages 站点上 404——front-matter 写了本地
    时区的"未来日期"，Jekyll 静默丢弃。仓库层验证对这个缺陷**完全无感**。
    用户点开的链接才是最终交付物，所以这一层不能省。

    三种结果，宁可少判也不误判：
      - ok=True                 站点 200，确定可访问
      - ok=False + building=True 构建还没完成，属于"尚未可知"，调用方不该判失败
      - ok=False + building=False 构建明确失败（errored）或长期不可达
    """
    if not url:
        return {"checked": False, "detail": "没配置 site_base，无法探活站点"}
    last = ""
    for i in range(max(1, attempts)):
        try:
            r = requests.get(url, timeout=15, allow_redirects=True)
            last = f"HTTP {r.status_code}"
            if r.status_code == 200:
                return {"checked": True, "ok": True, "detail": f"站点可访问（{last}）"}
            if r.status_code not in (404, 000):
                return {"checked": True, "ok": False, "building": False,
                        "detail": f"站点返回 {last}（路径或配置不对）"}
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:80]}"
        if i < attempts - 1:
            import time as _t
            _t.sleep(wait)
    # 仍然不是 200：问平台到底在干什么，而不是自己猜
    build = _pages_build_state(owner, repo) if owner and repo else {"known": False}
    if build.get("known") and build.get("status") == "errored":
        return {"checked": True, "ok": False, "building": False,
                "detail": f"Pages 构建失败（{build.get('detail')}）。"
                          f"常见原因：front-matter 日期在未来被 Jekyll 丢弃、"
                          f"或站点需要 _config.yml / 依赖缺失"}
    if build.get("known") and build.get("status") in ("building", "queued", ""):
        return {"checked": True, "ok": False, "building": True,
                "detail": f"站点尚未就绪（{last}），{build.get('detail')}——"
                          f"构建是异步的，稍后复核即可"}
    return {"checked": True, "ok": False, "building": False,
            "detail": f"站点多次探测仍未就绪（最后：{last}）"}


@register
class GitHubPagesAdapter(PlatformAdapter):
    id = "github"
    name = "GitHub Pages"
    needs_browser = False
    readback_mode = "api"

    home_url = "https://github.com/"
    login_url = "https://github.com/settings/tokens"

    def read_back(self, pub: dict, article: dict = None) -> dict:
        """回读：GET contents/{path} 取回文件并解码，确认内容真的进了仓库。

        PUT 返回 200/201 只说明 GitHub 接下了这次 commit 请求；真正的证据是
        再 GET 一次拿回同一个路径，且解出来的正文里目标片段都在（Pages 构建
        是否成功另说，那属于站点侧，见 `site_check`）。
        """
        path = (pub or {}).get("post_id") or ""
        if not path:
            return {"supported": True, "found": False, "detail": "缺 post_id（文件路径），无法回读"}
        c = _creds()
        try:
            r = requests.get(f"{API}/repos/{c['owner']}/{c['repo']}/contents/{path}",
                             headers=_headers(), params={"ref": c["branch"]},
                             timeout=TIMEOUT)
        except Exception as e:
            return {"supported": True, "found": False,
                    "detail": f"回读请求失败：{type(e).__name__}: {str(e)[:120]}"}
        if r.status_code == 404:
            return {"supported": True, "found": False, "detail": f"仓库里找不到 {path}（commit 未落盘）"}
        if r.status_code != 200:
            return {"supported": True, "found": False,
                    "detail": f"回读失败 HTTP {r.status_code}: {r.text[:120]}"}
        data = r.json() or {}
        raw = data.get("content") or ""
        try:
            text = base64.b64decode(raw).decode("utf-8", "replace") if raw else ""
        except Exception:
            text = ""
        if not text:
            return {"supported": True, "found": False, "detail": "回读到的文件内容为空或无法解码"}
        # 站点层：仓库里有 ≠ 用户点开能看。2026-11-07 真机踩到过
        # commit 成功、API 回读通过、站点 404（未来日期被 Jekyll 丢弃）。
        #
        # URL 必须优先用**发布时算出来的那个**（pub.post_url）：_site_url()
        # 每次调用都用"现在"重算路径，跨过 UTC 零点再回读就会指向一个不存在的
        # 日期目录，必然 404。发布时那个才是真实的落地地址。
        site_url = ((pub or {}).get("post_url")
                    or (article or {}).get("_site_url")
                    or (_site_url(article) if article else ""))
        c2 = _creds()
        site = _site_check(site_url, owner=c2["owner"], repo=c2["repo"])
        return {"supported": True, "found": True,
                "title": _front_title(text),
                "body": _strip_front_matter(text),
                "url": data.get("html_url", ""),
                "sha": data.get("sha", ""),
                "site": site,
                "detail": ""}

    def verify_readback(self, pub: dict, article: dict) -> tuple:
        """GitHub Pages 的回读要比基类更严：仓库层 + 站点层都要过。

        基类只比标题与正文。这里再叠加站点探活——用户拿到的是公开链接，
        链接 404 就是没发布成功，无论仓库里文件多完整。
        """
        ok, ev = super().verify_readback(pub, article)
        if ok is not True:
            return ok, ev
        site = ev.get("site") or {}
        if not site.get("checked"):
            # 没配 site_base：仓库层已确认，站点层无法验证。标为通过但把
            # "未检查站点"写进 detail，避免让人以为站点也验过了。
            ev["detail"] = "回读通过（仓库层）；站点层未检查：" + site.get("detail", "")
            return True, ev
        if site.get("building"):
            # 构建还没完成 ≠ 失败。仓库层已确认内容在，站点只是还在异步构建。
            # 判通过但明确写"待复核"，让状态可追溯而不是假装已完成。
            ev["detail"] = "回读通过（仓库层）；站点构建中，稍后复核：" + site.get("detail", "")
            return True, ev
        if not site.get("ok"):
            ev["detail"] = "仓库层通过但站点不可访问：" + site.get("detail", "")
            return False, ev
        ev["detail"] = "回读通过：仓库文件与站点页面都确认存在"
        return True, ev

    def check_auth(self, page=None) -> bool:
        c = _creds()
        try:
            r = requests.get(f"{API}/repos/{c['owner']}/{c['repo']}",
                             headers=_headers(), timeout=TIMEOUT)
        except Exception:
            return False
        if r.status_code == 429 or r.status_code == 403 and "rate limit" in r.text.lower():
            raise PlatformError(f"HTTP {r.status_code}: rate limited (github check)")
        if r.status_code in (401, 403):
            return False
        if r.status_code == 404:
            raise PlatformError(f"仓库 {c['owner']}/{c['repo']} 不存在或 token 无权限")
        return r.status_code == 200

    def _get_sha(self, path: str):
        c = _creds()
        r = requests.get(f"{API}/repos/{c['owner']}/{c['repo']}/contents/{path}",
                         headers=_headers(), params={"ref": c["branch"]},
                         timeout=TIMEOUT)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise PlatformError(f"GET contents/{path} 失败({r.status_code}): {r.text[:160]}")
        return r.json().get("sha")

    def list_articles(self, page=None, limit=50):
        """列出仓库 content 目录下的 Markdown（按 commit 时间近先）。"""
        c = _creds()
        r = requests.get(f"{API}/repos/{c['owner']}/{c['repo']}/contents/{c['path_prefix']}",
                         headers=_headers(), params={"ref": c["branch"]},
                         timeout=TIMEOUT)
        if r.status_code == 404:
            return []
        if r.status_code != 200:
            raise PlatformError(f"GET contents/{c['path_prefix']} 失败({r.status_code})")
        out = []
        for item in (r.json() or []):
            if item.get("type") != "file" or not str(item.get("name", "")).endswith(".md"):
                continue
            name = item["name"][:-3]
            out.append({
                "post_id": item["path"],              # path 就是更新钥匙
                "title": name[11:] if len(name) > 11 and name[10] == "-" else name,
                "url": _site_url({"title": name}),
                "edit_url": f"https://github.com/{c['owner']}/{c['repo']}/edit/{c['branch']}/{item['path']}",
                "status": "published",
                "stats": {},
            })
        return out[:limit]

    def publish(self, page, article, options=None):
        options = options or {}
        content = f"{_front_matter(article)}\n\n{article.get('content_md', '')}\n"
        raw = content.encode("utf-8")
        if len(raw) > MAX_BYTES:
            raise PlatformError(f"文章超过 GitHub API 1MB 限制（当前 {len(raw) // 1024}KB）")
        path = _file_path(article)
        c = _creds()
        sha = self._get_sha(path)
        payload = {
            "message": f"publish: {article.get('title', 'untitled')}",
            "content": base64.b64encode(raw).decode("ascii"),
            "branch": c["branch"],
        }
        if sha:
            payload["sha"] = sha
        r = requests.put(f"{API}/repos/{c['owner']}/{c['repo']}/contents/{path}",
                         headers=_headers(), json=payload, timeout=TIMEOUT)
        if r.status_code in (409,):
            raise PlatformError(f"HTTP 409: commit 冲突（{path} 已被并行修改），重试即可")
        if r.status_code not in (200, 201):
            raise PlatformError(f"PUT contents/{path} 失败({r.status_code}): {r.text[:200]}")
        data = r.json()
        commit = data.get("commit") or {}
        return {
            "post_id": path,
            "post_url": _site_url(article) or (commit.get("html_url") or ""),
            "edit_url": f"https://github.com/{c['owner']}/{c['repo']}/edit/{c['branch']}/{path}",
            "draft_only": False,          # commit 即发布；draft_only 语义由文件名 date 未来扩展
            "commit_url": commit.get("html_url", ""),
        }

    def update(self, page, pub, article):
        """原地更新 = 同 path 再 PUT（内容寻址的 commit 语义天然支持）。"""
        path = pub.get("post_id") or ""
        if not path or "/" not in path:
            raise PlatformError("github 原地更新需要 post_id（文件路径）")
        c = _creds()
        sha = self._get_sha(path)
        if not sha:
            raise PlatformError(f"文件 {path} 已不存在，无法原地更新")
        content = f"{_front_matter(article)}\n\n{article.get('content_md', '')}\n"
        raw = content.encode("utf-8")
        if len(raw) > MAX_BYTES:
            raise PlatformError("文章超过 GitHub API 1MB 限制")
        r = requests.put(f"{API}/repos/{c['owner']}/{c['repo']}/contents/{path}",
                         headers=_headers(),
                         json={"message": f"update: {article.get('title', 'untitled')}",
                               "content": base64.b64encode(raw).decode("ascii"),
                               "sha": sha, "branch": c["branch"]},
                         timeout=TIMEOUT)
        if r.status_code != 200:
            raise PlatformError(f"PUT contents/{path} 失败({r.status_code}): {r.text[:200]}")
        return True
