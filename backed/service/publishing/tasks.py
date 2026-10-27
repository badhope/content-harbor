# -*- coding: utf-8 -*-
"""统一任务引擎（重构第一刀）。

替代三样东西：
  1. LangGraph 工作流层（workflows/ 870 行）—— 一个「遍历平台 + 失败重试 +
     暂停等人」的循环被包装成双状态图，功能上等价于本模块 100 行。
  2. server/api.py 里三个复制粘贴的内存任务字典（LOGIN/PUBLISH/ASSIST_TASKS）。
  3. 双数据库（hub.db + workflow.db）—— 任务状态统一落 hub.db 的 tasks 表。

设计要点：
  - 任务持久化到 SQLite，进程重启不丢（waiting_human 任务可恢复）。
  - 单后台线程串行消费队列：当前浏览器是全局单实例，串行是正确约束，
    不是性能瓶颈；未来若换多实例浏览器，把 worker 换成线程池即可。
  - 失败分诊用本地确定性规则（不依赖 LLM）：验证码/风控 → 转人工；
    网络/临时错误 → 自动重试；其余 → 判失败并留 error。
  - 语义单一：REST 与 MCP 都通过 submit() 提交、get()/list() 查询，
    不再有 legacy 与 workflow 双路径。
"""

import json
import threading
import time
import uuid

from service.publishing import db
from service.publishing import events as evt_mod


def _has_pending_user(result) -> bool:
    """发布结果里是否存在中断模式（assist/manual）的实例。"""
    items = result if isinstance(result, list) else (
        (result or {}).get("results") if isinstance(result, dict) else None) or []
    return any(isinstance(r, dict) and r.get("pending_user") for r in items)


def _pending_user_message(result) -> str:
    items = result if isinstance(result, list) else (
        (result or {}).get("results") if isinstance(result, dict) else None) or []
    msgs = [r.get("message") for r in items if isinstance(r, dict) and r.get("pending_user")]
    return msgs[0] if msgs else "平台页已打开，等待你在平台上确认后发布"


def _load_result(task) -> object:
    """把任务的 result 列解析成对象（解析失败给空）。"""
    try:
        return json.loads(task.get("result") or "[]")
    except Exception:   # aqg: top-level boundary（result 列损坏时按空处理，不炸任务查询）
        return []


class TaskManager:
    """后台任务队列。线程安全：submit/get/list/resume 可跨线程调用。"""

    # 本地分诊关键词（小写匹配）。确定性规则，不引入 LLM 依赖。
    HUMAN_HINTS = (
        "验证码", "captcha", "风控", "异常请求", "登录已失效",
        "需要重新登录", "滑块", "安全验证", "security check",
        "simulate", "click to verify",
    )
    RETRY_HINTS = (
        "超时", "timeout", "timed out", "connection", "网络",
        "refused", "econnreset", "eof", "429", "503", "504",
        "econnaborted", "broken pipe",
    )
    MAX_ATTEMPTS = 3

    def __init__(self, hub, conn):
        self.hub = hub
        self.conn = conn
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._loop, daemon=True,
                                        name="task-manager")
        self._worker.start()

    # ---------------- 对外接口 ----------------

    def submit(self, kind, article_id=None, platforms=None, account="default",
               draft_only=False, **extra):
        """提交一个任务，立即返回 task_id。"""
        task_id = uuid.uuid4().hex[:10]
        db.create_task(self.conn, task_id, kind, article_id=article_id,
                       platforms=platforms, account=account,
                       draft_only=draft_only)
        # 附带的扩展参数（如 assist 的 url）直接写进 result 初始段
        if extra:
            db.update_task(self.conn, task_id, result=json.dumps(extra,
                                                                 ensure_ascii=False))
        self._wake.set()
        return task_id

    def get(self, task_id):
        """按 task_id 查任务状态。"""
        return db.get_task(self.conn, task_id)

    def list(self, limit=50, kind=None, status=None, offset=0):
        return db.list_tasks(self.conn, limit=limit, kind=kind, status=status,
                             offset=offset)

    def count(self, kind=None, status=None):
        return db.count_tasks(self.conn, kind=kind, status=status)

    def resume(self, task_id, approved=True):
        """恢复 waiting_human 任务。

        两种语义（2026-10-14）：
        - **中断模式（pending_user）**：用户在平台上已亲自发布 → approved=True 只
          **标记完成**（绝不重跑——重跑会在平台上发第二遍）；approved=False 标记放弃。
        - **风控中断（任务执行中失败）**：approved=True 重跑；False 放弃。
        """
        with self._lock:
            task = db.get_task(self.conn, task_id)
            if not task:
                return {"ok": False, "error": "任务不存在"}
            if task["status"] != "waiting_human":
                return {"ok": False, "error": f"任务当前状态为 {task['status']}，不可恢复"}
            was_pending_user = _has_pending_user(_load_result(task))
            if approved and was_pending_user:
                # 用户在平台上已自行发布：标记完成并落库（不再跑发布逻辑）
                db.update_task(self.conn, task_id, status="ok",
                               message="用户已在平台上完成发布（中断模式收尾）",
                               finished_at=time.time())
            elif approved:
                db.update_task(self.conn, task_id,
                               status="pending", error="",
                               message="人工已处理，重新执行")
            else:
                db.update_task(self.conn, task_id,
                               status="failed", message="已放弃",
                               finished_at=time.time())
        if approved and not was_pending_user:
            self._wake.set()
        return {"ok": True, "status": db.get_task(self.conn, task_id)["status"]}

    def close(self):
        self._stop.set()
        self._wake.set()
        # 等 worker 线程结束（最多 30 秒），避免浏览器资源未清理
        self._worker.join(timeout=30)
        evt_mod.stop_dispatcher()

    # ---------------- 内部实现 ----------------

    def _loop(self):
        while not self._stop.is_set():
            task = self._next_pending()
            if task is None:
                self._wake.wait(2.0)
                self._wake.clear()
                continue
            self._run_task(task)

    def _next_pending(self):
        with self._lock:
            rows = db.next_pending_tasks(self.conn, limit=10)
            if not rows:
                return None
            task = rows[0]
            db.update_task(self.conn, task["task_id"],
                           status="running", updated_at=time.time())
            return task

    def _run_task(self, task):
        task_id = task["task_id"]
        kind = task["kind"]
        attempts = task.get("attempts", 0) + 1
        db.update_task(self.conn, task_id, attempts=attempts,
                       message="执行中…")
        try:
            result = self._dispatch(kind, task)
            if _has_pending_user(result):
                # 中断模式（assist/manual）：浏览器已打开、等用户在平台上操作。
                # 不是失败（不重试）、也不是完成（不标 ok）——挂 waiting_human，
                # 用户处理完走 resume（resume 对 pending_user 是"标记完成"语义，
                # 不重跑，避免重复发布）。
                msg = _pending_user_message(result)
                db.update_task(self.conn, task_id, status="waiting_human",
                               result=json.dumps(result, ensure_ascii=False),
                               message=msg, error="")
                completed = db.get_task(self.conn, task_id)
                if completed:
                    try:
                        completed["platforms"] = json.loads(completed.get("platforms") or "[]")
                    except Exception:   # aqg: top-level boundary（事件装饰字段解析失败不阻断 emit）
                        pass
                    evt_mod.on_task_complete(completed)
                return
            db.update_task(self.conn, task_id, status="ok",
                           result=json.dumps(result, ensure_ascii=False),
                           message="完成", finished_at=time.time())
            # 触发事件：任务完成
            completed = db.get_task(self.conn, task_id)
            if completed:
                try:
                    completed["platforms"] = json.loads(completed.get("platforms") or "[]")
                    completed["result"] = json.loads(completed.get("result") or "{}")
                except Exception:   # aqg: top-level boundary（同上：装饰字段解析失败不阻断 emit）
                    pass
                evt_mod.on_task_complete(completed)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            action, reason = self._triage(kind, err, attempts)
            if action == "retry":
                # 重试：任务回到 pending，不算终态，不 emit on_task_complete
                # （旧实现在这里也调 on_task_complete，会把 "retry" 事件
                # 发给订阅方，语义不对：任务并没结束）。
                db.update_task(self.conn, task_id, status="pending",
                               error=err, message=f"自动重试（第 {attempts} 次）")
                self._wake.set()   # 立即再消费
            else:
                status = "waiting_human" if action == "human" else "failed"
                db.update_task(self.conn, task_id, status=status,
                               error=err, message=reason,
                               finished_at=time.time())
                # 触发事件：任务失败/等人工
                completed = db.get_task(self.conn, task_id)
                if completed:
                    try:
                        completed["platforms"] = json.loads(completed.get("platforms") or "[]")
                    except Exception:
                        pass
                    evt_mod.on_task_complete(completed)
        finally:
            db.prune_tasks(self.conn)

    def _dispatch(self, kind, task):
        """按任务类型分发到业务层。返回可 JSON 化的结果。"""
        article_id = task.get("article_id")
        platforms = json.loads(task.get("platforms") or "[]")
        account = task.get("account") or "default"
        draft_only = bool(task.get("draft_only"))

        if kind == "publish":
            extra = json.loads(task.get("result") or "{}")
            return self.hub.publish(article_id, platforms, account, draft_only,
                                    settings=extra.get("settings"),
                                    human_confirmed=bool(extra.get("human_confirmed")),
                                    mode=extra.get("mode") or None)
        if kind == "update":
            return self.hub.update(article_id, platforms, account)
        if kind == "sync":
            return self.hub.sync_pending(account)
        if kind == "refresh":
            extra = json.loads(task.get("result") or "{}")
            return self.hub.refresh(platforms[0] if platforms else None,
                                    account, limit=extra.get("limit", 50))
        if kind == "login":
            extra = json.loads(task.get("result") or "{}")
            return self.hub.login(platforms[0] if platforms else None, account,
                                  timeout=extra.get("timeout", 600),
                                  on_captcha=extra.get("on_captcha"))
        if kind == "assist":
            extra = json.loads(task.get("result") or "{}")
            return self.hub.assist_open(platforms[0] if platforms else None,
                                        extra.get("url", ""), account=account)
        raise ValueError(f"未知任务类型: {kind}")

    # 永久失败关键词：命中就立即 fail，不重试（重试也不会改变结果）。
    PERMANENT_HINTS = (
        "合规门禁", "422", "门禁拒绝", "安全扫描", "高危内容",
    )

    def _triage(self, kind, err, attempts):
        """确定性失败分诊。返回 (action, reason)。

        优先级：HUMAN > RETRY > PERMANENT > 未分类（重试一次→fail）。
        F-20 改进：未分类错误第 1 次给一次自动重试机会。
        新增 PERMANENT_HINTS：合规门禁拒绝 / 422 等确定性错误立即 fail，不浪费重试。
        """
        low = (err or "").lower()
        for h in self.HUMAN_HINTS:
            if h.lower() in low:
                return "human", "命中风控/验证码/登录失效，需要人工处理"
        for h in self.RETRY_HINTS:
            if h.lower() in low:
                if attempts < self.MAX_ATTEMPTS:
                    return "retry", "网络/临时错误，自动重试"
                return "fail", f"重试 {self.MAX_ATTEMPTS} 次仍失败"
        # 永久错误：合规门禁 / 4xx 客户端错误，重试也不会变
        for h in self.PERMANENT_HINTS:
            if h.lower() in low:
                return "fail", f"确定性错误（{h}），不再重试"
        # 未分类错误：第一次给一次重试机会，第二次永久 fail
        if attempts < 2:
            return "retry", "未分类错误，自动重试一次"
        return "fail", f"未分类错误（已重试 {attempts} 次）"
