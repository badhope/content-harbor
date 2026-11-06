# -*- coding: utf-8 -*-
"""Webhook 存储 + HMAC 签名：注册/查询/更新/删除 webhook + 派发签名。

敏感字段（secret）落库时 Fernet 加密，查询时解密。
"""

import hashlib
import hmac
import json
import sqlite3 as _sql
from pathlib import Path

from service.publishing import crypto

def _default_db_path() -> Path:
    """与 db.DB_PATH 同源，避免桌面版/容器两套路径打架。

    保留模块级 `_DB_PATH` 变量（conftest 会 monkeypatch 它），但初始化时
    仍以 db.DB_PATH 为准——两者不一致时取 db 的值。
    """
    from service.publishing import db
    return db.DB_PATH


_DB_PATH = _default_db_path()


def _conn():
    """独立连接（仅用于没有共享连接可用的场景）。

    busy_timeout 与主库 db.connect 保持一致（30s）：此前写 5000，是主库的
    1/6。并发写入时（API 线程 + webhook 派发线程 + 测试）5 秒就放弃，
    直接抛 sqlite3.OperationalError: database is locked——实测复现。
    """
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = _sql.connect(str(_DB_PATH), timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    return c


def _use(conn=None):
    """返回 (连接, 是否自建，需要在结束时关闭)。"""
    if conn is not None:
        return conn, False
    return _conn(), True


def init(conn=None):
    """建 webhooks 表（如果还没有）。

    无 conn 时自建并在完成后关闭（避免旧实现里每调一次 register/list_all
    就泄漏一个 sqlite 连接，长时间跑会耗尽文件句柄）。传了 conn 则
    不关，由调用方管理。
    """
    own = conn is None
    c = conn or _conn()
    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                secret TEXT DEFAULT '',
                events TEXT DEFAULT '*',
                enabled INTEGER DEFAULT 1,
                created_at REAL DEFAULT (strftime('%s','now')),
                last_triggered REAL DEFAULT 0,
                fail_count INTEGER DEFAULT 0
            )
        """)
        c.commit()
    finally:
        if own:
            c.close()


def register(url, events=None, secret="", enabled=True, conn=None):
    """注册一个 webhook（secret 加密落库）。返回 id。"""
    init(conn)
    c, own = _use(conn)
    encrypted_secret = crypto.encrypt(secret) if secret else ""
    events_json = json.dumps(events or ["*"], ensure_ascii=False)
    cur = c.execute(
        "INSERT INTO webhooks (url, secret, events, enabled) VALUES (?, ?, ?, ?)",
        (url, encrypted_secret, events_json, 1 if enabled else 0))
    c.commit()
    wid = cur.lastrowid
    if own:
        c.close()
    return wid


def list_all(conn=None):
    """查询所有 webhook（secret 不返回实际值，仅返回 has_secret）。"""
    init(conn)
    c, own = _use(conn)
    rows = c.execute(
        "SELECT id, url, secret, events, enabled, created_at, last_triggered, fail_count "
        "FROM webhooks ORDER BY id").fetchall()
    if own:
        c.close()
    result = []
    for r in rows:
        evts = json.loads(r[3]) if r[3] else ["*"]
        plain_secret = crypto.decrypt(r[2]) if r[2] else ""
        result.append({
            "id": r[0], "url": r[1], "has_secret": bool(plain_secret),
            "events": evts, "enabled": bool(r[4]),
            "created_at": r[5], "last_triggered": r[6] or 0,
            "fail_count": r[7] or 0,
        })
    return result


def update(wid, **kw):
    """更新 webhook。只接受 url/secret/events/enabled 四个字段。"""
    conn = kw.pop("conn", None)
    init(conn)
    allowed = {"url", "secret", "events", "enabled"}
    sets, vals = [], []
    for k, v in kw.items():
        if k not in allowed:
            continue
        if k == "secret" and v:
            v = crypto.encrypt(v)
        elif k == "secret" and not v:
            v = ""
        if k == "events":
            v = json.dumps(v, ensure_ascii=False)
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return False
    vals.append(wid)
    c, own = _use(conn)
    cur = c.execute(f"UPDATE webhooks SET {','.join(sets)} WHERE id=?", vals)
    c.commit()
    # 必须用 rowcount 而不是 total_changes：total_changes 是连接级累计值，
    # 共享连接下永远 > 0，于是"更新不存在的 id"也会返回成功（实测 200 而非 404）。
    ok = cur.rowcount > 0
    if own:
        c.close()
    return ok


def delete(wid, conn=None):
    """删除 webhook。返回 True/False。"""
    init(conn)
    c, own = _use(conn)
    cur = c.execute("DELETE FROM webhooks WHERE id=?", (wid,))
    c.commit()
    ok = cur.rowcount > 0
    if own:
        c.close()
    return ok


def sign_payload(payload: str, secret_encrypted: str) -> str:
    """HMAC-SHA256 派发签名。secret_encrypted 是密文（先解密再签）。"""
    if not secret_encrypted:
        return ""
    plain = crypto.decrypt(secret_encrypted)
    if not plain:
        return ""
    return hmac.new(plain.encode(), payload.encode(), hashlib.sha256).hexdigest()
