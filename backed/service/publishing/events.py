# -*- coding: utf-8 -*-
"""事件系统总入口：聚合通知缓冲 / SSE 实时流 / Webhook 派发。

子模块拆分：
  - ``_event_notifier`` : 站内通知环形缓冲（内存，最近 200 条）
  - ``_sse_broker``     : SSE 订阅/广播（多客户端长连接管理）
  - ``_webhook_store``  : Webhook 存储 + HMAC 签名 + 异步派发

本模块保持向后兼容的导出：``emit()`` / ``init_events()`` /
``get_notifications()`` / ``subscribe_sse()`` / ``unsubscribe_sse()`` /
``on_task_complete()`` 等。
"""

import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque

# 子模块
from . import _event_notifier as _notifier
from . import _sse_broker as _sse
from . import _webhook_store as _wh

# ---------------- 事件类型 → 说明 -----------------

EVENT_TYPES = {
    "task.ok": "任务完成",
    "task.failed": "任务失败",
    "task.waiting_human": "任务等待人工",
    "publish.ok": "发布成功",
    "publish.failed": "发布失败",
    "article.created": "新建文章",
    "article.updated": "文章更新",
    "account.login": "登录成功",
    "account.logout": "登出",
    "ai.write": "AI 写稿完成",
    "ai.rewrite": "AI 改写完成",
    "api.access": "API 调用",
    "test": "测试事件",
}

# ---------------- 对外接口 -----------------

_dispatch_q = deque()
_dispatch_cv = threading.Condition()
_dispatcher_running = False


def init_events(conn=None):
    """初始化事件系统（建 webhook 表 + 启动异步派发线程）。

    由 Hub.__init__ 调用一次即可；幂等：重复调不会起第二个线程。
    独立使用 events 模块（不经 Hub）的场景需先调 init_events() 再 emit()。
    """
    _wh.init(conn)
    start_dispatcher()


def get_notifications(since=None, limit=50):
    """查询站内通知。"""
    return _notifier.get_since(since=since, limit=limit)


def subscribe_sse():
    """注册一个 SSE 客户端。返回 Queue。"""
    return _sse.subscribe()


def unsubscribe_sse(q):
    """注销 SSE 客户端。"""
    _sse.unsubscribe(q)


def emit(event_type, data=None):
    """发射一个事件：写通知缓冲 + SSE 广播 + 异步 webhook 派发。"""
    evt = {"type": event_type, "data": data or {}, "timestamp": time.time()}
    _notifier.append(evt)
    _sse.broadcast(evt)
    _enqueue_webhook(evt)
    return evt


# ---------------- Webhook CRUD（代理） -----------------

def register_webhook(url, events=None, secret="", enabled=True):
    """注册 webhook。"""
    return _wh.register(url, events, secret, enabled)


def list_webhooks():
    """列出所有 webhook。"""
    return _wh.list_all()


def update_webhook(wid, **kw):
    """更新 webhook。"""
    return _wh.update(wid, **kw)


def delete_webhook(wid):
    """删除 webhook。"""
    return _wh.delete(wid)


# ---------------- 异步 Webhook 派发 -----------------

def _enqueue_webhook(evt):
    """把事件推入异步派发队列。"""
    with _dispatch_cv:
        _dispatch_q.append(evt)
        _dispatch_cv.notify()


def _dispatch_async(evt):
    """把事件 POST 到所有匹配的 webhook（在后台线程执行）。"""
    rows = _wh_list_enabled()
    if not rows:
        return
    payload = json.dumps(evt, ensure_ascii=False, default=str)
    for wid, url, secret_enc, evts_json, fail_count in rows:
        evts = json.loads(evts_json) if evts_json else ["*"]
        if "*" not in evts and evt["type"] not in evts:
            continue
        try:
            headers = {"Content-Type": "application/json", "X-Harbor-Event": evt["type"]}
            sig = _wh.sign_payload(payload, secret_enc)
            if sig:
                headers["X-Harbor-Signature"] = f"sha256={sig}"
            req = urllib.request.Request(
                url, data=payload.encode(), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=8) as resp:
                _ = resp.read()
            _update_webhook_status(wid, success=True)
        except Exception:
            _update_webhook_status(wid, success=False, prev_fail=fail_count)


def _wh_list_enabled():
    """查询所有启用的 webhook。"""
    try:
        c = _wh._conn()
        rows = c.execute(
            "SELECT id, url, secret, events, fail_count FROM webhooks WHERE enabled=1"
        ).fetchall()
        c.close()
        return rows
    except Exception:
        return []


def _update_webhook_status(wid, success, prev_fail=0):
    """更新 webhook 最后派发时间 + 失败计数。连续失败 5 次自动禁用。"""
    try:
        c = _wh._conn()
        if success:
            c.execute("UPDATE webhooks SET last_triggered=?, fail_count=0 WHERE id=?",
                      (time.time(), wid))
        else:
            new_fail = (prev_fail or 0) + 1
            c.execute("UPDATE webhooks SET fail_count=?, enabled=? WHERE id=?",
                      (new_fail, 1 if new_fail < 5 else 0, wid))
        c.commit()
        c.close()
    except Exception:
        pass


def _dispatcher_loop():
    """后台派发线程：从队列取事件 → POST 到 webhook。"""
    global _dispatcher_running
    _dispatcher_running = True
    while _dispatcher_running:
        with _dispatch_cv:
            while not _dispatch_q and _dispatcher_running:
                _dispatch_cv.wait(timeout=1.0)
            if not _dispatcher_running:
                break
            evt = None
            if _dispatch_q:
                evt = _dispatch_q.popleft()
        if evt:
            _dispatch_async(evt)


def start_dispatcher():
    """启动异步 webhook 派发线程（幂等）。"""
    global _dispatcher_thread
    if _dispatcher_thread is None:
        from threading import Thread
        _dispatcher_thread = Thread(target=_dispatcher_loop, daemon=True,
                                    name="event-dispatcher")
        _dispatcher_thread.start()


def stop_dispatcher():
    """停止异步 webhook 派发线程（确定性回收：置停 + 唤醒 + join + 允许重启）。

    2026-10-14 修复：旧实现只置停不 join、也不清引用——线程可能在连接已关闭后
    才醒来处理队列（Windows 上表现为解释器退出期 sqlite 访问违例，测试进程
    间歇性崩溃）；且残留的线程引用让后续 start_dispatcher() 永远 no-op。
    """
    global _dispatcher_running, _dispatcher_thread
    _dispatcher_running = False
    with _dispatch_cv:
        _dispatch_cv.notify()
    t = _dispatcher_thread
    if t is not None:
        if t.is_alive():
            t.join(timeout=2)
        _dispatcher_thread = None


# ---------------- TaskManager 事件包装 -----------------

def on_task_complete(task):
    """任务完成 / 失败 / 等人工时，根据 status 触发对应事件。"""
    kind = task.get("kind", "")
    status = task.get("status", "")
    article_id = task.get("article_id")
    if status == "ok":
        emit("task.ok", {"task_id": task.get("task_id"), "kind": kind,
                         "article_id": article_id})
        if kind == "publish":
            result = task.get("result", {}) or {}
            platforms = []
            if isinstance(result, list):
                platforms = [r.get("platform") for r in result if isinstance(r, dict)]
            elif isinstance(result, dict) and "results" in result:
                platforms = [r.get("platform") for r in result["results"] if isinstance(r, dict)]
            emit("publish.ok", {"task_id": task.get("task_id"),
                                "article_id": article_id, "platforms": platforms})
    elif status == "failed":
        emit("task.failed", {"task_id": task.get("task_id"), "kind": kind,
                             "error": task.get("error", "")[:200]})
        if kind == "publish":
            emit("publish.failed", {"task_id": task.get("task_id"),
                                    "article_id": article_id,
                                    "error": task.get("error", "")[:200]})
    elif status == "waiting_human":
        emit("task.waiting_human", {"task_id": task.get("task_id"), "kind": kind,
                                    "message": task.get("message", "")})


_dispatcher_thread = None
# 注意：派发线程改由 init_events() 显式启动（F-15 修复）。
# 旧的模块导入时自动 start_dispatcher() 会导致 pytest 里无关测试也拉起守护线程，
# 且多实例 Hub 场景下无法控制线程生命周期。
# 独立使用 events 模块的场景需先调 init_events() 再 emit()。
