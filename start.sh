#!/usr/bin/env bash
# 本地启动后端（无语言依赖，仅依赖 uv）
# 用 app 工厂：start.py 的 app 建在 main() 内，模块级没有 app 属性，
# 按模块属性方式导入该入口会直接 AttributeError。
set -e
cd "$(dirname "$0")/backed"
exec uv run uvicorn app.factory:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}" --reload
