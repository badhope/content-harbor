# -*- coding: utf-8 -*-
"""SSE 客户端管理：订阅、注销、广播、断线重放。

为什么要有重放：浏览器 EventSource 断线后会自动重连并带上
``Last-Event-ID``，服务端若不认这个头，重连期间产生的事件就永久丢失——
用户看到的是"任务卡住"，刷新一下又好了。这里维护一个定长环形缓冲，
按 id 补发缺口，超出缓冲窗口则明确告知需要全量刷新（``resync``）。
"""

import itertools
import queue
import threading

# 每个连接一个 Queue（maxsize=64，满了就丢弃事件，防慢客户端拖垮服务端）
_clients: set[queue.Queue] = set()
_lock = threading.Lock()

# 事件 id：单调递增，进程内唯一。客户端据此判断重放窗口。
_event_id = itertools.count(1)

# 环形缓冲：保留最近 N 条，供断线客户端补发。N 取 256——单条事件约 200B，
# 256 条约 50KB，远小于一个网页；足够覆盖前端自动重连（通常 3 秒内）。
_REPLAY_SIZE = 256
_replay: list[dict] = []
_replay_lock = threading.Lock()


def next_event_id() -> int:
    return next(_event_id)


def subscribe() -> queue.Queue:
    """注册一个 SSE 客户端，返回 Queue，新事件会推入其中。"""
    q = queue.Queue(maxsize=64)
    with _lock:
        _clients.add(q)
    return q


def unsubscribe(q: queue.Queue):
    """注销 SSE 客户端（前端断开连接时调用）。"""
    with _lock:
        _clients.discard(q)


def broadcast(event: dict):
    """把事件推送到所有已注册的 SSE 客户端，并写入重放缓冲。

    事件必须先分配 id 再进缓冲，否则客户端拿到的 id 与实际存储顺序不一致，
    重连补发会错位。
    """
    evt = dict(event)
    evt.setdefault("id", next_event_id())
    with _replay_lock:
        _replay.append(evt)
        if len(_replay) > _REPLAY_SIZE:
            del _replay[: len(_replay) - _REPLAY_SIZE]
    with _lock:
        clients = list(_clients)
    for q in clients:
        try:
            q.put_nowait(evt)
        except queue.Full:
            pass   # 客户端来不及消费就跳过（它重连时会用 Last-Event-ID 补）


def replay_after(last_id: int) -> tuple[list[dict], bool]:
    """返回 ``(id > last_id 的事件, 是否已超出重放窗口)``。

    ``last_id <= 0`` 视为首次连接，不补发。窗口外的缺口无法补，
    用第二个返回值告诉调用方发 ``resync`` 事件让前端整体刷新。
    """
    if not last_id or last_id <= 0:
        return [], False
    with _replay_lock:
        if not _replay:
            return [], False
        oldest = _replay[0].get("id", 0)
        missed = [e for e in _replay if e.get("id", 0) > last_id]
    gap = bool(oldest and oldest > last_id + 1)
    return missed, gap


def client_count() -> int:
    """当前活跃 SSE 连接数。"""
    with _lock:
        return len(_clients)


def reset_for_tests():
    """清空重放缓冲与订阅者（仅测试使用）。"""
    global _event_id
    with _replay_lock:
        _replay.clear()
    with _lock:
        _clients.clear()
    _event_id = itertools.count(1)
