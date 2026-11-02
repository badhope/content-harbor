@echo off
rem 本地启动后端（无语言依赖，仅依赖 uv）
cd /d "%~dp0backed"
if "%PORT%"=="" set PORT=8000
uv run uvicorn app.factory:create_app --factory --host 0.0.0.0 --port %PORT% --reload
