# -*- coding: utf-8 -*-
"""系统端点：health / status / metrics / jobs / publications / pending-human。"""

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from service.publishing import observability as obs, db

from ..deps import get_hub

router = APIRouter()


@router.get("/health")
def health():
    """桌面版 Tauri sidecar 健康检查端点。"""
    return {"status": "ok", "service": "harbour"}


@router.get("/status")
def status():
    return get_hub().status()


@router.get("/metrics")
def metrics(format: str = Query("json", pattern="^(json|prometheus)$")):
    """实时指标。`format=json`（默认）给调试用结构化快照；
    `format=prometheus` 输出标准文本 exposition，可直接被 Prometheus 抓取。

    为什么两种都要：JSON 便于人读和前端图表，Prometheus 文本是监控生态的
    唯一通用契约。之前只有 JSON，接 Grafana 得自己写 exporter。
    """
    if format == "prometheus":
        return PlainTextResponse(obs.prometheus_exposition(), media_type="text/plain; version=0.0.4")
    snap = obs.METRICS.snapshot()
    snap["events_log"] = str(obs.EVENT_LOG)
    snap["trace_hint"] = "发布/更新链路 grep 'trace' + trace_id 于 events.log"
    snap["prometheus_url"] = "/api/v1/hub/metrics?format=prometheus"
    return snap


@router.get("/jobs")
def jobs(limit: int = 50):
    return [dict(r) for r in db.list_jobs(get_hub().conn, limit)]


@router.get("/publications")
def publications(article_id: int = None, platform: str = None):
    return [dict(r) for r in db.list_publications_joined(
        get_hub().conn, article_id=article_id, platform=platform)]


@router.get("/pending-human")
def pending_human():
    """需要人工处理的发布实例（如掘金草稿等人点"确定并发布"）。"""
    return [dict(r) for r in db.list_pending_human(get_hub().conn)]
