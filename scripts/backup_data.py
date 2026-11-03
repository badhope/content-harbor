#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备份 Harbor 的不可再生资产：文章库、登录快照、AI 凭据配置。

为什么是个 Python 脚本而不是 docs/design/m5-data.md:60 里写的 `cp`：
SQLite 跑在 WAL 模式下且服务常在写，`cp hub.db` 拿到的是半截事务的副本——
能打开、但可能缺最近几条写入。`sqlite3.Connection.backup()` 走 SQLite 自己的
备份 API，在线拿一致性快照，**不需要停服务**。

备份目标默认放在用户主目录下的 harbor-backups/，刻意不复用仓库内的任何目录：
auth.json 是 session cookie、config.json 是明文 API Key，入库或上传等于泄密。

用法（在 backed/ 下，让 uv 环境生效）：
    uv run python ../scripts/backup_data.py                # 备份到 ~/harbor-backups
    uv run python ../scripts/backup_data.py --dry-run      # 只打印计划，不落盘
    uv run python ../scripts/backup_data.py --keep 14      # 保留 14 天
    uv run python ../scripts/backup_data.py --include-profiles   # 连浏览器 profile 一起
    uv run python ../scripts/backup_data.py --json          # 机器可读结果

退出码：0 成功 · 1 备份失败 · 2 参数/安全校验失败（含目标目录落在仓库内）
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---- 路径：以 db.DB_PATH 为唯一真理源，不在这里重算一套 -------------------
# db.py 只 import json/sqlite3/threading/time/pathlib，轻量；且只在模块级算路径，
# 不会 connect()，所以导入它零副作用。
BACKED_ROOT = Path(__file__).resolve().parent.parent / "backed"
sys.path.insert(0, str(BACKED_ROOT))

from service.publishing import db  # noqa: E402


DATA_DIR = db.DB_PATH.parent                 # backed/service/data
DB_FILE = db.DB_PATH                          # backed/service/data/hub.db
PROFILE_DIR = DATA_DIR / "profiles"           # *.auth.json + 浏览器 profile 目录
# 凭据类配置：AI/门禁读 backed/service/config.json，REST 鉴权读 backed/config.json，
# 两份都可能存在，谁在备份谁。
CONFIG_CANDIDATES = [
    BACKED_ROOT / "config.json",
    DATA_DIR.parent / "config.json",
]

REPO_ROOT = BACKED_ROOT.parent


class RefuseError(Exception):
    """安全校验拒绝执行（备份目标落在仓库内等）。与普通失败分开计数。"""


@dataclass
class Plan:
    """一次备份的完整计划。dry-run 与真跑共用，保证"看到的"就是"做到的"。"""
    dest: Path
    db: Path
    auth_files: list = field(default_factory=list)
    config_files: list = field(default_factory=list)
    profile_dirs: list = field(default_factory=list)


@dataclass
class Result:
    ok: bool
    dest: str
    db_bytes: int = 0
    auth_count: int = 0
    config_count: int = 0
    profile_count: int = 0
    pruned: int = 0
    skipped: list = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------- 工具

def _repo_relative(p: Path):
    """路径在仓库内则返回相对表示，否则 None。用于安全校验与展示。"""
    try:
        return str(p.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return None


def _refuse_dest_inside_repo(dest: Path) -> None:
    """备份落点绝不能是仓库内的目录——auth.json/config.json 一旦落进去就有入库风险。"""
    rel = _repo_relative(dest)
    if rel is not None:
        raise RefuseError(
            f"备份目标位于仓库内（{rel}）。auth.json 与 config.json 含凭据，"
            f"入库即泄密。请用 --dest 指向仓库外的目录，例如 ~/harbor-backups")


def _harden(path: Path) -> None:
    """尽力收紧权限。Windows 上 os.chmod 只影响只读位，真实 ACL 需另配 icacls，
    这里不假装做了做不到的事——打印时说明即可。"""
    try:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    except (NotImplementedError, OSError):
        pass


def _prune(dest: Path, keep_days: int) -> int:
    """删掉超过 keep_days 的备份目录，至少保留最新一个（防止全删光）。"""
    if keep_days <= 0:
        return 0
    cutoff = time.time() - keep_days * 86400
    # 按目录名排序（名字带时间戳，字典序即时间序）；永远不动最后一个
    dirs = sorted((p for p in dest.glob("backup-*") if p.is_dir()),
                  key=lambda p: p.name)
    pruned = 0
    for p in dirs[:-1]:
        if p.stat().st_mtime < cutoff:
            shutil.rmtree(p, ignore_errors=True)
            pruned += 1
    return pruned


# ---------------------------------------------------------------- SQLite 备份

def _backup_db(src: Path, dst: Path) -> int:
    """在线一致性备份。返回备份文件字节数。"""
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    try:
        dst_conn = sqlite3.connect(str(dst), timeout=30)
        try:
            src_conn.backup(dst_conn)
            dst_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dst_conn.commit()
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    # 校验副本本身可用：能在只读模式下跑通完整性检查才算备份成功。
    # 少了这一步，"生成了个文件"会被误当成"备份好了"。
    check = sqlite3.connect(f"file:{dst}?mode=ro", uri=True, timeout=30)
    try:
        row = check.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise RuntimeError(f"备份副本完整性检查未通过：{row}")
    finally:
        check.close()

    return dst.stat().st_size


# ---------------------------------------------------------------- 计划与执行

def build_plan(dest: Path, include_profiles: bool) -> Plan:
    _refuse_dest_inside_repo(dest)
    plan = Plan(dest=dest, db=DB_FILE)
    if PROFILE_DIR.is_dir():
        plan.auth_files = sorted(PROFILE_DIR.glob("*.auth.json"))
        if include_profiles:
            plan.profile_dirs = sorted(
                p for p in PROFILE_DIR.iterdir() if p.is_dir())
    for cfg in CONFIG_CANDIDATES:
        if cfg.is_file():
            plan.config_files.append(cfg)
    return plan


def run_backup(dest: Path, keep_days: int, include_profiles: bool = False,
               dry_run: bool = False) -> Result:
    res = Result(ok=False, dest=str(dest))
    try:
        plan = build_plan(dest, include_profiles)
    except RefuseError as e:
        res.error = f"拒绝执行：{e}"
        return res

    # 有的备、没的报，只有"一件都没有"才算失败。
    # 不能反过来：hub.db 缺失往往是数据事故的信号，此时更该把还在的凭据备份下来，
    # 而不是整体失败让用户白跑一趟（fresh install 同理，hub.db 本来就不存在）。
    if plan.db.is_file():
        have_db = True
    else:
        have_db = False
        res.skipped.append(f"文章库不存在：{plan.db}")

    if dry_run:
        res.ok = True
        res.db_bytes = plan.db.stat().st_size if have_db else 0
        res.auth_count = len(plan.auth_files)
        res.config_count = len(plan.config_files)
        res.profile_count = len(plan.profile_dirs)
        if not (have_db or plan.auth_files or plan.config_files or plan.profile_dirs):
            res.ok = False
            res.error = "没有可备份的数据"
        return res

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = dest / f"backup-{stamp}"
    try:
        out.mkdir(parents=True, exist_ok=True)
        _harden(out)

        if have_db:
            res.db_bytes = _backup_db(plan.db, out / "hub.db")
        else:
            res.skipped.append("hub.db 未备份（源文件不存在）")

        for src in plan.auth_files:          # 含 session cookie，权限收紧
            shutil.copy2(src, out / src.name)
            _harden(out / src.name)
        res.auth_count = len(plan.auth_files)

        for src in plan.config_files:        # 含明文 API Key
            shutil.copy2(src, out / f"config-{src.parent.name}.json")
            _harden(out / f"config-{src.parent.name}.json")
        res.config_count = len(plan.config_files)

        for src in plan.profile_dirs:        # 可选：登录态 L1 层，体积大
            shutil.copytree(src, out / f"profile-{src.name}",
                            ignore_dangling_symlinks=True)
            _harden(out / f"profile-{src.name}")
        res.profile_count = len(plan.profile_dirs)

        res.pruned = _prune(dest, keep_days)
        if res.db_bytes or res.auth_count or res.config_count or res.profile_count:
            res.ok = True
        else:
            res.error = "没有可备份的数据"
    # aqg: top-level boundary 落盘步骤跨 sqlite/shutil/copytree 三族 API，任何一个失败都
    # 应记为"本次备份失败"并把错误带出，而不是让半成品目录以成功形态留下
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
    return res


# ---------------------------------------------------------------- CLI

def _default_dest() -> Path:
    return Path.home() / "harbor-backups"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="备份 Harbor 文章库与登录态（在线安全，无需停服务）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", type=Path, default=_default_dest(),
                        help="备份根目录，默认 %%(default)s（必须在仓库外）")
    parser.add_argument("--keep", type=int, default=7, metavar="N",
                        help="保留最近 N 天的备份，0=不清理，默认 %(default)s")
    parser.add_argument("--include-profiles", action="store_true",
                        help="连浏览器 profile 目录一起备份（体积大，auth.json 已兜底）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印备份计划，不落盘")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="输出机器可读 JSON")
    args = parser.parse_args(argv)

    res = run_backup(args.dest, keep_days=args.keep,
                     include_profiles=args.include_profiles,
                     dry_run=args.dry_run)

    if args.as_json:
        print(json.dumps(res.__dict__, ensure_ascii=False, indent=2))
    elif res.ok:
        mode = "（dry-run，未落盘）" if args.dry_run else ""
        print(f"备份计划{mode}：备份根 {res.dest}")
        print(f"  hub.db        {res.db_bytes} bytes")
        print(f"  auth.json     {res.auth_count} 个（session cookie，已收紧权限）")
        print(f"  config.json   {res.config_count} 个（明文凭据，已收紧权限）")
        if res.profile_count:
            print(f"  profiles      {res.profile_count} 个浏览器 profile 目录")
        if res.pruned:
            print(f"  清理过期备份  {res.pruned} 个")
        for s in res.skipped:
            print(f"  跳过          {s}")
    else:
        print(f"备份失败：{res.error}", file=sys.stderr)

    return 0 if res.ok else (2 if res.error.startswith("拒绝执行") else 1)


if __name__ == "__main__":
    sys.exit(main())
