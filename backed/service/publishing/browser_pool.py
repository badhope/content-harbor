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
        # key -> Event：正在锁外关闭的实例。同 key 的 _acquire 必须等它关完，
        # 否则会在旧实例还占着 profile 时就 launch_persistent_context（必失败）。
        self._closing = {}

    def _busy_for(self, platform, account):
        """取（或建）per-key 互斥锁。"""
        key = (platform, account)
        with self._pool_lock:
            if key not in self._busy:
                self._busy[key] = threading.Lock()
            return self._busy[key]

    def _mark_closing(self, key):
        """在持有 _pool_lock 时登记"该 key 正在关闭"，返回 (event, 是否本次新建)。"""
        ev = self._closing.get(key)
        if ev is not None:
            return ev, False
        ev = threading.Event()
        self._closing[key] = ev
        return ev, True

    def _close_victims(self, victims):
        """锁外关闭，并在关完后释放同 key 的等待者。"""
        for key, victim, ev, owner in victims:
            try:
                browser_thread_run(victim.close)
            except Exception:  # aqg: top-level boundary 关闭失败不影响已取到的实例
                pass
            finally:
                if owner:
                    with self._pool_lock:
                        if self._closing.get(key) is ev:
                            self._closing.pop(key, None)
                    ev.set()

    def _acquire(self, platform, account):
        """拿（或起）一个 headless 实例。拿到的不关，池里常驻复用。"""
        key = (platform, account)
        while True:
            victims, br, wait_ev = [], None, None
            with self._pool_lock:
                wait_ev = self._closing.get(key)
                if wait_ev is None:
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
                            # 跳过正在被使用的 key：它的 _busy 锁已被占用，
                            # 现在 close 掉会让对方正在跑的发布直接 Target closed。
                            # 以前只看 LRU 顺序、不看占用，并发超过 POOL_MAX 个
                            # 平台时必然误杀（实测并发 5 个 key 就会触发）。
                            victim_key = None
                            for cand in list(self._pool_order):
                                lock = self._busy.get(cand)
                                if lock is None or not lock.locked():
                                    victim_key = cand
                                    break
                            if victim_key is None:
                                # 所有实例都在用：本次不淘汰，宁可暂时超限
                                break
                            self._pool_order.remove(victim_key)
                            victim = self._pool.pop(victim_key, None)
                            if victim:
                                ev, owner = self._mark_closing(victim_key)
                                victims.append((victim_key, victim, ev, owner))
            if wait_ev is not None:
                # 同 key 的旧实例还在关：等它关完再重试，避免 profile 被占用
                wait_ev.wait()
                continue
            # 淘汰实例必须在池锁外关闭：close() 会走浏览器线程并可能阻塞数秒，
            # 持锁关闭会把整池取用（含其它 key 的 _acquire）一起堵死。
            self._close_victims(victims)
            return br

    def _drop(self, platform, account):
        """实例疑似崩了：踢出池关掉，下次 _acquire 会起全新的。"""
        key = (platform, account)
        with self._pool_lock:
            victim = self._pool.pop(key, None)
            if key in self._pool_order:
                self._pool_order.remove(key)
            ev, owner = (self._mark_closing(key) if victim else (None, False))
        if victim:
            self._close_victims([(key, victim, ev, owner)])

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
