# AI 内容中台后端镜像（基于 uv 管理依赖）
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/root/.local/bin:$PATH" \
    FASTAPI_ENV=docker

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc libffi-dev build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && (curl -LsSf https://astral.sh/uv/install.sh | sh) \
    && groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app

# 仅复制后端代码（上下文为项目根目录）
COPY backed /app/backed
WORKDIR /app/backed

RUN uv sync --no-dev \
    && (uv run patchright install chromium || uv run playwright install chromium) \
    && mkdir -p /app/backed/data /app/backed/logs /app/data \
    && chown -R app:app /app/backed /app/data

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# 注意：不要写 `uvicorn start:app`——start.py 的 app 在 main() 里构造，
# 模块级没有 `app` 属性，uvicorn 导入会直接 AttributeError 退出。
CMD ["uv", "run", "python", "start.py", "--host", "0.0.0.0", "--port", "8000"]
