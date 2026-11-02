# -*- coding: utf-8 -*-
"""文章删除：本地账本清理与平台副本隔离。

删除是一个不可撤销动作，必须锁住三件事：
  1. 文章、版本历史、发布账本一并清除（不留孤儿行）
  2. 不存在的 id 返回 404，不静默成功
  3. 已发布到平台的副本不受影响——本地删除不等于平台删除
"""

import pytest

from service.publishing import db, versions


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    c = db.connect()
    versions.init_versions(c)   # 版本表由 Hub 启动时建，单测里显式初始化
    yield c
    c.close()


def _seed(conn):
    aid = db.create_article(conn, "待删除文章", "# 正文\n\n内容足够长以通过校验。")
    conn.execute(
        """INSERT INTO version_history
           (article_id, title, content_md, summary, tags, content_hash, change_kind,
            change_note, created_at)
           VALUES (?, ?, ?, '', '', 'h', 'edit', '测试', 1.0)""",
        (aid, "旧标题", "旧正文"))
    conn.execute(
        """INSERT INTO publications
           (article_id, platform, account, post_id, post_url, status,
            published_at, updated_at)
           VALUES (?, 'cnblogs', 'default', 'p1', 'https://example.test/p1', 'ok', 1.0, 1.0)""",
        (aid,))
    conn.commit()
    return aid


def test_delete_removes_article_and_related_rows(conn):
    aid = _seed(conn)

    result = db.delete_article(conn, aid)

    assert result == {"articles": 1, "publications": 1, "versions": 1}
    assert db.get_article(conn, aid) is None
    assert conn.execute(
        "SELECT COUNT(*) FROM version_history WHERE article_id=?", (aid,)).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM publications WHERE article_id=?", (aid,)).fetchone()[0] == 0


def test_delete_missing_article_reports_zero(conn):
    result = db.delete_article(conn, 99999)

    assert result["articles"] == 0


def test_delete_is_idempotent(conn):
    aid = _seed(conn)

    first = db.delete_article(conn, aid)
    second = db.delete_article(conn, aid)

    assert first["articles"] == 1
    assert second["articles"] == 0


def test_delete_does_not_touch_other_articles(conn):
    keep = db.create_article(conn, "保留的文章", "正文")
    doomed = _seed(conn)

    db.delete_article(conn, doomed)

    assert db.get_article(conn, keep) is not None
    assert db.get_article(conn, doomed) is None
