# 本地启动后端（无语言依赖，仅依赖 uv）
$port = if ($env:PORT) { $env:PORT } else { "8000" }
Set-Location "$PSScriptRoot/backed"
uv run uvicorn app.factory:create_app --factory --host 0.0.0.0 --port $port --reload
