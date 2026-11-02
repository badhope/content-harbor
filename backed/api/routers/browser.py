# -*- coding: utf-8 -*-
"""浏览器操控面端点：带登录态抓取 / 截图。

同步执行（FastAPI sync def 走线程池），内部整体超时兜底。
安全边界：scheme 白名单 + SSRF 黑名单（browser_ops.validate_url）；
浏览器进程以本机用户身份运行，凭据本就属于本机用户，与 assist 同级风险。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..deps import get_hub
from .accounts import _check_platform, _check_account

router = APIRouter()


class BrowserFetchIn(BaseModel):
    url: str
    account: str = "default"
    timeout: int = 60          # 页面加载超时（秒）
    want_html: bool = False    # 导入场景需要原始 HTML 时打开


class BrowserShotIn(BaseModel):
    url: str
    account: str = "default"
    full_page: bool = False
    timeout: int = 60


def _check_url(url: str) -> str:
    from service.publishing.browser_ops import validate_url
    try:
        return validate_url(url)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/browser/{platform}/fetch")
def browser_fetch(platform: str, body: BrowserFetchIn):
    """带平台登录态抓取页面正文（登录墙后的内容也能拿）。"""
    _check_platform(platform)
    _check_account(body.account)
    _check_url(body.url)
    try:
        from service.publishing import browser_ops
        return browser_ops.browser_fetch(get_hub(), platform, body.url,
                                         account=body.account,
                                         timeout=body.timeout,
                                         want_html=body.want_html)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(502, f"浏览器抓取失败: {type(e).__name__}: {e}")


@router.post("/browser/{platform}/screenshot")
def browser_screenshot(platform: str, body: BrowserShotIn):
    """带登录态打开页面截图，落盘 data/exports/screenshots/。"""
    _check_platform(platform)
    _check_account(body.account)
    _check_url(body.url)
    try:
        from service.publishing import browser_ops
        return browser_ops.browser_screenshot(get_hub(), platform, body.url,
                                              account=body.account,
                                              full_page=body.full_page,
                                              timeout=body.timeout)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(502, f"浏览器截图失败: {type(e).__name__}: {e}")
