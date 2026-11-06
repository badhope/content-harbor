# -*- coding: utf-8 -*-
"""API 鉴权与安全中间件：IP 校验 + Token 验证 + X-Forwarded-For 校验。

从 ``api/hub.py`` 抽出，让子路由器也能复用同一套鉴权语义。
"""

import json
import os
from ipaddress import ip_address, ip_network
from typing import Optional

from fastapi import FastAPI, Request as FastAPIRequest
from starlette.requests import Request

from service.publishing import observability as obs

from service.publishing.db import config_file

# ---------------- 可选 API 鉴权 ----------------

# 基准目录：仅作为测试打桩点存在（既有用例 monkeypatch.setattr(auth_mod, "ROOT", tmp_path)
# 期望 token 写到该目录）。真实路径一律走 config_file()，它才是环境变量的唯一口径。
ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent

_api_token_cache: Optional[tuple[float, str]] = None


def _resolve_token() -> str:
    """Token 解析：HARBOR_API_TOKEN 环境变量 > config.json；都缺失时自动生成并落盘。"""
    tok = os.environ.get("HARBOR_API_TOKEN", "").strip()
    if tok:
        return tok
    try:
        # 路径解析：
        #   - ROOT 被测试打过桩 → 写到桩目录（既有 bootstrap 用例的契约）
        #   - 否则走 config_file()，它是"环境变量 > 数据根 > 旧版位置"的唯一口径
        import pathlib as _pathlib
        default_root = _pathlib.Path(__file__).resolve().parent.parent
        cfg_path = (ROOT / "config.json") if ROOT != default_root else config_file()
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
        tok = (cfg or {}).get("api_token") or ""
        if not tok:
            # uuid4 由 os.urandom 支撑，122 位熵足够做 API token；
            # 保持 UUID 形态是 README 与既有测试的契约（"自动生成 UUID"）。
            import uuid as _uuid
            tok = str(_uuid.uuid4())
            cfg_path.write_text(json.dumps({**(cfg or {}), "api_token": tok},
                                           ensure_ascii=False, indent=2),
                                encoding="utf-8")
            obs.get_logger(__name__).warning(
                "api_token 未配置：已自动生成并写入 %s（生产环境建议改设 HARBOR_API_TOKEN）",
                cfg_path)
    except Exception:  # aqg: top-level boundary
        import uuid as _uuid
        tok = str(_uuid.uuid4())
    return tok


def _api_token() -> str:
    """读取 API Token（带 1s 缓存，避免每次请求读盘；conftest 打桩点）。"""
    global _api_token_cache
    import time as _t
    now = _t.monotonic()
    if _api_token_cache and (now - _api_token_cache[0]) < 1.0:
        return _api_token_cache[1]
    tok = _resolve_token()
    _api_token_cache = (now, tok)
    return tok


def _trusted_sources() -> list:
    """本机回环 + 测试客户端 + 配置的信任网段。"""
    nets = ["127.0.0.0/8", "::1/128"]
    extra = os.environ.get("TRUSTED_NETS", "").strip()
    if extra:
        nets += [n.strip() for n in extra.split(",") if n.strip()]
    return nets


def _trusted_proxies() -> list:
    """受信反向代理 IP 网段。"""
    nets = os.environ.get("TRUSTED_PROXIES", "").strip()
    return [n.strip() for n in nets.split(",") if n.strip()]


def get_client_ip(request: FastAPIRequest):
    """取真实客户端 IP（安全策略：仅信任代理网段内才看 X-Forwarded-For）。"""
    host = request.client.host if request.client else ""
    trusted = _trusted_proxies()
    if trusted and host:
        try:
            host_ip = ip_address(host)
            if any(host_ip in ip_network(net, strict=False) for net in trusted):
                fwd = request.headers.get("x-forwarded-for")
                if fwd:
                    # 只采信由受信代理追加的最后一跳，客户端自带的 XFF 前缀不可信。
                    last = fwd.split(",")[-1].strip()
                    try:
                        return ip_address(last)
                    except ValueError:
                        pass
        except (ValueError, TypeError):
            pass
    if host:
        try:
            return ip_address(host)
        except ValueError:
            pass
    return None


def is_trusted(request: FastAPIRequest) -> bool:
    """该请求是否来自信任来源（本机 / 信任网段）。"""
    ip = get_client_ip(request)
    if ip is None:
        return False
    for net in _trusted_sources():
        try:
            if ip in ip_network(net, strict=False):
                return True
        except Exception:
            continue
    return False


def attach_auth_middleware(app: FastAPI) -> None:
    """注册 API 鉴权 + 访问日志中间件。"""

    # 无需 Token 的探活路径：容器编排与负载均衡器必须能在未认证时判断存活，
    # 否则 Docker healthcheck / k8s probe 一律 401，服务永远起不来。
    # 这些路径只返回进程状态，不暴露任何业务数据。
    _PUBLIC_PATHS = frozenset({"/health", "/api/health"})

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        token = _api_token()
        # 常量时间比较：`!=` 会按首个不同字节提前返回，攻击者可用响应时间
        # 逐字节猜 token（经典时序侧信道）。hmac.compare_digest 与内容无关。
        import hmac as _hmac
        supplied = request.headers.get("X-API-Token") or ""
        token_ok = bool(token) and bool(supplied) and _hmac.compare_digest(supplied, token)
        if token and not is_trusted(request) \
                and request.url.path.rstrip("/") not in _PUBLIC_PATHS \
                and not token_ok:
            # 中间件层抛 HTTPException 不走 FastAPI 异常处理器，会退化成 500；
            # 必须直接返回 JSONResponse（审计 B4）。
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=401,
                                content={"code": 401, "message": "无效的 API Token",
                                         "success": False, "data": None})
        return await call_next(request)

    _LOG_SKIP = ("/metrics", "/health", "/docs", "/openapi.json", "/redoc")

    @app.middleware("http")
    async def _access_log(request: Request, call_next):
        import time as _t
        t0 = _t.time()
        resp = await call_next(request)
        dur = round(_t.time() - t0, 3)
        path = request.url.path
        if not any(path.startswith(s) for s in _LOG_SKIP):
            status = getattr(resp, "status_code", 0)
            ok = 200 <= status < 400
            obs.METRICS.incr(f"api.{'ok' if ok else 'fail'}")
            obs.METRICS.observe("api.dur", dur)
            obs.METRICS.incr(f"api.{request.method.lower()}.hits")
            obs.emit("api.access",
                     method=request.method, path=path, status=status,
                     duration=dur,
                     client_ip=(request.client.host if request.client else ""))
        return resp
