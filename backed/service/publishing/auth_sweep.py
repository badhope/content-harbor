# -*- coding: utf-8 -*-
"""登录态自动巡检：周期核查已登录账号，过期即标离线并发事件。

"持续记忆登录"的活的部分：profile 持久化解决"记住"，巡检解决"知道还有效"。
  - 周期默认 30 分钟，env HUB_AUTH_SWEEP_MINUTES 可调，0=禁用
  - 只核查 status=logined 的账号（offline 的等用户手动重登）
  - check 抛异常 ≠ 登录过期（可能是浏览器弹不出来/网络抖动），只记日志不动状态
    ——误标 offline 会让前端就绪摘要变红、任务被拒，比"晚发现过期"伤害大
  - 巡检前查平台忙：与发布/登录任务抢浏览器会互相干扰，忙则本轮跳过
  - 过期 → accounts 表标 offline + webhook 事件 auth.expired（前端就绪摘要
    与发布前检查自动感知，不需要用户手动点「检测」）

线程模型：daemon 线程随应用 lifespan 启停；状态（last_sweep 等）模块级持有，
/status 原样下发。
"""

import threading
import time

from utils.logger import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
_state = {"last_sweep": None, "next_run": None, "checked": 0, "expired": 0,
          "errors": 0, "running": False}

_sweeper = None   # 单例引用，factory lifespan 持有


def interval_minutes() -> int:
    env = __import__("os").environ.get("HUB_AUTH_SWEEP_MINUTES", "").strip()
    try:
        return max(0, int(env)) if env else 30
    except ValueError:
        return 30


def status() -> dict:
    with _lock:
        return dict(_state)


class AuthSweeper:
    """后台巡检线程。hub 只需提供 check(platform, account) 与 conn。"""

    def __init__(self, hub, interval_min: int | None = None):
        self.hub = hub
        self.interval = (interval_min if interval_min is not None
                         else interval_minutes())
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self.interval <= 0:
            logger.info("登录态巡检已禁用（HUB_AUTH_SWEEP_MINUTES=0）")
            return
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="auth-sweep")
        self._thread.start()
        logger.info("登录态巡检已启动，周期 %s 分钟", self.interval)

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                return False
        return True

    def _loop(self):
        # 首轮等一个周期再跑：应用刚启动时浏览器池/网络未必就绪，且登录态
        # 刚落盘多半有效，先让业务跑。
        self._state_next(time.time() + self.interval * 60)
        while not self._stop.wait(self.interval * 60):
            try:
                self.sweep_once()
            except Exception as e:
                logger.warning("登录态巡检异常: %s", e)
                with _lock:
                    _state["errors"] += 1
            self._state_next(time.time() + self.interval * 60)

    @staticmethod
    def _state_next(ts: float):
        with _lock:
            _state["next_run"] = ts

    def sweep_once(self):
        """单轮巡检：逐个核查 logined 账号。可在测试里直接调用。"""
        from service.publishing import db
        from service.publishing import events

        with _lock:
            if _state["running"]:
                return
            _state["running"] = True
        checked = expired = 0
        try:
            rows = db.list_accounts(self.hub.conn)
            for r in rows:
                if self._stop.is_set():
                    break
                if r["status"] != "logined":
                    continue
                platform, account = r["platform"], r["name"]
                # 忙判定：该平台/账号正在登录/发布/辅助操作时跳过本轮
                try:
                    busy_cm = self.hub._busy_for(platform, account)
                    if getattr(busy_cm, "locked", None) and busy_cm.locked():
                        continue
                except Exception:
                    pass
                checked += 1
                try:
                    ok = bool(self.hub.check(platform, account))
                except Exception as e:
                    logger.info("巡检 %s/%s 出错（不改动状态）: %s",
                                platform, account, type(e).__name__)
                    with _lock:
                        _state["errors"] += 1
                    continue
                if not ok:
                    expired += 1
                    db.upsert_account(self.hub.conn, platform, account,
                                      r["profile_dir"] or "-", "offline")
                    try:
                        events.emit("auth.expired", {"platform": platform,
                                                     "account": account})
                    except Exception:
                        pass
                    logger.warning("登录态过期: %s/%s（已标离线并发事件）",
                                   platform, account)
        finally:
            with _lock:
                _state["running"] = False
                _state["last_sweep"] = time.time()
                _state["checked"] += checked
                _state["expired"] += expired
        if checked:
            logger.info("巡检完成：检查 %s 个账号，过期 %s", checked, expired)
