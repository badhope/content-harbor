# -*- coding: utf-8 -*-
"""定时发布调度器：周期轮询 scheduled_tasks 表，到点的任务提交进 TaskManager。

独立于 TaskManager 又复用它的串行引擎——调度器只负责"按时投递"，
真正的发布/更新动作还是走 TaskManager 的确定性分流+恢复流程。

调度器是 Hub 的伴随线程：Hub.__init__ 里启动，Hub.close_all 里关闭。
不是独立进程，所以 Hub 退出时调度器自动停止，不留孤儿线程。

使用方式：
  1. 在 API 层创建 scheduled_task（POST /schedules）
  2. 调度器每 30 秒轮询一次，发现到点的任务就提交进 TaskManager
  3. 任务执行完自动更新 last_run / next_run / run_count
  4. 一次性任务（schedule_type=once）执行完自动 disable

任务字段说明：
  - article_id: 要发布的文章 ID
  - platforms: 目标平台 JSON 数组
  - account: 账号名（默认 default）
  - draft_only: 是否只发草稿
  - schedule_type: once(一次性) / daily(每天) / weekly(每周) / cron(cron 表达式)
  - schedule_expr: 调度表达式（HH:MM / 周几+HH:MM / 标准 cron）
  - enabled: 是否启用
"""

import json
import re
import threading
import time
from datetime import datetime, timedelta

try:                      # croniter 是成熟 cron 解析器（6.x，纯 Python，无额外依赖）
    from croniter import croniter as _croniter
except Exception:         # 未安装时退回内置最小解析器，功能不丢
    _croniter = None



# scheduled_tasks 表的 DDL（由 ensure_schema 建表，不走 MIGRATIONS 因为它不在 hub.db 里）
# 实际落在同一个库里，只是单独建表。
SCHEDULED_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id    INTEGER NOT NULL,
    platforms     TEXT NOT NULL DEFAULT '[]',
    account       TEXT NOT NULL DEFAULT 'default',
    draft_only    INTEGER NOT NULL DEFAULT 0,
    schedule_type TEXT NOT NULL DEFAULT 'once',  -- once/daily/weekly/cron
    schedule_expr TEXT NOT NULL DEFAULT '',      -- 表达式（解释方式取决于 type）
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_run      REAL,
    next_run      REAL,
    run_count     INTEGER NOT NULL DEFAULT 0,
    last_task_id  TEXT DEFAULT '',               -- 最近一次投递的 task_id
    title         TEXT DEFAULT '',               -- 任务标题（方便列表展示）
    created_at    REAL,
    updated_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_sched_next ON scheduled_tasks(next_run);
CREATE INDEX IF NOT EXISTS idx_sched_enabled ON scheduled_tasks(enabled);
"""


def _ensure_table(conn):
    """幂等建表：SCHEMA 不在 db.py 的表列表里，调度器独立管理自己的表。"""
    try:
        conn.executescript(SCHEDULED_TABLE_DDL)
        conn.commit()
    except Exception:
        pass


def _calc_next_run(schedule_type: str, schedule_expr: str, base: float = None) -> float:
    """根据调度类型 + 表达式，计算下一次执行的时间戳。

    支持的表达式：
      once    → "YYYY-MM-DD HH:MM" 或 "HH:MM"（今天或明天）
      daily   → "HH:MI"（每天此时）
      weekly  → "1-08:30"（周一 08:30；1=周一 ~ 7=周日）
      cron    → 标准 5 段 cron 表达式（分 时 日 月 周）

    base 保留为公开签名的一部分（唯一调用方 _execute 传 base=now，语义即"以
    执行时刻为基准"）。注意实现当前一律以 datetime.now() 为基准：base 不是
    无效参数就是冗余，行为上等价，暂不改语义。
    """
    now = datetime.now()

    try:
        if schedule_type == "once":
            # 尝试完整日期时间，失败退化为 "HH:MM"
            for fmt in ("%Y-%m-%d %H:%M", "%H:%M"):
                try:
                    t = datetime.strptime(schedule_expr.strip(), fmt)
                    if fmt == "%H:%M":
                        candidate = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                        if candidate.timestamp() <= now.timestamp():
                            candidate += timedelta(days=1)
                        return candidate.timestamp()
                    else:
                        return t.timestamp()
                except ValueError:
                    continue
            # 全失败：延后 1 小时兜底
            return (now + timedelta(hours=1)).timestamp()

        elif schedule_type == "daily":
            m = re.match(r"^(\d{1,2}):(\d{2})$", schedule_expr.strip())
            if not m:
                raise ValueError(f"daily 格式应为 HH:MI，收到 {schedule_expr!r}")
            h, mi = int(m.group(1)), int(m.group(2))
            candidate = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if candidate.timestamp() <= now.timestamp():
                candidate += timedelta(days=1)
            return candidate.timestamp()

        elif schedule_type == "weekly":
            m = re.match(r"^([1-7])-(\d{1,2}):(\d{2})$", schedule_expr.strip())
            if not m:
                raise ValueError(f"weekly 格式应为 周几-HH:MI（如 1-08:30），收到 {schedule_expr!r}")
            weekday, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3))
            # weekday: 1=周一 ... 7=周日 → Python weekday(): 0=周一 ... 6=周日
            target_wd = (weekday - 1) % 7  # 周一(1)→0, ..., 周日(7)→6
            candidate = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            days_ahead = (target_wd - candidate.weekday()) % 7
            if days_ahead == 0 and candidate.timestamp() <= now.timestamp():
                days_ahead = 7
            candidate += timedelta(days=days_ahead)
            return candidate.timestamp()

        elif schedule_type == "cron":
            # 优先 croniter：完整 5 段语义（含 L/W/#、秒级扩展、夏令时），
            # 手撸解析器保留为兜底（无第三方依赖环境）。
            expr = schedule_expr.strip()
            if _croniter is not None:
                try:
                    return _croniter(expr, now).get_next(datetime).timestamp()
                except Exception:
                    pass
            return _cron_next(expr, now)

    except Exception:
        pass
    # 任何解析失败兜底：24 小时后
    return (now + timedelta(days=1)).timestamp()


def _cron_next(expr: str, now: datetime) -> float:
    """最小 cron 下次计算（支持 *、逗号、连字符、星号/步长）。

    不追求 100% 标准 cron 兼容——覆盖常见的 "分 时 日 月 周" 五段即可。
    """
    parts = expr.split()
    if len(parts) != 5:
        return (now + timedelta(hours=1)).timestamp()

    minute_s, hour_s, dom_s, month_s, dow_s = parts

    # 从 now+1min 开始搜，最多搜 366 天（cron 表达式重复周期 ≤ 1 年）
    # 优化：按天递增，每天先做 cheap reject（月/周不匹配则跳过 1440 步）
    candidate = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    for _ in range(366):  # 按天搜而非按分钟搜
        if not (_cron_match(candidate.day, dom_s, 1, 31)
                and _cron_match(candidate.month, month_s, 1, 12)
                and _cron_match(candidate.isoweekday(), dow_s, 1, 7)):
            candidate += timedelta(days=1)
            candidate = candidate.replace(hour=0, minute=0)
            continue
        # 日匹配成功，再按小时+分钟搜这一天
        for _m in range(1440):  # 当天分钟级搜索 ≤ 1440 步
            if (_cron_match(candidate.minute, minute_s, 0, 59)
                    and _cron_match(candidate.hour, hour_s, 0, 23)):
                return candidate.timestamp()
            candidate += timedelta(minutes=1)
        # 当天无匹配，继续下一天
        candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0)

    return (now + timedelta(days=1)).timestamp()  # 兜底：24 小时后


def _cron_match(value: int, field: str, min_v: int, max_v: int) -> bool:
    """判断 value 是否匹配 cron 的一段。"""
    if field == "*":
        return True
    for part in field.split(","):
        part = part.strip()
        if "/" in part:
            base, step = part.split("/", 1)
            step = int(step)
            if base == "*":
                base_val = min_v
            else:
                base_val = int(base)
            if (value - base_val) % step == 0 and min_v <= value <= max_v:
                return True
        elif "-" in part:
            lo, hi = part.split("-", 1)
            if int(lo) <= value <= int(hi):
                return True
        else:
            try:
                if int(part) == value:
                    return True
            except ValueError:
                pass
    return False


class Scheduler:
    """定时发布调度器。伴随 Hub 启动/停止，轮询 scheduled_tasks 表投递到 TaskManager。"""

    POLL_INTERVAL = 30  # 轮询间隔（秒）

    def __init__(self, hub, conn):
        self.hub = hub
        self.conn = conn
        self._stop_evt = threading.Event()
        self._thread = None
        _ensure_table(conn)

    def start(self):
        """启动后台轮询线程（daemon=True，主线程退出自动终止）。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        """请求停止并等待当前轮询结束。"""
        self._stop_evt.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                return False
        self._thread = None
        return True

    def _loop(self):
        """主轮循环：检查到期任务、execute、sleep。"""
        while not self._stop_evt.is_set():
            try:
                self._tick()
            except Exception:
                import traceback
                traceback.print_exc()
            # 等 POLL_INTERVAL 或 stop 事件（先到先走）
            self._stop_evt.wait(self.POLL_INTERVAL)

    def _tick(self):
        """单次轮询：找出所有 enabled 且 next_run <= now 的任务，逐个执行。"""
        now = time.time()
        rows = self.conn.execute(
            """SELECT * FROM scheduled_tasks
               WHERE enabled=1 AND next_run IS NOT NULL AND next_run <= ?
               ORDER BY next_run ASC""", (now,)).fetchall()
        for row in rows:
            row = dict(row)
            self._execute(row)

    def _execute(self, task_row: dict):
        """投递一个定时任务进 TaskManager，更新 last_run/next_run/run_count。"""
        now = time.time()
        try:
            # 文章可能已被删除：推迟下一次执行，避免高频无效重试
            art = self.conn.execute(
                "SELECT id FROM articles WHERE id=?", (task_row["article_id"],)).fetchone()
            if not art:
                # 推迟下一次执行 10 分钟，避免被删文章导致高频无效重试
                self.conn.execute(
                    "UPDATE scheduled_tasks SET next_run=?, updated_at=? WHERE id=?",
                    (now + 600, now, task_row["id"]))
                self.conn.commit()
                return

            task_id = self.hub.tasks.submit(
                "publish",
                article_id=task_row["article_id"],
                platforms=json.loads(task_row["platforms"]),
                account=task_row["account"],
                draft_only=bool(task_row["draft_only"]),
            )
            # 投递成功：更新统计
            next_run = _calc_next_run(task_row["schedule_type"],
                                     task_row["schedule_expr"], base=now)
            self.conn.execute(
                """UPDATE scheduled_tasks SET
                    last_run=?, next_run=?, run_count=run_count+1, last_task_id=?, updated_at=?
                   WHERE id=?""",
                (now, next_run, task_id or "", now, task_row["id"]))
            # 一次性任务执行完自动 disable
            if task_row["schedule_type"] == "once":
                self.conn.execute(
                    "UPDATE scheduled_tasks SET enabled=0, updated_at=? WHERE id=?",
                    (now, task_row["id"]))
            self.conn.commit()
        except Exception:  # aqg: top-level boundary（投递失败不得让任务卡在过去，推迟重试是兜底契约）
            # 推迟 5 分钟重试，防止雪崩。
            # row 是入参 task_row，不是 _tick 的循环变量——原先写 row 直接 NameError，
            # 失败分支自己也崩，结果 next_run 不更新、每 30 秒重复炸一次。
            self.conn.execute(
                """UPDATE scheduled_tasks SET next_run=?, updated_at=? WHERE id=?""",
                (now + 300, now, task_row["id"]))
            self.conn.commit()

    # ---------------- CRUD 接口（API 层调用）----------------

    def create(self, article_id, platforms, account="default", draft_only=False,
               schedule_type="once", schedule_expr="", title=""):
        """创建定时任务。自动计算 next_run。"""
        now = time.time()
        # 表达式为空也交给 _calc_next_run：它有 +1h 兜底，返回非空。
        # 原先写成 `if schedule_expr else None`，结果是建出一条 next_run=NULL
        # 的僵尸任务——_tick 明确过滤 NULL，它永远不会执行也没人报错。
        next_run = _calc_next_run(schedule_type, schedule_expr or "")
        cur = self.conn.execute(
            """INSERT INTO scheduled_tasks
               (article_id, platforms, account, draft_only, schedule_type,
                schedule_expr, enabled, next_run, title, created_at, updated_at)
               VALUES (?,?,?,?,?,?, 1, ?, ?, ?, ?)""",
            (article_id, json.dumps(platforms), account, 1 if draft_only else 0,
             schedule_type or "once", schedule_expr, next_run,
             title or f"文章{article_id}定时发布", now, now))
        self.conn.commit()
        return cur.lastrowid

    def list_all(self, include_disabled=False):
        """列出所有定时任务。"""
        sql = "SELECT * FROM scheduled_tasks"
        if not include_disabled:
            sql += " WHERE enabled=1"
        sql += " ORDER BY next_run ASC"
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def get(self, sid):
        r = self.conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None

    def update(self, sid, **kw):
        """动态更新任务字段。改了 schedule_type/next_run 时自动重算 next_run。"""
        allowed = {"title", "platforms", "account", "draft_only",
                   "schedule_type", "schedule_expr", "enabled"}
        kw = {k: v for k, v in kw.items() if k in allowed}
        if not kw:
            return False
        # platforms 序列化
        if "platforms" in kw and isinstance(kw["platforms"], list):
            kw["platforms"] = json.dumps(kw["platforms"])
        # 重算 next_run
        if "schedule_type" in kw or "schedule_expr" in kw:
            cur_row = self.conn.execute(
                "SELECT schedule_type, schedule_expr FROM scheduled_tasks WHERE id=?",
                (sid,)).fetchone()
            if cur_row is None:
                return False
            st = kw.get("schedule_type", cur_row["schedule_type"])
            se = kw.get("schedule_expr", cur_row["schedule_expr"]) or ""
            kw["next_run"] = _calc_next_run(st, se)
        kw["updated_at"] = time.time()
        sets = ", ".join(f"{k}=?" for k in kw)
        cur = self.conn.execute(f"UPDATE scheduled_tasks SET {sets} WHERE id=?",
                                (*kw.values(), sid))
        self.conn.commit()
        return cur.rowcount > 0

    def pause(self, sid):
        return self.update(sid, enabled=0)

    def resume(self, sid):
        """启用一个被暂停的任务：重新计算 next_run（避免它一启用就立刻补发）。"""
        now = time.time()
        row = self.conn.execute(
            "SELECT schedule_type, schedule_expr FROM scheduled_tasks WHERE id=?",
            (sid,)).fetchone()
        if not row:
            return False
        next_run = _calc_next_run(row["schedule_type"], row["schedule_expr"] or "")
        self.conn.execute(
            "UPDATE scheduled_tasks SET enabled=1, next_run=?, updated_at=? WHERE id=?",
            (next_run, now, sid))
        self.conn.commit()
        return True

    def delete(self, sid):
        cur = self.conn.execute("DELETE FROM scheduled_tasks WHERE id=?", (sid,))
        self.conn.commit()
        return cur.rowcount > 0

    def trigger_now(self, sid):
        """手动投递一次，不修改调度计划字段。"""
        row = self.conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (sid,)).fetchone()
        if not row:
            return None
        row = dict(row)
        article = self.conn.execute(
            "SELECT id FROM articles WHERE id=?", (row["article_id"],)).fetchone()
        if not article:
            raise ValueError("定时任务关联文章不存在")
        task_id = self.hub.tasks.submit(
            "publish", article_id=row["article_id"],
            platforms=json.loads(row["platforms"]), account=row["account"],
            draft_only=bool(row["draft_only"]))
        return {"ok": True, "schedule_id": sid, "task_id": task_id, "mode": "manual"}


def init_scheduler(hub):
    """工厂函数：建 Scheduler、注入 Hub、启动后台线程。"""
    scheduler = Scheduler(hub, hub.conn)
    scheduler.start()
    return scheduler
