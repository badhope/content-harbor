"""应用配置（不依赖 docs/，直接读 config.settings）"""

from typing import Any, Dict

from config import settings


class AppConfig:
    """FastAPI 应用与 CORS 等装配配置。"""

    @staticmethod
    def get_app_config() -> Dict[str, Any]:
        return {
            "title": getattr(settings, "project_name", "FastAPI App"),
            "description": getattr(settings, "APP_DESCRIPTION", "FastAPI enterprise starter"),
            "version": getattr(settings, "project_version", "1.0.0"),
            # 不要把 settings.debug 直接传给 FastAPI：Starlette 在 debug=True 时
            # 会用 ServerErrorMiddleware 返回 HTML traceback，绕过我们统一的
            # JSON 异常处理器（实测 500 响应里带出本地绝对路径、源码行与调用栈）。
            # 调试信息一律走日志，响应体永远是 {code,message,success,data}。
            "debug": False,
            "docs_url": getattr(settings, "docs_url", "/docs"),
            "redoc_url": getattr(settings, "redoc_url", "/redoc"),
            "openapi_url": getattr(settings, "openapi_url", "/openapi.json"),
        }

    @staticmethod
    def get_cors_config() -> Dict[str, Any]:
        return {
            "allow_origins": settings.cors_origins,
            "allow_credentials": settings.cors_allow_credentials,
            "allow_methods": settings.cors_allow_methods,
            "allow_headers": settings.cors_allow_headers,
        }


__all__ = ["AppConfig"]
