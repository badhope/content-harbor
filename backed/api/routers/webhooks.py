# -*- coding: utf-8 -*-
"""Webhook / 事件通知 / SSE 端点。"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


router = APIRouter()


class WebhookIn(BaseModel):
    url: str
    secret: str = ""
    events: Optional[List[str]] = None   # None=["*"] 表示全订阅
    enabled: bool = True
    # 显式允许推送到内网地址。默认关闭：webhook 是"服务端主动发起请求"，
    # 不做校验就等于把 SSRF 原语交给任何能调 API 的人（实测可注册到
    # 169.254.169.254 与 127.0.0.1）。自托管用户要推内网 n8n 时把它打开。
    allow_private: bool = False


def _validate_webhook_url(url: str, allow_private: bool = False) -> str:
    """校验 webhook 目标。

    与 articles/import、media/ingest 的差异（刻意的，不是漏写）：

    - 抓取类是**立即执行**的，所以必须在请求时解析并校验 IP；
    - webhook 是**注册后延迟投递**的，注册那一刻域名可能还没上线
      （自建站常先配 webhook 再部署）。因此这里只做静态检查：
      协议、字面量 IP、明显的内网主机名；真正的 DNS 与 IP 校验放在
      派发时做（见 `_dispatch_async` → `guard_outbound`），
      这样既不误伤正常注册，也不会在投递时被打穿。

    实测过的问题：此前这里完全没有校验，可直接注册
    `http://169.254.169.254/...` 与 `http://127.0.0.1:6379/`。
    """
    import ipaddress
    from urllib.parse import urlparse

    from service.publishing.url_safety import URLSafetyError, _is_public_ip

    if not isinstance(url, str) or not url.strip():
        raise HTTPException(422, "url 必须是非空字符串")
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ("http", "https"):
        raise HTTPException(422, f"仅支持 http/https: {url[:100]}")
    if parsed.username is not None or parsed.password is not None:
        raise HTTPException(422, "url 不允许携带用户凭据")
    host = parsed.hostname
    if not host:
        raise HTTPException(422, "url 必须含有效域名")

    if allow_private:
        return url.strip()

    # 字面量 IP：直接判公网性，不需要 DNS
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_ip(literal):
            raise HTTPException(
                422, f"webhook 目标 {host} 不是公网地址（如确需内网，请传 allow_private=true）")
        return url.strip()

    # 明显的内网/本机主机名
    lowered = host.lower().rstrip(".")
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".localhost"):
        raise HTTPException(
            422, "webhook 目标指向本机（如确需内网，请传 allow_private=true）")

    # 域名形态做一次「能解析就检查」的尽力而为校验：解析失败不拦（可能还没上线），
    # 解析到内网则拦。投递前会再校验一次。
    try:
        from service.publishing.url_safety import resolve_public_ips
        resolve_public_ips(host)
    except URLSafetyError as e:
        if "无法解析" in str(e):
            return url.strip()
        raise HTTPException(
            422, f"{e}（如确需推送到内网，请传 allow_private=true）")
    return url.strip()


# ---- Webhook CRUD ----

@router.get("/webhooks")
def webhooks_list():
    from service.publishing import events as evt_mod
    return evt_mod.list_webhooks()


@router.post("/webhooks")
def webhooks_create(body: WebhookIn):
    from service.publishing import events as evt_mod
    _validate_webhook_url(body.url, body.allow_private)
    wid = evt_mod.register_webhook(body.url, body.events, body.secret, body.enabled)
    return {"id": wid, "message": "Webhook 注册成功"}


@router.put("/webhooks/{wid}")
def webhooks_update(wid: int, body: WebhookIn):
    from service.publishing import events as evt_mod
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    # 改 url 同样要校验，否则"先注册合法地址再 PUT 成内网"绕过创建时的检查
    if "url" in data:
        _validate_webhook_url(data["url"], data.pop("allow_private", False))
    else:
        data.pop("allow_private", None)
    ok = evt_mod.update_webhook(wid, **data)
    if not ok:
        raise HTTPException(404, "Webhook 不存在")
    return {"ok": True}


@router.delete("/webhooks/{wid}")
def webhooks_delete(wid: int):
    from service.publishing import events as evt_mod
    ok = evt_mod.delete_webhook(wid)
    if not ok:
        raise HTTPException(404, "Webhook 不存在")
    return {"ok": True}


@router.post("/webhooks/{wid}/test")
def webhooks_test(wid: int):
    """发送测试事件到指定 webhook。

    以前对不存在的 id 也返回 ``{"ok": True}``——用户点"测试"看到成功，
    实际什么都没发出去。现在先校验存在性，不存在直接 404。
    """
    from service.publishing import events as evt_mod
    existing = [w for w in evt_mod.list_webhooks() if w.get("id") == wid]
    if not existing:
        raise HTTPException(404, "Webhook 不存在")
    evt_mod.init_events()
    evt_mod.emit("test", {"message": "Harbor 测试事件", "webhook_id": wid})
    return {"ok": True, "message": "测试事件已派发", "webhook_id": wid}


# ---- 站内通知 ----

@router.get("/notifications")
def notifications_list(since: float = None, limit: int = 50):
    from service.publishing import events as evt_mod
    return evt_mod.get_notifications(since=since, limit=limit)


# ---- SSE 实时事件流 ----

@router.get("/events/stream")
async def events_stream(request: Request):
    """SSE 实时推送任务/发布/文章事件。前端用 EventSource 连。

    支持 ``Last-Event-ID`` 重放：浏览器断线重连时会自动带上该头，
    服务端补发缺口事件；缺口超出缓冲窗口时先发一条 ``resync``，
    前端据此整体刷新而不是静默显示旧状态。
    """
    import asyncio
    import json
    import queue
    import time
    from service.publishing import events as evt_mod

    async def event_generator():
        q = evt_mod.subscribe_sse()
        try:
            # 先补发，再发 connected：客户端拿到的事件顺序与真实发生顺序一致
            raw_last = request.headers.get("last-event-id", "")
            try:
                last_id = int(raw_last)
            except (TypeError, ValueError):
                last_id = 0
            missed, gap = evt_mod.replay_after(last_id)
            if gap:
                yield ("event: resync\n"
                       f"data: {json.dumps({'ts': time.time(), 'reason': 'replay-window-missed'})}\n\n")
            for evt in missed:
                yield (f"id: {evt.get('id', '')}\n"
                       f"data: {json.dumps(evt, ensure_ascii=False, default=str)}\n\n")
            yield (f"event: connected\n"
                   f"data: {json.dumps({'ts': time.time(), 'replayed': len(missed)}, ensure_ascii=False)}\n\n")
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.to_thread(q.get, timeout=1.0)
                    data = json.dumps(evt, ensure_ascii=False, default=str)
                    yield f"id: {evt.get('id', '')}\ndata: {data}\n\n"
                except queue.Empty:
                    # 心跳：防止 nginx/CDN 断连
                    yield f": heartbeat {time.time()}\n\n"
                except Exception:
                    await asyncio.sleep(0.5)
        finally:
            evt_mod.unsubscribe_sse(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })
