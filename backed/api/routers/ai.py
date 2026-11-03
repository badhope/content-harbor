# -*- coding: utf-8 -*-
"""AI 写稿 / Provider 管理 / AIGC 合规门禁端点。"""

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..deps import get_hub

router = APIRouter()


class AIWriteIn(BaseModel):
    topic: str
    style: str = ""
    words: int = 2000
    tags_hint: str = ""
    publish_to: Optional[List[str]] = None
    model: str = ""
    preferred_provider: str = ""


class AIModelIn(BaseModel):
    model: str = ""
    preferred_provider: str = ""


class AIRewriteIn(BaseModel):
    instruction: str
    publish_to: Optional[List[str]] = None
    model: str = ""
    preferred_provider: str = ""


class AIWriteTemplateIn(BaseModel):
    topic: str
    template: str = ""
    style: str = ""
    words: int = 2000
    tags_hint: str = ""


# ---- AI 写稿 ----

@router.post("/ai/write")
def ai_write(body: AIWriteIn):
    return get_hub().ai_write(body.topic, body.style, body.words,
                        body.tags_hint, body.publish_to,
                        model=body.model or None,
                        preferred_provider=body.preferred_provider or None)


@router.post("/ai/write-template")
def ai_write_template(body: AIWriteTemplateIn):
    """用预置模板 AI 写文章。"""
    art = get_hub().ai_write_with_template(
        body.topic, body.template, style=body.style, words=body.words, tags_hint=body.tags_hint)
    aid = get_hub().create(art["title"], art["content_md"],
                           summary=art["summary"], tags=art["tags"],
                           source="ai", ai_model=art.get("ai_model", ""))
    return {"id": aid, "title": art["title"], "summary": art["summary"]}


@router.get("/ai/status")
def ai_status():
    ready = get_hub().ai_ready()
    providers = []
    try:
        from service.publishing import ai_providers as prov_mod
        providers = prov_mod.list_providers()
    except Exception:
        pass
    return {"ready": ready, "providers": providers}


@router.get("/ai/providers")
def ai_providers():
    """列出所有已配置的 AI Provider + 模型（掩码，不含明文密钥）。

    注意：这是 AI 模型供应商（DeepSeek/Kimi/GLM/...）；
    平台侧"外部工具"（biliup/wenyan 等）在 /ai/tool-providers。
    """
    from service.publishing import ai_providers as prov_mod
    return prov_mod.list_providers()


@router.get("/ai/tool-providers")
def ai_tool_providers():
    """平台外部工具 Provider（biliup-rs / wenyan-cli 等）安装与登录状态。"""
    from service.publishing import providers as prov_mod
    return prov_mod.list_available_providers()


@router.get("/ai/templates")
def ai_templates():
    """列出所有可用写作模板。"""
    return get_hub().list_ai_templates()


@router.get("/ai/gate")
def ai_gate():
    """AIGC 合规门禁状态：开关、审查模型、高危词数、直发开关、最近拒绝。"""
    from service.publishing import gate as g
    snap = {"enabled": g.is_enabled(),
            "review_model": g._cfg("REVIEW_MODEL", "").strip() or "(未配置，走本地 heuristic)",
            "dangerous_terms": len(g.DANGEROUS_PATTERNS),
            "ai_direct_publish": g.is_ai_direct_publish(),
            "human_review_gate": "AI 源内容默认须人工确认（勾选「我已核验」）或开启 AI 稿直发后才能正式上线"}
    try:
        from service.publishing import db
        rows = db.list_failed_jobs(get_hub().conn, type_="publish", limit=50)
        denied = [dict(r) for r in rows if any(
            k in (r["message"] or "") for k in ("门禁", "高危", "人工确认", "合规"))]
        snap["recent_denied"] = denied[:10]
    except Exception:
        snap["recent_denied"] = []
    return snap


# ---- AI 配置（密钥 / 模型 / 路由 / 门禁开关）----

class AIConfigPatchIn(BaseModel):
    providers: Optional[Dict[str, Dict]] = None
    review_model: Optional[str] = None
    ai_direct_publish: Optional[bool] = None


class AITestIn(BaseModel):
    provider: str
    model: str = ""
    base_url: str = ""
    api_key: str = ""


@router.get("/ai/config")
def ai_config_get():
    """AI 配置全貌（密钥只回掩码，永不回显完整 key）。"""
    from service.publishing import ai_config
    return ai_config.get_ai_config()


@router.put("/ai/config")
def ai_config_put(body: AIConfigPatchIn):
    """白名单写回：providers.<key> / REVIEW_MODEL / AI_DIRECT_PUBLISH。

    api_key 语义：不传=不改；空串=清除；有值=设置。
    其余 config.json 键（HARBOR_API_TOKEN 等）原样保留。
    """
    from service.publishing import ai_config
    try:
        return ai_config.update_ai_config(body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/ai/config/test")
def ai_config_test(body: AITestIn):
    """真实最小调用测连通（ping→pong）。允许对未保存字段先测。"""
    from service.publishing import ai_config
    return ai_config.test_provider(body.provider, body.model,
                                   body.base_url, body.api_key)
