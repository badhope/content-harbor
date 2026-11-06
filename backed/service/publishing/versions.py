# -*- coding: utf-8 -*-
"""文章版本管理：每次 article 更新前自动快照旧内容进 version_history 表。

提供"时光机"能力：误改 / AI 改写坏了一键回滚到任意历史版本。
不引入 git 那种重工具——SQLite 里存 diff 就够。

触发时机：Hub.edit() 改文章之前调 capture() 存一份旧快照。
AI 改写 / 润色前也各自 capture 一份——人审闸门的第一道：让你能对比前后差异。
"""

import hashlib
import time

from service.publishing import db


VERSION_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS version_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id   INTEGER NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    content_md   TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT '',
    tags         TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',   -- sha256，diff 时去重
    change_kind  TEXT NOT NULL DEFAULT 'edit',  -- edit/rollback/ai_rewrite/ai_polish/import
    change_note  TEXT NOT NULL DEFAULT '',   -- 变更说明（"AI 改写: 改成更口语"）
    created_at   REAL,
    created_by   TEXT DEFAULT ''              -- human/ai/system
);
CREATE INDEX IF NOT EXISTS idx_ver_article ON version_history(article_id, id DESC);
"""


def _ensure_table(conn):
    try:
        conn.executescript(VERSION_TABLE_DDL)
        conn.commit()
    except Exception:
        pass


def _md_hash(title, content_md, summary):
    return hashlib.sha256(
        f"{title}\n{content_md}\n{summary}".encode("utf-8")).hexdigest()


# 每篇文章保留的版本数。30 对"回滚误改"够用；再多也翻不到，
# 却要一直占着全文副本（每版含完整 content_md）。
VERSION_KEEP = 30


def capture(conn, article, change_kind="edit", change_note="", created_by="human"):
    """在修改 article 之前快照一份旧版本。

    article 是 db.get_article 返回的 Row（dict-like）。
    如果旧版本内容_hash 与最近一条历史完全相同（幂等编辑），跳过以节省空间。
    """
    _ensure_table(conn)
    h = _md_hash(
        article.get("title", ""),
        article.get("content_md", ""),
        article.get("summary", ""),
    )
    # 去重：跟最新版本比，相同就不存
    last = conn.execute(
        "SELECT content_hash FROM version_history WHERE article_id=? ORDER BY id DESC LIMIT 1",
        (article["id"],)).fetchone()
    if last and last["content_hash"] == h:
        return None  # 内容没变，跳过

    cur = conn.execute(
        """INSERT INTO version_history
           (article_id, title, content_md, summary, tags, content_hash,
            change_kind, change_note, created_at, created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (article["id"], article.get("title", ""), article.get("content_md", ""),
         article.get("summary", ""), article.get("tags", ""),
         h, change_kind, change_note, time.time(), created_by))
    conn.commit()
    # 捕获后立即裁剪：prune_versions 此前只有测试在调，生产代码从不调——
    # 实测连续编辑 40 次留下 40 个完整正文副本，长期使用会让库无限膨胀。
    # 每次捕获都是"刚新增一条"，正是裁剪的最佳时机（摊销成本 O(1) 次删除）。
    prune_versions(conn, article["id"], keep=VERSION_KEEP)
    return cur.lastrowid


def list_versions(conn, article_id, limit=50):
    """列出文章的全部版本历史（最新在前）。"""
    _ensure_table(conn)
    rows = conn.execute(
        "SELECT * FROM version_history WHERE article_id=? ORDER BY id DESC LIMIT ?",
        (article_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_version(conn, version_id):
    """取单个版本的完整内容。"""
    _ensure_table(conn)
    r = conn.execute("SELECT * FROM version_history WHERE id=?", (version_id,)).fetchone()
    return dict(r) if r else None


def rollback(conn, version_id):
    """把文章回滚到某个版本。返回 (ok, message)。

    回滚本身也会产生一条新的 version_history 记录（change_kind=rollback），
    所以回滚是可逆的——"回滚的回滚"就是恢复。
    """
    _ensure_table(conn)
    v = conn.execute("SELECT * FROM version_history WHERE id=?", (version_id,)).fetchone()
    if not v:
        return False, "版本不存在"
    # 当前内容先快照（防误操作）
    art = db.get_article(conn, v["article_id"])
    if art:
        art = dict(art)
        # F-27：目标版本与当前内容逐字相同 → 显式短路，不写历史。
        # 旧行为照常走两处 capture，均被内容哈希去重跳过，返回值却说"已回滚"，
        # pre-rollback / rollback 两条记录都写不进去，语义含混。
        # 注意：比对必须含 tags——回滚本体会 restore tags，只改了标签时
        # 不是 no-op（_md_hash 不含 tags，所以在这里单独比）。
        h_cur = _md_hash(art.get("title", ""), art.get("content_md", ""),
                         art.get("summary", ""))
        h_ver = _md_hash(v["title"], v["content_md"], v["summary"])
        if h_cur == h_ver and (art.get("tags") or "") == (v["tags"] or ""):
            return True, f"当前内容与版本 {version_id} 相同，无需回滚"
        capture(conn, art, change_kind="pre-rollback",
                change_note=f"回滚前快照（version_id={version_id}）", created_by="system")
    # 执行回滚
    db.update_article(conn, v["article_id"],
                      title=v["title"], content_md=v["content_md"],
                      summary=v["summary"], tags=v["tags"])
    # 记录这次回滚。必须存**回滚后的内容**（dict(v)）而不是 dict(art)：
    # art 是回滚前的内容，上一步已经以 pre-rollback 存过，content_hash 与
    # 最新一条相同会被 capture 的去重逻辑跳过，于是 change_kind='rollback'
    # 这条记录永远写不进去——回滚在历史里就看不到了。
    capture(conn, dict(v), change_kind="rollback",
            change_note=f"回滚到版本 {version_id}", created_by="human")
    return True, f"已回滚到版本 {version_id}"


def diff_versions(conn, v1_id, v2_id):
    """对比两个版本的 content_md，返回行级 unified diff。"""
    import difflib
    _ensure_table(conn)
    v1 = get_version(conn, v1_id)
    v2 = get_version(conn, v2_id)
    if not v1 or not v2:
        return {"error": "版本不存在"}
    lines1 = v1["content_md"].splitlines(keepends=True)
    lines2 = v2["content_md"].splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        lines1, lines2,
        fromfile=f"v{v1['id']}",
        tofile=f"v{v2['id']}",
        lineterm="",
    ))
    return {
        "v1": {"id": v1["id"], "change_kind": v1["change_kind"],
               "created_at": v1["created_at"], "title": v1["title"]},
        "v2": {"id": v2["id"], "change_kind": v2["change_kind"],
               "created_at": v2["created_at"], "title": v2["title"]},
        "diff": diff,  # unified diff 文本行列表
        "has_changes": len(diff) > 0,
    }


def prune_versions(conn, article_id, keep=30):
    """只保留一篇文章最近 keep 个版本，超出的按 id 删除（最老的先删）。"""
    _ensure_table(conn)
    try:
        conn.execute(
            """DELETE FROM version_history WHERE article_id=? AND id NOT IN
               (SELECT id FROM version_history WHERE article_id=?
                ORDER BY id DESC LIMIT ?)""",
            (article_id, article_id, keep))
        conn.commit()
    except Exception:
        pass


def init_versions(conn):
    """工厂函数：确保表存在。在 Hub.__init__ 调用一次即可。"""
    _ensure_table(conn)
