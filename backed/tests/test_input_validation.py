# -*- coding: utf-8 -*-
"""文章输入校验：坏数据不能进库。

回归：实测空标题、纯空格标题、10 万字标题、含 NUL 的标题全部 200 入库。
后果是前端渲染卡死、平台侧提交被拒，而用户不知道是哪个字段的问题。
"""
import pytest


@pytest.mark.parametrize("payload,fragment", [
    ({"title": "", "content_md": "x"}, "不能为空"),
    ({"title": "   ", "content_md": "x"}, "不能为空"),
    ({"title": "标" * 400, "content_md": "x"}, "过长"),
    ({"title": "a\u0000b", "content_md": "x"}, "控制字符"),
    ({"title": "a\u0001b", "content_md": "x"}, "控制字符"),
])
def test_create_rejects_bad_title(client, payload, fragment):
    r = client.post("/api/v1/hub/articles", json=payload)
    assert r.status_code == 422, f"应拒绝但返回 {r.status_code}"
    assert fragment in r.json()["message"]


def test_create_accepts_normal_and_emoji(client):
    for title in ("正常标题", "🚀 发射", "تحت الاختبار"):
        r = client.post("/api/v1/hub/articles", json={"title": title, "content_md": "x"})
        assert r.status_code == 200, f"{title} 被误拒: {r.text[:120]}"


def test_update_rejects_oversized_title(client):
    """更新路径也要校验，否则先建合法再 PUT 成超长可绕过。"""
    aid = client.post("/api/v1/hub/articles",
                      json={"title": "合法", "content_md": "x"}).json()["id"]
    r = client.put(f"/api/v1/hub/articles/{aid}", json={"title": "标" * 400})
    assert r.status_code == 422


def test_oversized_content_rejected(client):
    big = "x" * (3 * 1024 * 1024)
    r = client.post("/api/v1/hub/articles", json={"title": "大", "content_md": big})
    assert r.status_code == 422
    assert "正文" in r.json()["message"]


def test_long_but_legal_title_accepted(client):
    """300 字以内要放行（中文标题偶尔很长，不能一刀切）。"""
    r = client.post("/api/v1/hub/articles",
                    json={"title": "标" * 300, "content_md": "x"})
    assert r.status_code == 200
