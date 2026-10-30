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
from datetime import datetime
from pathlib import Path

import requests

from service.publishing.adapters.base import PlatformAdapter, PlatformError, register

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "config.json"
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


def _file_path(article) -> str:
    c = _creds()
    name = f"{datetime.now().strftime('%Y-%m-%d')}-{_slug(article.get('title'))}.md"
    return f"{c['path_prefix']}/{name}" if c["path_prefix"] else name


def _front_matter(article) -> str:
    meta = {
        "title": article.get("title", ""),
        "date": datetime.now().isoformat(timespec="seconds"),
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
    now = datetime.now()
    slug = _slug(article.get("title"))
    return f"https://{c['site_base']}/{now.year}/{now.month:02d}/{now.day:02d}/{slug}.html"


@register
class GitHubPagesAdapter(PlatformAdapter):
    id = "github"
    name = "GitHub Pages"
    needs_browser = False

    home_url = "https://github.com/"
    login_url = "https://github.com/settings/tokens"

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
