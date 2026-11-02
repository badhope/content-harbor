# -*- coding: utf-8 -*-
"""REST 集成：路由 → 真实模块 的接线回归。

单测只锁单个模块；这里把 db / tags / events / TaskManager 换成一个连了
临时库的真桩 Hub，让 /api/hub 的端点真正跑一遍——接线断了（改错参数名、
少传 conn、把函数搬错地方）只有这一层能抓到。
"""

import pytest
from fastapi.testclient import TestClient

from app.factory import create_app
from service.publishing import db, events
from service.publishing.tasks import TaskManager


class StubHub:
    """真连接 + 真 TaskManager，但业务方法全造假——不开浏览器。"""

    def __init__(self, conn):
        self.conn = conn
        self.demo = True
        self.tasks = TaskManager(self, conn)

    def list(self, status=None, limit=100, offset=0, slim=False):
        return [dict(r) for r in db.list_articles(self.conn, status=status,
                                                  limit=limit, offset=offset,
                                                  slim=slim)]

    def count_articles(self, status=None):
        return db.count_articles(self.conn, status=status)

    def create(self, title, **kw):
        return db.create_article(self.conn, title, kw.get("content_md", ""),
                                 **{k: v for k, v in kw.items()
                                    if k != "content_md"})

    def get(self, aid):
        r = db.get_article(self.conn, aid)
        return dict(r) if r else None

    def edit(self, aid, **data):
        ok = db.update_article(self.conn, aid, **data)
        return ok, False

    def search(self, kw):
        return [dict(r) for r in db.search_articles(self.conn, kw)]

    def accounts(self):
        return [dict(r) for r in db.list_accounts(self.conn)]

    def status(self):
        return {"articles": len(db.list_articles(self.conn, limit=999)),
                "tasks": len(self.tasks.list(limit=999))}

    def publish(self, *a, **kw):
        return []

    def update(self, *a, **kw):
        return {}

    def sync_pending(self, *a, **kw):
        return {}

    def refresh(self, *a, **kw):
        return {}

    def login(self, *a, **kw):
        return {"ok": True}

    def assist_open(self, *a, **kw):
        return {"ok": True}


@pytest.fixture
def client(sandbox):
    hub = StubHub(db.connect(sandbox["db_file"]))

    import api.deps as hub_deps
    hub_deps._hub_instance = hub

    with TestClient(create_app()) as c:
        yield c
    hub.tasks.close()
    hub.tasks._worker.join(timeout=10)
    hub.conn.close()


def test_articles_crud_roundtrip(client):
    r = client.post("/api/v1/hub/articles", json={"title": "第一稿", "content_md": "v1"})
    assert r.status_code == 200
    aid = r.json()["id"]
    assert aid > 0

    assert client.get(f"/api/v1/hub/articles/{aid}").json()["title"] == "第一稿"

    assert client.put(f"/api/v1/hub/articles/{aid}",
                      json={"title": "改过的标题"}).json()["ok"] is True
    assert client.get(f"/api/v1/hub/articles/{aid}").json()["title"] == "改过的标题"

    rows = client.get("/api/v1/hub/articles").json()
    assert [x["id"] for x in rows] == [aid]

    assert client.get("/api/v1/hub/articles/search/标题").json()[0]["id"] == aid
    assert client.get("/api/v1/hub/articles/999").status_code == 404


def test_edit_marks_publication_pending(client):
    aid = client.post("/api/v1/hub/articles", json={"title": "T"}).json()["id"]
    hub = _hub()
    db.upsert_publication(hub.conn, aid, "juejin", status="ok", post_id="p1")

    client.put(f"/api/v1/hub/articles/{aid}", json={"content_md": "v2"})
    pubs = client.get("/api/v1/hub/publications", params={"platform": "juejin"}).json()
    assert pubs[0]["status"] == "pending"
    assert pubs[0]["title"] == "T"


def _hub():
    import api.deps as hub_deps
    return hub_deps._hub_instance


def _wait_task(client, tid):
    """轮询任务直到终态，返回终态任务体。容忍短暂 404（submit 在途）。"""
    import time
    for _ in range(200):
        got = client.get(f"/api/v1/hub/tasks/{tid}")
        body = got.json()
        if got.status_code == 200 and body["status"] in ("ok", "failed", "waiting_human"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"任务 {tid} 未在超时前进入终态")


def test_tasks_lifecycle_over_rest(client, monkeypatch):
    monkeypatch.setattr(StubHub, "publish", lambda self, *a, **kw: ["ok"])
    aid = client.post("/api/v1/hub/articles", json={"title": "T"}).json()["id"]
    r = client.post(f"/api/v1/hub/articles/{aid}/publish",
                    json={"platforms": ["juejin"]})
    assert r.status_code == 200
    tid = r.json()["task_id"]
    assert r.json()["poll"] == f"/tasks/{tid}"

    assert _wait_task(client, tid)["status"] == "ok"
    assert client.get("/api/v1/hub/tasks/doesnotexist").status_code == 404
    assert any(t["task_id"] == tid for t in client.get("/api/v1/hub/tasks").json())


def test_task_pagination_endpoint(client):
    """任务列表分页：/tasks/page 给出 total/has_more，且不抢 /tasks/{task_id} 路由。"""
    for i in range(3):
        client.post("/api/v1/hub/articles",
                    json={"title": f"任务分页{i}"})
    from service.publishing import db
    conn = _hub().conn
    for i in range(3):
        db.create_task(conn, f"tkpage{i}", "publish", article_id=1)

    p1 = client.get("/api/v1/hub/tasks/page?limit=2&offset=0").json()
    assert len(p1["items"]) == 2
    assert p1["total"] == 3
    assert p1["has_more"] is True

    p2 = client.get("/api/v1/hub/tasks/page?limit=2&offset=2").json()
    assert len(p2["items"]) == 1
    assert p2["has_more"] is False


def test_article_pagination(client):
    """R4：/articles/page 必须给出 total/has_more，且不被 /articles/{aid} 抢路由。"""
    for i in range(3):
        client.post("/api/v1/hub/articles", json={"title": f"分页{i}"})

    p1 = client.get("/api/v1/hub/articles/page?limit=2&offset=0").json()
    assert len(p1["items"]) == 2
    assert p1["total"] == 3
    assert p1["has_more"] is True

    p2 = client.get("/api/v1/hub/articles/page?limit=2&offset=2").json()
    assert len(p2["items"]) == 1
    assert p2["has_more"] is False


def test_webhooks_rest_crud(client):
    created = client.post("/api/v1/hub/webhooks",
                          json={"url": "https://example.com/hook",
                                "events": ["task.ok"], "secret": "s3cret"})
    assert created.status_code == 200
    wid = created.json()["id"]
    assert wid > 0

    listed = client.get("/api/v1/hub/webhooks").json()
    row = [h for h in listed if h["id"] == wid][0]
    assert row["events"] == ["task.ok"]
    assert row["has_secret"] is True

    assert client.put(f"/api/v1/hub/webhooks/{wid}",
                      json={"url": "https://other"}).json()["ok"] is True
    assert client.put("/api/v1/hub/webhooks/9999",
                      json={"url": "https://x"}).status_code == 404

    assert client.delete(f"/api/v1/hub/webhooks/{wid}").json()["ok"] is True
    assert client.delete(f"/api/v1/hub/webhooks/{wid}").status_code == 404


def test_tags_rest_gateway(client):
    aid = client.post("/api/v1/hub/articles", json={"title": "T", "tags": "python"}).json()["id"]
    assert aid
    assert client.post("/api/v1/hub/tags/sync").json()["synced"] == 1
    assert client.get("/api/v1/hub/tags").json()[0]["tag"] == "Python"
    assert client.get("/api/v1/hub/tags/trending").json()[0]["count"] == 1


def test_notifications_and_gate_and_metrics(client):
    events.emit("task.ok", {"task_id": "rest"})
    got = client.get("/api/v1/hub/notifications").json()
    assert any(n["data"].get("task_id") == "rest" for n in got)

    gate = client.get("/api/v1/hub/ai/gate").json()
    assert "enabled" in gate and "recent_denied" in gate

    snap = client.get("/api/v1/hub/metrics").json()
    assert "hits" in snap or "counters" in snap


def test_pending_human_endpoint(client):
    hub = _hub()
    aid = client.post("/api/v1/hub/articles", json={"title": "待人工"}).json()["id"]
    db.upsert_publication(hub.conn, aid, "juejin", status="pending_human")
    rows = client.get("/api/v1/hub/pending-human").json()
    assert [r["platform"] for r in rows] == ["juejin"]
    assert rows[0]["title"] == "待人工"


def test_platforms_endpoint_lists_adapters(client):
    got = client.get("/api/v1/hub/platforms").json()
    ids = {p["id"] for p in got}
    assert "juejin" in ids and "csdn" in ids
    assert all("needs_browser" in p for p in got)


def test_accounts_endpoint(client):
    assert client.get("/api/v1/hub/accounts").json() == []
    _hub().conn.execute(
        "INSERT INTO accounts (platform, name, profile_dir, status) "
        "VALUES ('juejin','default','/tmp/p','logined')")
    _hub().conn.commit()
    rows = client.get("/api/v1/hub/accounts").json()
    assert rows[0]["platform"] == "juejin"


def test_jobs_endpoint(client):
    hub = _hub()
    db.add_job(hub.conn, "publish", article_id=1)
    rows = client.get("/api/v1/hub/jobs").json()
    assert rows[0]["type"] == "publish"


def test_tasks_list_parses_platforms(client):
    """列表端点必须把 platforms 解析成数组。

    回归：/tasks 与 /tasks/page 直接返回 DB 原始行，platforms 是 JSON 字符串；
    前端 `(row.platforms || []).join()` 会抛 TypeError，整个单元格渲染失败
    ——实测任务表「对象」列空白且后续列错位，状态标签跑到对象列。
    """
    from service.publishing import db
    conn = _hub().conn
    db.create_task(conn, "tkplat0001", "publish", article_id=1,
                   platforms=["juejin", "csdn"])

    rows = client.get("/api/v1/hub/tasks").json()
    row = [r for r in rows if r["task_id"] == "tkplat0001"][0]
    assert row["platforms"] == ["juejin", "csdn"], f"应为数组，实际 {type(row['platforms'])}"

    page = client.get("/api/v1/hub/tasks/page?limit=50").json()
    prow = [r for r in page["items"] if r["task_id"] == "tkplat0001"][0]
    assert prow["platforms"] == ["juejin", "csdn"]
