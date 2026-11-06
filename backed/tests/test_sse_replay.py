# -*- coding: utf-8 -*-
"""SSE 断线重放：重连后不能静默丢事件。

EventSource 断线会自动重连并带 Last-Event-ID；服务端不认这个头时，
断线期间的事件永久丢失，界面表现为"任务卡住"，刷新才好。
"""
import service.publishing._sse_broker as broker


def setup_function():
    broker.reset_for_tests()


def test_events_get_monotonic_ids():
    # 先订阅再广播：广播只投递给"当前在线"的客户端，历史事件靠 replay 补
    q = broker.subscribe()
    broker.broadcast({"type": "task.started", "data": {}})
    broker.broadcast({"type": "task.done", "data": {}})
    a = q.get_nowait()
    b = q.get_nowait()
    assert a["id"] < b["id"]


def test_replay_returns_only_missed_events():
    for i in range(5):
        broker.broadcast({"type": f"e{i}", "data": {}})
    missed, gap = broker.replay_after(3)
    assert [e["type"] for e in missed] == ["e3", "e4"]
    assert gap is False


def test_replay_after_zero_returns_nothing():
    broker.broadcast({"type": "e0", "data": {}})
    missed, gap = broker.replay_after(0)
    assert missed == [] and gap is False


def test_replay_detects_gap_outside_window():
    # 灌满缓冲再多发一条，使最早一条被挤出
    for i in range(broker._REPLAY_SIZE + 5):
        broker.broadcast({"type": f"e{i}", "data": {}})
    missed, gap = broker.replay_after(1)
    assert gap is True, "超出窗口必须告知前端需要整体刷新"
    assert len(missed) <= broker._REPLAY_SIZE


def test_replay_after_latest_returns_empty_no_gap():
    broker.broadcast({"type": "only", "data": {}})
    missed, gap = broker.replay_after(1)
    assert missed == [] and gap is False
