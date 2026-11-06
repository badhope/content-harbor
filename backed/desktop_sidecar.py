# -*- coding: utf-8 -*-
"""
桌面版 sidecar 入口。

被 Tauri 作为 externalBin 打包或由 lib.rs spawn，执行时：
  1. 解析参数 (--port 0 则由 OS 随机)
  2. 启动 FastAPI + uvicorn
  3. 通过 patched startup 将实际端口写入 --tauri-port-file

该文件比 start.py 更"瘦"：没有 reload、没有多余的路径处理。
"""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="港 Harbor 桌面版后端 sidecar")
    parser.add_argument("--port", type=int, default=0,
                        help="监听端口 (0=OS 随机)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--tauri-port-file", required=True,
                        help="写入实际端口到此文件供 Tauri 父进程发现")
    parser.add_argument("--data-dir", default=None,
                        help="数据目录 (默认: 用户数据目录下的 harbor/)")
    args = parser.parse_args()

    # 确保数据目录
    if args.data_dir:
        data_path = Path(args.data_dir).resolve()
    else:
        # Windows: %APPDATA%\harbor\data
        # macOS:   ~/Library/Application Support/com.harbor.hub/data
        # Linux:   ~/.local/share/com.harbor.hub/data
        if sys.platform == "win32":
            base = Path.home() / "AppData" / "Roaming" / "harbor"
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support" / "com.harbor.hub"
        else:
            base = Path.home() / ".local" / "share" / "com.harbor.hub"
        data_path = base / "data"

    data_path.mkdir(parents=True, exist_ok=True)
    import os
    os.environ["HUB_DATA_DIR"] = str(data_path)

    from config import settings
    from app import create_app

    app = create_app()
    port_path = Path(args.tauri_port_file)
    if port_path.parent:
        port_path.parent.mkdir(parents=True, exist_ok=True)

    # 手动控制 uvicorn Server，以便获取 OS 随机分配的实际端口
    import uvicorn

    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level=settings.log_level.lower() if hasattr(settings, "log_level") else "info",
        access_log=False,
        # 与 start.py 同一口径：server 头必须在协议层关掉，中间件删不掉
        server_header=False,
    )
    server = uvicorn.Server(config)

    # 在 startup 完成后写入实际绑定的端口
    original_startup = server.startup

    async def patched_startup(sockets=None):
        await original_startup(sockets)
        if server.servers:
            for srv in server.servers:
                for sock in srv.sockets:
                    try:
                        actual_port = sock.getsockname()[1]
                        port_path.write_text(str(actual_port), encoding="utf-8")
                        break
                    except Exception:
                        pass
                break

    server.startup = patched_startup
    server.run()


if __name__ == "__main__":
    main()
