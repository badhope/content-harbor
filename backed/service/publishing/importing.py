# -*- coding: utf-8 -*-
"""外部内容导入：URL 抓取 + Markdown front-matter 解析。

对标 Wechatsync / Artipub 后的两个明确缺口：

1. **只能导入本地 .md 文件**。用户的存量内容大量在别人家的网页上（公众号、
   掘金、个人博客），要迁进来只能手工复制。这里用 trafilatura（正文提取的
   事实标准库，Mozilla Readability 的 Python 等价物之一）把网页转成 Markdown。
2. **front-matter 解析是手写字符串切割**。`text.split("---", 2)` 遇到正文里
   出现 `---` 分隔线就切错，也不支持 YAML 列表/嵌套/多行。改用
   python-frontmatter（PyYAML 底座）后 `tags: [a, b]`、`title: "带: 冒号"`
   这类写法都能正确解析。
"""

import re
from pathlib import Path
from urllib.parse import urlparse

import frontmatter

from service.publishing.db import data_root
from service.publishing.url_safety import validate_timeout, validate_url

# 正文少于这个长度视为"没抓到内容"（登录墙/JS 渲染页/404 页）
MIN_CONTENT_CHARS = 120
MAX_MARKDOWN_BYTES = 5 * 1024 * 1024
_ALLOWED_SUFFIXES = frozenset((".md", ".markdown"))
_SENSITIVE_NAMES = frozenset(("config", "config.json", ".env", ".gitignore"))
_SENSITIVE_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".log")

# 常见站点里 trafilatura 抓不到时用来兜底的正文容器选择器
# （只用于提示，不在这里做 HTML 解析——抓不到就如实报错）
_JS_HEAVY_HOSTS = ("mp.weixin.qq.com", "zhuanlan.zhihu.com", "juejin.cn",
                   "www.toutiao.com", "blog.csdn.net")


class ImportError_(ValueError):
    """导入失败（与内置 ImportError 区分）。"""


def parse_markdown(text: str) -> tuple[dict, str]:
    """解析 front-matter，返回 (meta dict, 正文)。解析失败按无 meta 处理。

    旧实现手写 `split("---", 2)`，正文里出现水平分隔线 `---` 就会把后半段
    当正文起点吃掉。python-frontmatter 只在文件开头识别，语义正确。
    """
    try:
        post = frontmatter.loads(text)
    except Exception:
        return {}, text
    meta = dict(post.metadata or {})
    body = post.content or ""
    return meta, body


def _normalize_tags(value) -> str:
    if not value:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip().strip("[]").replace("'", "").replace('"', "")


def _default_import_root() -> Path:
    """生产导入根：与应用数据隔离，避免把仓库配置当内容读入。"""
    root = data_root() / "imports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _is_safe_markdown_name(target: Path, root: Path) -> bool:
    relative_parts = target.relative_to(root).parts
    if any(part.startswith(".") for part in relative_parts):
        return False
    name = target.name.lower()
    stem = target.stem.lower()
    if target.suffix.lower() not in _ALLOWED_SUFFIXES:
        return False
    if (name in _SENSITIVE_NAMES or stem == "config" or name.startswith(".env")
            or stem.endswith(_SENSITIVE_SUFFIXES)):
        return False
    return True


def read_markdown_file(path: Path, allow_root: Path = None) -> tuple[dict, str]:
    """读取指定安全根内的 Markdown，保留 resolve 后的 symlink 越权防护。"""
    try:
        root = (allow_root if allow_root is not None else _default_import_root())
        root = Path(root).expanduser().resolve()
        target = Path(path).expanduser().resolve()
    except Exception as exc:  # aqg: top-level boundary（路径解析必须 fail-closed）
        raise ImportError_(f"非法导入路径: {path}") from exc
    if target == root or root not in target.parents:
        raise ImportError_(f"导入路径越权（仅允许安全导入根内）: {target}")
    if not _is_safe_markdown_name(target, root):
        raise ImportError_(f"不允许导入该文件类型或敏感路径: {target.name}")
    if not target.is_file():
        raise ImportError_(f"导入文件不存在: {target}")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise ImportError_(f"无法读取导入文件: {target}") from exc
    if size > MAX_MARKDOWN_BYTES:
        raise ImportError_(f"Markdown 文件超过 {MAX_MARKDOWN_BYTES // 1024 // 1024}MB 上限")
    text = target.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_markdown(text)
    meta["title"] = meta.get("title") or target.stem
    meta["tags"] = _normalize_tags(meta.get("tags"))
    return meta, body.lstrip("\n")


def _extract_from_html(html: str, url: str) -> tuple[dict, str]:
    """HTML → (meta, Markdown)。fetch_url 与浏览器通道共用。

    抽不到有效正文时抛 ImportError_（含下一步建议）。
    """
    import trafilatura

    parsed = urlparse(url)
    body = trafilatura.extract(
        html, output_format="markdown", include_links=True,
        include_images=True, include_tables=True, favor_recall=True) or ""
    if len(body.strip()) < MIN_CONTENT_CHARS:
        host = (parsed.hostname or "").lower()
        hint = ("该站正文由 JS 渲染或需要登录，建议用浏览器打开后复制正文"
                if host in _JS_HEAVY_HOSTS or not body.strip()
                else "正文过短，可能抓到了导航/提示页")
        raise ImportError_(f"未能提取到有效正文（{len(body.strip())} 字符）：{hint}")

    meta = trafilatura.extract_metadata(html)
    title = (getattr(meta, "title", "") or "").strip()
    author = (getattr(meta, "author", "") or "").strip()
    sitename = (getattr(meta, "sitename", "") or "").strip()
    date = (getattr(meta, "date", "") or "").strip()
    summary = (getattr(meta, "description", "") or "").strip()
    tags = _normalize_tags(getattr(meta, "tags", "") or "")

    return {
        "title": title or (parsed.hostname or "导入文章"),
        "summary": summary[:300],
        "tags": tags,
        "origin_url": url,
        "author": author,
        "sitename": sitename,
        "date": date,
    }, _clean_markdown(body)


MAX_REDIRECTS = 5
MAX_HTML_BYTES = 10 * 1024 * 1024


def _fetch_public_html(url: str, timeout: float) -> tuple[str, str]:
    """抓取公网网页：连接固定在已校验 IP 上，规避 DNS 重绑定。"""
    from service.publishing.url_safety import URLSafetyError, fetch_pinned

    try:
        data, final_url = fetch_pinned(
            url, timeout=timeout, max_bytes=MAX_HTML_BYTES,
            max_redirects=MAX_REDIRECTS,
            headers={"User-Agent": "HarbourImporter/1.0"})
    except URLSafetyError as exc:
        raise ImportError_(str(exc)) from exc
    return data.decode("utf-8", errors="replace"), final_url


def fetch_url(url: str, timeout: int = 30) -> tuple[dict, str]:
    """抓取公网网页并转成 Markdown。返回 (meta, 正文)。"""
    try:
        bounded_timeout = validate_timeout(timeout, default=30, name="timeout")
        validate_url(url)
    except ValueError as exc:
        raise ImportError_(str(exc)) from exc
    try:
        downloaded, final_url = _fetch_public_html(url, bounded_timeout)
    except ImportError_:
        raise
    except Exception as e:
        raise ImportError_(f"抓取失败: {type(e).__name__}: {e}") from e
    if not downloaded:
        raise ImportError_(f"页面抓取为空（可能需要登录或禁止抓取）: {final_url}")
    return _extract_from_html(downloaded, final_url)


def fetch_url_browser(url: str, platform: str, hub, account="default",
                      timeout=60) -> tuple[dict, str]:
    """带平台登录态的浏览器通道导入——登录墙后的内容 trafilatura 拿不到。

    走 browser_ops.browser_fetch（复用该平台 profile 登录态），HTML 交给
    与 HTTP 通道同一套抽取后处理。
    """
    from service.publishing import browser_ops

    try:
        r = browser_ops.browser_fetch(hub, platform, url, account=account,
                                      timeout=timeout, want_html=True)
    except ValueError as e:
        raise ImportError_(str(e))
    except Exception as e:
        raise ImportError_(f"浏览器抓取失败: {type(e).__name__}: {e}")
    html = r.get("html") or ""
    if not html.strip():
        raise ImportError_(f"浏览器未取到页面内容: {r.get('url') or url}")
    return _extract_from_html(html, r.get("url") or url)


def _clean_markdown(text: str) -> str:
    """去掉转换后残留的空链接、连续空行与图片占位。"""
    text = re.sub(r"\[\s*\]\([^)]*\)", "", text)          # 空锚文本链接
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
