# -*- coding: utf-8 -*-
"""调度表达式必须创建时校验。

回归：此前任何表达式都能创建成功——`not a cron` / `99 99 99 99 99` / 空串
全部 200，任务被 _calc_next_run 的兜底静默排到 24 小时后。用户既等不到
执行，也看不到错误。
"""
import pytest

from service.publishing.scheduler import (
    ScheduleExpressionError,
    validate_schedule_expr,
)


@pytest.mark.parametrize("stype,expr", [
    ("cron", "not a cron"),
    ("cron", "99 99 99 99 99"),
    ("cron", ""),
    ("cron", "* * *"),          # 段数不足
    ("daily", "25:00"),
    ("daily", "09:60"),
    ("daily", "9:3"),
    ("daily", ""),
    ("weekly", "8-08:30"),      # 周几只有 1-7
    ("weekly", "1-25:00"),
    ("weekly", "bad"),
    ("once", "bad"),
    ("unknown", "x"),
])
def test_invalid_expressions_rejected(stype, expr):
    with pytest.raises(ScheduleExpressionError):
        validate_schedule_expr(stype, expr)


@pytest.mark.parametrize("stype,expr", [
    ("cron", "0 9 * * *"),
    ("cron", "*/15 * * * *"),
    ("cron", "0 9 * * 1-5"),
    ("daily", "09:30"),
    ("daily", "00:00"),
    ("daily", "23:59"),
    ("weekly", "1-08:30"),
    ("weekly", "7-23:59"),
    ("once", "2026-12-01 09:00"),
    ("once", "09:30"),
    ("once", ""),   # 空 = 立即执行一次（既有默认语义）
])
def test_valid_expressions_accepted(stype, expr):
    assert validate_schedule_expr(stype, expr) == expr


def test_api_returns_422_for_bad_expression(client):
    art = client.post("/api/v1/hub/articles",
                      json={"title": "x", "content_md": "y"}).json()
    r = client.post("/api/v1/hub/schedules", json={
        "article_id": art["id"], "platforms": ["juejin"],
        "schedule_type": "daily", "schedule_expr": "25:00",
    })
    assert r.status_code == 422
    assert "时间越界" in r.json()["message"] or "daily" in r.json()["message"]


def test_changing_type_revalidates_merged_expression(client):
    """只改 type 不改 expr 时，旧表达式对新类型可能非法，必须一起校验。"""
    art = client.post("/api/v1/hub/articles",
                      json={"title": "x", "content_md": "y"}).json()
    sch = client.post("/api/v1/hub/schedules", json={
        "article_id": art["id"], "platforms": ["juejin"],
        "schedule_type": "cron", "schedule_expr": "0 9 * * *",
    }).json()
    r = client.put(f"/api/v1/hub/schedules/{sch['id']}", json={"schedule_type": "daily"})
    assert r.status_code == 422, "改类型后旧表达式非法却放过"
