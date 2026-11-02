# -*- coding: utf-8 -*-
"""任务引擎回归：提交、串行消费、失败分诊、人工恢复。

用 FakeHub 顶掉真实 Hub，只验证 TaskManager 自己的契约——
不碰浏览器、不碰平台。
"""

import threading
import time

import pytest

from service.publishing import db
from service.publishing.tasks import TaskManager


class FakeHub:
    """按脚本返回/抛错的假业务层。"""

    def __init__(self, script=None):
        self.script = script or {}
        self.calls = []
        self.conn = None

    def _act(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        outcome = self.script.get(name, "ok")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def publish(self, *a, **kw):
        return self._act("publish", *a, **kw)

    def update(self, *a, **kw):
        return self._act("update", *a, **kw)

    def sync_pending(self, *a, **kw):
        return self._act("sync", *a, **kw)

    def refresh(self, *a, **kw):
        return self._act("refresh", *a, **kw)

    def login(self, *a, **kw):
        return self._act("login", *a, **kw)

    def assist_open(self, *a, **kw):
        return self._act("assist", *a, **kw)


@pytest.fixture
def tm(sandbox):
    conn = db.connect(sandbox["db_file"])
    hub = FakeHub()
    hub.conn = conn
    mgr = TaskManager(hub, conn, backoff_base=0, backoff_max=0)
    yield mgr
    # 必须先让工作线程真正退出再关连接：worker 正跑在任务里时 conn.close()
    # 会在另一条线程上踩已释放的 sqlite3 句柄（Windows 上直接 access violation）。
    mgr.close()
    mgr._worker.join(timeout=10)
    conn.close()


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_submit_returns_task_id_and_persists(tm, sandbox):
    tid = tm.submit("publish", article_id=7, platforms=["juejin"], draft_only=True)
    assert isinstance(tid, str) and len(tid) == 10
    assert tm.get(tid)["kind"] == "publish"
    assert tm.get(tid)["platforms"] == '["juejin"]'
    assert tm.list(kind="publish")[0]["task_id"] == tid


def test_extra_params_ride_in_result_field(tm):
    """额外参数（如 assist 的 url）必须随任务落库，worker 读得到。"""
    gate = threading.Event()

    def blocking_assist(*a, **kw):
        tm.hub.calls.append(("assist", a, kw))
        gate.wait(timeout=5)
        return "ok"

    tm.hub.assist_open = blocking_assist
    tid = tm.submit("assist", platforms=["zhihu"], url="https://x.dev/a?b=1")

    assert _wait(lambda: (tm.get(tid) or {}).get("status") == "running")
    running = tm.get(tid)
    assert "https://x.dev/a?b=1" in running["result"]

    gate.set()
    assert _wait(lambda: tm.get(tid)["status"] == "ok")
    assert tm.hub.calls[0][2] == {"account": "default"}


def test_task_runs_to_completion(tm):
    tid = tm.submit("publish", article_id=1, platforms=["juejin"])
    assert _wait(lambda: tm.get(tid)["status"] == "ok")
    task = tm.get(tid)
    assert task["finished_at"] > 0
    assert tm.hub.calls[0][0] == "publish"


def test_captcha_error_routes_to_human(tm):
    tm.hub.script["publish"] = RuntimeError("CAPTCHA 验证码出现")
    tid = tm.submit("publish", article_id=1, platforms=["juejin"])
    assert _wait(lambda: tm.get(tid)["status"] == "waiting_human")
    task = tm.get(tid)
    assert "风控" in task["message"] or "验证码" in task["message"]
    assert task["attempts"] == 1  # 命中人工不再重试


def test_network_error_retries_then_fails(tm):
    tm.hub.script["publish"] = TimeoutError("connection timed out")
    tid = tm.submit("publish", article_id=1, platforms=["juejin"])
    assert _wait(lambda: tm.get(tid)["status"] == "failed")
    task = tm.get(tid)
    assert task["attempts"] == TaskManager.MAX_ATTEMPTS
    assert "自动重试" not in task["message"]


@pytest.mark.parametrize("err,attempts,expected", [
    ("ValueError: 未知任务类型: bogus", 1, "retry"),
    ("ValueError: 未知任务类型: bogus", 2, "fail"),
    ("RuntimeError: 登录已失效，请重新登录", 1, "human"),
    ("ConnectionResetError: econnreset", 1, "retry"),
    # F-20 新增：合规门禁 422 是确定性错误，不重试
    ("HTTPException: 422: 合规门禁拒绝发布", 1, "fail"),
    ("HTTPException: 422: 安全扫描命中高危内容", 1, "fail"),
])
def test_triage_rules(tm, err, attempts, expected):
    """F-20: 未分类错误第 1 次 retry，第 2 次 fail；合规门禁 422 立即 fail。"""
    action, reason = tm._triage("publish", err, attempts)
    assert action == expected, f"{err!r} attempts={attempts} → {action} (expected {expected}): {reason}"


def test_triage_retry_gives_up_after_max_attempts(tm):
    action, reason = tm._triage("publish", "ConnectionResetError: 429", 3)
    assert action == "fail"
    assert "3" in reason


def test_resume_approved_reruns(tm):
    tm.hub.script["publish"] = RuntimeError("安全验证 security check")
    tid = tm.submit("publish", article_id=1, platforms=["juejin"])
    assert _wait(lambda: tm.get(tid)["status"] == "waiting_human")

    tm.hub.script["publish"] = "ok"
    out = tm.resume(tid, approved=True)
    assert out["ok"] is True
    assert _wait(lambda: tm.get(tid)["status"] == "ok")


def test_resume_rejected_marks_failed(tm):
    tm.hub.script["publish"] = RuntimeError("captcha")
    tid = tm.submit("publish", article_id=1, platforms=["juejin"])
    assert _wait(lambda: tm.get(tid)["status"] == "waiting_human")
    assert tm.resume(tid, approved=False)["ok"] is True
    assert tm.get(tid)["status"] == "failed"
    assert tm.get(tid)["message"] == "已放弃"


def test_resume_running_task_is_refused(tm):
    tm.hub.script["publish"] = "ok"
    tid = tm.submit("publish", article_id=1, platforms=["juejin"])
    assert _wait(lambda: tm.get(tid)["status"] == "ok")
    out = tm.resume(tid, approved=True)
    assert out["ok"] is False
    assert "不可恢复" in out["error"]


def test_worker_is_serial(tm):
    """单工作线程串行：并发提交不得并发执行业务层。"""
    active = []
    peak = []

    def guarded_publish(*a, **kw):
        active.append(1)
        peak.append(len(active))
        time.sleep(0.01)
        active.pop()
        return "ok"

    tm.hub.publish = guarded_publish
    for _ in range(5):
        tm.submit("publish", article_id=1, platforms=["juejin"])
    assert _wait(lambda: len(peak) == 5)
    assert max(peak) == 1
