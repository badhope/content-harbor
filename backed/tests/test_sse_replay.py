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


def test_dispatch_queue_is_bounded():
    """派发队列必须有上限：webhook 挂掉时事件堆在内存会 OOM。"""
    from service.publishing import events as evt_mod

    assert evt_mod._dispatch_q.maxlen == evt_mod._DISPATCH_MAX
    for i in range(evt_mod._DISPATCH_MAX + 50):
        evt_mod._enqueue_webhook({"type": "x", "data": {"i": i}})
    stats = evt_mod.dispatch_queue_stats()
    assert stats["queued"] == evt_mod._DISPATCH_MAX, "未按上限截断"
    assert stats["dropped"] >= 50, "丢弃未计数"


def test_corrupt_events_column_does_not_kill_dispatch(monkeypatch):
    """events 列损坏时不能打死派发线程。

    回归：json.loads 在 try 之外，一条坏数据冒到 _dispatcher_loop 就把
    线程杀掉，之后所有 webhook 静默停止且无法自愈。
    """
    from service.publishing import events as evt_mod

    sent = []
    monkeypatch.setattr(evt_mod, "_wh_list_enabled",
                        lambda: [(1, "https://example.com/hook", "", "{not json", 0)])
    monkeypatch.setattr(evt_mod, "_webhook_target_allowed", lambda url: True)
    monkeypatch.setattr(evt_mod._wh, "sign_payload", lambda p, s: "")
    monkeypatch.setattr(evt_mod, "_update_webhook_status", lambda *a, **k: None)

    class FakeResp:
        status = 200
        def read(self): return b"ok"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(evt_mod.urllib.request, "urlopen",
                        lambda *a, **k: sent.append(a[0].full_url) or FakeResp())
    evt_mod._dispatch_async({"type": "task.ok", "data": {}})
    assert sent, "订阅表损坏时应退化为全订阅并投递，而不是静默跳过"
