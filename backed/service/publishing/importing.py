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

# 正文少于这个长度视为"没抓到内容"（登录墙/JS 渲染页/404 页）
MIN_CONTENT_CHARS = 120

# 常见站点里 trafilatura 抓不到时用来兜底的正文容器选择器
# （只用于提示，不在这里做 HTML 解析——抓不到就如实报错）
_JS_HEAVY_HOSTS = ("mp.weixin.qq.com", "zhuanlan.zhihu.com", "juejin.cn",
                   "www.toutiao.com", "blog.csdn.net")


class ImportError_(Exception):
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


def read_markdown_file(path: Path, allow_root: Path = None) -> tuple[dict, str]:
    """读本地 Markdown（含 front-matter），带路径越权防护。"""
    try:
        target = path.expanduser().resolve()
    except Exception:
        raise ImportError_(f"非法导入路径: {path}")
    root = (allow_root or Path(__file__).resolve().parents[2]).resolve()
    if target != root and root not in target.parents:
        raise ImportError_(f"导入路径越权（仅允许项目目录内）: {target}")
    if not target.is_file():
        raise ImportError_(f"导入文件不存在: {target}")
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


def fetch_url(url: str, timeout: int = 30) -> tuple[dict, str]:
    """抓取网页并转成 Markdown。返回 (meta, 正文)。

    安全边界与 media 模块一致：只允许 http/https，解析后必须是公网 IP，
    防止把导入接口当 SSRF 跳板读内网/云元数据。
    """
    import trafilatura
    from service.publishing import media

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ImportError_(f"仅支持 http/https 链接: {url[:100]}")
    media._validate_url(url)          # 复用公网 IP 校验（含 DNS 解析）

    try:
        downloaded = trafilatura.fetch_url(url)
    except Exception as e:
        raise ImportError_(f"抓取失败: {type(e).__name__}: {e}")
    if not downloaded:
        raise ImportError_(f"页面抓取为空（可能需要登录或禁止抓取）: {url}")

    return _extract_from_html(downloaded, url)


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
