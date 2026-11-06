# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for harbor sidecar.

打包命令（在 backed/ 目录下执行）：
  uv run pyinstaller pyinstaller.spec --clean

产物：
  dist/harbor-backend.exe

启动参数：
  harbor-backend.exe --port 0 --host 127.0.0.1
  端口选 0 时 OS 随机分配，stdout 第一行输出实际端口号，供 Tauri 父进程读取。

数据目录：
  桌面版将数据存放在 %APPDATA%\harbor\data\ 下（由 Rust 通过
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
# 只列真实存在的：md_editor_v3 是前端 npm 包，Python 侧没有这个模块，
# 写进来会让 PyInstaller 在分析阶段报 missing module（不致命但污染日志）；
# playwright 同理——项目实际用 patchright 分叉，未装 playwright。
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
    "patchright",
    "patchright.sync_api",
    "ahocorasick",
    "PIL.Image",
    "PIL.ImageFile",
    "rapidfuzz.distance",
    "trafilatura",
    "telegraph",
    "markdown_it",
    "frontmatter",
    "croniter",
    "prometheus_client",
    "tenacity",
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
    # PIL 不能排除：media.py 用 PIL 做图片魔数校验（防止改扩展名绕过白名单），
# 排除后打包版一上传图片就崩，而开发环境永远发现不了。
excludes=["tkinter", "matplotlib", "numpy", "scipy"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],          # one-file：Tauri externalBin 只接受单文件，且 README 承诺"单 exe 双击即用"
    exclude_binaries=False,
    name="harbor-backend",
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

