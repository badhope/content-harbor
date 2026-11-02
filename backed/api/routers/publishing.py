# -*- coding: utf-8 -*-
"""发布 / 更新 / 同步端点 + 统一任务查询。"""

import json
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..deps import get_hub

router = APIRouter()


class PublishIn(BaseModel):
    platforms: List[str]
    account: str = "default"
    draft_only: bool = False
    human_confirmed: bool = False   # AI 稿「我已核验」显式确认，允许正式发布（留痕 ext）
    mode: str = "auto"              # auto 全自动 / assist 中断改配置 / manual 全手动
    settings: Optional[Dict[str, Dict]] = None


class UpdateIn(BaseModel):
    platforms: Optional[List[str]] = None
    account: str = "default"


class ResumeIn(BaseModel):
    approved: bool = True
    note: str = ""


# ---- 发布 / 更新 / 同步 ----

@router.post("/articles/{aid}/publish")
def publish(aid: int, body: PublishIn):
    """异步发布：提交到统一任务引擎，立即返回 task_id。

    draft_only=False 为真实上线（AI 源文章需 human_confirmed=True 或开启
    AI_DIRECT_PUBLISH，否则门禁 422 拒绝）。

    预检（同步、确定性）：任务引擎是异步的，若把门禁拒绝全留给任务，
    用户要到任务跑完才知道被拒——高危内容 / AI 未确认这两种确定性拒绝
    在提交前就地 422（完整门禁含 LLM 二审仍在任务里跑，作兜底）。
    """
    from service.publishing import gate as aigc_gate

    hub = get_hub()
    art = hub.get(aid)
    if not art:
        raise HTTPException(404, f"文章 {aid} 不存在")
    if body.mode not in ("auto", "assist", "manual"):
        raise HTTPException(422, f"未知发布模式: {body.mode}（可选 auto/assist/manual）")
    # 中断/手动模式：用户在平台上亲自确认，无需勾选核验；auto 模式仍走人审闸门
    if not body.draft_only and aigc_gate.is_enabled() and body.mode == "auto":
        hits = aigc_gate.scan_dangerous(art.get("content_md", ""), art.get("title", ""))
        if hits:
            raise HTTPException(422, f"合规门禁拒绝发布: 高危内容 {hits}")
        if aigc_gate.is_ai_sourced(art) and not body.human_confirmed \
                and not aigc_gate.is_ai_direct_publish():
            raise HTTPException(
                422, "合规门禁拒绝发布: AI 生成内容全自动发布需人工确认"
                     "（勾选「我已核验」，或改用「中断改配置」亲自在平台上发布）")
    elif not body.draft_only and aigc_gate.is_enabled():
        # 中断/手动模式仍做高危扫描（确定性安全项），人审由用户亲自操作承接
        hits = aigc_gate.scan_dangerous(art.get("content_md", ""), art.get("title", ""))
        if hits:
            raise HTTPException(422, f"合规门禁拒绝发布: 高危内容 {hits}")

    task_id = hub.tasks.submit("publish", article_id=aid, platforms=body.platforms,
                               account=body.account, draft_only=body.draft_only,
                               human_confirmed=body.human_confirmed,
                               mode=body.mode,
                               settings=body.settings)
    return {"task_id": task_id, "status": "pending", "poll": f"/tasks/{task_id}"}


@router.post("/articles/{aid}/update")
def update(aid: int, body: UpdateIn):
    """异步原地更新：提交任务，轮询 GET /tasks/{task_id}。"""
    task_id = get_hub().tasks.submit("update", article_id=aid, platforms=body.platforms,
                               account=body.account)
    return {"task_id": task_id, "status": "pending", "poll": f"/tasks/{task_id}"}


@router.post("/sync/pending")
def sync_pending(body: Optional[UpdateIn] = None):
    """异步同步扇出：把所有待更新实例提交一个 sync 任务。"""
    account = body.account if body else "default"
    task_id = get_hub().tasks.submit("sync", account=account)
    return {"task_id": task_id, "status": "pending", "poll": f"/tasks/{task_id}"}


# ---- 统一任务查询 / 恢复 ----

def _task_row(row: dict, parse_result: bool = False) -> dict:
    """把 DB 原始行转成前端契约：platforms 必须是数组。

    `tasks.platforms` 在库里是 JSON 字符串。列表端点原先直接返回原始行，
    前端 `(row.platforms || []).join()` 拿到字符串就抛 TypeError——
    实测任务表「对象」列空白、后续列整体错位。详情端点早就解析了，
    列表端点漏了，这里统一收口。
    """
    out = dict(row)
    try:
        out["platforms"] = json.loads(out.get("platforms") or "[]")
    except Exception:   # aqg: top-level boundary（脏数据不应让整表 500）
        out["platforms"] = []
    if parse_result:
        try:
            out["result"] = json.loads(out.get("result") or "{}")
        except Exception:   # aqg: top-level boundary（同上）
            out["result"] = {}
    return out


@router.get("/tasks")
def tasks_list(limit: int = 50, kind: str = None, status: str = None):
    """任务列表：按 kind/status 过滤，默认返回最近 50 条。"""
    limit = max(1, min(limit, 200))
    rows = get_hub().tasks.list(limit=limit, kind=kind, status=status)
    return [_task_row(r) for r in rows]


@router.get("/tasks/page")
def tasks_page(limit: int = 50, offset: int = 0, kind: str = None, status: str = None):
    """分页视图：返回 {items,total,has_more}。保持 /tasks 的原样返回不变。"""
    limit = max(1, min(limit, 200))
    offset = max(0, min(offset, 1_000_000))
    tm = get_hub().tasks
    items = [_task_row(r) for r in tm.list(limit=limit, kind=kind, status=status,
                                           offset=offset)]
    total = tm.count(kind=kind, status=status)
    return {"items": items, "total": total, "offset": offset, "limit": limit,
            "has_more": offset + len(items) < total}


@router.get("/tasks/{task_id}")
def tasks_detail(task_id: str):
    """查询任意任务状态：pending / running / ok / failed / waiting_human。"""
    t = get_hub().tasks.get(task_id)
    if not t:
        raise HTTPException(404, "任务不存在或已过期")
    out = _task_row(t, parse_result=True)
    # 任务详情要能直连到文章：前端从任务跳到编辑器只靠 article_id
    if out.get("article_id"):
        art = get_hub().get(out["article_id"])
        out["article_title"] = (art or {}).get("title", "")
    return out


@router.post("/tasks/{task_id}/resume")
def tasks_resume(task_id: str, body: Optional[ResumeIn] = None):
    """恢复 waiting_human 任务：approved=true 重新执行；false 标记放弃。"""
    approved = body.approved if body else True
    r = get_hub().tasks.resume(task_id, approved=approved)
    if not r.get("ok"):
        raise HTTPException(409, r.get("error", "恢复失败"))
    return r
