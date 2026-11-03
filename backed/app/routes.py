"""基础路由配置"""

from fastapi import FastAPI

from config import settings


def setup_routes(app: FastAPI) -> None:
    """设置基础路由
    
    Args:
        app: FastAPI应用实例
    """
    
    @app.get("/health", include_in_schema=False)
    async def health_check():
        """健康检查接口
        
        Returns:
            dict: 健康状态信息
        """
        return {
            "status": "ok", 
            "version": settings.api_version,
            "service": "内容港 Harbor"
        }
    
    @app.get("/api/info", include_in_schema=settings.debug)
    async def api_info():
        """API路由信息接口（仅在调试模式下可用）
        
        Returns:
            dict: 路由信息
        """
        from api import get_router_info
        return get_router_info()
