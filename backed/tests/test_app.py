# -*- coding: utf-8 -*-
"""应用装配 + REST 契约冒烟。

`create_app()` 是唯一装配入口（AGENT.md 铁律：只有 api/hub.py 能挂路由），
这里锁住它的可观测行为，而不是实现细节。
"""

from fastapi.testclient import TestClient

from app.factory import create_app


def test_create_app_assembles():
    app = create_app()
    assert app is not None


def test_openapi_has_full_hub_surface():
    """/api/v1/hub 这几十个端点是对外契约，少了就是装配回归。

    59 是当前真实值（66 个装饰器里有几条路径同名），阈值留 55 的余量：
    真回归会成片掉端点，不会只掉几个。
    """
    app = create_app()
    paths = app.openapi()["paths"]
    hub = [p for p in paths if p.startswith("/api/v1/hub")]
    assert len(hub) >= 55
    for must in ("/api/v1/hub/articles", "/api/v1/hub/tasks",
                 "/api/v1/hub/ai/providers", "/api/v1/hub/qa/analyze",
                 "/api/v1/hub/tags", "/api/v1/hub/notifications"):
        assert must in paths


def test_health():
    with TestClient(create_app()) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_read_only_hub_endpoints():
    """articles / ai/providers / notifications 三条只读链路真实跑通。

    用假 Hub 顶掉真实单例：真实 Hub 会拉起任务线程 + 调度器 + atexit 钩子，
    单测不需要那份代价。
    """
    import api.deps as hub_deps
    hub_deps._hub_instance = _fake_hub()
    with TestClient(create_app()) as client:
        r = client.get("/api/v1/hub/articles")
        assert r.status_code == 200
        articles = r.json()
        assert isinstance(articles, list)
        assert articles[0]["title"] == "冒烟文章"

        r = client.get("/api/v1/hub/ai/providers")
        assert r.status_code == 200

        r = client.get("/api/v1/hub/notifications")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


def _fake_hub():
    from service.publishing import db

    class _FakeHub:
        def __init__(self):
            self.conn = db.connect()

        def list(self, status=None, limit=100, offset=0, slim=False):
            return [dict(r) for r in db.list_articles(self.conn, status=status,
                                                      limit=limit, offset=offset,
                                                      slim=slim)]

        def count_articles(self, status=None):
            return db.count_articles(self.conn, status=status)

        def list_models(self):
            return []

    h = _FakeHub()
    db.create_article(h.conn, "冒烟文章", "# 标题\n\n正文内容。")
    return h
