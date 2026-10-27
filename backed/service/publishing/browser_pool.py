# -*- coding: utf-8 -*-
"""无头浏览器实例池。

管理 (platform, account) → BuiltinBrowser 的 LRU 池，带 per-key 互斥。
Hub 通过 self._browser_pool 委托；close_all_browsers() 仅关浏览器，
scheduler/TaskManager 的关停仍由 Hub.close_all() 编排。
"""

import threading

from service.publishing.browser import BuiltinBrowser, browser_thread_run


class BrowserPoolManager:
    """headless 浏览器实例池：LRU + per-key 互斥 + POOL_MAX 上限。"""

    POOL_MAX = 4

    def __init__(self, headless=True):
        self.headless = headless
        self._pool = {}
        self._pool_order = []
        self._pool_lock = threading.Lock()
        self._busy = {}

    def _busy_for(self, platform, account):
        """取（或建）per-key 互斥锁。"""
        key = (platform, account)
        with self._pool_lock:
            if key not in self._busy:
                self._busy[key] = threading.Lock()
            return self._busy[key]

    def _acquire(self, platform, account):
        """拿（或起）一个 headless 实例。拿到的不关，池里常驻复用。"""
        key = (platform, account)
        victims = []
        br = None
        with self._pool_lock:
            br = self._pool.get(key)
            if br is not None:
                if key in self._pool_order:
                    self._pool_order.remove(key)
                self._pool_order.append(key)
            else:
                br = BuiltinBrowser(platform, account, headless=self.headless)
                self._pool[key] = br
                self._pool_order.append(key)
                while len(self._pool_order) > self.POOL_MAX:
                    old = self._pool_order.pop(0)
                    victim = self._pool.pop(old, None)
                    if victim:
                        victims.append(victim)
        # 淘汰实例必须在池锁外关闭：close() 会走浏览器线程并可能阻塞数秒，
        # 持锁关闭会把整池取用（含其它 key 的 _acquire）一起堵死。
        for victim in victims:
            try:
                browser_thread_run(victim.close)
            except Exception:  # aqg: top-level boundary 关闭失败不影响已取到的实例
                pass
        return br

    def _drop(self, platform, account):
        """实例疑似崩了：踢出池关掉，下次 _acquire 会起全新的。"""
        key = (platform, account)
        with self._pool_lock:
            victim = self._pool.pop(key, None)
            if key in self._pool_order:
                self._pool_order.remove(key)
        if victim:
            try:
                browser_thread_run(victim.close)
            except Exception:
                pass

    def close_all_browsers(self):
        """关池中所有浏览器（不清 scheduler/TaskManager）。"""
        with self._pool_lock:
            victims = list(self._pool.values())
            self._pool.clear()
            self._pool_order.clear()
        for br in victims:
            try:
                browser_thread_run(br.close)
            except Exception:
                pass

    @property
    def pool_size(self):
        """当前池中活跃浏览器数（可观测性指标用）。"""
        return len(self._pool)
