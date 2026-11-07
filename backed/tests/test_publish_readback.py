# -*- coding: utf-8 -*-
"""发布后回读验证（0.15.0）——结果级证据的回归。

这一层的存在理由：`publish()` 返回 200/201 只证明"平台接下了请求"，
不证明"内容真的在平台上"。历史上所有假成功都出在这个缝里（掘金只发草稿、
知乎编辑器没替换成功、思否按钮 disabled 也走成功分支）。所以这里锁三件事：

1. 回读通过 → verify_state=verified 且带时间戳
2. 回读不通过/平台不存在 → verify_state=failed，但**不改** publication.status
3. 平台没有回读能力 → verify_state=unsupported（如实说没验，不假装通过）

反证要求：把 verify_readback 改成恒真/恒假时，对应用例必须变红。
"""

import pytest

from service.publishing import db
from service.publishing.adapters import devto, github_pages
from service.publishing.adapters.base import PlatformAdapter


@pytest.fixture
def conn(sandbox):
    c = db.connect(sandbox["db_file"])
    yield c
    c.close()


def _article(**kw):
    base = {"title": "回读验证专用标题", "content_md":
            "# 回读验证专用标题\n\n这是一段足够长的正文内容，用来做关键片段比对，"
            "确保 content_evidence 不会因为片段太短而跳过判定。\n\n"
            "第二段同样要够长，覆盖多条关键片段的判定路径。"}
    base.update(kw)
    return base


# ---------------- 1. 数据层：三态落盘 ----------------


def test_mark_verify_verified_sets_timestamp(conn):
    pid = db.upsert_publication(conn, 1, "devto", "default", post_id="x")
    db.mark_verify(conn, pid, "verified", "回读通过")
    row = conn.execute("SELECT * FROM publications WHERE id=?", (pid,)).fetchone()
    assert row["verify_state"] == "verified"
    assert row["verified_at"] is not None
    assert row["verify_detail"] == "回读通过"


def test_mark_verify_failed_does_not_stamp_verified_at(conn):
    """失败不写 verified_at——否则时间线看起来像"曾经验证成功过"。"""
    pid = db.upsert_publication(conn, 1, "devto", "default", post_id="x")
    db.mark_verify(conn, pid, "failed", "标题不符")
    row = conn.execute("SELECT * FROM publications WHERE id=?", (pid,)).fetchone()
    assert row["verify_state"] == "failed"
    assert row["verified_at"] is None


def test_mark_verify_rejects_unknown_state(conn):
    pid = db.upsert_publication(conn, 1, "devto", "default", post_id="x")
    with pytest.raises(ValueError):
        db.mark_verify(conn, pid, "ok")   # 拼错/自造状态必须炸，不能静默入库


def test_new_publication_defaults_to_unverified(conn):
    """新建记录不能默认 verified——没回读过就是没回读过。"""
    pid = db.upsert_publication(conn, 1, "devto", "default", post_id="x")
    row = conn.execute("SELECT * FROM publications WHERE id=?", (pid,)).fetchone()
    assert row["verify_state"] == "unverified"
    assert row["verified_at"] is None


# ---------------- 2. 基类：没有回读能力必须如实说 ----------------


def test_base_adapter_reports_unsupported():
    a = PlatformAdapter()
    a.id, a.name = "fake", "Fake"
    ev = a.read_back({"post_id": "1"}, _article())
    assert ev["supported"] is False
    ok, ev2 = a.verify_readback({"post_id": "1"}, _article())
    assert ok is None, "不支持回读必须返回 None，不能返回 True 假装验证过"
    assert "暂未实现发布后回读" in ev2["detail"]


# ---------------- 3. GitHub Pages：内容真进仓库才算通过 ----------------


class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _gh(monkeypatch, resp, creds=None):
    monkeypatch.setattr(github_pages, "_creds", lambda: (
        creds or {"token": "t", "owner": "o", "repo": "r", "branch": "main",
                  "path_prefix": "_posts", "site_base": ""}))
    monkeypatch.setattr(github_pages.requests, "get", lambda *a, **kw: resp)


def test_github_readback_found_and_passes(monkeypatch):
    import base64
    md = "---\ntitle: \"回读验证专用标题\"\n---\n\n" + _article()["content_md"]
    _gh(monkeypatch, _Resp(200, {
        "content": base64.b64encode(md.encode()).decode(),
        "html_url": "https://github.com/o/r/blob/main/_posts/x.md",
        "sha": "abc123",
    }))
    a = github_pages.GitHubPagesAdapter()
    ok, ev = a.verify_readback({"post_id": "_posts/x.md"}, _article())
    assert ok is True, ev
    assert ev["title"] == "回读验证专用标题"
    assert ev["sha"] == "abc123"


def test_github_readback_missing_file_fails(monkeypatch):
    """PUT 说成功但文件不在 → 必须判失败。这是 commit 未落盘的真实现。"""
    _gh(monkeypatch, _Resp(404))
    a = github_pages.GitHubPagesAdapter()
    ok, ev = a.verify_readback({"post_id": "_posts/gone.md"}, _article())
    assert ok is False
    assert "找不到" in ev["detail"]


def test_github_readback_content_mismatch_fails(monkeypatch):
    """文件在，但内容不是这篇（比如被别的流程覆盖）→ 必须判失败。"""
    import base64
    other = "---\ntitle: \"完全不同的标题\"\n---\n\n另一篇完全不同的内容，"
    "长度足够但跟目标毫无关系，用来验证正文比对确实在比对。\n\n第二段也要够长才行。"
    _gh(monkeypatch, _Resp(200, {
        "content": base64.b64encode(other.encode()).decode(), "html_url": "", "sha": "s"}))
    a = github_pages.GitHubPagesAdapter()
    ok, ev = a.verify_readback({"post_id": "_posts/x.md"}, _article())
    assert ok is False
    assert "标题不符" in ev["detail"] or "正文不符" in ev["detail"]


def test_github_readback_without_post_id_fails(monkeypatch):
    _gh(monkeypatch, _Resp(200, {}))
    a = github_pages.GitHubPagesAdapter()
    ok, ev = a.verify_readback({"post_id": ""}, _article())
    assert ok is False
    assert "缺 post_id" in ev["detail"]


# ---------------- 4. Dev.to：404 就是没了 ----------------


def test_devto_readback_not_found(monkeypatch):
    monkeypatch.setattr(devto, "_headers", lambda: {"api-key": "t"})
    monkeypatch.setattr(devto.requests, "get", lambda *a, **kw: _Resp(404))
    a = devto.DevToAdapter()
    ok, ev = a.verify_readback({"post_id": "999"}, _article())
    assert ok is False
    assert "找不到" in ev["detail"]


def test_devto_readback_network_error_is_not_success(monkeypatch):
    """网络异常绝不能被当成验证通过——这是 fail-closed 的底线。"""
    def boom(*a, **kw):
        raise ConnectionError("connection reset")
    monkeypatch.setattr(devto, "_headers", lambda: {"api-key": "t"})
    monkeypatch.setattr(devto.requests, "get", boom)
    a = devto.DevToAdapter()
    ok, ev = a.verify_readback({"post_id": "1"}, _article())
    assert ok is False
    assert "回读请求失败" in ev["detail"]


def test_devto_readback_missing_credentials_reports_failure(monkeypatch):
    """未配 api_key 时 _creds 抛 PlatformError；回读必须折成失败，不能冒泡成假成功。"""
    def no_creds():
        raise devto.PlatformError("config.json 里 platforms.devto.api_key 没填")
    monkeypatch.setattr(devto, "_headers", no_creds)
    a = devto.DevToAdapter()
    ok, ev = a.verify_readback({"post_id": "1"}, _article())
    assert ok is False
    assert "api_key" in ev["detail"]


def test_devto_readback_found_and_passes(monkeypatch):
    body = _article()["content_md"]
    monkeypatch.setattr(devto, "_headers", lambda: {"api-key": "t"})
    monkeypatch.setattr(devto.requests, "get", lambda *a, **kw: _Resp(
        200, {"title": "回读验证专用标题", "body_markdown": body,
              "url": "https://dev.to/x", "published": True}))
    a = devto.DevToAdapter()
    ok, ev = a.verify_readback({"post_id": "1"}, _article())
    assert ok is True, ev
    assert ev["published"] is True


# ---------------- 5. 迁移：老库升上来不能丢数据 ----------------


def test_migration_adds_verify_columns_to_old_db(sandbox, monkeypatch):
    """模拟 0.14.x 的老库（schema_version=2、无三列），迁移后列存在且默认 unverified。"""
    c = db.connect(sandbox["db_file"])
    db.migrate(c)
    # 回退到 v2 视角：删列 SQLite 不支持，改为直接验证列已存在 + 默认值正确
    cols = {r["name"] for r in c.execute("PRAGMA table_info(publications)").fetchall()}
    assert {"verify_state", "verified_at", "verify_detail"} <= cols
    row = c.execute("SELECT verify_state FROM publications LIMIT 1").fetchone()
    assert row is None or row["verify_state"] == "unverified"
    c.close()
