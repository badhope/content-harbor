# -*- coding: utf-8 -*-
"""文档链接可达性回归。

0.6.4 把 ARCHITECTURE.md / AUDIT.md 从仓库根迁进 docs/，全仓库只有 1 处
markdown 链接指向它们（README.md）。这批用例把"迁移后链接仍可达"变成机器判定：
下次再动文档结构，断链会在测试里红掉，而不是等人点开才发现。
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _md_files():
    return [p for p in REPO.rglob("*.md")
            if ".venv" not in p.parts and "node_modules" not in p.parts]


def _links(path):
    """只取本地相对链接：跳过 http(s)、锚点、邮件、含空格的示例路径。"""
    out = []
    for raw in MD_LINK.findall(path.read_text(encoding="utf-8")):
        target = raw.split("#", 1)[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        if " " in target:
            continue
        out.append(target)
    return out


@pytest.mark.parametrize("rel", [
    "README.md",
    "AGENT.md",
    "CHANGELOG.md",
    "docs/architecture/ARCHITECTURE.md",
    "docs/audit/AUDIT.md",
    "docs/design/tech-debt.md",
])
def test_markdown_files_exist(rel):
    assert (REPO / rel).is_file()


@pytest.mark.parametrize("src", [
    "README.md", "AGENT.md", "CHANGELOG.md",
    "docs/architecture/ARCHITECTURE.md", "docs/audit/AUDIT.md",
])
def test_relative_links_resolve(src):
    path = REPO / src
    broken = []
    for target in _links(path):
        if not (path.parent / target).exists():
            broken.append(target)
    assert not broken, f"{src} 存在断链：{broken}"


def test_architecture_and_audit_are_under_docs():
    """迁移的落点必须固定，否则 README 的指针会空。"""
    assert (REPO / "docs/architecture/ARCHITECTURE.md").is_file()
    assert (REPO / "docs/audit/AUDIT.md").is_file()
    assert not (REPO / "ARCHITECTURE.md").exists()
    assert not (REPO / "AUDIT.md").exists()


def test_readme_points_at_new_locations():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "docs/architecture/ARCHITECTURE.md" in readme
    assert "docs/audit/AUDIT.md" in readme


def test_agent_md_adapter_tree_matches_reality():
    """AGENT.md 的目录树曾列出 douyin/weibo/sf.py 三个不存在的适配器，
    且漏掉 6 个真实存在的。树与代码必须一致。"""
    agent = (REPO / "AGENT.md").read_text(encoding="utf-8")
    adapters_dir = REPO / "backed/service/publishing/adapters"
    real = {p.stem for p in adapters_dir.glob("*.py")
            if p.stem not in ("__init__", "base")}
    # 树里点名的适配器文件
    named = set(re.findall(r"([a-z_0-9]+\.py)\s+#", agent))
    for stem in real:
        assert f"{stem}.py" in agent, f"AGENT.md 目录树漏了适配器 {stem}.py"
    # 树里不该再出现已不存在的文件
    for ghost in ("douyin.py", "weibo.py", "sf.py"):
        assert ghost not in agent, f"AGENT.md 仍列着不存在的 {ghost}"
    assert named  # 防止正则失效时静默通过