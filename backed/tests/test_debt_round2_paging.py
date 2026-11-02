# -*- coding: utf-8 -*-
"""文章/任务分页回归（R4）：列表 API 必须可分页且不被路由参数抢匹配。"""

from service.publishing import db

# ---------------- 文章分页 ----------------

def test_db_article_pagination_and_count(conn):
    for i in range(3):
        db.create_article(conn, f"文{i}", "正文")
    page1 = db.list_articles(conn, limit=2, offset=0)
    page2 = db.list_articles(conn, limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 1
    assert db.count_articles(conn) == 3
    assert db.count_articles(conn, status="draft") == 3


def test_hub_list_accepts_offset(conn):
    from service.publishing.service import Hub

    for i in range(3):
        db.create_article(conn, f"文{i}", "正文")
    # 只验证 Hub.list 的签名与切片行为，不构造完整 Hub（会开浏览器池）
    rows = [dict(r) for r in db.list_articles(conn, limit=2, offset=1)]
    assert len(rows) == 2
    assert Hub.list.__code__.co_varnames[:4] == ("self", "status", "limit", "offset")


def test_task_pagination_and_count(conn):
    for i in range(3):
        db.create_task(conn, f"tk{i}", "publish", article_id=i)
    assert db.count_tasks(conn) >= 3
    first = db.list_tasks(conn, limit=2, offset=0)
    second = db.list_tasks(conn, limit=2, offset=2)
    assert len(first) == 2 and len(second) == 1
    assert first[0]["id"] > first[1]["id"], "任务列表按 id 倒序"


def test_pagination_clamps_bad_input(conn):
    """limit<=0 在 SQLite 里等于不限量，超大 offset 会溢出 INTEGER——都必须被夹住。"""
    for i in range(3):
        db.create_article(conn, f"夹紧{i}", "正文")
    assert len(db.list_articles(conn, limit=-1)) <= 3
    assert len(db.list_articles(conn, limit=0)) == 1
    assert len(db.list_articles(conn, limit=10**9)) <= 3
    assert db.list_articles(conn, offset=10**12) == []


def test_quantile_uses_max_when_in_inf_bucket():
    """分位落进 +Inf 桶时必须用实测 max，不能压到 60s 边界。"""
    from service.publishing import observability as obs
    reg = obs.MetricRegistry()
    reg.observe("slow", 120.0)
    q = reg.snapshot()["histograms"]["slow"]["quantiles"]
    assert q["p99"] == 120.0
    assert q["p50"] == 120.0


def test_get_hub_is_thread_safe(monkeypatch, tmp_path):
    """并发首次访问 get_hub() 只能创建一个 Hub。

    前端首屏并发打 5-6 个端点，多线程各自 new Hub 会在 SQLite 上
    撞 `database is locked`（实测 8 线程可复现），首屏 500。
    """
    import threading

    import api.deps as deps
    from service.publishing.service import Hub

    created = []
    lock = threading.Lock()

    class SlowHub(Hub):
        def __init__(self, *a, **kw):
            with lock:
                created.append(1)
            import time as _t
            _t.sleep(0.05)          # 放大竞态窗口
            super().__init__(*a, **kw)

    monkeypatch.setattr(deps, "_hub_instance", None)
    monkeypatch.setattr("service.publishing.service.Hub", SlowHub)

    barrier = threading.Barrier(8)
    errors = []

    def hit():
        try:
            barrier.wait()
            deps.get_hub()
        except Exception as e:      # aqg: top-level boundary（收集线程内异常带回主断言）
            errors.append(e)

    ts = [threading.Thread(target=hit) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]

    assert not errors, f"并发首次访问抛异常: {errors[:2]}"
    assert len(created) == 1, f"应只创建 1 个 Hub，实际 {len(created)}"
    monkeypatch.setattr(deps, "_hub_instance", None)


def test_status_separates_instance_and_article_counts(conn):
    """`published` 是平台实例数，`published_articles` 是文章数，两者不能混用。

    回归：侧栏「N 篇已发布」曾直接取 `published`，一篇发两个平台会显示 2 篇。
    """
    from service.publishing.service import Hub

    for i in range(3):
        db.create_article(conn, f"文章{i}", "正文")
    db.update_article(conn, 1, status="published")
    db.update_article(conn, 2, status="published")
    # 文章 1 发到两个平台 → 2 条上线实例
    db.upsert_publication(conn, 1, "juejin", status="ok", post_id="j1")
    db.upsert_publication(conn, 1, "csdn", status="ok", post_id="c1")

    hub = Hub.__new__(Hub)          # 只测 status 的 SQL，不起调度器/任务线程
    hub.conn = conn
    hub.accounts = lambda: []
    snap = hub.status()

    assert snap["published"] == 2, "published 应为上线实例数"
    assert snap["published_articles"] == 2, "published_articles 应为已发布文章数"
