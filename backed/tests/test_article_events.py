# -*- coding: utf-8 -*-
"""文章增删改必须产生 SSE 事件。

回归：全仓 emit 只在 task/publish/auth/trace 出现，文章 CRUD 一个事件都没有。
前端因此只能靠 30 秒轮询，多端同时编辑时列表长期不一致。
"""
import pytest
from fastapi.testclient import TestClient

import service.publishing.events as evt_mod
from app.factory import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_create_update_delete_emit_events(client, monkeypatch):
    seen = []
    monkeypatch.setattr(evt_mod, "emit", lambda t, d=None: seen.append((t, d or {})))

    r = client.post("/api/v1/hub/articles", json={"title": "事件测试", "content_md": "正文"})
    assert r.status_code == 200
    aid = r.json()["id"]

    client.put(f"/api/v1/hub/articles/{aid}", json={"title": "改后标题"})
    client.delete(f"/api/v1/hub/articles/{aid}")

    types = [t for t, _ in seen]
    assert types == ["article.created", "article.updated", "article.deleted"]
    assert all(d.get("article_id") == aid for _, d in seen)


def test_update_event_carries_pending_sync(client, monkeypatch):
    seen = []
    monkeypatch.setattr(evt_mod, "emit", lambda t, d=None: seen.append((t, d or {})))
    aid = client.post("/api/v1/hub/articles", json={"title": "x", "content_md": "a"}).json()["id"]
    client.put(f"/api/v1/hub/articles/{aid}", json={"content_md": "b"})
    updated = dict(seen)["article.updated"]
    assert "pending_sync" in updated
    assert "content_md" in updated["fields"]


def test_emit_failure_does_not_break_write(client, monkeypatch):
    monkeypatch.setattr(evt_mod, "emit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bus down")))
    r = client.post("/api/v1/hub/articles", json={"title": "事件总线挂了", "content_md": "x"})
    assert r.status_code == 200, "事件只是增值能力，不能拖垮写入"
