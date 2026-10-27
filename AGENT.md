# AGENT.md — 开发者指南

面向 AI 编码助手和人类开发者的项目结构说明。**反映 0.7.0 架构**（2026-10-12）。

## 1. 目录结构（0.7.0 真实）

```
harbour/
├── backed/
│   ├── app/
│   │   ├── factory.py              # FastAPI 应用工厂 (create_app)
│   │   ├── config.py               # AppConfig（CORS / 应用元数据）
│   │   ├── exceptions.py           # 全局异常处理器
│   │   ├── middleware.py           # 中间件注册（CORS/限流/审计/安全头）
│   │   └── routes.py              # 基础路由（/health /api/info）
│   ├── api/
│   │   ├── __init__.py            # 导出 hub_router + get_router_info
│   │   ├── hub.py                 # REST 聚合入口（51 行，只做 include）
│   │   ├── auth.py                # 鉴权中间件（Token + XFF 校验）
│   │   ├── deps.py                # 共享依赖（get_hub / get_task_manager）
│   │   └── routers/               # 按领域拆分的子路由器
│   │       ├── __init__.py
│   │       ├── system.py          # /health /status /metrics /jobs /publications /pending-human
│   │       ├── articles.py        # 文章 CRUD + 分页 + 搜索 + 导入 + 版本历史 + AI 增强 + 质检
│   │       ├── accounts.py        # 登录/登出/诊断/验证码/辅助/刷新（含 SSRF 防护）
│   │       ├── publishing.py      # 发布/更新/同步 + 任务查询/恢复
│   │       ├── tags.py            # 标签治理
│   │       ├── schedules.py       # 定时发布
│   │       ├── webhooks.py        # Webhook CRUD + 站内通知 + SSE
│   │       └── ai.py             # AI 写稿/Provider/模板/门禁状态
│   ├── middleware/
│   │   ├── rate_limiter.py        # 令牌桶限流（含 XFF 绕过防护 + 桶表上限）
│   │   ├── security_headers.py    # 安全响应头（CSP/X-Frame-Options 等）
│   │   └── audit_middleware.py    # HTTP 审计日志
│   ├── core/
│   │   ├── human_click.py         # 拟人鼠标轨迹（贝塞尔采样）
│   │   ├── permissions.py         # 权限校验
│   │   └── security.py            # 安全辅助
│   ├── config/
│   │   ├── __init__.py            # settings 导出
│   │   ├── config_unified.py      # 统一配置（Pydantic Settings）
│   │   ├── config_factory.py      # 配置工厂（env > json > default）
│   │   └── manage.py             # 配置管理 CLI
│   ├── service/publishing/
│   │   ├── service.py             # Hub 编排器（696 行）
│   │   ├── db.py                  # SQLite 唯一真理源（388 行）
│   │   ├── tasks.py               # TaskManager（统一任务引擎）
│   │   ├── scheduler.py           # 定时发布调度器
│   │   ├── browser.py             # Patchright Chromium（729 行）
│   │   ├── browser_pool.py        # 浏览器实例池（LRU + per-key 互斥）
│   │   ├── gate.py                # AIGC 合规门禁（四道闸门）
│   │   ├── ai.py                  # OpenAI 兼容 LLM 调用
│   │   ├── ai_service.py          # AI 增强操作（写稿/改写/润色/翻译/工具）
│   │   ├── ai_enhancements.py     # AI 工具函数（大纲/SEO/配图/克隆/模板）
│   │   ├── observability.py       # 日志/指标/可观测（585 行）
│   │   ├── events.py              # 事件系统（通知/SSE/Webhook 聚合）
│   │   ├── _event_notifier.py     # 站内通知环形缓冲
│   │   ├── _sse_broker.py         # SSE 订阅/广播
│   │   ├── _webhook_store.py      # Webhook 存储 + HMAC 签名
│   │   ├── _account_ops.py        # 账号操作（登录/登出/验证码/辅助）
│   │   ├── versions.py            # 版本管理（快照/回滚/diff）
│   │   ├── tags.py                # 标签治理
│   │   ├── content_qa.py          # 内容质检（可读性/SEO/重复度）
│   │   ├── crypto.py              # Fernet 加密（Webhook secret）
│   │   ├── humanize.py            # 人机模拟（WPM/延迟）
│   │   ├── cnblogs_login.py       # 博客园浏览器登录 + MetaWeblog 令牌提取
│   │   ├── adapters/              # 平台适配器（10 个）
│   │   │   ├── __init__.py        # @register 注册器
│   │   │   ├── base.py            # PlatformAdapter 基类（527 行）
│   │   │   ├── bilibili.py        # B站专栏
│   │   │   ├── cnblogs.py         # 博客园（XML-RPC）
│   │   │   ├── csdn.py            # CSDN
│   │   │   ├── juejin.py          # 掘金
│   │   │   ├── metaweblog.py     # 博客园元数据
│   │   │   ├── oschina.py         # 开源中国
│   │   │   ├── segmentfault.py   # SegmentFault
│   │   │   ├── toutiao.py         # 头条号
│   │   │   ├── wuyi_cto.py       # 51CTO
│   │   │   └── zhihu.py          # 知乎
│   │   ├── ai_providers.py        # AI 多 Provider 路由（8 家：DeepSeek/Kimi/GLM/…）
│   │   ├── ai_config.py           # AI 配置读写（掩码/白名单原子写/连通性测试）
│   │   ├── providers/             # 平台外部工具 Provider（biliup/wenyan 等薄壳）
│   │   │   ├── __init__.py
│   │   │   ├── base.py            # Provider 基类
│   │   │   ├── bilibili.py        # B站 API Provider
│   │   │   ├── registry.py        # Provider 注册 + 路由
│   │   │   └── toutiao.py         # 头条 API Provider
│   │   └── tools/                 # 平台 API 工具（0.7.0 新增）
│   │       ├── juejin_api.py      # 掘金 OpenAPI
│   │       └── metaweblog.py      # MetaWeblog XML-RPC 客户端
│   ├── utils/
│   │   ├── auth.py                # JWT / Token
│   │   ├── logger.py             # 日志（loguru + 结构化）
│   │   ├── masking.py            # 敏感信息脱敏
│   │   ├── response_code.py      # 响应码枚举
│   │   ├── security.py           # 安全工具（bcrypt/常量比较）
│   │   └── custom_exceptions.py  # 自定义异常
│   ├── cli.py                    # 命令行入口
│   ├── start.py                  # 服务启动
│   └── desktop_sidecar.py        # Tauri 桌面端 sidecar 启动
├── web/
│   ├── src/                      # Vue3 + Element Plus 前端
│   │   ├── api.js               # REST 客户端（baseURL = /api/v1/hub）
│   │   ├── stores/              # Pinia stores（hub/article/task/account/tag/webhook/schedule）
│   │   ├── components/          # Vue 组件
│   │   └── composables/        # 组合式函数
│   ├── src-tauri/               # Tauri 2.0 壳（Cargo + Rust）
│   └── package.json             # pnpm 管理
├── scripts/
│   ├── check_all.ps1            # 一键门禁（Windows）
│   ├── check_all.sh             # 一键门禁（Linux/macOS）
│   └── backup_data.py           # 数据备份（在线一致性快照）
├── mcp_server.py                # MCP Server（23 Tool，stdio + SSE）
├── deploy/                      # Docker 部署
└── docs/
    ├── architecture/ARCHITECTURE.md
    ├── audit/AUDIT.md
    ├── design/tech-debt.md
    └── writing-style.md
```

## 2. 数据流（0.7.0）

```
HTTP Request
    │
    ├─ 认证中间件 (api/auth.py: X-API-Token 校验 + XFF 受信判定)
    │
    ▼
api/hub.py (hub_router, 聚合 8 个子路由器)
    │  ├─ /system/*        → system_router
    │  ├─ /articles/*     → articles_router
    │  ├─ /accounts/*     → accounts_router (含 platform/account 正则校验 + SSRF 防护)
    │  ├─ /publishing 路径 → publishing_router
    │  ├─ /tags/*         → tags_router
    │  ├─ /schedules/*    → schedules_router
    │  ├─ /webhooks/*     → webhooks_router
    │  └─ /ai/*           → ai_router
    │
    ▼
get_hub() → Hub 实例 (api/deps.py 惰性单例)
    │  ├─ Hub.publish/update/sync → TaskManager.submit() → Worker 线程
    │  ├─ Hub.login/check/solve_captcha → _account_ops → browser_pool → BuiltinBrowser
    │  └─ Hub.ai_* → AIService → ai.py / ai_enhancements.py → LLM
    │
    ▼
TaskManager Worker (串行, tasks.py)
    │  ├─ Gate.check (AIGC 合规四道闸门)
    │  ├─ _with_adapter → adapters/*.publish/update
    │  ├─ Browser 操作 (Patchright Chromium, 专属线程)
    │  └─ Triage: HUMAN_HINTS→waiting_human / RETRY_HINTS→retry / 未分类→retry once→fail
    │
    ▼
SQLite (db.py, WAL 模式, LockedConnection 线程安全)
```

**REST 异步模型**：`submit()` 立即返回 `task_id`，客户端轮询 `GET /api/v1/hub/tasks/{id}`。
**MCP 同步模型**：`mcp_server.py` 直接调 `hub.publish(...)`，同步返回结果。

## 3. 适配器列表（10 个，由 test_docs_links.py 校验）

| 适配器 | 文件 | needs_browser | 认证方式 |
|--------|------|:---:|---|
| 51cto | wuyi_cto.py | False | API |
| bilibili | bilibili.py | True | 浏览器 |
| cnblogs | cnblogs.py | False | XML-RPC |
| csdn | csdn.py | True | 浏览器 |
| juejin | juejin.py | True | 浏览器 |
| metaweblog | metaweblog.py | False | XML-RPC |
| oschina | oschina.py | True | 浏览器 |
| segmentfault | segmentfault.py | True | 浏览器 |
| toutiao | toutiao.py | True | 浏览器 |
| zhihu | zhihu.py | True | 浏览器 |

新增平台：在 `adapters/` 下创建 `{platform}.py`，继承 `PlatformAdapter`，末尾 `@register`，
在 `adapters/__init__.py` 加导入，在 `config/config_unified.py` 加默认配置。

## 4. 开发命令

### 启动 Web 服务

```bash
cd backed
uv run uvicorn app.factory:create_app --host 0.0.0.0 --port 8000 --reload
```

### 启动桌面端

```bash
cd web
pnpm install
pnpm run tauri dev
```

### CLI

```bash
cd backed
uv run python -m backed.cli <command>
uv run python -m backed.cli sync              # 同步平台状态
uv run python -m backed.cli list-tasks        # 查看任务
uv run python -m backed.cli approve-task <id> # 批准待人工任务
```

### 测试

```bash
# 一键门禁（六道：compileall → ruff → pytest → eslint → vitest → vite build）
pwsh -File scripts/check_all.ps1        # Windows
bash scripts/check_all.sh              # Linux/macOS

# 单跑
cd backed
uv run pytest -q                        # 全部 (402 用例)
uv run pytest tests/test_tasks.py -q    # 任务引擎
uv run ruff check .                     # 静态门禁

# 前端（在仓库根执行）
pnpm --dir web lint                     # eslint
pnpm --dir web test                     # vitest（39 用例）
```

`conftest.py` 的 `sandbox` fixture（autouse）把 DB/事件日志/Webhook 派发线程
全部关进 pytest 临时目录，**不碰开发库**。

### 备份

```bash
cd backed
uv run python ../scripts/backup_data.py              # 到 ~/harbour-backups
uv run python ../scripts/backup_data.py --dry-run    # 只看计划
```

## 5. 不要做的事

- 不要在 `db.py` 之外写原生 SQL（跨表查询走 `db.list_*` 系列函数）
- 不要在 `browser.py` 之外直接操作 Playwright context
- 不要在 `api/hub.py` 之外创建 FastAPI router（所有端点走 `api/routers/*`）
- 不要绕过 TaskManager 持久化任务（MCP 同步调用除外）
- 不要复用 0.4.0 之前的 scaffold 模式（v1/schemas/repository/_service.py 已废弃）
- 不要改 `check_all.ps1` 里的 `exit 1` 为 scriptblock 内的裸 exit（会杀整个 pwsh）

## 6. 配置

| 环境变量 | 说明 | 默认 |
|---|---|---|
| `HARBOUR_API_TOKEN` | REST 鉴权 Token（优先于 config.json） | 未配置时首启自动生成 UUID 并落盘 |
| `HARBOUR_SECRET_KEY` | 签名密钥（生产必须设） | 开发随机 |
| `TRUSTED_NETS` | 受信代理网段（XFF 校验） | 127.0.0.0/8,::1/128 |
| `TRUSTED_PROXIES` | 受信反向代理 IP（限流键） | 空 |
| `CORS_ORIGINS` | 跨域白名单（逗号分隔） | 同源 |
| `DEMO=1` | 演示模式（不开浏览器不调外部 API） | 0 |
| `AI_API_KEY` | LLM API 密钥 | 空 |
| `AI_BASE_URL` | LLM 端点 | deepseek |
| `ASSIST_ALLOW_LOCAL=1` | 允许 assist 打开 localhost（桌面版调试） | 0 |
| `FERNET_KEY` | Webhook secret 加密主密钥 | 自动生成 |
