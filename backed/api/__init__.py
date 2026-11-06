"""API 路由包：聚合各版本路由并挂载到应用。

注：`register_hub`（挂 `/api/v1/hub`）在 `api/hub.py` 里，才是实际入口。
本模块只保留 `hub_router` 导出（供 `app/routes.py` 列举路由信息）。
旧版 `register_routers`（挂 `/api/hub`、未版本化）已因与 `register_hub` 不一致
而删掉，避免使用者以为 prefix 可配置但实际写死 `/api/v1/hub`。
"""

from fastapi import FastAPI  # noqa: F401  (re-exported for API compat)

from .hub import hub_router  # noqa: F401  (exported)

__all__ = ["hub_router", "get_router_info"]


def _iter_routes(router, prefix=""):
    """递归展开 router 里的全部叶子路由。

    FastAPI 0.141 起 ``include_router`` 不再把子路由拍平进 ``.routes``，
    而是插入一个 ``_IncludedRouter`` 包装（只有 ``original_router`` 与
    ``include_context.prefix``）。旧实现直接读包装对象上的 ``path``，
    于是 /api/info 返回十条空记录——接口在、内容全丢，比 404 更难发现。
    """
    for route in getattr(router, "routes", []):
        sub = getattr(route, "original_router", None)
        if sub is not None:
            ctx = getattr(route, "include_context", None)
            sub_prefix = getattr(ctx, "prefix", "") or ""
            yield from _iter_routes(sub, prefix + sub_prefix)
            continue
        path = getattr(route, "path", None)
        if path is None:
            continue
        yield route, prefix + path


def get_router_info() -> dict:
    """当前 API 路由概览（调试接口 /api/info 用）。"""
    base = "/api/v1/hub"
    routes = []
    for route, path in _iter_routes(hub_router):
        routes.append(
            {
                "path": base + path,
                "methods": sorted(getattr(route, "methods", None) or []),
                "name": getattr(route, "name", "") or "",
                "tags": list(getattr(route, "tags", None) or []),
            }
        )
    routes.sort(key=lambda r: (r["path"], r["methods"]))
    return {
        "prefix": base,  # 与 register_hub 保持一致
        "count": len(routes),
        "routes": routes,
    }

