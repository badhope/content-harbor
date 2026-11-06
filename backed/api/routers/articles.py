# -*- coding: utf-8 -*-
"""文章端点：CRUD + 搜索 + 导入 + 版本历史 + AI 增强工具 + 质检。"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..deps import get_hub

router = APIRouter()


# ---- 入参模型 ----

class ArticleIn(BaseModel):
    title: str
    content_md: str = ""
    summary: str = ""
    tags: str = ""
    cover: str = ""
    status: str = "draft"
    source: str = "human"
    ai_model: str = ""


class ArticlePatch(BaseModel):
    title: Optional[str] = None
    content_md: Optional[str] = None
    summary: Optional[str] = None
    tags: Optional[str] = None
    cover: Optional[str] = None
    status: Optional[str] = None
    # 发布设置：封面已有 cover；AI 辅助标识与来源此前不能编辑，导致
    # 「发布前把设置点满」在编辑器里做不到（写库后下次打开又丢）。
    ai_model: Optional[str] = None
    source: Optional[str] = None


class ImportIn(BaseModel):
    path: str = ""
    url: str = ""
    tags: str = ""
    browser: bool = False      # True 走内置浏览器通道（带平台登录态）
    platform: str = ""         # browser=True 时必填（哪个平台的登录态）


class AIModelIn(BaseModel):
    model: str = ""           # "provider:model_name" 或 "model_name"
    preferred_provider: str = ""


class AIRewriteIn(BaseModel):
    instruction: str
    publish_to: Optional[list] = None
    model: str = ""
    preferred_provider: str = ""


# ---- 文章 CRUD ----

@router.get("/articles")
def list_articles(status: str = None, limit: int = 100, offset: int = 0,
                  slim: bool = True):
    """文章列表。默认只返回列表元信息（不含正文），避免整页正文 JSON 化。

    需要正文时传 `slim=false`（MCP/CLI 的 list 语义保持原样）。
    """
    # 夹到安全区间：limit<=0 在 SQLite 里等于不限量，超大 offset 会溢出 INTEGER
    limit = max(1, min(limit, 200))
    offset = max(0, min(offset, 1_000_000))
    return get_hub().list(status, limit, offset, slim=slim)


@router.get("/articles/page")
def articles_page(status: str = None, limit: int = 20, offset: int = 0):
    """分页视图：返回 {items,total,has_more}，供前端「加载更多」。

    必须声明在 /articles/{aid} 之前，否则 "page" 会被当成 aid 匹配。
    """
    limit = max(1, min(limit, 200))
    offset = max(0, min(offset, 1_000_000))
    hub = get_hub()
    items = hub.list(status, limit, offset, slim=True)
    total = hub.count_articles(status)
    return {"items": items, "total": total,
            "offset": offset, "limit": limit,
            "has_more": offset + len(items) < total}


def _emit_article_event(event_type: str, aid: int, **extra):
    """发一条文章事件到 SSE/Webhook。失败不影响主流程（事件是增值能力）。"""
    try:
        from service.publishing import events as evt_mod
        evt_mod.emit(event_type, {"article_id": aid, **extra})
    except Exception:
        pass


@router.post("/articles")
def create_article(a: ArticleIn):
    aid = get_hub().create(**a.model_dump())
    _emit_article_event("article.created", aid, title=a.title)
    return {"id": aid}


@router.get("/articles/{aid}")
def get_article(aid: int):
    r = get_hub().get(aid)
    if not r:
        raise HTTPException(404, "文章不存在")
    return r


@router.put("/articles/{aid}")
def update_article(aid: int, patch: ArticlePatch):
    data = {k: v for k, v in patch.model_dump().items() if v is not None}
    ok, pending = get_hub().edit(aid, **data)
    if ok:
        # 带 pending_sync 一起走事件：多端同时开着时，"哪里还没同步"能立刻更新，
        # 而不是等下一次轮询。正文变更会让已发布实例变成待同步，这是用户最关心的状态。
        _emit_article_event("article.updated", aid, pending_sync=pending,
                            fields=sorted(data.keys()))
    return {"ok": ok, "pending_sync": pending}


@router.delete("/articles/{aid}")
def delete_article(aid: int):
    """删除本地文章记录。平台侧已发布的副本不会被动到。"""
    result = get_hub().delete(aid)
    if result is None:
        raise HTTPException(404, "文章不存在")
    _emit_article_event("article.deleted", aid, **result)
    return {"ok": True, **result}



@router.get("/articles/search/{keyword}")
def search(keyword: str):
    return get_hub().search(keyword)


@router.post("/articles/import")
def import_md(body: ImportIn):
    """导入文章：`path` 读本地 Markdown，`url` 抓网页转 Markdown（二选一）。

    url 通道默认 trafilatura 纯 HTTP；`browser: true` 改走内置浏览器
    （带 `platform` 的登录态），登录墙后的内容也能导。
    先试本地路径，再试 URL；两者都给时以 path 为准（本地导入更可控）。
    """
    hub = get_hub()
    if body.path:
        try:
            return {"id": hub.import_md(body.path, tags=body.tags), "source": "file"}
        except ValueError as e:
            raise HTTPException(422, str(e))
    if body.url:
        if body.browser:
            if not body.platform:
                raise HTTPException(422, "browser 导入必须指定 platform（用哪个平台的登录态）")
            from .accounts import _check_platform
            _check_platform(body.platform)
            from service.publishing import importing
            from service.publishing import db
            try:
                meta, content = importing.fetch_url_browser(body.url,
                                                            body.platform, hub)
            except importing.ImportError_ as e:
                raise HTTPException(422, str(e))
            aid = db.create_article(hub.conn, meta["title"], content,
                                    tags=body.tags or meta.get("tags", ""),
                                    summary=meta.get("summary", ""),
                                    source="import", origin_url=body.url)
            return {"id": aid, "title": meta["title"], "chars": len(content),
                    "sitename": meta.get("sitename", ""),
                    "author": meta.get("author", ""), "source": "browser"}
        try:
            return {**hub.import_url(body.url, tags=body.tags), "source": "url"}
        except ValueError as e:
            raise HTTPException(422, str(e))
    raise HTTPException(422, "path 与 url 至少要填一个")


@router.post("/articles/{aid}/clone")
def article_clone(aid: int):
    return get_hub().clone(aid)


@router.get("/articles/{aid}/export")
def article_export(aid: int, download: bool = False):
    """导出为带 YAML front-matter 的 Markdown（Hexo/Hugo/Obsidian 可直接用）。

    为什么要有：在此之前 Harbor 是"内容只进不出"——导进来的文章改完就锁在
    SQLite 里，想迁走只能手工复制。这与"自托管、数据属于自己"的定位冲突。
    """
    from fastapi.responses import PlainTextResponse

    art = get_hub().get(aid)
    if not art:
        raise HTTPException(404, "文章不存在")
    text = build_markdown_export(art)
    headers = {}
    if download:
        safe = _safe_filename(art.get("title") or f"article-{aid}")
        headers["Content-Disposition"] = f'attachment; filename="{safe}.md"'
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                             headers=headers)


def _safe_filename(title: str) -> str:
    """标题转文件名：去掉路径分隔与保留字符，避免 Windows 上写盘失败。"""
    import re as _re
    cleaned = _re.sub(r'[\\/:*?"<>|\r\n\t]+', "-", title).strip(" .-")
    return (cleaned or "article")[:80]


def build_markdown_export(art: dict) -> str:
    """把文章记录渲染成 front-matter + 正文。导出格式稳定，便于脚本消费。"""
    import datetime as _dt

    def _yaml_scalar(value):
        text = "" if value is None else str(value)
        if text == "" or any(c in text for c in ':#[]{}&*!|>\'"%@`') or text != text.strip():
            escaped = text.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        return text

    tags = [t.strip() for t in str(art.get("tags") or "").split(",") if t.strip()]
    lines = ["---"]
    lines.append(f"title: {_yaml_scalar(art.get('title'))}")
    if art.get("summary"):
        lines.append(f"description: {_yaml_scalar(art.get('summary'))}")
    if tags:
        lines.append("tags:")
        lines.extend(f"  - {_yaml_scalar(t)}" for t in tags)
    lines.append(f"status: {_yaml_scalar(art.get('status'))}")
    if art.get("cover"):
        lines.append(f"cover: {_yaml_scalar(art.get('cover'))}")
    if art.get("origin_url"):
        lines.append(f"source: {_yaml_scalar(art.get('origin_url'))}")
    lines.append(f"harbor_id: {art.get('id')}")
    for key in ("created_at", "updated_at"):
        if art.get(key):
            try:
                stamp = _dt.datetime.fromtimestamp(float(art[key])).isoformat(timespec="seconds")
            except (TypeError, ValueError, OSError):
                stamp = str(art[key])
            lines.append(f"{key}: {stamp}")
    lines.append("---")
    lines.append("")
    lines.append(art.get("content_md") or "")
    return "\n".join(lines)


# ---- 版本历史 ----

@router.get("/articles/{aid}/versions")
def versions_list(aid: int, limit: int = 50):
    from service.publishing.versions import list_versions
    return list_versions(get_hub().conn, aid, limit=limit)


# /diff 必须放在 /{vid} 之前，否则 "diff" 会被当作 vid 路径参数匹配
@router.get("/articles/{aid}/versions/diff")
def versions_diff(aid: int, v1: int, v2: int):
    from service.publishing.versions import diff_versions
    return diff_versions(get_hub().conn, v1, v2)


@router.get("/articles/{aid}/versions/{vid}")
def versions_get(aid: int, vid: int):
    from service.publishing.versions import get_version
    v = get_version(get_hub().conn, vid)
    if not v:
        raise HTTPException(404, "版本不存在")
    return v


@router.post("/articles/{aid}/versions/{vid}/rollback")
def versions_rollback(aid: int, vid: int):
    from service.publishing.versions import rollback
    ok, msg = rollback(get_hub().conn, vid)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg}


# ---- AI 增强 ----

@router.post("/articles/{aid}/ai-rewrite")
def ai_rewrite(aid: int, body: AIRewriteIn):
    return get_hub().ai_rewrite(aid, body.instruction, body.publish_to,
                               model=body.model or None,
                               preferred_provider=body.preferred_provider or None)


@router.post("/articles/{aid}/ai-polish")
def ai_polish(aid: int, body: AIModelIn = None):
    model = body.model if body else None
    preferred = body.preferred_provider if body else None
    return get_hub().ai_polish(aid, model=model, preferred_provider=preferred)


@router.post("/articles/{aid}/ai-translate")
def ai_translate(aid: int, body: AIModelIn = None, target_lang: str = "en"):
    model = body.model if body else None
    preferred = body.preferred_provider if body else None
    return get_hub().ai_translate(aid, target_lang, model=model,
                                  preferred_provider=preferred)


@router.get("/articles/{aid}/ai-image-prompts")
def ai_image_prompts(aid: int, n: int = 3, model: str = None,
                     preferred_provider: str = None):
    return get_hub().ai_image_prompts(aid, n, model=model,
                                       preferred_provider=preferred_provider)


@router.get("/articles/{aid}/ai-outline")
def ai_outline(aid: int):
    return get_hub().ai_outline(aid)


@router.get("/articles/{aid}/ai-seo")
def ai_seo(aid: int):
    return get_hub().ai_seo(aid)


# ---- 质检 ----

@router.post("/articles/{aid}/qa")
def article_qa(aid: int):
    return get_hub().content_qa(article_id=aid)


class QAAnalyzeIn(BaseModel):
    content_md: str = ""
    title: str = ""
    summary: str = ""
    tags: str = ""


@router.post("/qa/analyze")
def qa_analyze(request: Request, body: QAAnalyzeIn | None = None):
    """正文质检。JSON body 与 query 参数两种调用方式都支持。

    回归：前端 `api.qaAnalyze` 一直发 JSON body，而端点原先只声明了 query
    参数（FastAPI 对非 Body 的 str 一律按 query 解析），请求体被忽略后
    `content_md` 缺失 → 422，编辑器里的「质检」按钮从来没成功过。
    """
    merged = QAAnalyzeIn()
    if body is not None:
        merged = body
    qp = request.query_params
    data = merged.model_dump()
    for k in data:
        if not data[k] and qp.get(k):
            data[k] = qp[k]
    if not data["content_md"]:
        raise HTTPException(422, "content_md 不能为空")
    return get_hub().content_qa(content_md=data["content_md"], title=data["title"],
                                summary=data["summary"], tags=data["tags"])
