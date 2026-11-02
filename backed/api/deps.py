# -*- coding: utf-8 -*-
"""子路由器共享依赖：get_hub() / get_task_manager() / 鉴权检查。"""

import threading

_hub_instance = None
_hub_lock = threading.Lock()


def get_hub():
    """惰性创建 Hub 实例，避免模块导入时产生副作用。

    必须加锁：前端启动会并发打 status/platforms/articles/tasks/pending-human
    五六个端点，FastAPI 把它们丢进线程池，多个线程同时看到 ``_hub_instance is None``
    就会各自 new 一个 Hub——每个 Hub 都开一条连接并跑 ``PRAGMA journal_mode=WAL``
    + 建表脚本，SQLite 上互斥失败直接 ``database is locked`` → 首屏 500。
    实测 8 线程并发首次调用可稳定复现。
    """
    global _hub_instance
    if _hub_instance is not None:
        return _hub_instance
    with _hub_lock:
        if _hub_instance is None:
            from service.publishing.service import Hub
            _hub_instance = Hub(headless=True)
    return _hub_instance


def get_task_manager():
    """返回 Hub 内部的 TaskManager。

    .. deprecated:: 统一通过 ``get_hub().tasks`` 访问。
    """
    return get_hub().tasks
