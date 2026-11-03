"""应用入口文件

支持参数：
  --port N             监听端口 (默认 8000；桌面版 sidecar 模式由 Tauri 父进程传 0 表示 OS 分配)
  --host HOST          监听地址 (默认 127.0.0.1)
  --tauri-port-file F  启动成功后将端口写入此文件（桌面版 Tauri 用于发现后端端口）
  --data-dir DIR       数据目录 (默认 ./data)
"""

import argparse
from pathlib import Path

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="内容港 Harbor 后端")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--tauri-port-file", default=None,
                        help="写入实际端口到此文件供 Tauri 父进程发现")
    parser.add_argument("--data-dir", default=None,
                        help="数据目录 (默认 ./data)")
    args = parser.parse_args()

    # 数据目录
    if args.data_dir:
        data_path = Path(args.data_dir).resolve()
        data_path.mkdir(parents=True, exist_ok=True)
        import os
        os.environ["HUB_DATA_DIR"] = str(data_path)

    from config import settings
    from app import create_app

    app = create_app()

    # 桌面版 sidecar 模式：写端口文件后启动
    port_path = None
    if args.tauri_port_file:
        port_path = Path(args.tauri_port_file)

    if port_path is not None:
        # 手动控制 uvicorn Server，以便获取 OS 随机分配的端口（port=0 时）
        config = uvicorn.Config(
            app,
            host=args.host,
            port=args.port,
            log_level=settings.log_level.lower() if hasattr(settings, "log_level") else "info",
            access_log=True,
        )
        server = uvicorn.Server(config)

        # 在 startup 完成后写入实际绑定的端口
        original_startup = server.startup

        async def _patched_startup(sockets=None):
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

        server.startup = _patched_startup
        server.run()
    else:
        # 普通模式：直接运行
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            reload=False,
            log_level=settings.log_level.lower() if hasattr(settings, "log_level") else "info",
            access_log=True,
        )


if __name__ == "__main__":
    main()
