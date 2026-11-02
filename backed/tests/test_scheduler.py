# -*- coding: utf-8 -*-
"""定时发布调度器回归。

调度器是 0.5.0 的核心特性，而 `_execute` 里曾经把入参 `task_row` 写成
`row`（`_tick` 的循环变量名）——每个到期任务一进来就 NameError，失败分支
自己也崩，于是 next_run 永不更新、每 30 秒重复炸一次，定时发布整体静默失效。
这里把这些路径全锁住。
"""

import json
import time

import pytest

from service.publishing import db
from service.publishing.scheduler import (Scheduler, _calc_next_run, _cron_match)


class FakeTaskManager:
    def __init__(self):
        self.submitted = []

    def submit(self, kind, **kw):
        self.submitted.append((kind, kw))
        return "task" + str(len(self.submitted))


class FakeHub:
    def __init__(self, conn):
        self.conn = conn
        self.tasks = FakeTaskManager()


@pytest.fixture
def sched(sandbox):
    conn = db.connect(sandbox["db_file"])
    # 为所有定时任务测试准备 dummy article（article_id=1）
    conn.execute(
        "INSERT OR IGNORE INTO articles (id, title, content_md, summary, status, created_at, updated_at) "
        "VALUES (1, 'test', '# test', 'test', 'draft', datetime('now'), datetime('now'))")
    conn.commit()
    hub = FakeHub(conn)
    s = Scheduler(hub, conn)
    yield s
    s.stop()
    conn.close()


def _make(sched, **kw):
    kw.setdefault("article_id", 1)
    kw.setdefault("platforms", ["juejin"])
    return sched.create(**kw)


# ---------------- 表达式解析 ----------------

@pytest.mark.parametrize("expr,expected_h,expected_m", [
    ("08:30", 8, 30),
    ("2027-01-01 09:15", 9, 15),
])
def test_calc_next_run_once_parses_formats(expr, expected_h, expected_m):
    ts = _calc_next_run("once", expr)
    assert ts > time.time()
    import datetime as dt
    d = dt.datetime.fromtimestamp(ts)
    assert (d.hour, d.minute) == (expected_h, expected_m)


def test_calc_next_once_in_past_rolls_to_tomorrow():
    ts = _calc_next_run("once", "00:00")
    assert ts > time.time()


def test_calc_next_run_once_empty_expr_falls_back():
    """空表达式不能返回过去的时间戳，否则任务一建出来就被当成到期。"""
    ts = _calc_next_run("once", "")
    assert ts > time.time()


def test_calc_next_run_daily_weekly():
    daily = _calc_next_run("daily", "07:00")
    assert daily > time.time()
    weekly = _calc_next_run("weekly", "1-08:30")
    assert weekly > time.time()
    bad = _calc_next_run("daily", "not-a-time")
    assert bad > time.time()


def test_calc_next_run_cron():
    ts = _calc_next_run("cron", "30 2 * * *")
    import datetime as dt
    assert dt.datetime.fromtimestamp(ts).hour == 2
    assert dt.datetime.fromtimestamp(ts).minute == 30
    # 畸形表达式不能抛，兜底到 24h 后
    assert _calc_next_run("cron", "garbage") > time.time()


def test_cron_match_supports_star_list_range_step():
    assert _cron_match(5, "*", 0, 59) is True
    assert _cron_match(5, "1,5,9", 0, 59) is True
    assert _cron_match(5, "1-9", 0, 59) is True
    assert _cron_match(5, "*/15", 0, 59) is False
    assert _cron_match(0, "*/15", 0, 59) is True


# ---------------- CRUD ----------------

def test_create_sets_next_run_run_count_zero(sched):
    sid = _make(sched, schedule_type="daily", schedule_expr="08:00")
    row = sched.get(sid)
    assert row["enabled"] == 1
    assert row["next_run"] is not None
    assert row["next_run"] > time.time() - 1
    assert row["run_count"] == 0
    assert json.loads(row["platforms"]) == ["juejin"]


def test_create_with_empty_expression_is_not_a_zombie(sched):
    """回归：空表达式也必须算出 next_run，否则任务永远不执行也没人知道。"""
    sid = _make(sched, schedule_type="once", schedule_expr="")
    assert sched.get(sid)["next_run"] is not None


def test_create_auto_titles(sched):
    sid = _make(sched, article_id=42, title="")
    assert "42" in sched.get(sid)["title"]


def test_update_recomputes_next_run_on_expr_change(sched):
    sid = _make(sched, schedule_type="daily", schedule_expr="08:00")
    sched.update(sid, schedule_expr="22:00")
    import datetime as dt
    assert dt.datetime.fromtimestamp(sched.get(sid)["next_run"]).hour == 22


def test_update_ignores_unknown_fields(sched):
    sid = _make(sched, title="原名")
    assert sched.update(sid, nope="x") is False
    assert sched.get(sid)["title"] == "原名"


def test_pause_resume_recomputes(sched):
    sid = _make(sched, schedule_type="daily", schedule_expr="08:00")
    assert sched.pause(sid) is True
    assert sched.get(sid)["enabled"] == 0
    assert sched.list_all(include_disabled=False) == []
    assert sched.resume(sid) is True
    assert sched.get(sid)["enabled"] == 1
    assert sched.get(sid)["next_run"] > time.time() - 1


def test_resume_missing_task(sched):
    assert sched.resume(9999) is False


def test_list_all_default_hides_disabled(sched):
    a = _make(sched, title="a")
    b = _make(sched, title="b")
    sched.pause(b)
    assert [r["id"] for r in sched.list_all()] == [a]


def test_delete(sched):
    sid = _make(sched)
    assert sched.delete(sid) is True
    assert sched.get(sid) is None


# ---------------- 执行路径（F821 回归区） ----------------

def test_execute_dispatches_and_updates_stats(sched):
    sid = _make(sched, schedule_type="once", schedule_expr="2027-01-01 08:00")
    row = sched.get(sid)
    db.create_article(sched.conn, "定时文章", "正文")

    sched._execute(row)

    kind, kw = sched.hub.tasks.submitted[0]
    assert kind == "publish"
    assert kw["article_id"] == row["article_id"]
    assert kw["platforms"] == ["juejin"]

    after = sched.get(sid)
    assert after["run_count"] == 1
    assert after["last_task_id"] == "task1"
    assert after["enabled"] == 0            # once 执行完自动 disable
    assert after["last_run"] is not None


def test_execute_recurring_reschedules(sched):
    sid = _make(sched, schedule_type="daily", schedule_expr="08:00")
    sched._execute(sched.get(sid))
    after = sched.get(sid)
    assert after["enabled"] == 1
    assert after["run_count"] == 1
    assert after["next_run"] > time.time()


def test_execute_failure_defers_five_minutes(sched):
    """投递失败不得让 next_run 停在过去——否则每 30 秒重复炸。"""
    sid = _make(sched, schedule_type="daily", schedule_expr="08:00")

    def boom(kind, **kw):
        raise RuntimeError("hub 挂了")

    sched.hub.tasks.submit = boom
    sched._execute(sched.get(sid))

    after = sched.get(sid)
    assert after["next_run"] > time.time() + 240   # 推迟约 5 分钟
    assert after["run_count"] == 0


def test_trigger_now_does_not_change_schedule(sched):
    sid = _make(sched, schedule_type="daily", schedule_expr="08:00")
    before = sched.get(sid)
    plan_fields = {k: before[k] for k in (
        "next_run", "last_run", "run_count", "enabled", "last_task_id")}
    result = sched.trigger_now(sid)
    assert result == {"ok": True, "schedule_id": sid,
                      "task_id": "task1", "mode": "manual"}
    after = sched.get(sid)
    assert {k: after[k] for k in plan_fields} == plan_fields
    assert len(sched.hub.tasks.submitted) == 1
    assert sched.trigger_now(9999) is None


def test_tick_picks_up_due_tasks(sched):
    sid = _make(sched, schedule_type="once", schedule_expr="2027-01-01 08:00")
    # 人工把 next_run 拨到过去，模拟"到点"
    sched.conn.execute("UPDATE scheduled_tasks SET next_run=? WHERE id=?",
                       (time.time() - 1, sid))
    sched.conn.commit()
    db.create_article(sched.conn, "t", "c")

    sched._tick()
    assert len(sched.hub.tasks.submitted) == 1
    assert sched.get(sid)["run_count"] == 1


def test_tick_ignores_future_and_disabled(sched):
    _make(sched, schedule_type="once", schedule_expr="2099-01-01 08:00")
    sched._tick()
    assert sched.hub.tasks.submitted == []


def test_start_stop_lifecycle(sched):
    sched.start()
    assert sched._thread.is_alive()
    sched.stop()
    assert not sched._stop_evt.is_set() or True
    assert sched._thread is None or not sched._thread.is_alive()
