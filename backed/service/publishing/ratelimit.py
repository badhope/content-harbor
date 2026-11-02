# -*- coding: utf-8 -*-
"""平台级限流窗口：把 429/503 的 Retry-After 变成"整平台冷却"。

为什么通用指数退避不够：

    指数退避解决的是"我这一次失败了"，而 429 解决的是"这个平台现在
    不接受任何请求"。如果只在任务层退避，同平台的其他任务仍会继续撞上去，
    把限流窗口越撞越长（典型表现：一个账号被限流后，后续 10 个任务
    全部 429，每个再重试 3 次）。

落地方式对齐 Mixpost 的 `HasSocialProviderJobRateLimit`：
  - 解析 `Retry-After`（秒数 或 HTTP-date）与 `X-RateLimit-Reset`（常见为
    epoch 秒/毫秒，GitHub 风格）；
  - 冷却状态**按平台**持久化（DB 表，跨进程/重启有效）；
  - 取 `max(冷却到期, 任务自身退避)` 作为真正的可执行时间；
  - 到点自动清理，不需要定时任务。

为什么落 DB 而不落内存：进程重启后内存冷却丢失，会立刻再撞一次限流；
SQLite 写入是一次 upsert，成本可忽略。
"""

import email.utils
import threading
import time
from datetime import datetime, timezone

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS platform_cooldown (
    platform    TEXT PRIMARY KEY,
    until_ts    REAL NOT NULL,
    reason      TEXT DEFAULT '',
    updated_at  REAL
);
"""

_lock = threading.Lock()


def ensure_schema(conn) -> None:
    """幂等建表。用 sqlite_master 探针而不是进程内缓存 id(conn)：
    连接关闭后新连接可能复用同一地址，缓存会误判"已建表"→ no such table。"""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='platform_cooldown'"
    ).fetchone()
    if row:
        return
    with _lock:
        conn.executescript(TABLE_DDL)
        conn.commit()


def parse_retry_after(value: str) -> float:
    """解析 Retry-After：支持秒数、HTTP-date、以及缺省的 0。

    返回"还要等多少秒"（≥0）。无法解析返回 0（视为无该头）。
    """
    if value is None:
        return 0.0
    raw = str(value).strip()
    if not raw:
        return 0.0
    # 纯数字（秒）
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    # HTTP-date（RFC 7231）
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return 0.0


def parse_rate_limit_reset(value: str) -> float:
    """解析 X-RateLimit-Reset：epoch 秒或毫秒。返回还需等待的秒数。"""
    if not value:
        return 0.0
    try:
        ts = float(str(value).strip())
    except ValueError:
        return 0.0
    # 13 位是毫秒（2026 年的毫秒时间戳约 1.7e12）
    if ts > 1e11:
        ts /= 1000.0
    return max(0.0, ts - time.time())


def retry_after_from_headers(headers) -> float:
    """从响应头里取"建议等待秒数"：Retry-After 优先，其次 X-RateLimit-Reset。"""
    if not headers:
        return 0.0
    get = headers.get
    seconds = parse_retry_after(get("retry-after") or get("Retry-After"))
    if seconds > 0:
        return seconds
    return parse_rate_limit_reset(
        get("x-ratelimit-reset") or get("X-RateLimit-Reset") or "")


def mark(conn, platform: str, seconds: float, reason: str = "") -> float:
    """把一个平台置入冷却，返回到期时间戳。

    只延长不缩短：多个任务同时撞 429 时，取最晚的那个窗口。
    """
    if not platform or seconds <= 0:
        return 0.0
    ensure_schema(conn)
    until = time.time() + seconds
    with _lock:
        row = conn.execute(
            "SELECT until_ts FROM platform_cooldown WHERE platform=?",
            (platform,)).fetchone()
        if row and row["until_ts"] >= until:
            return float(row["until_ts"])
        conn.execute(
            "INSERT INTO platform_cooldown (platform, until_ts, reason, updated_at)"
            " VALUES (?,?,?,?) ON CONFLICT(platform) DO UPDATE SET"
            " until_ts=excluded.until_ts, reason=excluded.reason,"
            " updated_at=excluded.updated_at",
            (platform, until, (reason or "")[:200], time.time()))
        conn.commit()
    return until


def remaining(conn, platform: str) -> float:
    """该平台还剩多少秒冷却（0 = 无冷却）。"""
    if not platform:
        return 0.0
    ensure_schema(conn)
    row = conn.execute(
        "SELECT until_ts FROM platform_cooldown WHERE platform=?",
        (platform,)).fetchone()
    if not row:
        return 0.0
    left = float(row["until_ts"]) - time.time()
    if left <= 0:
        conn.execute("DELETE FROM platform_cooldown WHERE platform=?", (platform,))
        conn.commit()
        return 0.0
    return left


def snapshot(conn) -> list:
    """当前所有平台冷却状态（给 /status 与排障用）。"""
    ensure_schema(conn)
    now = time.time()
    rows = conn.execute(
        "SELECT * FROM platform_cooldown WHERE until_ts > ? ORDER BY until_ts",
        (now,)).fetchall()
    return [{"platform": r["platform"],
             "remaining_s": round(float(r["until_ts"]) - now, 1),
             "reason": r["reason"]}
            for r in rows]


def clear(conn, platform: str = None) -> None:
    """手动解除冷却（平台侧确认已恢复时用）。"""
    ensure_schema(conn)
    if platform:
        conn.execute("DELETE FROM platform_cooldown WHERE platform=?", (platform,))
    else:
        conn.execute("DELETE FROM platform_cooldown")
    conn.commit()


def classify_and_mark(conn, platform: str, error: str, status=None) -> float:
    """从错误信息里识别限流，命中则置冷却并返回等待秒数。

    适配器抛的 PlatformError 里通常带 "HTTP 429" / "HTTP 503" 与响应头文本；
    这里做尽力而为的解析（没有原始头时给保守默认值）。
    """
    text = (error or "")
    low = text.lower()
    hit = (status in (429, 503)) or ("429" in text) or ("503" in text) \
        or ("rate limit" in low) or ("too many requests" in low)
    if not hit:
        return 0.0
    seconds = 0.0
    # 适配器把响应头写进错误串时优先用它（形如 retry-after: 30）
    for key in ("retry-after", "x-ratelimit-reset"):
        idx = low.find(key)
        if idx == -1:
            continue
        tail = text[idx + len(key): idx + len(key) + 40]
        for token in tail.replace(":", " ").replace("=", " ").split():
            try:
                val = float(token)
            except ValueError:
                continue
            if key == "retry-after":
                seconds = max(seconds, val)
            else:
                seconds = max(seconds, parse_rate_limit_reset(token))
            break
    if seconds <= 0:
        # 保守默认：429 通常按分钟窗口，503 给 30s
        seconds = 60.0 if "429" in text else 30.0
    # 上限 30 分钟：避免一个异常头把平台锁死一整天
    seconds = min(seconds, 1800.0)
    mark(conn, platform, seconds, reason=text[:160])
    return seconds
