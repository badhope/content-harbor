# -*- coding: utf-8 -*-
"""数据层回归：schema、迁移幂等、串行化连接、文章/发布 CRUD。

AGENT.md 铁律：SQL 只出现在 db.py。这一层是唯一真源，测它等于测地基。
"""

import threading

from service.publishing import db


def test_connect_creates_schema_and_migrates(sandbox):
    c = db.connect(sandbox["db_file"])
    tables = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for t in ("accounts", "articles", "publications", "jobs", "tasks", "meta"):
        assert t in tables
    # 断言"升到了最新一版"而不是写死数字：每次加迁移都要改测试，等于逼人
    # 把断言当噪音删掉。这里只锁住 migrate() 的结果等于 MIGRATIONS 的顶版。
    latest = max(v for v, _ in db.MIGRATIONS)
    assert c.execute("SELECT value FROM meta WHERE key='schema_version'"
                     ).fetchone()[0] == str(latest)
    c.close()


def test_migrate_is_idempotent(sandbox):
    c = db.connect(sandbox["db_file"])
    latest = max(v for v, _ in db.MIGRATIONS)
    assert db.migrate(c) == latest
    assert db.migrate(c) == latest  # 二次执行不得报 duplicate column
    c.close()


def test_locked_connection_serializes_access(conn):
    """B4 回归：同连接跨线程并发 execute/commit 不得交叠出错。"""
    errors = []

    def worker(n):
        try:
            for i in range(40):
                db.add_job(conn, f"job-{n}", article_id=i)
                conn.execute("UPDATE jobs SET message=? WHERE id=1", (f"m{i}",))
                conn.commit()
        except Exception as e:  # aqg: top-level boundary 收集线程内异常带回主断言
            errors.append(e)

    ts = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert not errors
    assert db.list_jobs(conn, limit=400).__len__() == 240


def test_article_crud(sandbox, conn):
    aid = db.create_article(conn, "标题", "正文", tags="python,sqlite")
    row = db.get_article(conn, aid)
    assert row["title"] == "标题"
    assert dict(row)["status"] == "draft"

    db.update_article(conn, aid, title="新标题")
    assert db.get_article(conn, aid)["title"] == "新标题"

    assert len(db.list_articles(conn, limit=10)) == 1
    assert db.list_articles(conn, status="published") == []
    db.update_article(conn, aid, status="published")
    assert len(db.list_articles(conn, status="published")) == 1

    assert len(db.search_articles(conn, "标题")) == 1
    assert db.search_articles(conn, "不存在的关键词") == []


def test_content_change_marks_publications_pending(sandbox, conn):
    """内容改了 → 已发布实例转 pending，这是原地更新的触发条件。"""
    aid = db.create_article(conn, "A", "v1")
    db.upsert_publication(conn, aid, "juejin", status="ok", post_id="p1")
    db.update_article(conn, aid, content_md="v2")
    rows = db.get_publications(conn, aid)
    assert rows[0]["status"] == "pending"
    assert "待同步" in rows[0]["last_error"]

    pending = db.get_pending_updates(conn)
    assert len(pending) == 1
    assert pending[0]["post_id"] == "p1"


def test_upsert_publication_is_unique_per_account(sandbox, conn):
    aid = db.create_article(conn, "A", "x")
    id1 = db.upsert_publication(conn, aid, "csdn", account="default")
    id2 = db.upsert_publication(conn, aid, "csdn", account="default")
    id3 = db.upsert_publication(conn, aid, "csdn", account="other")
    assert id1 == id2
    assert id3 != id1


def test_prune_jobs_keeps_recent(sandbox, conn):
    """prune_jobs 在 connect() 时清一次，之后调用方显式再调（启动兜底语义）。"""
    for i in range(600):
        db.add_job(conn, "flush", article_id=i)
    db.prune_jobs(conn, keep=500)
    rows = db.list_jobs(conn, limit=1000)
    assert len(rows) == 500
    assert rows[0]["article_id"] == 599   # list_jobs 按 id DESC，最新在前
    assert rows[-1]["article_id"] == 100  # 最旧的 0-99 被清掉


def test_task_crud_and_filters(sandbox, conn):
    db.create_task(conn, "t1", "publish", article_id=1, platforms=["juejin"])
    db.create_task(conn, "t2", "update", article_id=1)
    assert db.get_task(conn, "t1")["platforms"] == '["juejin"]'
    assert len(db.list_tasks(conn, kind="publish")) == 1
    assert len(db.list_tasks(conn, status="pending")) == 2
    db.update_task(conn, "t1", status="ok")
    assert len(db.list_tasks(conn, kind="publish", status="ok")) == 1
    assert db.get_task(conn, "nope") is None


def test_prune_tasks_keeps_waiting_human(sandbox, conn):
    """prune 只清终态，绝不删 pending / waiting_human。

    旧实现用 `status != 'waiting_human'` 做过滤，会把积压的 pending 一起删——
    任务超过 keep 上限时，排在后面的待执行任务静默消失（既不跑也不报错）。
    """
    for i in range(250):
        db.create_task(conn, f"wh{i}", "publish")
    db.update_task(conn, "wh0", status="waiting_human")
    db.prune_tasks(conn, keep=200)
    rows = db.list_tasks(conn, limit=1000)
    # 250 条全是 pending：一条都不该被删
    assert len(rows) == 250, f"pending 被误删: 剩 {len(rows)}"
    assert any(r["task_id"] == "wh0" for r in rows)


def test_prune_tasks_removes_old_terminal_only(sandbox, conn):
    """终态任务超限才清理，且只清最老的。"""
    for i in range(30):
        db.create_task(conn, f"t{i}", "publish")
        db.update_task(conn, f"t{i}", status="ok")
    db.prune_tasks(conn, keep=10)
    rows = db.list_tasks(conn, limit=1000)
    assert len(rows) == 10
    ids = {r["task_id"] for r in rows}
    assert "t29" in ids and "t0" not in ids, "应保留最新的、删最老的"


def test_upsert_account_conflict_updates(sandbox, conn):
    db.upsert_account(conn, "juejin", "default", "/tmp/a", "unknown")
    db.upsert_account(conn, "juejin", "default", "/tmp/a", "logined")
    rows = db.list_accounts(conn)
    assert len(rows) == 1
    assert rows[0]["status"] == "logined"


def test_list_pending_human_joins_title(sandbox, conn):
    aid = db.create_article(conn, "待发布", "x")
    db.upsert_publication(conn, aid, "juejin", status="pending_human")
    db.upsert_publication(conn, aid, "csdn", status="ok")     # 不算待人工
    db.upsert_publication(conn, aid, "zhihu", status="pending")  # 也不算
    rows = [dict(r) for r in db.list_pending_human(conn)]
    assert len(rows) == 1
    assert rows[0]["platform"] == "juejin"
    assert rows[0]["title"] == "待发布"
    assert rows[0]["article_id"] == aid


def test_list_publications_joined_filters(sandbox, conn):
    a1 = db.create_article(conn, "A", "x")
    a2 = db.create_article(conn, "B", "y")
    db.upsert_publication(conn, a1, "juejin")
    db.upsert_publication(conn, a1, "csdn")
    db.upsert_publication(conn, a2, "juejin")

    assert len([dict(r) for r in db.list_publications_joined(conn)]) == 3
    assert len([dict(r) for r in db.list_publications_joined(conn, platform="juejin")]) == 2
    assert len([dict(r) for r in db.list_publications_joined(conn, article_id=a2)]) == 1
    for r in db.list_publications_joined(conn):
        assert dict(r)["title"] in ("A", "B")


def test_list_failed_jobs_filters_by_type_and_status(sandbox, conn):
    db.add_job(conn, "publish", article_id=1)
    db.finish_job(conn, db.list_jobs(conn, limit=10)[0]["id"], True)   # 成功
    db.add_job(conn, "publish", article_id=2)
    db.finish_job(conn, db.list_jobs(conn, limit=10)[0]["id"], False,
                  "命中 AIGC 门禁，需人工确认")                        # 失败
    db.add_job(conn, "update", article_id=2)
    db.finish_job(conn, db.list_jobs(conn, limit=10)[0]["id"], True)   # 成功

    failed = db.list_failed_jobs(conn, type_="publish", limit=50)
    assert len(failed) == 1
    assert failed[0]["article_id"] == 2
    assert "门禁" in failed[0]["message"]


def test_locked_connection_close_is_serialized(sandbox):
    """close() 与 execute 必须互斥（2026-10-14）。

    旧实现 close 不持锁：close 落在并发 executescript 中间 → 底层连接被抽走 →
    Windows 上 sqlite3 直接访问违例（高负载下全量测试偶发整进程崩）。
    """
    c = db.connect(sandbox["db_file"])
    done = threading.Event()
    c._exec_lock.acquire()          # 模拟另一线程正在 execute/executescript
    try:
        t = threading.Thread(target=lambda: (c.close(), done.set()), daemon=True)
        t.start()
        t.join(timeout=0.3)
        assert not done.is_set(), "close 没有等待执行锁（锁纪律缺口回归）"
    finally:
        c._exec_lock.release()
    t.join(timeout=2)
    assert done.is_set(), "释放锁后 close 应当完成"
