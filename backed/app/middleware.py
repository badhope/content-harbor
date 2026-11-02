"""中间件统一注册入口（配置驱动）

- CORS：始终注册（按环境收紧 cors_origins）
- 安全头 / 限流 / 审计：由 middleware_*_enabled 控制，默认关闭
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from utils.logger import get_logger
from .config import AppConfig
from middleware import (
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    AuditMiddleware,
    PublicBaseMiddleware,
)

logger = get_logger(__name__)


def setup_middleware(app: FastAPI) -> None:
    # 对外地址推断：媒体改写要在后台线程里知道"平台该访问哪个域名"，
    # 那里没有 request 上下文，只能靠这层把最近一次访问的 Host 记下来。
    app.add_middleware(PublicBaseMiddleware)

    cors_config = AppConfig.get_cors_config()
    app.add_middleware(CORSMiddleware, **cors_config)
    logger.info("中间件已启用: CORSMiddleware")

    if getattr(settings, "middleware_security_headers_enabled", False):
        app.add_middleware(SecurityHeadersMiddleware)
        logger.info("中间件已启用: SecurityHeadersMiddleware")

    if getattr(settings, "middleware_rate_limit_enabled", False):
        app.add_middleware(RateLimitMiddleware)
        logger.info("中间件已启用: RateLimitMiddleware")

    if getattr(settings, "middleware_audit_enabled", False):
        app.add_middleware(AuditMiddleware)
        logger.info("中间件已启用: AuditMiddleware")
