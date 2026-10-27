# -*- coding: utf-8 -*-
"""文章端点：CRUD + 搜索 + 导入 + 版本历史 + AI 增强工具 + 质检。"""

from typing import Optional

from fastapi import APIRouter, HTTPException
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


class ImportIn(BaseModel):
    path: str
    tags: str = ""


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
def list_articles(status: str = None, limit: int = 100, offset: int = 0):
    return get_hub().list(status, limit, offset)


@router.get("/articles/page")
def articles_page(status: str = None, limit: int = 20, offset: int = 0):
    """分页视图：返回 {items,total,has_more}，供前端「加载更多」。

    必须声明在 /articles/{aid} 之前，否则 "page" 会被当成 aid 匹配。
    """
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    hub = get_hub()
    items = hub.list(status, limit, offset)
    total = hub.count_articles(status)
    return {"items": items, "total": total,
            "offset": offset, "limit": limit,
            "has_more": offset + len(items) < total}


@router.post("/articles")
def create_article(a: ArticleIn):
    return {"id": get_hub().create(**a.model_dump())}


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
    return {"ok": ok, "pending_sync": pending}


@router.get("/articles/search/{keyword}")
def search(keyword: str):
    return get_hub().search(keyword)


@router.post("/articles/import")
def import_md(body: ImportIn):
    return {"id": get_hub().import_md(body.path, tags=body.tags)}


@router.post("/articles/{aid}/clone")
def article_clone(aid: int):
    return get_hub().clone(aid)


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


@router.post("/qa/analyze")
def qa_analyze(content_md: str, title: str = "", summary: str = "", tags: str = ""):
    return get_hub().content_qa(content_md=content_md, title=title,
                                summary=summary, tags=tags)
