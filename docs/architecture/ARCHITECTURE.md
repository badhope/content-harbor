# ARCHITECTURE.md — 内容港 Harbor 架构

| 项 | 值 |
|---|---|
| 版本 | 0.10.0 |
| 日期 | 2026-10-29 |
| 状态 | 维护中 |
| 唯一真理源 | 本文件 + [`AGENT.md`](../../AGENT.md) |
| 位置 | 2026-10-10 从仓库根目录迁入 `docs/architecture/`（tech-debt 台账 #11） |

## 1. 系统概览

内容港 Harbor：**写 → 合规审查 → 多平台分发 → 原地更新 → 人机协作收尾**。

- 技术栈：Python 3.12+ / FastAPI / SQLite / Patchright (Playwright 反检测 fork) / Vue 3 + Element Plus + Pinia
- 架构模式：统一任务引擎 (TaskManager) + 10 个平台适配器
- 部署：单机 SQLite 跑通全部功能；生产可切 MySQL / PostgreSQL

## 2. 当前架构

```
┌────────────────────────────────────────────────────┐
│              Web 浏览器 / AI / 脚本                    │
│         Vue 3 前端（晴空主题 · 响应式）                  │
└──────────────────────┬─────────────────────────────┘
                       │  REST API
                       ▼
┌────────────────────────────────────────────────────┐
│            FastAPI Router  (api/hub.py)              │
│  /articles·/publish·/update·/tasks·/ai·/accounts    │
└──────────────────────┬─────────────────────────────┘
                       │  submit("publish"|"update"|"sync"|
                       │        "refresh"|"login"|"assist")
                       ▼
┌────────────────────────────────────────────────────┐
│            统一任务引擎  (tasks.py)                     │
│        单后台线程 · SQLite 持久化 · 确定性分流            │
└───┬──────────────┬──────────────┬───────────────────┘
    │              │              │
    ▼              ▼              ▼
┌────────┐   ┌──────────┐   ┌──────────┐
│  Hub    │   │   AI     │   │   Gate   │
│ (svc)   │   │ (OpenAI  │   │ (AIGC    │
│ +10 适配器│   │  兼容)   │   │  合规)   │
└────────┘   └──────────┘   └──────────┘
    │
    ▼
┌────────────────────────────────────────────────────┐
│  DB (db.py · 单文件 SQLite · WAL)                     │
│  Browser (Patchright Chromium · 每平台独立 profile)    │
│  Config (config_unified.py · 环境变量优先)              │
└────────────────────────────────────────────────────┘
```

## 3. 目录结构

```
harbor/
├── backed/                    # 后端（FastAPI + SQLite + 任务引擎）
│   ├── app/
│   │   ├── factory.py         # FastAPI 工厂 + lifespan
│   │   ├── config.py          # 应用配置
│   │   ├── middleware.py      # CORS / 信任主机 / 请求 ID
│   │   ├── exceptions.py      # 全局异常处理器
│   │   └── routes.py          # /health · /api/info
│   ├── api/
│   │   └── hub.py             # 核心 REST 入口（28+ 端点）
│   ├── service/publishing/    # 核心：业务层 + 平台适配器
│   │   ├── service.py         # Hub 编排器
│   │   ├── db.py              # 持久化层（LockedConnection RLock 串行化）
│   │   ├── browser.py         # Patchright 适配 + 反检测
│   │   ├── tasks.py           # TaskManager 统一任务引擎
│   │   ├── gate.py            # AIGC 合规门禁（高危词 + 来源标记）
│   │   ├── ai.py              # OpenAI 兼容 LLM 调用（DeepSeek / 豆包 / Ollama）
│   │   ├── humanize.py        # 人机延迟 / 贝塞尔鼠标轨迹
│   │   ├── observability.py   # 结构化日志 + 事件计数
│   │   ├── cnblogs_login.py   # 博客园自动抠 MetaWeblog 令牌
│   │   └── adapters/          # 10 个平台适配器
│   │       ├── __init__.py    # @register 注册器
│   │       ├── base.py        # PlatformAdapter 基类
│   │       ├── csdn.py
│   │       ├── juejin.py
│   │       ├── zhihu.py
│   │       ├── bilibili.py
│   │       ├── cnblogs.py
│   │       ├── segmentfault.py
│   │       ├── toutiao.py
│   │       ├── oschina.py
│   │       ├── wuyi_cto.py    # 51CTO
│   │       └── metaweblog.py  # 自建博客通用适配器
│   ├── config/
│   │   └── config_unified.py  # 多环境配置
│   ├── core/
│   │   ├── permissions.py
│   │   └── human_click.py     # 拟人鼠标点击
│   ├── utils/
│   │   ├── logger.py
│   │   ├── response_code.py
│   │   └── custom_exceptions.py
│   ├── cli.py                 # CLI 入口（status / serve）
│   └── pyproject.toml          # 依赖声明
├── web/                       # Vue 3 前端（独立工程）
│   └── src/
│       ├── App.vue            # 应用壳
│       ├── api.js             # REST 客户端
│       ├── stores/hub.js      # Pinia 状态管理
│       ├── styles/main.scss   # 晴空主题 · 设计 Token
│       └── components/        # 9 个组件
├── deploy/                    # Docker / Nginx 反代
├── docs/screenshots/          # 界面截图
├── start.sh / .bat / .ps1     # 一键启动脚本
├── AGENT.md                   # AI 助手开发指南
└── README.md                  # 本仓库说明
```

## 4. 任务引擎 (TaskManager)

### 核心调度流程

1. **提交**：`submit("publish"|"update"|"sync"|"refresh"|"login"|"assist", ...)`
   - 写入 SQLite `tasks` 表
   - 返回 `task_id`（UUID hex[:10]）
   - HTTP 响应 202 + `Location: /tasks/{task_id}`

2. **Worker 线程**：单后台线程串行处理，一次一个任务
   - 状态机：`pending` → `running` → `ok` / `failed` / `waiting_human`
   - 浏览器是全局单实例，串行是正确约束

3. **确定性分流 (Triage)** — 不依赖 LLM：
   - 匹配 `HUMAN_HINTS`（验证码/风控/滑块） → `waiting_human`
   - 匹配 `RETRY_HINTS`（超时/连接断开/429/503） → 指数退避重试（MAX_ATTEMPTS=3）
   - 其他 → `fail` 留 error 详情

4. **人机协作恢复**：`POST /tasks/{task_id}/resume`（approved=true 继续 / false 放弃）

### 任务持久化

- 所有任务落 SQLite `tasks` 表，进程重启不丢
- `waiting_human` 任务重启后可恢复
- 前端轮询间隔 2s，10 分钟超时

## 5. 平台适配器

### 接口契约

每个适配器实现 4 个动作：

| 方法 | 作用 |
|---|---|
| `check_auth(page) -> bool` | 登录态是否有效 |
| `list_articles(page, limit=50)` | 抓取账号内文章列表 |
| `publish(page, article, options)` | 发布新文章 |
| `update(page, pub, article) -> bool` | 打开 edit_url 原地修改 |

### 平台矩阵

| 平台 | 类型 | 认证 | 发布 | 更新 | 列表 |
|---|---|---|---|---|---|
| 掘金 | 浏览器+API | 页面 Cookies | ✅ | ✅ | ✅ |
| CSDN | 浏览器 | Cookies | ✅ | ✅ | ✅ |
| 知乎 | 浏览器 UI | Cookies | ✅ | ✅ | — |
| 思否 | 浏览器 UI | Cookies | ✅ | ✅ | — |
| B站专栏 | 浏览器 UI | Cookies | ✅ | ✅ | — |
| 头条号 | 浏览器 UI | Cookies | ✅ | — | — |
| 开源中国 | 浏览器 UI | Cookies | ✅ | — | — |
| 博客园 | **MetaWeblog** | 访问令牌 | ✅ | ✅ | ✅ |
| 51CTO | **MetaWeblog** | 账号密码 | ✅ | ✅ | ✅ |
| 自建博客 | **MetaWeblog** | 账号密码 | ✅ | ✅ | ✅ |

> `needs_browser = False` 的适配器（博客园/51CTO/MetaWeblog）自动跳过浏览器步骤。
> 验证码三层策略见 README 第七章。

## 6. 数据库设计 (db.py)

SQLite 单文件，WAL 模式 + 30s busy_timeout。

| 表 | 作用 | 关键字段 |
|---|---|---|
| `accounts` | 平台账号 | platform, name, profile_dir, status, updated_at |
| `articles` | 文章主库（唯一真源） | id, title, content_md, summary, tags, status, source |
| `publications` | 发布实例 | article_id, platform, post_id, post_url, edit_url, status, content_hash |
| `tasks` | 统一任务引擎 | task_id, kind, status, article_id, platforms, result, error, attempts |
| `jobs` | 任务流水（兼容旧版） | type, article_id, platform, status, message |
| `meta` | KV 元数据 | key, value |

`LockedConnection` (RLock) 保证同连接跨线程串行化。

## 7. REST API 路径总览

> 所有长任务返回 `task_id`，轮询 `GET /api/v1/hub/tasks/{task_id}` 获取进度。

| 域 | 端点 |
|---|---|
| **总览** | `GET /api/v1/hub/status`（`published`=已上线平台实例数、`published_articles`=已发布文章数，两口径别混用） · `GET /api/v1/hub/platforms` · `GET /api/v1/hub/metrics`（累积桶直方图 + p50/p90/p99） |
| **文章** | `GET/POST /api/v1/hub/articles` · `GET /api/v1/hub/articles/page?limit=&offset=` · `GET/PUT /api/v1/hub/articles/{id}` · `GET /api/v1/hub/articles/search/{kw}` |
| **发布** | `POST /api/v1/hub/articles/{id}/publish` · `POST /api/v1/hub/articles/{id}/update` · `POST /api/v1/hub/sync/pending` |
| **任务** | `GET /api/v1/hub/tasks` · `GET /api/v1/hub/tasks/page?limit=&offset=` · `GET /api/v1/hub/tasks/{id}` · `POST /api/v1/hub/tasks/{id}/resume` |
| **账号** | `GET /api/v1/hub/accounts` · `GET /api/v1/hub/accounts/{p}/check` · `POST /api/v1/hub/accounts/{p}/login` · `DELETE /api/v1/hub/accounts/{p}` |
| **账号+** | `GET /api/v1/hub/accounts/{p}/diagnose` · `POST /api/v1/hub/accounts/{p}/solve-captcha` · `POST /api/v1/hub/accounts/{p}/assist` · `POST /api/v1/hub/refresh/{p}` |
| **AI** | `POST /api/v1/hub/ai/write` · `POST /api/v1/hub/articles/{id}/ai-rewrite` · `POST /api/v1/hub/articles/{id}/ai-polish` · `GET /api/v1/hub/ai/status` · `GET /api/v1/hub/ai/gate` |
| **AI增强** | `POST /api/v1/hub/articles/{id}/ai-translate` · `GET /api/v1/hub/articles/{id}/ai-image-prompts` · `GET /api/v1/hub/articles/{id}/ai-outline` · `GET /api/v1/hub/articles/{id}/ai-seo` · `POST /api/v1/hub/articles/{id}/clone` · `GET /api/v1/hub/ai/templates` |
| **版本** | `GET /api/v1/hub/articles/{id}/versions` · `GET /api/v1/hub/articles/{id}/versions/{vid}` · `GET /api/v1/hub/articles/{id}/versions/diff` · `POST /api/v1/hub/articles/{id}/versions/{vid}/rollback` |
| **定时** | `GET/POST /api/v1/hub/schedules` · `GET/PUT /api/v1/hub/schedules/{sid}` · `POST /api/v1/hub/schedules/{sid}/pause` · `POST /api/v1/hub/schedules/{sid}/resume` · `POST /api/v1/hub/schedules/{sid}/trigger` · `DELETE /api/v1/hub/schedules/{sid}` |
| **标签** | `GET /api/v1/hub/tags` · `GET /api/v1/hub/tags/trending` · `POST /api/v1/hub/tags/sync` · `POST /api/v1/hub/tags/rename` · `POST /api/v1/hub/tags/merge` · `POST /api/v1/hub/tags/alias` · `POST /api/v1/hub/tags/suggest` |
| **AI模型** | `GET /api/v1/hub/ai/providers`（多 Provider 列表 + 模型 + 价格级别 + 冷却状态） |
| **质检** | `POST /api/v1/hub/articles/{id}/qa` · `POST /api/v1/hub/qa/analyze`（可读性 + SEO + 重复度 0-100 评分） |
| **Webhook** | `GET/POST /api/v1/hub/webhooks` · `PUT/DELETE /api/v1/hub/webhooks/{id}` · `POST /api/v1/hub/webhooks/{id}/test` |
| **通知** | `GET /api/v1/hub/notifications`（环形缓冲最近事件） |
| **发布实例** | `GET /api/v1/hub/publications` · `GET /api/v1/hub/pending-human` |

交互式文档：`GET /api/v1/hub/docs`（Swagger UI）/ `GET /api/v1/hub/redoc`。

## 8. 设计决策（ADR）

### D1: 统一任务引擎替代 LangGraph

旧版用 LangGraph 做"遍历平台 + 失败重试"，~870 行。实际行为等价于
`for platform in platforms: try 3 times; if captcha: yield to human`。
TaskManager 100 行实现同等功能 + SQLite 持久化 + 进程重启恢复。

### D2: Patchright 替代裸 Playwright

平台反爬越来越严，裸 Playwright 暴露 8+ 个自动化指纹。Patchright 原生隐藏
`navigator.webdriver`、伪装 WebGL vendor、补插件/语言数组等，显著降低验证码率。

### D3: 浏览器单实例串行复用

浏览器是全局单资源，多并发开只能抢锁。串行 worker + LRU 实例池上限 4 个，
扫码登录前自动释放该平台的池实例（profile 目录独占）。

### D4: 双库合一（hub.db 含所有状态）

原版拆 workflow.db + hub.db，状态分散。现在统一到 SQLite 单文件，WAL + RLock
串行化 + busy_timeout 30s，零外部依赖。

### D6: AIGC 合规四道闸门

1. **安全扫描**：高危词（政治/色情/违法/恶意）命中直接拒发
2. **AIGC 标识**：`source=ai` 的文章强制在 ext 打 `aigc:true` + 模型名 + 文末显式声明
3. **双模型审查**：配了 `REVIEW_MODEL` 走第二模型审；未配走本地 heuristic（篇幅/占位符/代码块标注）
4. **发布闸门**：AI 源文章强制 `draft_only`，禁止直接上线；须人工二次确认后才可正式发布

### D7: 版本历史无需 Git

改文章前自动 snapshot 到 `version_history` 表（行级 diff + 全文按 sha 去重）。
回滚本身也是一条新版本记录，所以"回滚的回滚"就是恢复——无覆盖式破坏。

### D8: 定时发布不引 Celery/APScheduler

30 秒轮询 `scheduled_tasks` 表 + 串行投递到 TaskManager，一次性线程（daemon=True）。
一次性任务执行完自动 disable，`cron` 表达式走手撸最小 5 段解析（*/n 步长 + 逗号 + 连字符）。

### D10: 能力矩阵用代码声明，不用文档表格

文档表格没人执行。`PlatformAdapter.capabilities` + `CAPABILITIES` 表把
"这个平台支持什么"变成可断言的代码：`/platforms` 下发、发布/更新前分流、
前端置灰、`test_capability_matrix_matches_reality` 反向校验"声明 update=True
的平台不能抛不支持"。新增平台必须显式声明，默认全 False——默认 True 会制造
新的"看起来支持"假象。

### D11: 媒体走内容寻址，不走数据库 BLOB

图片按 sha256 存 `data/media/<前2位>/<sha><ext>`，DB 只存元数据。
理由：SQLite 存 BLOB 会让单文件库随图片膨胀到 GB 级，备份/迁移/VACUUM 全变慢；
文件系统 + 内容寻址天然支持 immutable 缓存与 CDN 回源。同图只存一份。

### D12: 检索用 FTS5 而非外部分词器

中文分词（jieba）会引入词典与进程内状态，而 FTS5 的 trigram 分词对中文
是"3 字滑窗"，无需词典、无额外依赖、支持任意子串匹配。代价是 2 字查询
无法走索引（trigram 最少 3 字符），这类查询回退 LIKE——两字词本来就少。

### D13b: 外部 MCP Server 用桥接而非内置

社区已有高质量 MCP Server（Playwright 官方浏览器、fetch 抓取）。自研等于
重复造轮子，但直接依赖会把别人进程嵌进主服务。桥接方案：MCP Client 按
配置 stdio 拉起、工具白名单授权、超时兜底、默认关闭——用得上就接，
用不上零开销。

### D13c: 平台限流冷却按平台落库

429 的语义是"平台现在不收任何请求"，不是"我这次失败"。冷却状态按平台
存 SQLite（只延长不缩短），让同平台后续任务一起等，避免把限流窗口越撞越长。

### D13: 标签治理

本地 `tag_stats` 表记次数 + `config.json` 存 `alias_map` 同义合并规则。
分类靠关键词白名单（语言/框架/领域/其他），AI 推荐只补全本地 Top 20 高频之外的缺口。
