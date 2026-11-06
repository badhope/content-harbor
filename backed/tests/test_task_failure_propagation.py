# -*- coding: utf-8 -*-
"""平台级失败必须传播到任务状态。

回归：publish/update 返回 results 列表（每项带 ok），而 TaskManager 只认
(False, msg) 二元组，列表直接落进"完成"分支。结果是「所有平台都未登录失败」
也标成 ok，并发 publish.ok 事件——前端与 webhook 都看到假成功。
"""
from service.publishing.tasks import _failed_platforms


def test_detects_explicit_failures():
    result = [
        {"platform": "juejin", "ok": True, "post_id": "1"},
        {"platform": "csdn", "ok": False, "error": "未登录"},
    ]
    failed = _failed_platforms(result)
    assert len(failed) == 1 and failed[0]["platform"] == "csdn"


def test_all_ok_returns_empty():
    result = [{"platform": "a", "ok": True}, {"platform": "b", "ok": True}]
    assert _failed_platforms(result) == []


def test_all_failed_detected():
    result = [{"platform": "a", "ok": False, "error": "x"},
              {"platform": "b", "ok": False, "error": "y"}]
    assert len(_failed_platforms(result)) == 2


def test_dict_with_results_key():
    assert len(_failed_platforms({"results": [{"platform": "a", "ok": False}]})) == 1


def test_missing_ok_is_not_failure():
    """适配器只回 status 不带 ok 时不能误判成失败。"""
    assert _failed_platforms([{"platform": "a", "status": "ok"}]) == []


def test_non_list_returns_empty():
    assert _failed_platforms("done") == []
    assert _failed_platforms(None) == []
    assert _failed_platforms(42) == []


def test_task_manager_marks_failed_when_platform_fails(sandbox):
    """端到端：_dispatch 返回带失败项时，任务必须落 failed 而不是 ok。"""
    from service.publishing import db as db_mod
    from service.publishing.tasks import TaskManager

    # 自建连接：conn fixture 在多文件运行时可能已被别的用例关闭
    conn = db_mod.connect(sandbox["db_file"])

    class FakeHub:
        def publish(self, *a, **kw):
            return [
                {"platform": "juejin", "ok": True},
                {"platform": "csdn", "ok": False, "error": "未登录"},
            ]

    tm = TaskManager(FakeHub(), conn)
    task_id = tm.submit("publish", article_id=1, platforms=["juejin", "csdn"])
    # 走完整取任务路径（_next_pending 负责置 running，直接调 _run_task 会跳过）
    task = tm._next_pending()
    assert task is not None, "任务未被取到"
    tm._run_task(task)
    row = conn.execute("SELECT status, error FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    # 核心断言：绝不能是 ok。具体落 failed 还是 waiting_human 取决于分诊
    # （"未登录"命中 HUMAN_HINTS → 等人工，语义正确）。
    assert row["status"] != "ok", f"平台失败却标成成功: {row['status']}"
    assert row["status"] in ("failed", "waiting_human")
    assert "csdn" in (row["error"] or "")
    conn.close()
