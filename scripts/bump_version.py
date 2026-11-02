# 版本管理脚本
"""原子更新全部版本清单，避免五处版本号漂移。

用法（在仓库根执行）：
    uv run --project backed python scripts/bump_version.py 0.13.0
    uv run --project backed python scripts/bump_version.py 0.13.0 --dry-run

只改版本字段，不做提交/打标签——发布动作按 docs/VERSIONING.md 的清单执行。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (相对路径, 正则, 说明)  正则第 1 组为版本号所在位置
TARGETS = (
    ("backed/pyproject.toml",
     re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE),
     "pyproject"),
    ("web/package.json",
     re.compile(r'^(\s*)"version":\s*"([^"]+)"', re.MULTILINE),
     "package.json"),
    ("web/src-tauri/Cargo.toml",
     re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE),
     "Cargo.toml"),
    ("web/src-tauri/tauri.conf.json",
     re.compile(r'^(\s*)"version":\s*"([^"]+)"', re.MULTILINE),
     "tauri.conf.json"),
    ("backed/config/config_unified.py",
     re.compile(r'^(\s*)project_version:\s*str\s*=\s*"([^"]+)"', re.MULTILINE),
     "config_unified"),
)

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


def _read_target(rel: str, pattern: re.Pattern[str]) -> tuple[Path, str, int]:
    path = ROOT / rel
    if not path.is_file():
        raise SystemExit(f"找不到版本文件: {rel}")
    text = path.read_text(encoding="utf-8")
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"{rel} 中未匹配到版本字段")
    return path, text, match.lastindex or 1


def read_versions() -> dict[str, str]:
    versions = {}
    for rel, pattern, label in TARGETS:
        path, text, _ = _read_target(rel, pattern)
        match = pattern.search(text)
        versions[label] = match.group(match.lastindex)
    return versions


def write_version(new_version: str, *, dry_run: bool) -> list[str]:
    changed = []
    for rel, pattern, label in TARGETS:
        path, text, group = _read_target(rel, pattern)
        current = pattern.search(text).group(group)
        if current == new_version:
            continue
        updated = pattern.sub(
            lambda m: m.group(0).replace(f'"{current}"', f'"{new_version}"'), text, count=1)
        if not dry_run:
            path.write_text(updated, encoding="utf-8", newline="")
        changed.append(f"{label}: {current} -> {new_version}")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="更新全部版本清单")
    parser.add_argument("version", nargs="?", help="目标版本，如 0.13.0")
    parser.add_argument("--dry-run", action="store_true", help="只显示将要发生的修改")
    parser.add_argument("--check", action="store_true", help="仅校验五处版本是否一致")
    args = parser.parse_args()

    if args.check or not args.version:
        versions = read_versions()
        distinct = set(versions.values())
        for label, value in versions.items():
            print(f"  {label:16s} {value}")
        if len(distinct) != 1:
            print("版本不一致，请运行 bump_version.py <version> 修正", file=sys.stderr)
            return 1
        print(f"OK  版本一致: {distinct.pop()}")
        return 0

    if not SEMVER.match(args.version):
        print(f"版本号不符合 SemVer: {args.version}", file=sys.stderr)
        return 2

    changed = write_version(args.version, dry_run=args.dry_run)
    if not changed:
        print(f"全部文件已经是 {args.version}")
        return 0
    for line in changed:
        print(("  [dry-run] " if args.dry_run else "  ") + line)
    print(f"{'将更新' if args.dry_run else '已更新'} {len(changed)} 处版本号")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
