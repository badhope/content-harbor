# -*- coding: utf-8 -*-
"""REST API 总入口：聚合全部子路由器，统一挂载到 ``/api/hub`` 前缀。

子路由器按领域拆分（``api/routers/*.py``）：system / articles / accounts /
publishing / tags / schedules / webhooks / ai。本模块只做 include 聚合，
不再直接定义任何 ``@hub_router`` 路由。

保持向后兼容的导出：``get_hub`` / ``get_task_manager`` / ``hub_router`` / ``register_hub`` / ``app``。
"""

import os

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import attach_auth_middleware
from .deps import get_hub, get_task_manager  # noqa: F401  (re-export for backward compat)

# 子路由器
from .routers import (  # noqa: F401
    system_router,
    articles_router,
    accounts_router,
    publishing_router,
    tags_router,
    schedules_router,
    webhooks_router,
    ai_router,
    media_router,
)

hub_router = APIRouter()

# ---- 聚合全部子路由 ----
hub_router.include_router(system_router)
hub_router.include_router(articles_router)
hub_router.include_router(accounts_router)
hub_router.include_router(publishing_router)
hub_router.include_router(tags_router)
hub_router.include_router(schedules_router)
hub_router.include_router(webhooks_router)
hub_router.include_router(ai_router)
hub_router.include_router(media_router)


# ---------------- CORS（默认仅同源；跨域部署时用环境变量 CORS_ORIGINS 白名单） ----------------
_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]


def register_hub(app: FastAPI) -> None:
    """把平台发布 REST API 挂载进 FastAPI 应用（版本化前缀 ``/api/v1/hub``）。

    前端 ``api.js`` 的 baseURL 必须同步改为 ``/api/v1/hub``。
    """
    attach_auth_middleware(app)
    app.include_router(hub_router, prefix="/api/v1/hub")


# ---------------- 独立运行入口 ----------------
app = FastAPI(title="AI 内容中台", version="0.7.0")
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )
register_hub(app)
