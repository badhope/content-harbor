# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for harbour sidecar.

打包命令（在 backed/ 目录下执行）：
  uv run pyinstaller pyinstaller.spec --clean

产物：
  dist/harbour-backend.exe

启动参数：
  harbour-backend.exe --port 0 --host 127.0.0.1
  端口选 0 时 OS 随机分配，stdout 第一行输出实际端口号，供 Tauri 父进程读取。

数据目录：
  桌面版将数据存放在 %APPDATA%\harbour\data\ 下（由 Rust 通过
  --data-dir 参数传入）。开发/测试版默认用 backed/data/。
"""

import sys
from pathlib import Path

block_cipher = None

backed_dir = Path(SPECPATH).resolve()

# 主入口
entry = backed_dir / "start.py"

# 数据/配置：把 .env.example 和 config.example.json 打包进去，运行时释放
datas = [
    (str(backed_dir / ".env.example"), "."),
    (str(backed_dir / "config.example.json"), "."),
]

# Hidden imports：uvicorn/logging 等常被动态导入
hidden_imports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "multipart",
    "multipart.multipart",
    "md_editor_v3",
    "playwright",
    "playwright.sync_api",
    "patchright",
    "patchright.sync_api",
]

a = Analysis(
    [str(entry)],
    pathex=[str(backed_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "scipy"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],          # 不打包进单 exe（one-file 体积太大且解压慢），用 one-dir
    exclude_binaries=True,
    name="harbour-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,   # 需要控制台显示启动日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="harbour-backend",
)
