#!/usr/bin/env python3
"""扫描仓库中的疑似密钥，供 CI 与本地门禁调用。

检测三类信号，任何一类命中即失败：
  1. 已知服务商的 token 前缀（GitHub / GitCode / Gitee / OpenAI / Anthropic / AWS / Slack）
  2. PEM 私钥头
  3. 高熵赋值（长随机串直接赋给 token/key/secret/password 类变量）

只读不写。命中时打印文件:行号与**掩码后的**片段，绝不打印完整密钥。

用法：
    uv run --project backed python scripts/scan_secrets.py
    uv run --project backed python scripts/scan_secrets.py --staged   # 只查暂存区
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 不扫描的目录/文件：依赖、构建产物、二进制、文档里的占位符示例
SKIP_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", "dist", "build",
    ".ruff_cache", ".pytest_cache", "server/static", "target", ".aqg",
    # 本地临时目录：放的是调试脚本与一次性数据，既已被 .gitignore 排除、
    # 也不会进仓库，扫它只会用"我自己写的测试脚本里的占位 token"制造假红。
    ".tmp",
}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".svg",
    ".woff", ".woff2", ".ttf", ".eot", ".pdf", ".zip", ".gz",
    ".db", ".db-wal", ".db-shm", ".lock",
}
TEXT_SUFFIXES = {
    ".py", ".js", ".ts", ".vue", ".json", ".yml", ".yaml", ".toml",
    ".md", ".sh", ".ps1", ".bat", ".conf", ".ini", ".txt", ".env",
}

# 明确允许出现的占位/示例值
PLACEHOLDER = re.compile(
    r"(?i)(example|placeholder|your[-_]?|xxx+|changeme|redacted|<[^>]+>|\$\{[^}]+\}|"
    r"dummy|sample|test[-_]?token|sk-xxx|\.\.\.)"
)

TOKEN_PATTERNS = (
    ("github-pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("github-fine", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    ("gitcode-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("gitee-token", re.compile(r"\b[0-9a-f]{32}\b(?=.*gitee)", re.IGNORECASE)),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{32,}\b")),
    ("aws-access", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("pem-private", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
)

ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(api[_-]?key|secret|token|password|passwd|access[_-]?key|private[_-]?key)\b
    \s*[:=]\s*
    ["']([^"'\s]{16,})["']
    """
)


def entropy(value: str) -> float:
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def iter_files(staged: bool):
    if staged:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        for rel in out.stdout.splitlines():
            path = ROOT / rel
            if path.is_file():
                yield path
        return

    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.relative_to(ROOT).parts)
        if parts & SKIP_DIRS:
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {".env.example", ".gitignore"}:
            continue
        yield path


def scan_file(path: Path) -> list[str]:
    findings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings

    rel = path.relative_to(ROOT).as_posix()
    for lineno, line in enumerate(text.splitlines(), 1):
        if PLACEHOLDER.search(line):
            continue
        for label, pattern in TOKEN_PATTERNS:
            match = pattern.search(line)
            if match:
                findings.append(f"{rel}:{lineno} [{label}] {mask(match.group(0))}")
        match = ASSIGNMENT.search(line)
        if match:
            candidate = match.group(2)
            if entropy(candidate) >= 3.5:
                findings.append(
                    f"{rel}:{lineno} [high-entropy {match.group(1)}] {mask(candidate)}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描疑似密钥")
    parser.add_argument("--staged", action="store_true", help="只扫描暂存区")
    args = parser.parse_args()

    findings: list[str] = []
    scanned = 0
    for path in iter_files(args.staged):
        scanned += 1
        findings.extend(scan_file(path))

    if findings:
        print(f"发现 {len(findings)} 处疑似密钥：", file=sys.stderr)
        for item in findings:
            print(f"  {item}", file=sys.stderr)
        print("\n如为误报，把该值改成明确的占位符（如 your-token-here）后重试。",
              file=sys.stderr)
        return 1

    print(f"OK  已扫描 {scanned} 个文件，未发现疑似密钥")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
