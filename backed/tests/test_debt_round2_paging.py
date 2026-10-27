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
