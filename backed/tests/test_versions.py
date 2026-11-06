# -*- coding: utf-8 -*-
"""文章版本历史回归：快照去重、回滚、行级 diff、裁剪。"""

from service.publishing import db, versions


def _article(conn, title, content, summary=""):
    aid = db.create_article(conn, title, content, summary=summary)
    return db.get_article(conn, aid)


def test_capture_and_list(conn):
    art = _article(conn, "T1", "v1")
    vid = versions.capture(conn, dict(art), change_kind="edit")
    v = versions.get_version(conn, vid)
    assert v["title"] == "T1"
    assert v["content_md"] == "v1"
    assert v["change_kind"] == "edit"
    assert len(versions.list_versions(conn, art["id"])) == 1


def test_capture_dedups_identical_content(conn):
    art = _article(conn, "T1", "same")
    first = versions.capture(conn, dict(art))
    second = versions.capture(conn, dict(art))
    assert first is not None
    assert second is None           # 内容没变，不存重复快照
    assert len(versions.list_versions(conn, art["id"])) == 1


def test_capture_records_new_version_on_change(conn):
    art = _article(conn, "T1", "v1")
    versions.capture(conn, dict(art))
    db.update_article(conn, art["id"], content_md="v2")
    art2 = db.get_article(conn, art["id"])
    versions.capture(conn, dict(art2))
    rows = versions.list_versions(conn, art["id"])
    assert [r["content_md"] for r in rows] == ["v2", "v1"]   # 最新在前


def test_rollback_restores_content_and_is_auditable(conn):
    art = _article(conn, "T1", "v1")
    vid = versions.capture(conn, dict(art), change_kind="edit")
    db.update_article(conn, art["id"], content_md="v2", title="T2")

    ok, msg = versions.rollback(conn, vid)
    assert ok is True
    assert str(vid) in msg
    now = db.get_article(conn, art["id"])
    assert now["content_md"] == "v1"
    assert now["title"] == "T1"

    # 回滚也要留痕：pre-rollback 快照 + rollback 记录
    kinds = [r["change_kind"] for r in versions.list_versions(conn, art["id"])]
    assert "pre-rollback" in kinds
    assert "rollback" in kinds


def test_rollback_lets_you_undo_the_rollback(conn):
    """回滚的回滚：v2 内容在回滚后仍留在历史里，能再回滚回去。"""
    art = _article(conn, "T", "v1")
    versions.capture(conn, dict(art))
    db.update_article(conn, art["id"], content_md="v2")
    art2 = db.get_article(conn, art["id"])
    vid2 = versions.capture(conn, dict(art2))

    versions.rollback(conn, vid2)          # 现在回到 v2
    assert db.get_article(conn, art["id"])["content_md"] == "v2"

    v1 = versions.list_versions(conn, art["id"])[-1]
    versions.rollback(conn, v1["id"])
    assert db.get_article(conn, art["id"])["content_md"] == "v1"


def test_rollback_missing_version(conn):
    ok, msg = versions.rollback(conn, 9999)
    assert ok is False
    assert msg == "版本不存在"


def test_diff_versions_reports_changed_lines(conn):
    art = _article(conn, "T", "line1\nline2\nline3")
    a = versions.capture(conn, dict(art))
    db.update_article(conn, art["id"], content_md="line1\nCHANGED\nline3")
    art2 = db.get_article(conn, art["id"])
    b = versions.capture(conn, dict(art2))

    out = versions.diff_versions(conn, a, b)
    assert out["has_changes"] is True
    # 验证 unified diff 包含删除的旧行和添加的新行
    diff_text = "\n".join(out["diff"])
    assert "-line2" in diff_text and "+CHANGED" in diff_text


def test_diff_versions_missing_returns_error(conn):
    art = _article(conn, "T", "x")
    a = versions.capture(conn, dict(art))
    out = versions.diff_versions(conn, a, 9999)
    assert out["error"] == "版本不存在"


def test_prune_versions_keeps_recent(conn):
    art = _article(conn, "T", "v0")
    versions.capture(conn, dict(art))
    for i in range(1, 40):
        db.update_article(conn, art["id"], content_md=f"v{i}")
        versions.capture(conn, dict(db.get_article(conn, art["id"])))
    versions.prune_versions(conn, art["id"], keep=30)
    rows = versions.list_versions(conn, art["id"], limit=100)
    assert len(rows) == 30
    assert rows[0]["content_md"] == "v39"   # 最新保留


def test_capture_auto_prunes_old_versions(conn):
    """捕获版本后必须自动裁剪。

    回归：prune_versions 只有测试在调，生产代码从不调——实测连续编辑 40 次
    留下 40 个完整正文副本，长期使用会让数据库无限膨胀。
    """
    from service.publishing import db as db_mod, versions

    aid = db_mod.create_article(conn, "裁剪测试", "v0")
    for i in range(1, 36):
        art = dict(conn.execute(
            "SELECT * FROM articles WHERE id=?", (aid,)).fetchone())
        versions.capture(conn, art, change_kind="edit")
        db_mod.update_article(conn, aid, content_md=f"v{i}")

    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM version_history WHERE article_id=?", (aid,)).fetchone()
    assert rows["n"] <= versions.VERSION_KEEP, f"版本未裁剪: {rows['n']}"


def test_newest_version_survives_pruning(conn):
    """裁剪只能删最老的，最新一条必须留着（否则回滚到最近状态就没了）。"""
    from service.publishing import db as db_mod, versions

    aid = db_mod.create_article(conn, "顺序测试", "v0")
    for i in range(1, 36):
        art = dict(conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone())
        versions.capture(conn, art, change_kind="edit")
        db_mod.update_article(conn, aid, content_md=f"v{i}")

    latest = versions.list_versions(conn, aid, limit=1)[0]
    assert latest["content_md"] == "v34", f"最新版本被误删或内容错: {latest['content_md']}"
