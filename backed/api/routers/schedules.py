# -*- coding: utf-8 -*-
"""定时发布调度器端点。"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..deps import get_hub

router = APIRouter()


class ScheduleIn(BaseModel):
    article_id: int
    platforms: List[str]
    account: str = "default"
    draft_only: bool = False
    schedule_type: str = "once"        # once/daily/weekly/cron
    schedule_expr: str = ""
    title: str = ""


class SchedulePatch(BaseModel):
    title: Optional[str] = None
    platforms: Optional[List[str]] = None
    account: Optional[str] = None
    draft_only: Optional[bool] = None
    schedule_type: Optional[str] = None
    schedule_expr: Optional[str] = None
    enabled: Optional[bool] = None


@router.get("/schedules")
def schedules_list(include_disabled: bool = False):
    return get_hub().scheduler.list_all(include_disabled=include_disabled)


@router.post("/schedules")
def schedules_create(body: ScheduleIn):
    sid = get_hub().scheduler.create(
        body.article_id, body.platforms, body.account, body.draft_only,
        body.schedule_type, body.schedule_expr, body.title)
    return {"id": sid, **get_hub().scheduler.get(sid)}


@router.get("/schedules/{sid}")
def schedules_get(sid: int):
    s = get_hub().scheduler.get(sid)
    if not s:
        raise HTTPException(404, "定时任务不存在")
    return s


@router.put("/schedules/{sid}")
def schedules_update(sid: int, body: SchedulePatch):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    ok = get_hub().scheduler.update(sid, **data)
    if not ok:
        raise HTTPException(404, "定时任务不存在")
    return {"ok": True, **get_hub().scheduler.get(sid)}


@router.post("/schedules/{sid}/pause")
def schedules_pause(sid: int):
    if not get_hub().scheduler.pause(sid):
        raise HTTPException(404, "定时任务不存在")
    return {"ok": True, "id": sid, "enabled": False}


@router.post("/schedules/{sid}/resume")
def schedules_resume(sid: int):
    if not get_hub().scheduler.resume(sid):
        raise HTTPException(404, "定时任务不存在")
    return {"ok": True, "id": sid, "enabled": True}


@router.post("/schedules/{sid}/trigger")
def schedules_trigger(sid: int):
    """手动立即投递一次，不改调度计划。"""
    try:
        result = get_hub().scheduler.trigger_now(sid)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "定时任务不存在")
    return result


@router.delete("/schedules/{sid}")
def schedules_delete(sid: int):
    if not get_hub().scheduler.delete(sid):
        raise HTTPException(404, "定时任务不存在")
    return {"ok": True, "id": sid}
