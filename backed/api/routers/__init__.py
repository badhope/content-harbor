# -*- coding: utf-8 -*-
"""子路由器聚合：由 ``api.hub`` 统一 include 进 ``hub_router``。"""

from .system import router as system_router
from .articles import router as articles_router
from .accounts import router as accounts_router
from .publishing import router as publishing_router
from .tags import router as tags_router
from .schedules import router as schedules_router
from .webhooks import router as webhooks_router
from .ai import router as ai_router
from .media import router as media_router
from .browser import router as browser_router

__all__ = [
    "system_router", "articles_router", "accounts_router",
    "publishing_router", "tags_router", "schedules_router",
    "webhooks_router", "ai_router", "media_router", "browser_router",
]
