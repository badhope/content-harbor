# -*- coding: utf-8 -*-
"""平台适配器包：**导入本包即完成全部适配器注册**。

适配器用 `@register` 装饰器往 `base.ADAPTERS` 里登记自己，副作用发生在
模块被导入的那一刻。原先这件事只在 `service.py` 里做，于是任何没先创建
Hub 的进程（`GET /api/hub/platforms`、`mcp_server.list_platforms`）拿到的
都是空注册表——接口返回空列表，平台像"不存在"。

现在把导入集中到包初始化里：谁 import 这个包谁就得到完整注册表。
新增适配器时在下面加一行即可（保持 `service.py` 里那条导入不删，双保险）。
"""

from . import (  # noqa: F401
    bilibili,
    cnblogs,
    csdn,
    devto,
    github_pages,
    juejin,
    metaweblog,
    oschina,
    segmentfault,
    toutiao,
    wechat,
    wuyi_cto,
    zhihu,
)

from .base import ADAPTERS, PlatformAdapter, PlatformError, get_adapter  # noqa: F401
