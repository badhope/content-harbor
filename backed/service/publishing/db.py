# -*- coding: utf-8 -*-
"""数据层：SQLite 单文件，AI 管的就是这几张表。

四张核心表：
  accounts     平台账号（一个平台一个内置浏览器 profile）
  articles     文章主库（唯一真源，AI 写/改的都是它）
  publications 发布实例（一篇文章 × 一个平台 = 一条，存 post_id / edit_url，原地更新靠它）
  jobs         任务流水（谁在什么时候发了什么，失败原因可查）
"""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

def data_root() -> Path:
    """数据根目录：优先 HUB_DATA_DIR（Tauri/Docker 传入持久目录）。"""
    env = os.environ.get("HUB_DATA_DIR", "").strip()
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path(__file__).resolve().parent.parent / "data"


def config_file() -> Path:
    """运行配置路径：与数据库/媒体/登录态共用同一持久数据根。

    显式 HARBOUR_CONFIG_FILE 优先；否则固定使用数据根内的 config.json。
    旧版 backed/config.json 只由 ``legacy_config_file`` 提供只读回退，
    避免"新路径不存在就永远写回旧路径"的粘滞行为。
    """
    configured = os.environ.get("HARBOUR_CONFIG_FILE", "").strip()
    if configured:
        path = Path(configured)
    elif "HUB_DATA_DIR" in os.environ:
        path = data_root() / "config.json"
    else:
        return legacy_config_file()
    legacy = legacy_config_file()
    if path != legacy and not path.exists() and legacy.is_file():
        import shutil
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with legacy.open("rb") as source, path.open("xb") as target:
                shutil.copyfileobj(source, target)
        except FileExistsError:
            pass
    return path


def legacy_config_file() -> Path:
    """旧版配置位置（只读迁移来源）。"""
    return Path(__file__).resolve().parents[2] / "config.json"


def load_config_dict() -> dict:
    """Read runtime config, falling back to the legacy file without writing it."""
    for path in (config_file(), legacy_config_file()):
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            return data if isinstance(data, dict) else {}
    return {}


DB_PATH = data_root() / "hub.db"


class LockedConnection(sqlite3.Connection):
    """体检 B4 修复（QA 标质力 2026-09-21）：单连接跨线程共享必须串行化。

    check_same_thread=False 只是关掉检查；同一连接被 FastAPI 线程池端点、
    登录后台线程、发布主流程并发使用时，execute/commit 会交错——
    轻则 "cannot start a transaction within a transaction"，重则 commit
    吞掉别人半截事务。WAL/busy_timeout 只解决**跨连接**锁，管不了同连接竞态。

    方案（最小侵入）：自定义 factory，execute/commit/rollback 套 RLock 串行化。
    局限（如实说明）：跨多条语句的事务原子性不保证（调用点是"一条语句+commit"
    的模式，此局限不触发）；要严格事务请用 with conn: 包住多语句。
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._exec_lock = threading.RLock()

    def execute(self, sql, parameters=()):
        with self._exec_lock:
            return super().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        with self._exec_lock:
            return super().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script):
        with self._exec_lock:
            return super().executescript(sql_script)

    def commit(self):
        with self._exec_lock:
            return super().commit()

    def rollback(self):
        with self._exec_lock:
            return super().rollback()

    def close(self):
        """close 同样要串行化（2026-10-14）。

        close 若不持锁、落在别的线程并发 execute/executescript 中间，底层连接
        被抽走 → CPython sqlite3 在 Windows 上直接访问违例（高负载下概率上升，
        实测全量测试偶发整进程崩）。close 本身幂等，重复调用安全。
        """
        with self._exec_lock:
            return super().close()

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    platform     TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT 'default',
    profile_dir  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'unknown',   -- unknown/logined/offline
    last_check   REAL,
    UNIQUE(platform, name)
);

CREATE TABLE IF NOT EXISTS articles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    content_md  TEXT NOT NULL DEFAULT '',
    summary     TEXT DEFAULT '',
    tags        TEXT DEFAULT '',        -- 逗号分隔
    cover       TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'draft',  -- draft/review/published/archived
    source      TEXT DEFAULT 'human',   -- human/ai/import
    ai_model    TEXT DEFAULT '',
    origin_url  TEXT DEFAULT '',        -- 导入来源
    ext         TEXT DEFAULT '{}',      -- JSON 扩展字段
    created_at  REAL,
    updated_at  REAL
);

CREATE TABLE IF NOT EXISTS publications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id  INTEGER NOT NULL,
    platform    TEXT NOT NULL,
    account     TEXT NOT NULL DEFAULT 'default',
    post_id     TEXT DEFAULT '',        -- 平台文章 ID，原地更新的钥匙
    post_url    TEXT DEFAULT '',
    edit_url    TEXT DEFAULT '',        -- 编辑页地址，从列表页抓的，不猜
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending/ok/failed
    draft_only  INTEGER DEFAULT 1,      -- 是否只到草稿
    stats       TEXT DEFAULT '{}',      -- 阅读/点赞等 JSON
    last_error  TEXT DEFAULT '',
    content_hash TEXT DEFAULT '',      -- 上次发布内容的 sha256，update 前比对用
    published_at REAL,
    updated_at  REAL,
    UNIQUE(article_id, platform, account)
);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,          -- publish/update/sync/import
    article_id  INTEGER,
    platform    TEXT,
    status      TEXT NOT NULL DEFAULT 'running',  -- running/ok/failed
    message     TEXT DEFAULT '',
    created_at  REAL,
    finished_at REAL
);

CREATE INDEX IF NOT EXISTS idx_pub_article ON publications(article_id);
CREATE INDEX IF NOT EXISTS idx_pub_platform ON publications(platform);
-- 2026-09-22 存量体检补齐：articles/jobs 原本零索引，列表过滤与门禁追溯全表扫
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_updated ON articles(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_type_status ON jobs(type, status);

-- schema 版本表：重建不删数据，迁移靠 meta.schema_version + MIGRATIONS 增量
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 统一任务引擎表（重构第一刀：替代 LangGraph 双库 + 三个内存任务字典）
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL UNIQUE,     -- 对外暴露的短 id（uuid hex[:10]）
    kind        TEXT NOT NULL,            -- publish/update/sync/refresh/login/assist
    article_id  INTEGER,
    platforms   TEXT DEFAULT '[]',        -- JSON 数组
    account     TEXT DEFAULT 'default',
    draft_only  INTEGER DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending/running/ok/failed/waiting_human
    message     TEXT DEFAULT '',
    result      TEXT DEFAULT '{}',        -- JSON
    error       TEXT DEFAULT '',
    attempts    INTEGER DEFAULT 0,
    created_at  REAL,
    updated_at  REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
"""

# 全文检索：SQLite 自带 FTS5（trigram 分词，中文按 3 字滑窗，无需外部分词器）。
# 之前 search_articles 用 `LIKE '%kw%'` 全表扫 + 无法走索引，文章上千篇后
# 每次搜索都是 O(n) 的整表读。FTS5 把检索变成倒排索引查询，并能按相关度排序。
# 外部内容表（content='articles'）不重复存正文，只存索引。
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title, content_md, summary,
    content='articles', content_rowid='id',
    tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, content_md, summary)
    VALUES (new.id, new.title, new.content_md, new.summary);
END;
CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, content_md, summary)
    VALUES ('delete', old.id, old.title, old.content_md, old.summary);
END;
CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, content_md, summary)
    VALUES ('delete', old.id, old.title, old.content_md, old.summary);
    INSERT INTO articles_fts(rowid, title, content_md, summary)
    VALUES (new.id, new.title, new.content_md, new.summary);
END;
"""

# 列表投影：列表页只需要元信息，正文（可能几十 KB/篇）不参与列表查询。
# 100 篇 × 20KB 正文 = 2MB 无谓序列化 + JSON 传输，列表接口因此慢且吃内存。
LIST_COLUMNS = ("id, title, summary, tags, cover, status, source, ai_model, "
                "origin_url, created_at, updated_at, length(content_md) AS chars")

# 增量迁移：按版本号从小到大执行；已执行过的版本跳过。
# 每项是 (版本号, 需要执行的 SQL 列表)。迁移只增不改，禁止删除已有列。
MIGRATIONS = [
    # v1: 无——tasks/meta 表随 SCHEMA 创建，这里留占位以便后续版本对齐
    (1, []),
    # v2: publications 加 content_hash（update 前内容比对，没变不空发）
    (2, ["ALTER TABLE publications ADD COLUMN content_hash TEXT DEFAULT ''"]),
]


def _schema_version(conn):
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row["value"]) if row else 0
    except Exception:
        return 0


def _has_column(conn, table, column):
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r["name"] == column for r in rows)
    except Exception:
        return False


def migrate(conn):
    """把库从当前 schema_version 逐步升到最新。幂等：已执行的版本跳过。

    兼容新库（SCHEMA 已含新列）与旧库（需 ALTER）：执行迁移前先检查
    列是否存在，避免对新建表重复 ADD COLUMN 报 duplicate column。
    """
    v = _schema_version(conn)
    for ver, sqls in sorted(MIGRATIONS):
        if ver <= v:
            continue
        for sql in sqls:
            # ALTER TABLE ... ADD COLUMN 的幂等保护：列已存在则跳过
            if sql.lstrip().upper().startswith("ALTER TABLE") and "ADD COLUMN" in sql.upper():
                try:
                    table = sql.split("ADD COLUMN", 1)[0].replace("ALTER TABLE", "").strip()
                    column = sql.split("ADD COLUMN", 1)[1].split()[0].strip('"`')
                    if _has_column(conn, table, column):
                        continue
                except Exception:
                    pass
            conn.execute(sql)
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(ver),))
        conn.commit()
    return _schema_version(conn)


def connect(db_path=None):
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False：FastAPI 把同步端点丢进线程池执行，
    # 连接却建在主线程，不开这个开关会报 "created in a thread can only be used in that same thread"
    # factory=LockedConnection：同连接并发 execute/commit 串行化（体检 B4）
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30,
                           factory=LockedConnection)
    conn.row_factory = sqlite3.Row
    # WAL：读写不互斥；busy_timeout：多线程同时写时等待而不是立刻报 database is locked
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    # 建表后再跑增量迁移（v2+ 列变更），migrate 幂等，多开无副作用
    migrate(conn)
    _ensure_fts(conn)
    prune_jobs(conn, keep=500)
    return conn


def _ensure_fts(conn):
    """建 FTS5 索引并做一次性回填。FTS5 不可用时静默降级到 LIKE 检索。"""
    try:
        conn.executescript(FTS_SCHEMA)
        conn.commit()
    except Exception:
        return False
    try:
        # 回填：外部内容表的索引不会自动包含建表前已存在的行。
        # 用 meta 标记只做一次（同时覆盖"索引被手动删掉"的自愈场景）。
        done = conn.execute(
            "SELECT value FROM meta WHERE key='fts_backfilled'").fetchone()
        count = conn.execute("SELECT COUNT(*) FROM articles_fts").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        if count != total or not done:
            conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
            conn.execute("INSERT INTO meta(key,value) VALUES('fts_backfilled','1') "
                         "ON CONFLICT(key) DO UPDATE SET value='1'")
            conn.commit()
    except Exception:
        pass
    return True


def prune_jobs(conn, keep=500):
    """jobs 表只留最近 keep 条流水，防止无限膨胀（每次启动清一次）。"""
    try:
        conn.execute("""DELETE FROM jobs WHERE id NOT IN
                        (SELECT id FROM jobs ORDER BY id DESC LIMIT ?)""", (keep,))
        conn.commit()
    except Exception:
        pass


def now():
    return time.time()


# --------------------------- articles ---------------------------

def create_article(conn, title, content_md="", **kw):
    cur = conn.execute(
        """INSERT INTO articles (title, content_md, summary, tags, cover, status,
           source, ai_model, origin_url, ext, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (title, content_md, kw.get("summary", ""), kw.get("tags", ""),
         kw.get("cover", ""), kw.get("status", "draft"), kw.get("source", "human"),
         kw.get("ai_model", ""), kw.get("origin_url", ""), kw.get("ext", "{}"),
         now(), now()),
    )
    conn.commit()
    return cur.lastrowid


def update_article(conn, article_id, **kw):
    kw = {k: v for k, v in kw.items() if v is not None}
    if not kw:
        return False
    kw["updated_at"] = now()
    sets = ", ".join(f"{k}=?" for k in kw)
    cur = conn.execute(f"UPDATE articles SET {sets} WHERE id=?", (*kw.values(), article_id))
    conn.commit()
    if cur.rowcount == 0:
        return False
    # 内容变了，已发布的实例全部标记待更新
    if "content_md" in kw or "title" in kw:
        conn.execute(
            """UPDATE publications SET status='pending', last_error='内容已变更待同步'
               WHERE article_id=? AND status='ok'""", (article_id,))
        conn.commit()
    return True


def get_article(conn, article_id):
    return conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()


def delete_article(conn, article_id):
    """删除文章及其版本历史与发布账本，单事务完成。

    平台侧已发布的副本不受影响——那是平台自己的内容，本地只清账本。
    返回各表删除行数，调用方据此报告影响面。
    """
    with conn:
        versions = conn.execute(
            "DELETE FROM version_history WHERE article_id=?", (article_id,)).rowcount
        publications = conn.execute(
            "DELETE FROM publications WHERE article_id=?", (article_id,)).rowcount
        articles = conn.execute(
            "DELETE FROM articles WHERE id=?", (article_id,)).rowcount
    return {"articles": articles, "publications": publications, "versions": versions}


def list_articles(conn, status=None, limit=100, offset=0, slim=False):
    """列出文章。slim=True 时只取列表元信息（不取正文），列表页专用。

    limit<=0 在 SQLite 里等于"不限量"，会把整表拉出来；统一夹到 1..1000。
    """
    limit = max(1, min(int(limit), 1000))
    offset = max(0, min(int(offset), 1_000_000))
    sql = f"SELECT {LIST_COLUMNS} FROM articles" if slim else "SELECT * FROM articles"
    args = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    # id 作次键：updated_at 相同（批量导入常见）时保证分页顺序稳定
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return conn.execute(sql, args).fetchall()


def count_articles(conn, status=None):
    sql = "SELECT COUNT(*) FROM articles"
    args = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    return conn.execute(sql, args).fetchone()[0]


def search_articles(conn, keyword, limit=50):
    """全文检索。优先 FTS5（相关度排序），不可用/无命中时回退 LIKE。

    trigram 分词要求查询词至少 3 个字符；中文 2 字词（如「异步」）走
    LIKE 兜底更准，所以两个字符以下直接走 LIKE。
    """
    kw = (keyword or "").strip()
    if not kw:
        return []
    limit = max(1, min(int(limit), 200))
    if len(kw) >= 3:
        try:
            # 引号包裹避免 FTS5 把用户输入当语法（NEAR/OR/前缀 * 等）
            match = '"' + kw.replace('"', '""') + '"'
            rows = conn.execute(
                """SELECT a.* FROM articles_fts f
                   JOIN articles a ON a.id = f.rowid
                   WHERE articles_fts MATCH ?
                   ORDER BY rank LIMIT ?""", (match, limit)).fetchall()
            if rows:
                return rows
        except Exception:
            pass   # FTS 不可用或查询语法异常：回退 LIKE
    return conn.execute(
        """SELECT * FROM articles WHERE title LIKE ? OR content_md LIKE ?
           ORDER BY updated_at DESC LIMIT ?""",
        (f"%{kw}%", f"%{kw}%", limit)).fetchall()


# ------------------------- publications -------------------------

def upsert_publication(conn, article_id, platform, account="default", **kw):
    row = conn.execute(
        "SELECT * FROM publications WHERE article_id=? AND platform=? AND account=?",
        (article_id, platform, account)).fetchone()
    if row:
        kw = {k: v for k, v in kw.items() if v is not None}
        if kw:
            kw["updated_at"] = now()
            sets = ", ".join(f"{k}=?" for k in kw)
            conn.execute(f"UPDATE publications SET {sets} WHERE id=?",
                         (*kw.values(), row["id"]))
            conn.commit()
        return row["id"]
    cur = conn.execute(
        """INSERT INTO publications (article_id, platform, account, post_id, post_url,
           edit_url, status, draft_only, stats, last_error, content_hash,
           published_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (article_id, platform, account, kw.get("post_id", ""), kw.get("post_url", ""),
         kw.get("edit_url", ""), kw.get("status", "pending"),
         kw.get("draft_only", 1), kw.get("stats", "{}"), kw.get("last_error", ""),
         kw.get("content_hash", ""), kw.get("published_at"), now()))
    conn.commit()
    return cur.lastrowid


def get_publications(conn, article_id=None, platform=None, status=None):
    sql = "SELECT * FROM publications WHERE 1=1"
    args = []
    if article_id:
        sql += " AND article_id=?"
        args.append(article_id)
    if platform:
        sql += " AND platform=?"
        args.append(platform)
    if status:
        sql += " AND status=?"
        args.append(status)
    return conn.execute(sql + " ORDER BY updated_at DESC", args).fetchall()


def get_pending_updates(conn):
    """内容改过但还没同步到平台的实例——原地更新的待办清单"""
    return conn.execute(
        """SELECT p.*, a.title, a.content_md FROM publications p
           JOIN articles a ON a.id = p.article_id
           WHERE p.status='pending' AND p.post_id != '' """).fetchall()


# ---------------------------- jobs -----------------------------

def add_job(conn, type_, article_id=None, platform=None):
    cur = conn.execute(
        "INSERT INTO jobs (type, article_id, platform, status, created_at) VALUES (?,?,?,?,?)",
        (type_, article_id, platform, "running", now()))
    conn.commit()
    return cur.lastrowid


def finish_job(conn, job_id, ok, message=""):
    conn.execute("UPDATE jobs SET status=?, message=?, finished_at=? WHERE id=?",
                 ("ok" if ok else "failed", message, now(), job_id))
    conn.commit()


def list_jobs(conn, limit=50):
    return conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# --------------------------- accounts ---------------------------

def upsert_account(conn, platform, name, profile_dir, status="unknown"):
    conn.execute(
        """INSERT INTO accounts (platform, name, profile_dir, status, last_check)
           VALUES (?,?,?,?,?)
           ON CONFLICT(platform, name) DO UPDATE SET profile_dir=?, status=?, last_check=?""",
        (platform, name, profile_dir, status, now(), profile_dir, status, now()))
    conn.commit()


def list_accounts(conn):
    return conn.execute("SELECT * FROM accounts ORDER BY platform").fetchall()


# --------------------------- tasks（统一任务引擎） ---------------------------

def create_task(conn, task_id, kind, article_id=None, platforms=None,
                account="default", draft_only=False):
    conn.execute(
        """INSERT INTO tasks (task_id, kind, article_id, platforms, account,
           draft_only, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?, 'pending', ?,?)""",
        (task_id, kind, article_id, json.dumps(platforms or []),
         account, 1 if draft_only else 0, now(), now()))
    conn.commit()
    return task_id


def get_task(conn, task_id):
    row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        return None
    return dict(row)


def list_tasks(conn, limit=50, kind=None, status=None, offset=0):
    limit = max(1, min(int(limit), 1000))
    offset = max(0, min(int(offset), 1_000_000))
    sql = "SELECT * FROM tasks"
    where, args = [], []
    if kind:
        where.append("kind=?")
        args.append(kind)
    if status:
        where.append("status=?")
        args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def count_tasks(conn, kind=None, status=None):
    sql = "SELECT COUNT(*) FROM tasks"
    where, args = [], []
    if kind:
        where.append("kind=?")
        args.append(kind)
    if status:
        where.append("status=?")
        args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return conn.execute(sql, args).fetchone()[0]


def next_pending_tasks(conn, limit=10):
    """按创建时间升序取待跑任务（FIFO）。审计 B5：原实现取 id DESC 再排序，
    超 10 条积压时最早的会被永久饿死。"""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM tasks WHERE status='pending' ORDER BY created_at ASC, id ASC LIMIT ?",
        (limit,)).fetchall()]


def update_task(conn, task_id, **kw):
    if not kw:
        return False
    kw["updated_at"] = now()
    sets = ", ".join(f"{k}=?" for k in kw)
    cur = conn.execute(f"UPDATE tasks SET {sets} WHERE task_id=?", (*kw.values(), task_id))
    conn.commit()
    return cur.rowcount > 0


def prune_tasks(conn, keep=200):
    """tasks 表只留最近 keep 条（含全部 waiting_human），防无限膨胀。"""
    try:
        conn.execute("""DELETE FROM tasks WHERE status != 'waiting_human'
                        AND id NOT IN (SELECT id FROM tasks
                                       WHERE status != 'waiting_human'
                                       ORDER BY id DESC LIMIT ?)""", (keep,))
        conn.commit()
    except Exception:
        pass


# ------------------- 跨表查询（AGENT.md:339 原生 SQL 只住 db.py） -------------------

def list_pending_human(conn):
    """待人工处理的发布实例（join 标题），前端角标轮询用。"""
    return conn.execute(
        """SELECT p.article_id, a.title, p.platform, p.account, p.edit_url,
                  p.post_id, p.updated_at
           FROM publications p LEFT JOIN articles a ON a.id = p.article_id
           WHERE p.status = 'pending_human'
           ORDER BY p.updated_at DESC""").fetchall()


def list_publications_joined(conn, article_id=None, platform=None):
    """发布实例 + 文章标题，按更新时间倒序；两个过滤条件都可选。"""
    sql = ("SELECT p.*, a.title FROM publications p "
           "LEFT JOIN articles a ON a.id = p.article_id WHERE 1=1")
    args = []
    if article_id is not None:
        sql += " AND p.article_id=?"
        args.append(article_id)
    if platform is not None:
        sql += " AND p.platform=?"
        args.append(platform)
    sql += " ORDER BY p.updated_at DESC"
    return conn.execute(sql, args).fetchall()


def list_failed_jobs(conn, type_=None, limit=50):
    """失败的 jobs 流水（AIGC 门禁拒绝记录靠它回溯）。"""
    sql = "SELECT * FROM jobs WHERE 1=1"
    args = []
    if type_:
        sql += " AND type=?"
        args.append(type_)
    sql += " AND status='failed' ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()
