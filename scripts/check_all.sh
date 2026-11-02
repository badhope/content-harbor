#!/usr/bin/env bash
# 一键回归门禁 —— 提交前必跑（tech-debt 台账 T2，docs/audit/AUDIT.md 的"可回退"承诺落地）
#
# 用法：bash scripts/check_all.sh
# Windows 用户请用 scripts/check_all.ps1（同一套步骤，pwsh 版本）
#
# 覆盖八道：
#   1. 语法编译   python -m compileall（每个 .py 都能被解释器读）
#   2. 静态门禁   ruff（未定义名 / 运行时错误类，见 backed/pyproject.toml [tool.ruff]）
#   3. 版本一致   五处清单版本号必须相同（scripts/bump_version.py --check）
#   4. 密钥扫描   高熵字符串 / 已知 token 前缀 / 私钥头（scripts/scan_secrets.py）
#   5. 单元测试   pytest（DB/任务引擎/标签/版本/事件/调度器/质检/REST 接线）
#   6. 前端 lint    eslint（未使用变量 / prop 变更 / Vue 规则）
#   7. 前端单测     vitest（store 竞态/保存发布链路/重试策略）
#   8. 前端构建     vite build（产物落 server/static，FastAPI 挂载点）
#
# 任一步失败立刻停，最后打一行 PASS/FAIL 汇总。

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backed="$repo_root/backed"
failed=()

step() {
  local name="$1"; shift
  echo ""
  echo "===> $name"
  local start end
  start="$(date +%s)"
  if "$@"; then
    end="$(date +%s)"
    echo "<=== PASS $name ($((end - start))s)"
  else
    end="$(date +%s)"
    echo "<=== FAIL $name ($((end - start))s)" >&2
    failed+=("$name")
  fi
}

cd "$backed"
export UV_NO_PROGRESS=1

# 排除 .venv：站点包（jieba 等）在 Python 3.12+ 会打出 SyntaxWarning，
# 而且把 1 万多个依赖文件编一遍要 10 秒——项目自身源码只需 0.3 秒。
step "1/6 语法编译 compileall" uv run python -m compileall -q -x '(^|[\\/])(\.venv|node_modules|__pycache__)($|[\\/])' .
# scripts/ 在 backed 作用域外，单独编译
step "1/6b 语法编译 scripts/" uv run python -m compileall -q "$repo_root/scripts"
ruff_lint() {
  ( cd "$backed" && uv run ruff check . ) || return 1
  # mcp_server.py 在仓库根，不在 backed/ 的 pyproject 作用域内，显式带上同一套规则
  uv run ruff check "$repo_root/mcp_server.py" --select E9,F7,F82
}

step "2/8 静态门禁 ruff" ruff_lint
step "3/8 版本一致性"      uv run python "$repo_root/scripts/bump_version.py" --check
step "4/8 密钥扫描"        uv run python "$repo_root/scripts/scan_secrets.py"
step "5/8 单元测试 pytest" uv run pytest -q --no-header -p no:cacheprovider

if [ -d "$repo_root/web/node_modules" ]; then
  step "6/8 前端 lint eslint"  pnpm --dir "$repo_root/web" lint
  step "7/8 前端单测 vitest"   pnpm --dir "$repo_root/web" test
  step "8/8 前端构建 vite build" pnpm --dir "$repo_root/web" build
else
  echo ""
  echo "===> 6/8 前端 lint（跳过：web/node_modules 不存在）"
  echo "===> 7/8 前端单测（同上跳过）"
  echo "===> 8/8 前端构建（同上跳过）"
fi

echo ""
if [ "${#failed[@]}" -gt 0 ]; then
  echo "FAIL - 未通过：${failed[*]}" >&2
  exit 1
fi
echo "PASS - 八道门禁全绿，可以提交"
