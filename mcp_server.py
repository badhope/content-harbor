#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Harbor MCP Server — 让任意 AI Agent 通过 MCP 协议操控内容港。

启动（stdio 模式）：
    python mcp_server.py

启动（SSE/HTTP 模式，多客户端）：
    python mcp_server.py --transport sse --port 8900

注册到 Claude Code / Cursor / Windsurf：
    claude mcp add --transport stdio harbor python /path/to/mcp_server.py
    # 或 .mcp.json:
    {
      "mcpServers": {
        "harbor": {
          "command": "python",
          "args": ["/path/to/harbor/mcp_server.py"]
        }
      }
    }
"""

import hmac
import os
import sys
from pathlib import Path

# 把 backed/ 加入 Python 路径，确保能 import service
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR / "backed"))

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from service.publishing.service import Hub


class _StaticTokenVerifier:
    """Minimal Bearer verifier for explicitly configured remote SSE."""

    async def verify_token(self, token: str) -> AccessToken | None:
        expected = os.environ.get("HARBOR_MCP_TOKEN", "").strip()
        if not expected or not hmac.compare_digest(token, expected):
            return None
        return AccessToken(token=token, client_id="harbor", scopes=["mcp"])

# ── 单例 Hub（MCP server 进程内只启一次）──────────────────────────
_hub = None


def _get_hub() -> Hub:
    global _hub
    if _hub is None:
        _hub = Hub(headless=True)
    return _hub


_MCP_TOKEN = os.environ.get("HARBOR_MCP_TOKEN", "").strip()
_mcp_auth = AuthSettings(
    issuer_url="http://127.0.0.1:8900",
    resource_server_url="http://127.0.0.1:8900",
    validate_token_resource=False,
) if _MCP_TOKEN else None
mcp = FastMCP(
    "harbor",
    token_verifier=_StaticTokenVerifier() if _MCP_TOKEN else None,
    auth=_mcp_auth,
)


# ── 文章 ────────────────────────────────────────────────────────────

@mcp.tool()
def list_articles(status: str = None, limit: int = 20) -> list:
    """列出文章列表。status 可选：draft/published/review/archived。"""
    return _get_hub().list(status=status, limit=limit)


@mcp.tool()
def get_article(article_id: int) -> dict:
    """按 ID 获取文章完整字段（标题/正文/摘要/标签/状态）。"""
    r = _get_hub().get(article_id)
    if not r:
        raise ValueError(f"文章 {article_id} 不存在")
    return dict(r)


@mcp.tool()
def create_article(title: str, content_md: str = "", summary: str = "",
                    tags: str = "") -> dict:
    """新建一篇文章，返回 {id, title}。"""
    aid = _get_hub().create(title, content_md, summary=summary, tags=tags,
                             source="human")
    return {"id": aid, "title": title}


@mcp.tool()
def update_article(article_id: int, title: str = None, content_md: str = None,
                    summary: str = None, tags: str = None, status: str = None) -> dict:
    """更新文章的任意字段。内容变更后会自动 pend 已发布平台的同步。"""
    kw = {k: v for k, v in locals().items()
          if k != "article_id" and v is not None}
    ok, pending = _get_hub().edit(article_id, **kw)
    return {"ok": ok, "pending_sync": pending}


# ── AI 写稿 ──────────────────────────────────────────────────────────

@mcp.tool()
def ai_write_article(topic: str, words: int = 2000, tags_hint: str = "",
                      model: str = "", publish_to: list = None,
                      style: str = "") -> dict:
    """让 AI 写一篇技术文章，自动入库。可选直接发布到指定平台。

    model: "provider:model" 指定模型（如 "openai:gpt-4o"），不指定走 premium 路由。
    publish_to: ["juejin","csdn"] 写完直接发（草稿模式，需人工确认上线）。
    """
    return _get_hub().ai_write(topic, style=style, words=words,
                                tags_hint=tags_hint, publish_to=publish_to,
                                model=model or None)


@mcp.tool()
def ai_rewrite_article(article_id: int, instruction: str,
                        model: str = "") -> dict:
    """按指令 AI 改写已有文章（如"更口语""补充踩坑章节""压缩到 800 字"）。"""
    return _get_hub().ai_rewrite(article_id, instruction,
                                  model=model or None)


@mcp.tool()
def ai_polish_article(article_id: int, model: str = "") -> dict:
    """AI 润色文章：修错别字、顺语句、统一代码块语言标识。"""
    return _get_hub().ai_polish(article_id, model=model or None)


@mcp.tool()
def ai_translate_article(article_id: int, target_lang: str = "en",
                          model: str = "") -> dict:
    """翻译文章到目标语言（en/ja/ko/fr/de），保留代码块不动。"""
    return _get_hub().ai_translate(article_id, target_lang, model=model or None)


@mcp.tool()
def ai_image_prompts(article_id: int, n: int = 3) -> dict:
    """根据文章内容生成 n 个英文文生图 prompt（兼容 SDXL/Flux/DALL·E）。"""
    return _get_hub().ai_image_prompts(article_id, n)


# ── 发布 ────────────────────────────────────────────────────────────

@mcp.tool()
def publish_article(article_id: int, platforms: list,
                    draft_only: bool = False, account: str = "default",
                    human_confirmed: bool = False) -> dict:
    """异步发布文章到指定平台，返回 task_id。用 get_task 查询进度。

    draft_only=False 为真实上线；AI 源文章需 human_confirmed=True
    （表示人工已核验）或服务端开启 AI_DIRECT_PUBLISH，否则被合规门禁拒绝。
    """
    task_id = _get_hub().tasks.submit("publish", article_id=article_id,
                                       platforms=platforms, account=account,
                                       draft_only=draft_only,
                                       human_confirmed=human_confirmed)
    return {"task_id": task_id, "status": "pending",
            "poll": f"GET /tasks/{task_id}"}


@mcp.tool()
def update_remote_article(article_id: int, platforms: list = None,
                           account: str = "default") -> dict:
    """原地更新（不是重发）：打开已发布平台的编辑页改内容并保存。"""
    task_id = _get_hub().tasks.submit("update", article_id=article_id,
                                       platforms=platforms, account=account)
    return {"task_id": task_id, "status": "pending"}


@mcp.tool()
def list_platforms() -> list:
    """列出所有可用平台及其在线状态。"""
    # 适配器"导入即注册"，注册动作在 service.py 导入时发生；MCP 进程里若还没
    # 创建过 Hub，这里必须显式触发一次注册。
    from service.publishing import adapters as _adapters  # noqa: F401
    from service.publishing.adapters.base import ADAPTERS, capability_of
    return [{"id": a.id, "name": a.name, "needs_browser": a.needs_browser,
             "capabilities": capability_of(a.id)} for a in ADAPTERS.values()]


@mcp.tool()
def list_providers() -> list:
    """列出所有已配置的 AI Provider + 可用模型 + 价格级别。"""
    return _get_hub().list_models()


# ── 任务管理 ──────────────────────────────────────────────────────────

@mcp.tool()
def get_task(task_id: str) -> dict:
    """查询任务状态（pending/running/ok/failed/waiting_human）。"""
    t = _get_hub().tasks.get(task_id)
    if not t:
        raise ValueError(f"任务 {task_id} 不存在")
    return dict(t)


@mcp.tool()
def resume_task(task_id: str, approved: bool = True) -> dict:
    """恢复等待人工的任务。approved=True 重跑；False 标记放弃。"""
    return _get_hub().tasks.resume(task_id, approved=approved)


# ── 内容质检 ──────────────────────────────────────────────────────────

@mcp.tool()
def quality_check(article_id: int) -> dict:
    """对文章运行内容质检：可读性评分 + SEO 评分 + 库内重复度 + 改进建议。"""
    return _get_hub().content_qa(article_id=article_id)


@mcp.tool()
def analyze_text(content_md: str, title: str = "", summary: str = "",
                  tags: str = "") -> dict:
    """对任意文本（不保存入库）运行内容质检。"""
    return _get_hub().content_qa(content_md=content_md, title=title,
                                  summary=summary, tags=tags)


# ── 标签 ──────────────────────────────────────────────────────────────

@mcp.tool()
def list_tags(limit: int = 30) -> list:
    """列出热门标签及其出现次数。"""
    from service.publishing.tags import list_tags
    return list_tags(_get_hub().conn, limit=limit)


@mcp.tool()
def suggest_tags(title: str, content_md: str) -> list:
    """根据标题 + 正文推荐 3-5 个标签。"""
    from service.publishing.tags import suggest_for_article
    return suggest_for_article(_get_hub().conn, title, content_md)


# ── 内容导入 ──────────────────────────────────────────────────────────

@mcp.tool()
def import_markdown(path: str, tags: str = "") -> dict:
    """导入本地 Markdown 文件，自动提取 front-matter 元数据。"""
    aid = _get_hub().import_md(path, tags=tags)
    return {"id": aid}


@mcp.tool()
def search_articles(keyword: str, limit: int = 20) -> list:
    """全文检索文章库（FTS5 相关度排序），返回匹配文章列表。"""
    return _get_hub().search(keyword)[:max(1, min(limit, 100))]


@mcp.tool()
def import_from_url(url: str, tags: str = "") -> dict:
    """从网页抓取正文导入为文章（trafilatura 提取，需公网可访问）。"""
    return _get_hub().import_url(url, tags=tags)


@mcp.tool()
def list_media(limit: int = 50) -> dict:
    """列出已入库的图片资产（内容寻址去重）与统计。"""
    from service.publishing import media
    hub = _get_hub()
    return {"items": media.list_assets(hub.conn, limit=limit),
            "stats": media.stats(hub.conn)}


@mcp.tool()
def ingest_image(url: str) -> dict:
    """把远程图片抓进本地媒体库，返回可长期引用的稳定 URL。"""
    from service.publishing import media
    asset = media.ingest_url(_get_hub().conn, url)
    return {"sha256": asset["sha256"], "url": media.asset_url(asset),
            "bytes": asset["bytes"]}


# ── 内置浏览器操控面（带平台登录态抓取 / 截图）────────────────────────────

@mcp.tool()
def browser_fetch(platform: str, url: str, account: str = "default") -> dict:
    """带平台登录态的内置浏览器抓取页面：标题 + 正文（登录墙后的内容也能拿）。

    例：browser_fetch("juejin", "https://juejin.cn/user/123/posts")
    """
    from service.publishing import browser_ops
    return browser_ops.browser_fetch(_get_hub(), platform, url, account=account)


@mcp.tool()
def browser_screenshot(platform: str, url: str, full_page: bool = False,
                       account: str = "default") -> dict:
    """带平台登录态打开页面截图，落盘 data/exports/screenshots/，返回路径。"""
    from service.publishing import browser_ops
    return browser_ops.browser_screenshot(_get_hub(), platform, url,
                                          account=account,
                                          full_page=full_page)


# ── 外部 MCP Server 桥接（把别人的 MCP 工具当后端用）────────────────────

@mcp.tool()
def external_mcp_status() -> dict:
    """已配置的外部 MCP Server（Playwright / fetch / filesystem 等）状态与能力覆盖。

    默认关闭；在 config.json 的 MCP_BRIDGE 段配置后才会拉起子进程。
    """
    from service.publishing import mcp_bridge
    out = mcp_bridge.status()
    out["coverage"] = mcp_bridge.capability_coverage()
    return out


@mcp.tool()
def external_mcp_tools(server: str) -> dict:
    """列出一个外部 MCP Server 实际提供的工具（配白名单前先看）。"""
    from service.publishing import mcp_bridge
    return mcp_bridge.list_tools(server)


@mcp.tool()
def external_mcp_call(server: str, tool: str, arguments: dict = None) -> dict:
    """调用外部 MCP Server 的工具（仅白名单内）。

    例：external_mcp_call("playwright", "browser_navigate", {"url": "https://..."})
    """
    from service.publishing import mcp_bridge
    return mcp_bridge.call_tool(server, tool, arguments or {})


# ── 外部执行器：Wechatsync（可选后端，不可用时优雅降级）──────────────────
#
# 为什么接它：Wechatsync 支持 29+ 平台且直接调用各平台官方 Web API，比我们
# 自带的浏览器自动化更快、更稳、不触发风控。代价是它跑在用户本机的 Chrome
# 扩展里、需要各平台登录态——所以它是**可选执行后端**，不是替代品：
# 本模块不可用时，下面所有 tool 返回 {available: false, ...}，
# 原有 10 个适配器与 20 个 tool 完全不受影响。

import os
import shutil
import subprocess


def _ws_bin() -> str:
    """定位 wechatsync CLI。不在 PATH 时返回空串。"""
    return shutil.which("wechatsync") or ""


def _ws_env() -> dict:
    """透传必要环境变量给 wechatsync CLI（仅 PATH + WECHATSYNC_TOKEN，防泄露）。"""
    return {
        "PATH": os.environ.get("PATH", ""),
        "WECHATSYNC_TOKEN": os.environ.get("WECHATSYNC_TOKEN", ""),
    }


def _ws_available() -> dict:
    """探测外部执行器是否就绪（装了 CLI 且配了 token）。"""
    binpath = _ws_bin()
    token = os.environ.get("WECHATSYNC_TOKEN", "").strip()
    return {
        "installed": bool(binpath),
        "binary": binpath,
        "has_token": bool(token),
        "ready": bool(binpath) and bool(token),
    }


def _ws_run(args, timeout=120):
    """执行 wechatsync CLI 并解析其输出。

    aqg: top-level boundary —— 外部进程可能不存在/超时/输出非预期 JSON，
    一律转成结构化失败返回，绝不让异常冒泡打断整个 MCP 会话。
    """
    if not _ws_bin():
        return {"ok": False, "available": False,
                "error": "未安装 wechatsync CLI",
                "hint": "npm install -g @wechatsync/cli"}
    try:
        proc = subprocess.run(
            [_ws_bin(), *args], capture_output=True, text=True,
            timeout=timeout, env=_ws_env())
    except subprocess.TimeoutExpired:
        return {"ok": False, "available": True, "error": f"wechatsync 超时（>{timeout}s）",
                "hint": "确认 Chrome 扩展已安装并启用「MCP 连接」"}
    except Exception as e:
        return {"ok": False, "available": True, "error": f"{type(e).__name__}: {e}"}

    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return {"ok": False, "available": True,
                "error": (proc.stderr or out or "未知错误")[:500],
                "returncode": proc.returncode}
    return {"ok": True, "available": True, "output": out}


@mcp.tool()
def wechatsync_status() -> dict:
    """查询外部执行器 Wechatsync 的就绪状态（是否安装、是否配 token）。

    这是可选后端：未安装或未配 token 时返回 ready=false，
    本内容港自带的 10 个平台适配器仍可正常使用。
    """
    st = _ws_available()
    st["ok"] = True
    st["note"] = ("就绪" if st["ready"] else
                  "未就绪——安装 npm i -g @wechatsync/cli 并导出 WECHATSYNC_TOKEN")
    st["fallback"] = "可改用内置适配器（publish_article），功能不依赖外部执行器"
    return st


@mcp.tool()
def wechatsync_publish(article_id: int, platforms: str,
                       title: str = "", cover: str = "",
                       dry_run: bool = True) -> dict:
    """把文章库里的一篇交给外部执行器 Wechatsync 分发到多个平台。

    platforms：逗号分隔的平台 id（juejin,csdn,zhihu…），可超出内置 10 个。
    dry_run 默认 True：只报告将要执行的动作，不实际发布。
    真发布需用户本机 Chrome 扩展已安装并在各平台登录。

    返回 {ok, available, output/error}。available=false 时请改用
    publish_article（内置适配器）。
    """
    art = _get_hub().get(article_id)
    if not art:
        raise ValueError(f"文章 {article_id} 不存在")
    if not platforms.strip():
        raise ValueError("platforms 不能为空")

    tmp = Path(os.environ.get("TEMP", "/tmp")) / f"harbor-ws-{article_id}.md"
    try:
        tmp.write_text(art.get("content_md", ""), encoding="utf-8")
        args = ["sync", str(tmp), "-p", platforms]
        if title:
            args += ["-t", title]
        if cover:
            args += ["--cover", cover]
        if dry_run:
            args.append("--dry-run")
        res = _ws_run(args)
    finally:
        tmp.unlink(missing_ok=True)          # 文章内容不留在磁盘上

    res["article_id"] = article_id
    res["platforms"] = platforms
    res["dry_run"] = dry_run
    return res


@mcp.tool()
def wechatsync_platforms() -> dict:
    """经外部执行器列出 Wechatsync 支持的平台（29+，可超出内置 10 个）。

    需要本机已装 CLI 且 Chrome 扩展在运行；否则返回 available=false。
    """
    res = _ws_run(["platforms"], timeout=30)
    if res.get("ok"):
        res["output"] = res.get("output", "")[:4000]
    return res


# ── 入口 ──────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Harbor MCP Server")
    parser.add_argument(
        "--transport", choices=["stdio", "sse", "streamable-http"], default="stdio",
        help="stdio（本机客户端）/ streamable-http（推荐远程，MCP 2025-03-26 起取代 HTTP+SSE）/ sse（旧客户端兼容）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8900)
    args = parser.parse_args()

    if args.transport in {"sse", "streamable-http"}:
        if args.host not in {"127.0.0.1", "::1", "localhost"} \
                and not os.environ.get("HARBOR_MCP_TOKEN", "").strip():
            raise SystemExit(
                f"远程 {args.transport} 必须设置 HARBOR_MCP_TOKEN（当前 host={args.host}）")
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        # streamable-http 的路径可配置，默认 /mcp；显式设置保证文档与实现一致
        if args.transport == "streamable-http":
            mcp.settings.streamable_http_path = os.environ.get(
                "HARBOR_MCP_PATH", "/mcp")
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
