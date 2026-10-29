# -*- coding: utf-8 -*-
"""系统端点：health / status / metrics / jobs / publications / pending-human。"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

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


# ---- 适配器契约探针（替代无法执行的真机回归） ----

@router.get("/adapters/probe")
def adapters_probe(dom: bool = True):
    """离线检查全部平台适配器：接口实现 / 能力一致性 / 选择器合法性 / DOM 漂移。

    真机回归需要账号与验证码，CI 跑不了；这层把"能提前发现的"全部前移。
    `dom=false` 只跑静态契约。
    """
    from service.publishing import adapter_probe
    return adapter_probe.full_report(include_dom=dom)


@router.post("/adapters/probe/{platform}/dump")
def adapters_probe_dump(platform: str, html: str):
    """保存一份平台 DOM 采样，供 probe 做选择器漂移比对。

    采集方式：登录后用浏览器开发者工具「另存为 HTML」，把内容贴进来。
    """
    from service.publishing import adapter_probe
    if not (html or "").strip():
        raise HTTPException(422, "html 不能为空")
    path = adapter_probe.save_dump(platform, html)
    return {"ok": True, "path": str(path),
            "check": adapter_probe.check_dom(platform, html)}


# ---- 外部 MCP Server 桥接 ----

@router.get("/mcp/bridge/status")
def mcp_bridge_status():
    """已配置的外部 MCP Server 概览 + 能力覆盖情况。

    没配任何 server 时返回 configured=0（默认关闭，不拉起任何子进程）。
    """
    from service.publishing import mcp_bridge
    out = mcp_bridge.status()
    out["coverage"] = mcp_bridge.capability_coverage()
    return out


@router.get("/mcp/bridge/{server}/tools")
def mcp_bridge_tools(server: str):
    """列出某个外部 server 实际提供的工具（配白名单前先看这个）。"""
    from service.publishing import mcp_bridge
    r = mcp_bridge.list_tools(server)
    if not r.get("ok"):
        raise HTTPException(502, r.get("error", "外部 MCP Server 不可用"))
    return r


class MCPCallIn(BaseModel):
    tool: str
    arguments: Optional[dict] = None
    timeout: Optional[float] = None


@router.post("/mcp/bridge/{server}/call")
def mcp_bridge_call(server: str, body: MCPCallIn):
    """调用外部 MCP Server 的工具（仅白名单内工具）。

    用途：给任务引擎/排障提供统一入口，例如用 Playwright MCP 做页面快照。
    失败返回 502 + 结构化原因，不吞错。
    """
    from service.publishing import mcp_bridge
    r = mcp_bridge.call_tool(server, body.tool, body.arguments or {},
                             timeout=body.timeout)
    if not r.get("ok"):
        raise HTTPException(502, r.get("error", "调用失败"))
    return r


@router.post("/platform-cooldowns/clear")
def clear_cooldowns(platform: str = ""):
    """手动解除平台限流冷却（平台侧确认恢复后用）。"""
    from service.publishing import ratelimit
    ratelimit.clear(get_hub().conn, platform or None)
    return {"ok": True, "cleared": platform or "all"}


@router.get("/pending-human")
def pending_human():
    """需要人工处理的发布实例（如掘金草稿等人点"确定并发布"）。"""
    return [dict(r) for r in db.list_pending_human(get_hub().conn)]
