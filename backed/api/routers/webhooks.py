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


# ---- Webhook CRUD ----

@router.get("/webhooks")
def webhooks_list():
    from service.publishing import events as evt_mod
    return evt_mod.list_webhooks()


@router.post("/webhooks")
def webhooks_create(body: WebhookIn):
    from service.publishing import events as evt_mod
    wid = evt_mod.register_webhook(body.url, body.events, body.secret, body.enabled)
    return {"id": wid, "message": "Webhook 注册成功"}


@router.put("/webhooks/{wid}")
def webhooks_update(wid: int, body: WebhookIn):
    from service.publishing import events as evt_mod
    data = {k: v for k, v in body.model_dump().items() if v is not None}
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
