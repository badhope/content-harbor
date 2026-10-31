"""应用工厂：lifespan + 中间件 + 异常 + 路由。"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from utils.logger import setup_logging, get_logger
from .config import AppConfig
from .middleware import setup_middleware
from .exceptions import setup_exception_handlers
from .routes import setup_routes
from api.hub import register_hub

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """日志初始化。HTTPS/TLS 由反向代理负责，此处不强制跳转。"""
    logger.info("正在启动应用...")
    sweeper = None
    try:
        setup_logging(level=getattr(settings, "log_level", "INFO"))
        # 登录态自动巡检（HUB_AUTH_SWEEP_MINUTES=0 禁用；默认 30 分钟一轮）。
        # 启动失败不阻塞应用——巡检是增值能力，不能拖死主链路。
        try:
            from service.publishing.auth_sweep import AuthSweeper
            from api.deps import get_hub
            sweeper = AuthSweeper(get_hub())
            sweeper.start()
        except Exception as e:
            logger.warning("登录态巡检未启动: %s", e)
        logger.info("应用启动成功 - 环境: %s", settings.environment)
        yield
    except Exception as e:
        logger.error("应用启动失败: %s", e)
        raise
    finally:
        if sweeper is not None:
            sweeper.stop()
        logger.info("正在关闭应用资源...")
        logger.info("应用已安全关闭")


def _mount_web_ui(app: FastAPI) -> None:
    """挂载 Vue 管理界面（`web/` 构建产物落在 `server/static/`）。

    构建方式：``cd web && pnpm build``。产物不存在时跳过，后端仍照常提供
    REST API；``/`` 返回 ``index.html``，静态资源挂在 ``/static`` 下
    （与 vite 的 ``base: '/static/'`` 约定一致）。
    """
    static_dir = Path(__file__).resolve().parents[2] / "server" / "static"
    if not static_dir.is_dir():
        return
    # 构建产物文件名带内容 hash，可以永久缓存；index.html 必须每次校验。
    app.mount("/static", StaticFiles(directory=str(static_dir), html=False),
              name="static")
    index_file = static_dir / "index.html"
    if index_file.is_file():

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            """Web 管理界面入口：浏览器打开根路径即是这个。"""
            return FileResponse(str(index_file))


def create_app() -> FastAPI:
    app = FastAPI(**AppConfig.get_app_config(), lifespan=lifespan)

    # 非 debug 时限制 Host（需在 .env 设置 ALLOWED_HOSTS，否则不启用）
    if not settings.debug:
        hosts = getattr(settings, "allowed_hosts", None)
        if hosts:  # 非空列表才启用 TrustedHost
            app.add_middleware(
                TrustedHostMiddleware,
                allowed_hosts=hosts,
            )

    # 压缩：前端首屏要拉 element-plus（949KB 未压缩 / 305KB gzip）与 Vue 运行时。
    # 桌面版直接由 uvicorn 提供静态资源，没有 nginx 代劳 gzip，不装这层
    # 每个用户都要多下 ~1MB。阈值 1KB：小响应压了反而更慢。
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    setup_middleware(app)
    setup_exception_handlers(app)
    setup_routes(app)
    # 平台发布 REST API（api/hub.py）挂进主后端
    register_hub(app)
    # Web 管理界面（构建产物存在则挂载）
    _mount_web_ui(app)
    return app
