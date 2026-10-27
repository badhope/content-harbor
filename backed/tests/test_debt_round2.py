# -*- coding: utf-8 -*-
"""第二轮工程债回归：指标桶语义 / 限流桶生命周期 / 浏览器池关停。

每例都对应一条已确认的运行期隐患，修前必红。
"""

from service.publishing import observability as obs


# ---------------- R1 直方图累积桶 ----------------

def test_histogram_buckets_are_cumulative():
    reg = obs.MetricRegistry()
    for v in (0.3, 1.5, 4.0):
        reg.observe("t", v)
    buckets = reg.snapshot()["histograms"]["t"]["buckets"]
    # Prometheus 语义：le 桶是 <= 上界的累积计数，+Inf 必须等于总次数
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
    from middleware.rate_limiter import InMemoryRateLimiter
    lim = InMemoryRateLimiter(max_buckets=3)
    for i in range(5):
        lim.is_allowed(f"k{i}")
    assert len(lim._buckets) <= 3


def test_rate_limit_evicts_oldest_key():
    from middleware.rate_limiter import InMemoryRateLimiter
    lim = InMemoryRateLimiter(max_buckets=2)
    lim.is_allowed("old")
    lim.is_allowed("newer")
    lim.is_allowed("newest")
    assert "old" not in lim._buckets
    assert "newest" in lim._buckets


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
