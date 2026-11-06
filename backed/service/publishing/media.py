# -*- coding: utf-8 -*-
"""图片资产管线：本地化、去重、稳定 URL。

存在理由（对标 Wechatsync / Artipub 后的能力缺口）：

    这两个项目都把"逐平台转存图片"当作发布基线能力，而 Harbor 之前
    `md_to_html()` 直接把 Markdown 里的 `<img src>` 原样透传。结果是：
      - 外链图（图床防盗链、临时签名 URL）在平台上 403 / 裂图；
      - 同一张图在 10 个平台各拉一次，重复下载、重复占带宽；
      - 本地相对路径图片根本无法上传（浏览器侧拿不到文件）。

本模块做三件事，且都可以离线验证：

1. **入库**：把远程 URL 或本地文件读成字节，按 sha256 内容寻址存到
   `data/media/<sha256[:2]>/<sha256>.<ext>`，同一张图只有一份。
2. **改写**：把 Markdown 里的图片引用替换成 `PUBLIC_BASE_URL + /media/<sha>`，
   让平台侧拿到一个稳定、可缓存、无签名的地址。
3. **兜底**：下载失败（防盗链/超时/超大/内网地址）时保留原 URL 并记 warning，
   绝不因为一张图让整篇文章发不出去。

安全边界（这张表里全是"用户可控制的输入"）：
- 只允许 http/https；
- 解析后 IP 必须公网（拒绝回环/私网/链路本地/云元数据），防 SSRF；
- 单图上限 15MB、仅接受 image/* 且扩展名白名单；
- 重定向最多 3 跳（requests 默认会跟随无限重定向到内网）。
"""

import hashlib
import ipaddress
import mimetypes
import os
import re
import socket
import threading
import time
import urllib.parse
from pathlib import Path

MAX_BYTES = 15 * 1024 * 1024
TIMEOUT = 15
MAX_REDIRECTS = 3
# 像素上限：解码炸弹防护。一个 30000×30000 的 PNG 压缩后只有几 MB，
# 解码却要 3.6GB 内存；只在文件大小上限之外再卡一道像素数。
MAX_PIXELS = 40_000_000
# 允许的图片格式（Pillow format 名）。刻意排除 SVG：SVG 是 XML，
# 可携带 <script>/外链实体，作为"图片"直接托管等于给 XSS 开后门。
ALLOWED_FORMATS = {"PNG", "JPEG", "GIF", "WEBP", "BMP", "AVIF", "TIFF"}

_EXT_BY_MIME = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
    "image/webp": ".webp", "image/bmp": ".bmp", "image/svg+xml": ".svg",
    "image/avif": ".avif",
}
_ALLOWED_EXT = set(_EXT_BY_MIME.values())

# Markdown 图片：![alt](url "title")；同时覆盖 HTML <img src="...">
_MD_IMG = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
_HTML_IMG = re.compile(r"<img\b[^>]*?\bsrc=[\"']([^\"']+)[\"']", re.I)

MEDIA_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS media_assets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256      TEXT NOT NULL UNIQUE,
    ext         TEXT NOT NULL DEFAULT '.bin',
    mime        TEXT NOT NULL DEFAULT '',
    bytes       INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT '',   -- 原始 URL 或 本地路径
    created_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_media_created ON media_assets(created_at DESC);
"""

_init_lock = threading.Lock()


def media_root() -> Path:
    """媒体根目录：与 hub.db 同目录（桌面版在 APPDATA，容器在挂载卷）。

    刻意跟着 `db.DB_PATH` 而不是 data_root()：媒体是数据库的附属资产，
    两者必须同生命周期；测试里 conftest 只 monkeypatch DB_PATH，跟着它
    才不会被写进仓库。
    """
    from service.publishing import db
    d = Path(db.DB_PATH).parent / "media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_schema(conn) -> None:
    """幂等建表。用 sqlite_master 探针而不是进程内缓存：连接可能被回收后
    复用 id，缓存会误判"已建表"。"""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='media_assets'"
    ).fetchone()
    if row:
        return
    with _init_lock:
        conn.executescript(MEDIA_TABLE_DDL)
        conn.commit()


# 运行期自动探测到的对外基地址（由请求中间件写入）。
# 为什么需要：发布动作发生在后台任务里，没有 request 上下文；如果只认
# 环境变量，用户必须手工配 PUBLIC_BASE_URL 才能让图片被平台抓取——配错了
# 或忘了配就静默退化成"只入库不改写"，这是上一轮的遗留坑。
_runtime_base = ""
_runtime_base_lock = threading.Lock()


def set_runtime_base(base: str) -> None:
    """记录最近一次请求的对外地址（由 app 中间件调用）。

    只接受 http/https 且不带路径，避免把奇怪的 Host 头写进正文。
    """
    global _runtime_base
    b = (base or "").strip().rstrip("/")
    if not b:
        return
    if not (b.startswith("http://") or b.startswith("https://")):
        return
    if len(b) > 200:
        return
    with _runtime_base_lock:
        _runtime_base = b


def clear_runtime_base() -> None:
    global _runtime_base
    with _runtime_base_lock:
        _runtime_base = ""


def public_base() -> str:
    """媒体对外基地址。优先级：env > config.json > 最近请求自动探测 > 空。

    空串表示"不知道平台能否访问"，改写阶段会跳过（保留原 URL）——
    把图片改成本机 127.0.0.1 地址只会让平台侧更糟。
    """
    env = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if env:
        return env.rstrip("/")
    try:
        import json
        from service.publishing.gate import CONFIG_FILE
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            configured = str(data.get("PUBLIC_BASE_URL") or "").strip()
            if configured:
                return configured.rstrip("/")
    except Exception:
        pass
    with _runtime_base_lock:
        return _runtime_base


def base_source() -> str:
    """当前基地址的来源，给 /media/stats 显示（排查"为什么没改写"用）。"""
    if os.environ.get("PUBLIC_BASE_URL", "").strip():
        return "env:PUBLIC_BASE_URL"
    try:
        import json
        from service.publishing.gate import CONFIG_FILE
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if str(data.get("PUBLIC_BASE_URL") or "").strip():
                return "config.json"
    except Exception:
        pass
    with _runtime_base_lock:
        return "auto-detected" if _runtime_base else "none"


# ---------------------------------------------------------------- 安全下载

def _is_public_host(host: str) -> bool:
    """域名解析后所有地址都必须是公网 IP。解析失败一律拒绝。"""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _validate_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"仅支持 http/https 图片地址: {url[:80]}")
    host = parsed.hostname or ""
    if not _is_public_host(host):
        raise ValueError(f"图片地址指向内网/无法解析的主机，已拒绝: {host}")
    return url


class _SafeRedirects:
    """手动处理重定向：每一跳都重新做公网校验，防止 302 到 127.0.0.1。"""

    def __init__(self):
        import requests
        self._s = requests.Session()
        self._s.max_redirects = MAX_REDIRECTS

    def get(self, url: str, **kw):
        current = _validate_url(url)
        for _ in range(MAX_REDIRECTS + 1):
            r = self._s.get(current, allow_redirects=False, timeout=TIMEOUT, **kw)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                current = _validate_url(urllib.parse.urljoin(current, r.headers["location"]))
                continue
            return r
        raise ValueError("图片地址重定向次数过多")


def fetch_bytes(url: str) -> tuple[bytes, str]:
    """下载图片，返回 (bytes, mime)。带大小上限与流式截断。"""
    s = _SafeRedirects()
    r = s.get(url, stream=True, headers={"User-Agent": "HarborMedia/1.0"})
    if r.status_code != 200:
        raise ValueError(f"HTTP {r.status_code}")
    mime = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if mime and not mime.startswith("image/") and mime != "application/octet-stream":
        raise ValueError(f"不是图片内容: {mime}")
    buf = bytearray()
    for chunk in r.iter_content(64 * 1024):
        if not chunk:
            continue
        buf.extend(chunk)
        if len(buf) > MAX_BYTES:
            raise ValueError(f"图片超过 {MAX_BYTES // 1024 // 1024}MB 上限")
    if not buf:
        raise ValueError("空响应")
    data = bytes(buf)
    # 下载路径同样按内容校验：Content-Type 由对端决定，可以随便写
    info = sniff_image(data)
    return data, info["mime"]


# ---------------------------------------------------------------- 内容校验

def sniff_image(data: bytes) -> dict:
    """用内容而不是文件扩展名判断"这是不是图片"，并给出真实格式与尺寸。

    为什么必须做：`UploadFile.content_type` 与文件名都由客户端提供，
    把 `evil.html` 改名成 `a.png` 就能绕过扩展名白名单。上一轮只校验了
    扩展名 + 客户端 MIME，等于没校验。

    返回 {"format","mime","width","height"}；不是合法图片抛 ValueError。
    """
    import io

    from PIL import Image, UnidentifiedImageError

    if not data:
        raise ValueError("空文件")
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "").upper()
            width, height = im.size
            # verify() 会检查结构完整性（截断/坏块），但调用后对象不可再用
            im.verify()
    except UnidentifiedImageError:
        raise ValueError("不是可识别的图片格式")
    except Exception as e:
        raise ValueError(f"图片损坏或无法解析: {type(e).__name__}")

    if fmt not in ALLOWED_FORMATS:
        raise ValueError(f"不支持的图片格式: {fmt or '未知'}")
    if width <= 0 or height <= 0:
        raise ValueError("图片尺寸非法")
    if width * height > MAX_PIXELS:
        raise ValueError(
            f"图片像素过大（{width}×{height} > 上限 {MAX_PIXELS // 1_000_000}M 像素）")
    mime = _MIME_BY_FORMAT.get(fmt, "application/octet-stream")
    return {"format": fmt, "mime": mime, "width": width, "height": height}


_MIME_BY_FORMAT = {
    "PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif",
    "WEBP": "image/webp", "BMP": "image/bmp", "AVIF": "image/avif",
    "TIFF": "image/tiff",
}


# ---------------------------------------------------------------- 入库

def _guess_ext(mime: str, url: str) -> str:
    if mime in _EXT_BY_MIME:
        return _EXT_BY_MIME[mime]
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix in _ALLOWED_EXT:
        return suffix
    guessed = mimetypes.guess_extension(mime or "") or ""
    return guessed if guessed in _ALLOWED_EXT else ".bin"


def store_bytes(conn, data: bytes, mime: str = "", source: str = "") -> dict:
    """按内容寻址入库（同图只存一份），返回 asset 行。

    入库前用魔数嗅探确认"真的是图片"，并把 mime/扩展名换成真实值——
    客户端给的 content_type 与文件名都不可信。
    """
    info = sniff_image(data)
    mime = info["mime"]
    ensure_schema(conn)
    sha = hashlib.sha256(data).hexdigest()
    row = conn.execute("SELECT * FROM media_assets WHERE sha256=?", (sha,)).fetchone()
    if row:
        return {**dict(row), "deduped": True}
    ext = _EXT_BY_MIME.get(info["mime"]) or _guess_ext(mime, source)
    folder = media_root() / sha[:2]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sha}{ext}").write_bytes(data)
    conn.execute(
        "INSERT OR IGNORE INTO media_assets (sha256, ext, mime, bytes, source, created_at)"
        " VALUES (?,?,?,?,?,?)", (sha, ext, mime, len(data), source[:500], time.time()))
    conn.commit()
    row = conn.execute("SELECT * FROM media_assets WHERE sha256=?", (sha,)).fetchone()
    return {**dict(row), "deduped": False}


def asset_path(asset: dict) -> Path:
    return media_root() / asset["sha256"][:2] / f"{asset['sha256']}{asset['ext']}"


def asset_url(asset: dict, base: str = "") -> str:
    """资产地址。

    配了 PUBLIC_BASE_URL → 绝对地址（平台侧可抓取）；
    没配 → 返回同源相对路径（编辑器预览/本机 Web 直接可用）。
    发布时的远端改写只在配了绝对地址时才做（见 rewrite_markdown 的 base 判断），
    因为平台抓不到用户的 127.0.0.1。
    """
    base = base or public_base()
    suffix = f"/media/{asset['sha256']}{asset['ext']}"
    return f"{base}{suffix}" if base else f"/api/v1/hub{suffix}"


def ingest_url(conn, url: str) -> dict:
    data, mime = fetch_bytes(url)
    return store_bytes(conn, data, mime, url)


def ingest_path(conn, path: str, allow_root: Path = None) -> dict:
    """本地文件入库。默认只允许仓库内路径（与 import_md 同一越权防护）。"""
    p = Path(path).expanduser()
    try:
        target = p.resolve()
    except Exception:
        raise ValueError(f"非法图片路径: {path}")
    root = (allow_root or Path(__file__).resolve().parents[3]).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"图片路径越权（仅允许项目目录内）: {target}")
    if not target.is_file():
        raise ValueError(f"图片不存在: {target}")
    if target.suffix.lower() not in _ALLOWED_EXT:
        raise ValueError(f"不支持的文件类型: {target.suffix}")
    data = target.read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError("图片超过大小上限")
    mime = mimetypes.guess_type(str(target))[0] or ""
    return store_bytes(conn, data, mime, str(target))


# ---------------------------------------------------------------- Markdown 改写

def rewrite_markdown(conn, md: str, base: str = "", on_error=None,
                     allow_root: Path = None) -> tuple[str, dict]:
    """把 Markdown/HTML 里的图片引用本地化并改写成稳定 URL。

    返回 (新 markdown, 统计)。下载失败的图片保留原样并计入 stats["failed"]。
    base 为空时不做改写（只统计），因为平台拿不到可访问地址，改成本地相对路径
    反而更糟。
    """
    ensure_schema(conn)
    base = base or public_base()
    stats = {"total": 0, "localized": 0, "failed": 0, "skipped": 0}
    cache: dict[str, str] = {}

    def _resolve(src: str) -> str:
        src = (src or "").strip()
        # Windows 反斜杠路径统一成 / 再判断；Markdown 里写 ![](C:\a\b.png)
        # 是合法写法，旧正则只认 /，导致本地图永远"跳过"。
        literal = src.replace("\\", "/")
        if not src or src.startswith(("data:", "#", "mailto:")):
            stats["skipped"] += 1
            return src
        if src in cache:
            cached = cache[src]
            # 同一张图在正文里出现多次：只有第一次算"入库"，但每次引用都
            # 确实被改写成了稳定 URL，统计口径按"引用次数"算更符合直觉。
            if cached != src:
                stats["localized"] += 1
            else:
                stats["skipped"] += 1
            return cached
        try:
            if re.match(r"^https?://", literal, re.I):
                asset = ingest_url(conn, literal)
            elif literal.startswith("file://"):
                asset = ingest_path(conn, literal[7:], allow_root=allow_root)
            elif re.match(r"^[a-zA-Z]:/|^/|^\.\.?/", literal):
                asset = ingest_path(conn, literal, allow_root=allow_root)
            else:
                stats["skipped"] += 1       # 站内相对路径，交给平台自己解析
                return src
        except Exception as e:
            stats["failed"] += 1
            if on_error:
                on_error(src, e)
            cache[src] = src
            return src
        url = asset_url(asset, base)
        cache[src] = url or src
        if url:
            stats["localized"] += 1
        return cache[src]

    def _md_sub(m):
        stats["total"] += 1
        alt, src = m.group(1), m.group(2)
        return f"![{alt}]({_resolve(src)})"

    def _html_sub(m):
        stats["total"] += 1
        return m.group(0).replace(m.group(1), _resolve(m.group(1)))

    out = _MD_IMG.sub(_md_sub, md or "")
    out = _HTML_IMG.sub(_html_sub, out)
    return out, stats


def list_assets(conn, limit: int = 100, offset: int = 0) -> list:
    ensure_schema(conn)
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    rows = conn.execute(
        "SELECT * FROM media_assets ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset)).fetchall()
    base = public_base()
    return [{**dict(r), "url": asset_url(dict(r), base)} for r in rows]


def stats(conn) -> dict:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(bytes),0) b FROM media_assets").fetchone()
    return {"count": row["c"], "bytes": row["b"], "root": str(media_root()),
            "public_base": public_base() or "(未配置 PUBLIC_BASE_URL，图片不会改写)"}


def find_orphans(conn):
    """找出没有被任何文章引用的媒体资产。

    为什么要它：这个模块只有"入库"没有"出库"。删掉一篇文章后，它的配图
    仍永久占用磁盘，用户既看不到（列表里没入口）也删不掉——实测媒体目录
    只增不减。这里做只读扫描，删除动作交给 ``purge_orphans`` 显式触发。
    """
    ensure_schema(conn)
    rows = conn.execute("SELECT * FROM media_assets ORDER BY created_at").fetchall()
    if not rows:
        return []

    # 一次性把全部正文捞出来，避免每个资产查一次库（N+1）
    articles = conn.execute(
        "SELECT content_md, cover FROM articles").fetchall()
    haystack = "\n".join(
        f"{a['content_md'] or ''}\n{a['cover'] or ''}" for a in articles)

    orphans = []
    for row in rows:
        sha = row["sha256"]
        # 只认完整 hash 出现，避免 sha 前缀误判（内容寻址 URL 里是完整 64 位）
        if sha in haystack:
            continue
        orphans.append(dict(row))
    return orphans


def purge_orphans(conn, dry_run=False):
    """删除未引用的媒体资产（数据库行 + 磁盘文件）。

    ``dry_run=True`` 只统计不删。返回 ``{count, bytes, deleted, errors, dry_run}``。
    """
    ensure_schema(conn)
    orphans = find_orphans(conn)
    total_bytes = sum(int(o.get("bytes") or 0) for o in orphans)
    result = {"count": len(orphans), "bytes": total_bytes,
              "deleted": 0, "errors": 0, "dry_run": bool(dry_run)}
    if dry_run or not orphans:
        return result

    for asset in orphans:
        try:
            path = asset_path(asset)
            if path.is_file():
                path.unlink()
            conn.execute("DELETE FROM media_assets WHERE sha256=?", (asset["sha256"],))
            result["deleted"] += 1
        except OSError:
            result["errors"] += 1
    conn.commit()
    return result
