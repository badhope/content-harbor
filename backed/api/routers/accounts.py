# -*- coding: utf-8 -*-
"""账号 / 平台端点：登录 / 登出 / 诊断 / 验证码 / 辅助操作 / 刷新。"""


import re
import threading
import time
import uuid
from urllib.parse import urlparse

import ipaddress

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..deps import get_hub

router = APIRouter()

# 平台/账号名只能小写字母数字下划线短横。拒绝 `..` 或路径分隔符，
# 避免 `/accounts/../../etc` 之类的拼接到 PROFILE_ROOT / ".."_default
# 上，把 shutil.rmtree 变成删任意目录。
_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _check_platform(platform: str) -> str:
    if not _SAFE_NAME.match(platform or ""):
        raise HTTPException(400, "platform 只能含小写字母数字下划线短横，且长度 1-64")
    return platform


def _check_account(account: str) -> str:
    if not _SAFE_NAME.match(account or ""):
        raise HTTPException(400, "account 只能含小写字母数字下划线短横，且长度 1-64")
    return account


def _check_url_safety(url: str) -> str:
    """SSRF 防护：拒绝指向内网/本地/云元数据的 URL。

    允许的域名走白名单（平台域名），其余 IP 必须落在公网段。
    本机部署场景（桌面版）豁免：ASSIST_ALLOW_LOCAL=1 可放开 localhost。
    """
    import os
    allow_local = os.environ.get("ASSIST_ALLOW_LOCAL") == "1"
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host:
        raise HTTPException(422, "url 必须含有效域名")

    # 尝试解析为 IP（IP 字面量直接检查，域名查 DNS）
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private and not allow_local:
            raise HTTPException(422, f"url 指向内网地址 {host}，被 SSRF 防护拦截")
        if ip.is_loopback and not allow_local:
            raise HTTPException(422, f"url 指向本机回环 {host}，被 SSRF 防护拦截")
        if ip.is_link_local and not allow_local:
            raise HTTPException(422, f"url 指向链路本地 {host}（含云元数据 169.254.x.x），被拦截")
    except ValueError:
        # 非 IP 字面量（域名），做主机名黑名单兜底
        bad_hosts = ("localhost", "metadata.google.internal", "169.254.169.254")
        if any(host.lower() == b or host.lower().endswith(b) for b in bad_hosts):
            raise HTTPException(422, f"url 主机 {host} 在 SSRF 黑名单中")
    return url


class LoginIn(BaseModel):
    account: str = "default"
    timeout: int = 600
    on_captcha: str = "handoff"    # handoff=弹验证码交人工；abort=无人值守时直接放弃


class CaptchaIn(BaseModel):
    account: str = "default"
    wait: int = 180


class AssistIn(BaseModel):
    url: str
    timeout: int = 900


class BootstrapIn(BaseModel):
    username: str
    password: str
    account: str = "default"
    timeout: int = 600


# ---- cnblogs 应用内自动抠令牌 ----
#
# 为什么不走任务引擎：任务 DB 会把 extra 持久化，密码落库违反密钥纪律。
# 内存 job 注册表：凭据只传进工作线程，重启即失，DB 里零痕迹。
# 同一 platform+account 并发保护：已有 running job 时 409。

_BOOTSTRAP_JOBS: dict = {}
_BOOTSTRAP_LOCK = threading.Lock()


def _bootstrap_run(job_id, username, password, account, timeout):
    job = _BOOTSTRAP_JOBS[job_id]
    try:
        r = get_hub().bootstrap_cnblogs(username, password, account=account,
                                        timeout=timeout)
        job["status"] = "succeeded" if r.get("outcome") == "success" else "failed"
        job["result"] = r
        if job["status"] == "failed":
            job["error"] = str(r.get("reason") or r.get("error") or "登录未成功")
    except Exception as e:
        job["status"] = "failed"
        job["error"] = f"{type(e).__name__}: {e}"
    finally:
        job["finished_at"] = time.time()


@router.post("/accounts/cnblogs/bootstrap")
def bootstrap_cnblogs(body: BootstrapIn):
    """浏览器登录博客园并自动抠 MetaWeblog 三件套写 config.json。

    返回 job_id，轮询 GET /accounts/cnblogs/bootstrap/{job_id}。
    """
    _check_account(body.account)
    if not body.username or not body.password:
        raise HTTPException(422, "username 与 password 必填")
    with _BOOTSTRAP_LOCK:
        for j in _BOOTSTRAP_JOBS.values():
            if j.get("status") == "running":
                raise HTTPException(409, "已有 bootstrap 任务在跑，等它结束再试")
        job_id = uuid.uuid4().hex[:10]
        _BOOTSTRAP_JOBS[job_id] = {"status": "running", "started_at": time.time(),
                                   "finished_at": None, "result": None,
                                   "error": ""}
    threading.Thread(target=_bootstrap_run, daemon=True,
                     args=(job_id, body.username, body.password,
                           body.account, body.timeout)).start()
    return {"job_id": job_id, "status": "running",
            "poll": f"/accounts/cnblogs/bootstrap/{job_id}"}


@router.get("/accounts/cnblogs/bootstrap/{job_id}")
def bootstrap_cnblogs_job(job_id: str):
    job = _BOOTSTRAP_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job 不存在（服务重启后内存 job 即失效）")
    return {"job_id": job_id, **{k: job[k] for k in
            ("status", "result", "error", "started_at", "finished_at")}}


# ---- 平台列表 ----

@router.get("/platforms")
def platforms():
    """平台清单 + 代码级能力矩阵。

    能力矩阵不是文档：前端发布轨据此置灰不支持的选项（例如头条不支持
    原地更新、知乎支持回答问题），避免用户点了才发现平台不支持。
    """
    from service.publishing import adapters as _adapters  # noqa: F401
    from service.publishing.adapters.base import ADAPTERS, capability_of
    return [{"id": a.id, "name": a.name, "needs_browser": a.needs_browser,
             "capabilities": capability_of(a.id)}
            for a in ADAPTERS.values()]


# ---- 账号 ----

@router.get("/accounts")
def accounts():
    return get_hub().accounts()


@router.get("/accounts/{platform}/check")
def check_account(platform: str, account: str = "default"):
    _check_platform(platform)
    _check_account(account)
    return {"platform": platform, "logined": get_hub().check(platform, account)}


@router.post("/accounts/{platform}/login")
def login(platform: str, body: LoginIn):
    """异步启动登录：提交任务，轮询 GET /tasks/{task_id}。"""
    _check_platform(platform)
    _check_account(body.account)
    task_id = get_hub().tasks.submit(
        "login", platforms=[platform], account=body.account,
        timeout=body.timeout, on_captcha=body.on_captcha)
    return {"task_id": task_id, "platform": platform, "status": "pending",
            "poll": f"/tasks/{task_id}"}


@router.delete("/accounts/{platform}")
def logout(platform: str, account: str = "default"):
    """清除登录态：删 profile + cookie 快照，账号状态置离线。"""
    _check_platform(platform)
    _check_account(account)
    r = get_hub().logout(platform, account)
    return {"ok": True, **r}


@router.get("/accounts/{platform}/diagnose")
def diagnose(platform: str):
    """这个平台该怎么接、验证码怎么过——排查用。"""
    _check_platform(platform)
    return get_hub().diagnose(platform)


@router.post("/accounts/{platform}/solve-captcha")
def solve_captcha(platform: str, body: CaptchaIn):
    """打开页面就地处理验证码：先半自动试，不行转人工。"""
    _check_platform(platform)
    _check_account(body.account)
    return get_hub().solve_captcha(platform, body.account, body.wait)


@router.post("/accounts/{platform}/assist")
def assist_open(platform: str, body: AssistIn):
    """带登录态的内置有头浏览器打开平台页（人工步骤接管）。"""
    _check_platform(platform)
    _check_url_safety(body.url)
    task_id = get_hub().tasks.submit("assist", platforms=[platform], url=body.url)
    return {"task_id": task_id, "status": "pending", "poll": f"/tasks/{task_id}"}


@router.post("/refresh/{platform}")
def refresh(platform: str, limit: int = 50):
    """异步抓取平台文章入库。limit 传入 TaskManager，避免被 dispatch 硬编码为 50。"""
    _check_platform(platform)
    task_id = get_hub().tasks.submit("refresh", platforms=[platform], limit=limit)
    return {"task_id": task_id, "status": "pending", "poll": f"/tasks/{task_id}"}
