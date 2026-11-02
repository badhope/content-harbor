# -*- coding: utf-8 -*-
"""导入层回归测试。

守的是一个真实发生过的事故：`utils/audit.py` 里留着 `from sqlalchemy.orm
import Session` 这条死 import，而 `sqlalchemy` 早已不在 `pyproject.toml`
的依赖里、也不在 `uv.lock` 的解析结果里——`create_app()` 直接 ImportError，
整个后端起不来，而 `compileall` 抓不到（语法没问题，缺的是运行时装载）。

所以这里逐个 import 真实模块，而不是只做 py_compile。
"""

import importlib
import sys
from pathlib import Path

import pytest

BACKED_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKED_ROOT.parent

# 每个条目都要能裸 import。MCP server 在仓库根目录，自己会把 backed/ 加进
# sys.path，所以放进来一起守。
MODULES = [
    "app.factory",
    "app.config",
    "app.exceptions",
    "app.middleware",
    "app.routes",
    "api",
    "api.hub",
    "config",
    "core.security",
    "core.permissions",
    "middleware",
    "utils.logger",
    "utils.masking",
    "service.publishing.ai",
    "service.publishing.ai_enhancements",
    "service.publishing.ai_providers",
    "service.publishing.content_qa",
    "service.publishing.db",
    "service.publishing.events",
    "service.publishing.gate",
    "service.publishing.humanize",
    "service.publishing.observability",
    "service.publishing.providers",
    "service.publishing.scheduler",
    "service.publishing.service",
    "service.publishing.tags",
    "service.publishing.tasks",
    "service.publishing.versions",
    "service.publishing.adapters.base",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    assert importlib.import_module(module) is not None


def test_audit_has_no_undeclared_dependency_import():
    """service/publishing/observability.py 中的审计代码必须是零第三方 ORM 依赖（项目用裸 sqlite3）。"""
    src = (BACKED_ROOT / "service" / "publishing" / "observability.py").read_text(encoding="utf-8")
    assert "import sqlalchemy" not in src
    assert "from sqlalchemy" not in src


def test_mcp_server_imports():
    """MCP server 与 FastAPI 同依赖树，import 失败同样让 0.6.0 特性不可用。"""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        import mcp_server
        # 16 个 tool 声明即契约：工具漏注册会让上层 Agent 直接调不到
        assert len(mcp_server.mcp._tool_manager._tools) >= 16
    finally:
        sys.modules.pop("mcp_server", None)
        sys.path.remove(str(REPO_ROOT))


def test_no_undefined_names_anywhere():
    """F821 全仓零命中。

    血泪教训：`ruff --fix` 删掉一条"看起来重复"的局部 import 后，
    service.py:401 的 `wait_human_captcha` 变成未定义名——而那个符号
    只在 solve_captcha 的浏览器分支里用到，测试全绿也照样上线炸。
    ruff 的门禁规则集本来就含 F821，这条是把"门禁自己生效"也纳入回归。
    """
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path
    r = subprocess.run(
        [_sys.executable, "-m", "ruff", "check", ".", "--select", "F821"],
        cwd=str(_Path(__file__).resolve().parents[1]),
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_adapter_registry_is_populated_by_package_import():
    """回归：注册表原先只在 service.py 被导入时才填，/platforms 会返回空。"""
    for mod in [m for m in sys.modules
                if m.startswith("service.publishing.adapters") or m == "mcp_server"]:
        del sys.modules[mod]
    import service.publishing.adapters.base as base
    before = len(base.ADAPTERS)
    import service.publishing.adapters  # noqa: F401
    assert before >= 10
    assert "juejin" in base.ADAPTERS
    assert "csdn" in base.ADAPTERS


def test_mcp_main_wires_sse_port_into_settings(monkeypatch):
    """FastMCP.run() 只接 transport，host/port 得从 settings 走——原先传 port 必 TypeError。"""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        import mcp_server
        seen = {}
        monkeypatch.setattr(mcp_server.mcp, "run",
                            lambda transport=None: seen.update(transport=transport))
        monkeypatch.setattr("sys.argv", ["mcp_server.py", "--transport", "sse", "--port", "8971"])
        mcp_server.main()
        assert seen == {"transport": "sse"}
        assert mcp_server.mcp.settings.port == 8971
        assert mcp_server.mcp.settings.host == "127.0.0.1"

        monkeypatch.setattr("sys.argv", ["mcp_server.py"])
        mcp_server.main()
        assert seen == {"transport": "stdio"}
    finally:
        sys.modules.pop("mcp_server", None)
        sys.path.remove(str(REPO_ROOT))
