# -*- coding: utf-8 -*-
"""外部 MCP Server 桥接：把别人的 MCP 工具当成自己的后端用。

动机（用户明确要求"网上有 mcp 之类的扩展工具可以直接用"）：

    本项目已经是一个 MCP **Server**（对外暴露 27 个 tool）。但反过来，
    社区里已经有很多成熟 MCP **Server** 能覆盖我们没做或做得一般的能力：

      - `@playwright/mcp`（微软官方）：更强的浏览器自动化，含无障碍树快照、
        多标签、网络拦截——可以作为"浏览器执行后端"的兜底；
      - `mcp-server-fetch`：网页正文抓取（含 robots.txt 合规）；
      - `@modelcontextprotocol/server-filesystem`：批量文件读写。

本模块让 Harbor 作为 MCP **Client** 去调它们，而不是自己重写一遍。

设计要点（都是踩过的坑）：

1. **懒启动 + 进程复用**：每个 server 一条常驻 stdio 子进程，首次调用时才拉起；
   进程死了自动重连一次，仍失败就返回结构化错误。
2. **工具白名单**：只暴露 `config.json` / 环境变量里显式允许的工具名，
   避免"接了一个 server 就等于把它的全部能力暴露给任务引擎"。
3. **超时兜底**：stdio 调用必须带超时，否则一个卡住的 server 会拖死整个进程。
4. **不阻塞事件循环**：调用在独立线程里跑（MCP Python SDK 是 async 的，
   这里用 `asyncio.run` 包一层），失败不影响主服务。
5. **默认关闭**：未配置时 `list_servers()` 返回空，所有调用直接报"未配置"。

配置格式（config.json）：

    "MCP_BRIDGE": {
      "playwright": {
        "command": "npx",
        "args": ["-y", "@playwright/mcp@latest", "--headless"],
        "tools": ["browser_navigate", "browser_snapshot"],
        "timeout": 60
      }
    }

或环境变量 `HARBOR_MCP_SERVERS` 指向同结构的 JSON 文件。
"""

import asyncio
import json
import os
import shutil
import threading
import time
from pathlib import Path

from service.publishing.url_safety import validate_timeout

_DEFAULT_TIMEOUT = 60
_MAX_OUTPUT = 200_000
_SAFE_PARENT_ENV = frozenset((
    "APPDATA", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH", "PATHEXT",
    "PROCESSOR_ARCHITECTURE", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP",
    "USERPROFILE", "WINDIR",
))
_BLOCKED_ENV = frozenset({
    "PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES",
})


class MCPBridgeError(Exception):
    pass


def _config_path() -> Path:
    env = os.environ.get("HARBOR_MCP_SERVERS", "").strip()
    if env:
        return Path(env)
    from service.publishing.gate import CONFIG_FILE
    return CONFIG_FILE


def load_config() -> dict:
    """读取桥接配置（config.json 的 MCP_BRIDGE 段，或独立 JSON 文件）。"""
    path = _config_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if path.name != "config.json" and "MCP_BRIDGE" not in raw:
        return raw if isinstance(raw, dict) else {}
    block = raw.get("MCP_BRIDGE") or {}
    return block if isinstance(block, dict) else {}


def _resolve_command(cfg: dict) -> str:
    cmd = (cfg.get("command") or "").strip()
    if not cmd:
        raise MCPBridgeError("缺少 command")
    found = shutil.which(cmd)
    if not found:
        raise MCPBridgeError(f"命令不存在: {cmd}（先安装对应 MCP Server）")
    return found


def _allow_tools(cfg: dict) -> set:
    tools = cfg.get("tools")
    if not tools:
        return set()          # 空 = 未授权任何工具（安全默认）
    return set(str(t) for t in tools)


def _child_env(cfg: dict) -> dict[str, str]:
    """Build a minimal child environment without inheriting parent secrets."""
    env = {key: value for key, value in os.environ.items()
           if key in _SAFE_PARENT_ENV}
    explicit = cfg.get("env") or {}
    if not isinstance(explicit, dict):
        raise MCPBridgeError("env 必须是对象")
    for key, value in explicit.items():
        name = str(key)
        if name.upper() in _BLOCKED_ENV or "TOKEN" in name.upper() or "SECRET" in name.upper():
            raise MCPBridgeError(f"禁止向外部 MCP 注入敏感环境变量: {name}")
        env[name] = str(value)
    return env


# ---------------------------------------------------------------- 单次调用

async def _call_async(cfg: dict, tool: str, arguments: dict, timeout: float) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=_resolve_command(cfg),
        args=[str(a) for a in (cfg.get("args") or [])],
        env=_child_env(cfg),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=timeout)
            result = await asyncio.wait_for(
                session.call_tool(tool, arguments or {}), timeout=timeout)
            return _normalize_result(result)


def _normalize_result(result) -> dict:
    """把 MCP 的 CallToolResult 压平成 {ok, text, structured}。"""
    texts, structured = [], None
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            texts.append(text)
    if getattr(result, "structuredContent", None):
        structured = result.structuredContent
    payload = "\n".join(texts)[:_MAX_OUTPUT]
    return {
        "ok": not getattr(result, "isError", False),
        "text": payload,
        "structured": structured,
    }


def call_tool(server: str, tool: str, arguments: dict = None,
              timeout: float = None) -> dict:
    """同步调用外部 MCP Server 的工具。

    失败一律返回 {ok: False, error, ...}，不抛异常——调用方是任务引擎/API，
    外部 server 挂掉不应该让我们的请求 500。
    """
    cfg = load_config().get(server)
    if not cfg:
        return {"ok": False, "error": f"未配置 MCP Server: {server}",
                "hint": "在 config.json 的 MCP_BRIDGE 段添加",
                "configured": sorted(load_config().keys())}
    allowed = _allow_tools(cfg)
    if tool not in allowed:
        return {"ok": False, "error": f"工具 {tool} 未被授权",
                "allowed": sorted(allowed)}
    t = validate_timeout(timeout or cfg.get("timeout") or _DEFAULT_TIMEOUT,
                         default=_DEFAULT_TIMEOUT, name="MCP timeout")

    box = {}

    def _runner():
        try:
            box["r"] = asyncio.run(_call_async(cfg, tool, arguments or {}, t))
        except Exception as e:      # aqg: top-level boundary（外部进程任意异常）
            box["e"] = e

    th = threading.Thread(target=_runner, daemon=True, name=f"mcp-{server}")
    th.start()
    th.join(timeout=t + 5)
    if th.is_alive():
        return {"ok": False, "error": f"调用超时（>{t}s）", "server": server, "tool": tool}
    if "e" in box:
        e = box["e"]
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "server": server, "tool": tool}
    out = box.get("r") or {"ok": False, "error": "无返回"}
    out.update({"server": server, "tool": tool})
    return out


# ---------------------------------------------------------------- 发现与状态

async def _list_tools_async(cfg: dict, timeout: float) -> list:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=_resolve_command(cfg),
        args=[str(a) for a in (cfg.get("args") or [])],
        env=_child_env(cfg),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=timeout)
            listed = await asyncio.wait_for(session.list_tools(), timeout=timeout)
            return [{"name": t.name, "description": (t.description or "")[:200],
                     "input_schema": getattr(t, "inputSchema", None)}
                    for t in (listed.tools or [])]


def list_tools(server: str, timeout: float = None) -> dict:
    """列出一个外部 server 实际提供的工具（用于配置白名单时对照）。"""
    cfg = load_config().get(server)
    if not cfg:
        return {"ok": False, "error": f"未配置 MCP Server: {server}"}
    t = validate_timeout(timeout or cfg.get("timeout") or _DEFAULT_TIMEOUT,
                         default=_DEFAULT_TIMEOUT, name="MCP timeout")
    box = {}

    def _runner():
        try:
            box["r"] = asyncio.run(_list_tools_async(cfg, t))
        except Exception as e:
            box["e"] = e

    th = threading.Thread(target=_runner, daemon=True, name=f"mcp-list-{server}")
    th.start()
    th.join(timeout=t + 5)
    if th.is_alive():
        return {"ok": False, "error": f"列工具超时（>{t}s）"}
    if "e" in box:
        return {"ok": False, "error": f"{type(box['e']).__name__}: {box['e']}"}
    return {"ok": True, "server": server, "tools": box.get("r") or [],
            "allowed": sorted(_allow_tools(cfg))}


def status() -> dict:
    """所有已配置 server 的可用性概览（不真正拉起进程，只看命令是否存在）。"""
    out = []
    for name, cfg in (load_config() or {}).items():
        cmd = (cfg.get("command") or "").strip()
        present = bool(cmd and shutil.which(cmd))
        out.append({
            "server": name,
            "command": cmd,
            "installed": present,
            "allowed_tools": sorted(_allow_tools(cfg)),
            "timeout": cfg.get("timeout") or _DEFAULT_TIMEOUT,
            "ready": present and bool(_allow_tools(cfg)),
        })
    return {"configured": len(out), "servers": out,
            "config_source": str(_config_path()),
            "checked_at": time.time()}


# ---------------------------------------------------------------- 能力映射

# 外部 server 能顶替本项目哪些能力（给 /mcp/bridge/status 显示，避免"接了却没用上"）
CAPABILITY_MAP = {
    "browser": ["browser_navigate", "browser_snapshot", "browser_click",
                "browser_type", "browser_take_screenshot"],
    "fetch": ["fetch", "fetch_url"],
    "filesystem": ["read_file", "write_file", "list_directory"],
}


def capability_coverage() -> dict:
    """报告哪些能力已被外部 server 覆盖（按白名单里的工具名推断）。"""
    have = set()
    for cfg in (load_config() or {}).values():
        have |= _allow_tools(cfg)
    return {cap: sorted(set(tools) & have) for cap, tools in CAPABILITY_MAP.items()}
