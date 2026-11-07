#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 PyInstaller 产物准备成 Tauri sidecar 期望的文件名。

Tauri 的 ``externalBin: ["binaries/harbor-backend"]`` 要求实际文件带
target-triple 后缀：

    binaries/harbor-backend-x86_64-pc-windows-msvc.exe
    binaries/harbor-backend-x86_64-apple-darwin
    binaries/harbor-backend-aarch64-apple-darwin
    binaries/harbor-backend-x86_64-unknown-linux-gnu

缺这一步时 ``pnpm tauri build`` 会直接报
"binary not found: binaries/harbor-backend-x86_64-pc-windows-msvc.exe"，
而 README 里写的桌面构建步骤看起来是完整的——这是一个"文档说能做、
实际做不了"的缺口。

用法：
    uv run --project backed python scripts/stage_sidecar.py
    uv run --project backed python scripts/stage_sidecar.py --check   # 只检查
"""
from __future__ import annotations

import argparse
import platform
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "backed" / "dist"
BIN_DIR = ROOT / "web" / "src-tauri" / "binaries"
BASE_NAME = "harbor-backend"


def target_triple() -> str:
    """当前平台的 Rust target triple。"""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        arch = "x86_64" if machine in ("amd64", "x86_64") else "aarch64"
        return f"{arch}-pc-windows-msvc"
    if system == "darwin":
        arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
        return f"{arch}-apple-darwin"
    arch = "aarch64" if machine in ("aarch64", "arm64") else "x86_64"
    return f"{arch}-unknown-linux-gnu"


def source_binary() -> Path:
    """PyInstaller 产物路径（Windows 带 .exe）。"""
    suffix = ".exe" if platform.system().lower() == "windows" else ""
    return DIST / f"{BASE_NAME}{suffix}"


def main() -> int:
    parser = argparse.ArgumentParser(description="准备 Tauri sidecar 文件")
    parser.add_argument("--check", action="store_true", help="只检查不复制")
    args = parser.parse_args()

    triple = target_triple()
    suffix = ".exe" if platform.system().lower() == "windows" else ""
    target = BIN_DIR / f"{BASE_NAME}-{triple}{suffix}"

    if args.check:
        ok = target.is_file()
        print(("OK   " if ok else "MISS ") + str(target))
        if not ok:
            src = source_binary()
            print(f"  PyInstaller 产物: {src} ({'存在' if src.is_file() else '不存在'})")
            print("  先构建后端：cd backed && uv run pyinstaller pyinstaller.spec --clean")
        return 0 if ok else 1

    src = source_binary()
    if not src.is_file():
        print(f"找不到 PyInstaller 产物: {src}", file=sys.stderr)
        print("先运行：cd backed && uv run pyinstaller pyinstaller.spec --clean", file=sys.stderr)
        return 1

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)
    # 确保可执行位（POSIX 下 copy 会保留，保险起见再设一次）
    try:
        target.chmod(0o755)
    except OSError:
        pass
    size_mb = target.stat().st_size / 1024 / 1024
    print(f"已准备 sidecar: {target} ({size_mb:.1f} MB)")
    print("现在可以：cd web && pnpm tauri build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
