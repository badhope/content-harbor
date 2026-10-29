"""测试用最小 MCP Server（stdio）：验证 mcp_bridge 真的能拉起并调用外部 server。

刻意只用官方 SDK 的最小用法，不引入本项目任何代码——被测的桥接层必须能
对接"任意第三方 MCP Server"，而不是只认自家格式。
"""

import anyio
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("bridge-probe")


@mcp.tool()
def echo(text: str) -> str:
    """原样返回输入，用于验证调用链路。"""
    return f"echo:{text}"


@mcp.tool()
def boom() -> str:
    """总是抛错，用于验证错误归一化。"""
    raise ValueError("intentional failure")


if __name__ == "__main__":
    anyio.run(mcp.run_stdio_async)
