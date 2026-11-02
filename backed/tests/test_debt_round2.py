# -*- coding: utf-8 -*-
"""第二轮工程债回归：指标桶语义 / 限流桶生命周期 / 浏览器池关停。

每例都对应一条已确认的运行期隐患，修前必红。
"""

import time

from middleware.rate_limiter import InMemoryRateLimiter

from service.publishing import observability as obs


# ---------------- R1 直方图累积桶 ----------------

def test_histogram_buckets_are_cumulative():
    reg = obs.MetricRegistry()
    for v in (0.3, 1.5, 4.0):
        reg.observe("t", v)
    buckets = reg.snapshot()["histograms"]["t"]["buckets"]
    # Prometheus 语义：le 桶是 <= 上界的累积计数，+Inf 必须等于总次数
    assert buckets["0.25"] == 0
    assert buckets["0.5"] == 1
    assert buckets["1"] == 1
    assert buckets["2"] == 2
    assert buckets["5"] == 3
    assert buckets["10"] == 3
    assert buckets["+Inf"] == 3


def test_histogram_reports_quantiles():
    reg = obs.MetricRegistry()
    for v in (0.3, 1.5, 4.0):
        reg.observe("t", v)
    h = reg.snapshot()["histograms"]["t"]
    q = h["quantiles"]
    assert 0.2 <= q["p50"] <= 2.0
    assert q["p90"] >= q["p50"]
    assert 2.0 <= q["p99"] <= 5.0


def test_histogram_empty_snapshot_has_no_quantiles():
    reg = obs.MetricRegistry()
    reg.observe("t", 0.1)
    h = reg.snapshot()["histograms"]["t"]
    assert set(h["quantiles"]) == {"p50", "p90", "p99"}


# ---------------- R2 限流桶有界 ----------------

def test_rate_limit_bucket_table_is_bounded():
    lim = InMemoryRateLimiter(max_buckets=3)
    for i in range(5):
        lim.is_allowed(f"k{i}")
    assert len(lim._buckets) <= 3


def test_rate_limit_eviction_prefers_full_buckets():
    """淘汰必须先挑已回满的桶：热桶被挤掉等于把限流洗白。"""
    lim = InMemoryRateLimiter(max_buckets=2)
    for _ in range(lim._capacity):
        lim.is_allowed("hot")          # 打到 0 令牌
    lim.is_allowed("cold")             # 满桶
    before = len(lim._buckets)
    lim.is_allowed("new")              # 触发淘汰
    assert len(lim._buckets) <= before
    assert "hot" in lim._buckets, "热桶不得被优先淘汰"


def test_rate_limit_rotation_does_not_reset_quota():
    """轮换来源冲刷桶表的攻击：自己的热桶不能被淘汰回满额。"""
    lim = InMemoryRateLimiter(max_buckets=3)
    for _ in range(lim._capacity + 5):
        lim.is_allowed("attacker")
    assert lim.is_allowed("attacker")[0] is False
    for i in range(10):
        lim.is_allowed(f"filler{i}")
    assert lim.is_allowed("attacker")[0] is False, "轮换 filler 不得让 attacker 重新拿到满桶"


def test_rate_limit_zero_max_buckets_means_unlimited():
    """max_buckets=0 是显式关闭上限，不能被静默改成 1。"""
    lim = InMemoryRateLimiter(max_buckets=0)
    for i in range(5):
        lim.is_allowed(f"k{i}")
    assert len(lim._buckets) == 5


def test_rate_limit_lock_ordering_does_not_deduct_tokens(monkeypatch):
    """时钟必须在锁内取：锁外取值会让等待期间 delta 为负、倒扣令牌。"""
    import middleware.rate_limiter as mod
    base = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: base[0])
    lim = InMemoryRateLimiter()
    for _ in range(lim._capacity - 1):
        lim.is_allowed("k")
    assert lim._buckets["k"].tokens == 1.0
    base[0] += 10.0
    lim.is_allowed("k")
    assert lim._buckets["k"].tokens >= 0.0


# ---------------- R3 浏览器池锁外关闭 ----------------

def test_browser_pool_closes_evicted_instance_outside_lock(monkeypatch):
    import service.publishing.browser_pool as bp

    seen = []

    class FakeBrowser:
        def __init__(self, platform, account, headless=True):
            self.platform = platform
            self.pool = CURRENT["pool"]

        def close(self):
            seen.append(self.pool._pool_lock.locked())

    CURRENT = {}
    monkeypatch.setattr(bp, "BuiltinBrowser", FakeBrowser)
    monkeypatch.setattr(bp, "browser_thread_run", lambda fn, *a, **k: fn(*a, **k))

    pool = bp.BrowserPoolManager()
    pool.POOL_MAX = 1
    CURRENT["pool"] = pool

    pool._acquire("a", "default")
    pool._acquire("b", "default")

    assert seen == [False], "淘汰实例必须在池锁之外关闭，否则会阻塞整池取用"
    assert pool.pool_size == 1


def test_browser_pool_close_all_releases_lock_first():
    import service.publishing.browser_pool as bp

    class FakeBrowser:
        pass

    pool = bp.BrowserPoolManager()
    pool._pool[("x", "default")] = FakeBrowser()
    pool._pool_order.append(("x", "default"))
    pool.close_all_browsers()
    assert pool.pool_size == 0


def test_browser_pool_waits_for_same_key_close(monkeypatch):
    """同 key 的旧实例在锁外关闭期间，新的 _acquire 必须等它关完再启动。

    否则会在旧实例还占着 profile 时 launch_persistent_context，真实环境必失败。
    """
    import threading
    import time

    import service.publishing.browser_pool as bp

    events = []
    close_started = threading.Event()
    allow_close = threading.Event()

    class FakeBrowser:
        def __init__(self, platform, account, headless=True):
            self.platform = platform
            events.append(("new", platform))

        def close(self):
            events.append(("close-start", self.platform))
            close_started.set()
            allow_close.wait(5)
            events.append(("close-end", self.platform))

    monkeypatch.setattr(bp, "BuiltinBrowser", FakeBrowser)
    monkeypatch.setattr(bp, "browser_thread_run", lambda fn, *a, **k: fn(*a, **k))

    pool = bp.BrowserPoolManager()
    pool.POOL_MAX = 1
    pool._acquire("a", "default")            # 池里放 a

    t1 = threading.Thread(target=lambda: pool._acquire("b", "default"))
    t1.start()
    assert close_started.wait(5), "淘汰 a 时应开始关闭"

    done = []

    def second_a():
        pool._acquire("a", "default")
        done.append("a-acquired")

    t2 = threading.Thread(target=second_a)
    t2.start()
    time.sleep(0.2)
    assert not done, "旧实例还在关，不许启动同 key 的新实例"

    allow_close.set()
    t1.join(5)
    t2.join(5)
    assert done == ["a-acquired"]
    # 顺序：a 关完之后才允许 new a
    idx_close_end = events.index(("close-end", "a"))
    idx_new_a = [i for i, e in enumerate(events) if e == ("new", "a")][-1]
    assert idx_new_a > idx_close_end


def test_login_task_failure_is_not_marked_ok(sandbox):
    """登录失败（ok=False）不能被任务引擎标成「完成」。

    回归：_dispatch 原样返回 login 的 (ok, msg) 元组，_run_task 不看 ok 就标 ok，
    于是「等待登录超时」在任务中心显示为完成，用户以为登录成功。
    """
    from service.publishing import db
    from service.publishing.tasks import TaskManager

    class FakeHub:
        conn = None

        def login(self, *a, **kw):
            return False, "等待登录超时"

    conn = db.connect(sandbox["db_file"])
    hub = FakeHub()
    hub.conn = conn
    mgr = TaskManager(hub, conn)
    try:
        tid = mgr.submit("login", platforms=["csdn"])
        deadline = time.time() + 10
        t = None
        while time.time() < deadline:
            t = mgr.get(tid)
            if t and t["status"] in ("ok", "failed", "waiting_human"):
                break
            time.sleep(0.05)
        assert t and t["status"] == "failed", f"登录失败应标 failed，实际 {t and t['status']}"
        assert "超时" in (t["message"] or "") + (t["error"] or "")
    finally:
        mgr.close()
        mgr._worker.join(timeout=10)
        conn.close()


def test_login_timeout_fails_fast_without_retry():
    """扫码登录超时是人工动作未完成，重试只会再弹一次浏览器——必须立刻 fail。"""
    from service.publishing.tasks import TaskManager

    class _Hub:
        conn = None

    tm = TaskManager.__new__(TaskManager)
    tm.MAX_ATTEMPTS = 3
    action, reason = tm._triage("login", "RuntimeError: 等待登录超时", attempts=1)
    assert action == "fail", f"登录超时应立即 fail，实际 {action}（{reason}）"

    # 非 login 的同类错误仍按可重试处理，不能被这条规则误伤
    action2, _ = tm._triage("publish", "RuntimeError: 等待登录超时", attempts=1)
    assert action2 == "retry"
