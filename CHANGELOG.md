# Changelog

---

## [0.9.0] - 2026-10-29（遗留问题清零：MCP 桥接 / 平台限流 / 安全加固 / 架构拆分）

> 主题：**上一轮"剩余风险"全部关闭；接入外部 MCP Server 生态；service.py 988→483 行。**
> 测试：后端 453 → 480；前端 57 保持；六道门禁全绿。

### 外部 MCP Server 桥接（用户诉求："网上有 mcp 工具直接拿来用"）

- 新增 `service/publishing/mcp_bridge.py`：Harbour 作为 MCP **Client**，通过
  官方 `mcp` SDK（2.3.0）stdio 拉起并调用任意第三方 MCP Server。
- 已核实的可直用生态（npm/PyPI 实测）：
  `@playwright/mcp@0.0.83`（Apache-2.0，浏览器自动化）、
  `mcp-server-fetch@2026.10.28`（MIT，网页抓取，robots.txt 合规）、
  `@wechatsync/cli@1.1.0`（MIT）。
- 安全默认：**工具白名单**（未授权即拒绝）、调用超时兜底、独立线程不阻塞、
  默认关闭（未配置零子进程）。
- 端点：`GET /mcp/bridge/status`、`GET /mcp/bridge/{s}/tools`、
  `POST /mcp/bridge/{s}/call`；MCP 侧新增 3 tool（27 → 30）。
- 端到端测试：真实拉起子进程 → 握手 → 调工具 → 拿回结果（6 条回归）。
- 配置样例进 `config.example.json`。

### 平台级限流冷却（retry-after）

- 新增 `ratelimit.py`：解析 `Retry-After`（秒/HTTP-date）与
  `X-RateLimit-Reset`（秒/毫秒），冷却状态按平台落 SQLite（重启不丢、
  多任务共享），只延长不缩短，到期自清理，上限 30 分钟防锁死。
- 适配器 `api_get/api_post` 把限流响应头带进错误串，任务引擎识别 429/503
  自动置平台冷却，同平台后续任务整体延后（不再撞窗口）。
- `/status` 暴露 `platform_cooldowns`；`POST /platform-cooldowns/clear` 手动解除。

### 媒体安全与对外地址

- **上传按内容校验**（Pillow 魔数嗅探）：扩展名/Content-Type 均可伪造，
  现在解码验证 + 格式白名单（SVG 刻意排除——XML 可携带脚本）+
  4000 万像素上限（防解码炸弹）。
- **对外地址自动探测**（`middleware/public_base.py`）：媒体改写不再要求
  手工配 `PUBLIC_BASE_URL`——从请求 Host / 受信代理的 X-Forwarded-* 自动
  推断；本机回环访问不误报；`/media/stats` 显示来源与排查提示。

### AST 级内容变换

- 新增 `content_transform.py`（markdown-it-py token 流）：去开头重复 H1、
  删正文标签行、图片 URL 改写——代码块内容天然不受影响（字符串正则的
  经典误伤源）。规则表按平台接线进 `publish_single`，失败退回原文。

### 架构：service.py 上帝类拆分（行为逐字不变）

- AST 机械切片，不手抄：`article_ops.py`（文章/导入 95 行）、
  `ai_ops.py`（AI 委托 81 行）、`publish_ops.py`（发布编排 454 行）。
- `service.py` 988 → 483 行，只剩账号/浏览器池/生命周期；
  Hub 通过多继承组合，MRO 验证 + 全量 480 测试锁行为。

### 适配器离线契约探针

- 新增 `adapter_probe.py`：接口实现/能力一致性/选择器合法性/选择器版本
  静态检查；`tests/fixtures/dom/*.html` 采样真实页面后可做选择器漂移比对
  （平台改版的离线信号）。端点 `GET /adapters/probe`、
  `POST /adapters/probe/{p}/dump`。真机回归依旧需要账号，但能提前发现的
  问题（声明矛盾、选择器写错、改版失配）不再等上线才爆。

### 已核实的生态依赖

| 包 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| mcp (PyPI) | 2.3.0 | MIT | MCP Client/Server SDK |
| @playwright/mcp (npm) | 0.0.83 | Apache-2.0 | 可选浏览器后端 |
| mcp-server-fetch (PyPI) | 2026.10.28 | MIT | 可选抓取后端 |
| markdown-it-py | 4.2.0 | MIT | AST 内容变换 |
| Pillow | 13.0.0 | MIT-CMU | 图片魔数嗅探 |

---

## [0.8.0] - 2026-10-29（能力补全：媒体管线 / 检索 / 能力矩阵 / 部署修复）

> 主题：**把"文档写了但代码没实现"的 9 处缺口补齐；用成熟库替换 6 处自研半成品；
> 首屏 JS 从 2.5MB 降到 1.04MB；内容质检从 15.7s→1.1s、带缓存 0.22s。**
> 测试：后端 415 → 453（新增 38 条，全部实测可回退）；前端 57 保持。

### 修复：阻断级（用户可感知）

- **启动脚本全部是坏的**：`start.sh/.bat/.ps1` 用 `uvicorn start:app`，而
  `start.py` 的 `app` 建在 `main()` 内、模块级没有该属性 → 三个平台的一键启动都
  直接 AttributeError。改用 `app.factory:create_app --factory`，并支持 `PORT` 环境变量。
- **Tauri 健康探活打错路径**：`lib.rs` 请求 `/api/hub/health`，实际挂载在
  `/api/v1/hub/health`，桌面版永远认为后端未就绪。
- **Tauri 打包找不到前端产物**：`frontendDist` 指向 `../dist`，而 vite 实际输出到
  `server/static`；`tauri build` 会在打包阶段失败。
- **内容质检接口契约错位**：`POST /qa/analyze` 只声明 query 参数，前端一直发 JSON
  body → 每次点「质检」都 422。改为 body/query 双支持，空正文显式 422。
  同类修复：`POST /tags/suggest`。
- **未分类错误重试是"假重试"**：置 pending 后立刻 `_wake.set()`，3 次重试在 1 秒内
  打完，对 429/503 毫无意义。改为指数退避 2/4/8s（封顶 60s），退避窗口内不取任务。
- **能力矩阵与实现不符会静默失败**：头条/开源中国 `update()` 直接抛"不支持"，
  但 `sync_pending` 会把它们全塞进更新循环 → 每次同步都返回一串假失败。
  现在按能力矩阵跳过并如实标注 `skipped`。

### 新增：媒体资产管线（对标 Wechatsync / Artipub 的基线能力）

- `service/publishing/media.py`：内容寻址存储（sha256 去重）、Markdown/HTML 图片
  改写、SSRF 防护下载（公网 IP 校验 + 手动重定向 + 15MB 上限）、
  `/media/*` 本地托管（immutable 缓存头）。
- 发布与更新链路自动改写正文图片为稳定 URL（需配 `PUBLIC_BASE_URL`）；
  未配置时整段跳过，单图失败保留原地址——一张图不阻断整篇发布。
- 编辑器支持粘贴/拖拽上传（`md-editor-v3` `onUploadImg`），同图不重复入库。
- 新端点：`POST /media/upload`、`POST /media/ingest`、`GET /media`、`GET /media/stats`、
  `GET /media/{sha}{ext}`。

### 新增：检索与导入

- **全文检索换 FTS5**（trigram 分词，中文无需外部分词器）：`search_articles` 从
  `LIKE '%kw%'` 全表扫改为倒排索引 + 相关度排序；2 字查询走 LIKE 兜底（trigram 最少 3 字）。
  外部内容表 + 触发器保持同步，启动时自动 `rebuild` 回填历史数据。
- **列表接口默认不再返回正文**：新增 `slim` 投影（含 `chars` 字数），
  `GET /articles` 与 `/articles/page` 默认 slim，需要正文传 `slim=false`。
- **网页导入**：`trafilatura` 正文提取 → Markdown，`POST /articles/import` 支持
  `{url}`；本地导入改用 `python-frontmatter`（旧的手写 `split("---")` 遇到正文
  分隔线会切错，YAML 列表 tags 也解析不了）。
- MCP 新增 4 个 tool：`search_articles` / `import_from_url` / `list_media` / `ingest_image`（23 → 27）。

### 新增：能力矩阵（代码级，不是文档）

- `PlatformAdapter.capabilities` + `CAPABILITIES` 表：publish/draft/update/list/tags/
  cover/question/hosted_media 八项，逐平台按真实实现声明。
- `/platforms` 下发 `capabilities`；发布轨按此置灰并显示"该平台不支持发布"；
  `publish()` 与 `update()` 在进适配器前就按能力分流。

### 新增：可观测与调度

- `GET /metrics?format=prometheus`：标准文本 exposition（prometheus_client），
  Grafana 可直接抓；JSON 快照格式与字段完全不变。
- cron 表达式交给 `croniter`（完整 5 段语义，含 L/W/#），内置解析器保留为无依赖兜底。
- 相似度签名落表缓存（`article_simhash`，内容哈希失效），200 篇库质检
  冷启动 1.5s、热缓存 0.22s（优化前 15.7s）。

### 替换：自研 → 成熟库（功能只增不减）

| 位置 | 原实现 | 现实现 | 收益 |
|---|---|---|---|
| 高危词扫描 | 逐词 `in` 全文扫 | `pyahocorasick` | 300 词表 41.7× 提速；顺带修掉中文语境下 `` 漏检 |
| 标签同义合并 | 精确大小写匹配 | `rapidfuzz` 编辑距离 + 前缀/包含规则 | 新增 `GET /tags/clusters` 候选清单 |
| LLM 调用 | 裸 `requests.post` | `tenacity` 指数退避 | 429/5xx 自动重试，4xx 立即失败 |
| 内容相似度 | 每次全量 32×md5 | blake2b + 16 置换 + 签名缓存 | 15.7s → 1.1s（14×） |
| 定时 cron | 手撸 5 段解析 | `croniter` | 支持完整 cron 语义 |
| Markdown 元数据 | `split("---", 2)` | `python-frontmatter` | YAML 列表/冒号/嵌套正确解析 |

### 性能

- 首屏 JS（modulepreload）2.56MB → 1.04MB（gzip 860KB → 349KB）：
  视图改 `defineAsyncComponent`，`md-editor-v3`/`echarts` 按需加载；
  去掉把它们绑成固定 chunk 的 `manualChunks` 条目（那正是懒加载失效的根因）。
- 后端加 `GZipMiddleware`（桌面版没有 nginx 代劳压缩）。
- 门禁 `compileall` 排除 `.venv`：10.85s → 0.3s。
- `articles` 列表查询不再带正文（100 篇 × 20KB 正文的无效序列化消失）。

### 修复：工程卫生

- 数据目录统一：事件日志 / 验证码截图 / 调试 DOM / webhook 库 / 媒体全部跟随
  `db.data_root()`；此前桌面版会出现"DB 在 APPDATA、日志留在安装目录"的分裂。
- `media_root()` 跟随 `db.DB_PATH`（测试隔离依赖它，否则测试会写进仓库）。
- webhook 存储不再自己拼数据库路径。
- 测试：`test_capability_upgrade.py` 新增 36 条，覆盖上述每一条修复（含 3 条
  部署脚本/配置一致性断言）。

---

## [Unreleased] - 2026-10-27（品牌更名 Harbour + 工程质量债偿还）

> 主题：**项目更名 Harbour（含仓库/logo/代码标识）；前端从零建测试体系；修复 7 个阻断缺陷；门禁四道 → 六道；恢复 CI。**
> 测试：后端 379 → 390；前端 0 → 14（vitest）；新增 eslint 门禁。

### 品牌更名（breaking）

- 仓库原地改名 `badhope/content-harbor` → `badhope/harbour`（GitCode API，不删库重建）。
- 包名/产物：Cargo `harbour`、PyInstaller `harbour-backend`、Tauri identifier `com.harbour.hub`。
- 环境变量：`HARBOR_*` → `HARBOUR_*`（`DATABASE_URL` / `SECRET_KEY` / `API_TOKEN`）。
- Webhook 契约头：`X-Harbor-Event` / `X-Harbor-Signature` → `X-Harbour-*`（**外部集成需同步**）。
- 新 logo：蓝底白锚 + 双波浪，50 个 Tauri 图标全套重绘（ico/icns/ios/android/windows）。
- 文档统一中文单语，删除重复的 `README.en.md`。

### 修复：7 个阻断缺陷

- **鉴权失败返回 500**：中间件层 `raise HTTPException` 不走 FastAPI 异常处理器，改返回
  `JSONResponse(401)`。端到端实测（真实打包 sidecar）错 token → 401、对 token → 200。
- **任务队列饿死**：`list_tasks(limit=10)` 先按 id 倒序再重排，积压超 10 条时最早任务
  永不执行；新增 `db.next_pending_tasks()` 在 SQL 层 `ORDER BY created_at ASC, id ASC`。
- **定时任务假成功**：`update()` 不校验存在性且无条件 `return True`，路由对 None 解包抛 500；
  改 rowcount 判定。
- **`db.update_article` / `update_task` 静默成功**：不检查 rowcount，改返回布尔。
- **前端保存后 ReferenceError**：`article.js` 调用 `useHubStore()` 却未 import，保存必抛。
- **发布发旧稿**：`hub.publish` 只在新建时保存，已有文章改完点发布会发旧版；改为
  `!currentId || dirty` 都先保存。
- **POST 重复发布**：axios 拦截器对所有方法重试 5xx，限定仅 GET（幂等）。
- **未保存标记失效**：`WriteView` 直接给 Pinia getter 赋值（静默失败，有丢稿风险），
  新增 `markDirty()` action。

### 修复：其他工程质量

- `service` 层不再抛 `HTTPException`，改抛领域异常 `GateError`，由 `app/exceptions.py`
  映射 422；MCP/CLI 同路径可识别业务错误。
- `HUB_DATA_DIR` 此前只在 sidecar 设置、无人读取（桌面版数据写进安装目录）；
  `db.data_root()` 统一接管 db / Fernet 密钥 / browser profile 三处。
- 依赖声明纠正：删除零使用的 `aiosqlite`、`passlib`；显式声明 `bcrypt` / `cryptography`
  （原靠传递依赖，PyInstaller 精简环境会缺包）。
- 时间格式化 6 处重复收敛到 `web/src/utils/format.js`；`humanize.human_type` 与
  `human_click.human_type` 重复且前者零调用，删除。
- `pyinstaller.spec` 由 onedir 改 onefile：Tauri `externalBin` 只接受单文件，
  onedir 产物缺 `_internal` 无法启动；README 也承诺单 exe 双击即用。

### 新增

- **前端测试体系**：vitest + @vue/test-utils + jsdom + @pinia/testing，14 个用例
  （视图互斥、保存/发布契约、重试策略、dirty 状态）。均做过回退验证确认能抓回归。
- **ESLint 9 flat config** + eslint-plugin-vue。
- **CI 恢复**（`remove-ci-workflow` 后长期无自动化）：双 job，backend 跑
  compileall/ruff/pytest/装配冒烟，frontend 跑 eslint/vitest/build。
- **门禁四道 → 六道**：compileall → ruff → pytest → eslint → vitest → vite build
  （`scripts/check_all.ps1` / `.sh` 与 CI 对齐）。

### 验证

- 六道门禁全绿；pytest 390 passed / vitest 14 passed / ruff + eslint 零错误。
- `cargo check` 通过（Tauri Rust 侧首次实际编译验证）。
- onefile sidecar（25MB）实测启动 + `/health` 200。

---

## [Unreleased] - 2026-10-27（第二轮工程债：可观测性 / 资源边界 / 分页 / 无障碍）

> 主题：**修掉上一轮如实记录的长期运行隐患；列表不再静默截断；前端对比度与键盘可达达 WCAG 2.2 AA。**
> 测试：后端 390 → 402；前端 14 → 39（新增分页、列表可达性、a11y 静态门禁）。

### 修复：长期运行隐患

- **指标直方图不是累积桶**：`MetricRegistry.observe` 只统计首个命中的桶，`/metrics` 的桶计数
  无法用于 `histogram_quantile`。改为 Prometheus 累积语义，并新增 `quantiles`（p50/p90/p99，
  桶内线性插值）。
- **限流桶表无上限**：任意来源换 IP 即可让 `_buckets` 无限增长。新增 10000 桶上限，按最近
  使用淘汰最旧键（`pop` 后重插实现 LRU）。
- **浏览器池持锁关实例**：`_acquire` 淘汰 victim 时在池锁内调用 `close()`，一次关停会阻塞
  整池取用。改为锁内只摘除、锁外关闭。

### 新增：列表分页

- `GET /articles/page`、`GET /tasks/page`：返回 `{items,total,offset,limit,has_more}`，
  `limit` 上限 200；`/articles`、`/tasks` 原样保留。
- 前端文章库改用分页 store（首屏 50，`loadMore` 追加并去重，计数显示「已加载/总数」）；
  任务中心拉取上限提到 200 并在被截断时显示「仅显示最近 N / 总数」。
- 定时任务的文章下拉单独翻页取全量，避免只能选到第一页。
- 文章分页排序加 `id DESC` 次键，`updated_at` 相同的批量导入不再出现跨页重复/漏项。

### 无障碍与设计系统（WCAG 2.2 AA）

- 设计 token 对比度达标：`--tx-3` 5.5:1、`--tx-4` 5.0:1、`--accent` 5.67:1，
  `--ok/--warn/--bad` 作为文字与底色均 ≥ 4.5:1；Element Plus 主题变量同步。
- 文章列表项、平台卡片、标签云改为按钮/勾选语义（`role` + `tabindex` + Enter/Space）；
  图标按钮补 `aria-label`。
- 8 个 `el-dialog` / `el-drawer` 的固定 px 尺寸改为 `min(px, 94vw)`，窄屏不再横向溢出。
- 新增 `web/src/a11y.test.js` 静态门禁：token 对比度、弹窗宽度、交互语义，改坏即红。

### 验证

- 六道门禁全绿；pytest 402 passed / vitest 39 passed / ruff + eslint 零错误。
- 回退验证：直方图改回「首个命中」→ 累积桶用例转红；移除列表项 `role/tabindex` →
  可达性用例转红。

### 复审加固（同日第二轮）

独立复审后修掉 6 个真缺陷，每条都有回归测试与回退验证：

- **`get_hub()` 无锁（阻断）**：首屏并发 5-6 个端点时多线程各自 `new Hub`，每个都跑
  `PRAGMA journal_mode=WAL` + 建表，SQLite 上必现 `database is locked` → 首屏 500。
  改双检锁；8 线程实测修复前 6 轮有 4 轮报错，修复后 4 轮 0 错误。
- **登录失败被标成「完成」（阻断）**：`_dispatch` 返回 `(ok, msg)` 时 `_run_task` 不看 `ok`
  就写 `status=ok`，「等待登录超时」在任务中心显示成功。改为直接抛错；login 的超时 /
  无显示 / 遇验证码加入 `LOGIN_PERMANENT_HINTS` 快速失败，不再重开浏览器空转。
- **限流桶淘汰会洗白配额**：原「最旧未用」淘汰让轮换来源能把自己未花完的桶挤掉拿回满额。
  改为「剩余令牌最多者优先」；`time.monotonic()` 移进锁内（锁外取到会倒扣令牌）；
  `max_buckets<=0` 改为显式关闭上限。
- **分位尾部失真**：落进 `+Inf` 桶时原返回最大有限边界（60s），120s 观测被报成 `p99=60s`。
  改为返回实测 `max`。
- **浏览器池锁外 close 引入同 profile 二次启动窗口**：新增 `_closing` 事件表，同 key 的
  `_acquire` 等旧实例关完再启动。
- **参数越界**：`limit<=0` 在 SQLite 等于不限量（能拉整表），超大 `offset` 溢出 INTEGER
  直接 500；db 层与路由层统一夹到 `1..1000` / `0..1e6`。

前端同批：`loadMore` 的 offset 改为按「服务端已消费行数」前进（原先用去重后的本地条数，
批量导入后永久卡在同一页）；`loadList` 加代际防旧响应覆盖；`waiting_human` 任务单独查询
后合并（原可能落在 200 条之外导致角标漏项）；「已发布 N 篇」改用文章数口径。

门禁：pytest 411 / vitest 42。

---

## [Unreleased] - 2026-10-28（真实登录链路验证）

> 主题：把「内置浏览器扫码登录」从静态核对变成实机验证，并把验证结论写回代码注释。

### 验证结论（实测，非推断）

- **patchright Chromium 可在本机启动**：`BuiltinBrowser.start()` 有头 / 无头两种模式均成功
  （引擎 153.0.8010.12），`channel=msedge` 亦可（156.0.4314.42）。此前记录「浏览器路径未验证」
  一条已可销账。
- **CSDN 登录页可用**：真实打开 `passport.csdn.net/login`，页面正常渲染，微信扫码二维码
  （data URI，180×180）可读出并落盘，`CaptchaPolicy.detect` 未误报。
- **掘金登录页二维码可读**：`img.qrcode-img` 在 1 秒内生成 512×512 PNG data URI；两次会话
  QR 内容不同（哈希 8b9576d6… vs 602d70b6…），确认是服务端新签的二维码而非静态占位图。
  注：直接调 `passport/web/create-qr64` 返回 `error_code:16 该应用无权限`（缺前端签名参数），
  属于正常的接口防护，不影响页面内的二维码渲染。
- **知乎登录页为扫码优先布局**：`signQr-container` / `Qrcode-container` 存在，二维码由画布
  动态绘制（无 data URI，需 canvas 读取）。
- **整条登录链路可跑通**（本地 mock 平台端到端）：`POST /accounts/{p}/login` → TaskManager →
  `Hub.login` → `BuiltinBrowser` 有头启动 → `ensure_login` 轮询 → cookie 落库 + profile 快照
  写入 → `accounts` 状态 `logined` → 重新开浏览器 `check_auth` 返回 True（登录态可复用）。

### 修复（验证过程中发现）

- **登录失败假成功**：见上节「复审加固」。实机复现：掘金/知乎 `timeout=10` 的登录任务在
  修复前显示「完成 / 等待登录超时」，修复后正确显示
  「failed / 登录未完成（超时/未扫码），不再自动重试」。
- **首屏并发 500**：起真实服务并发打 `/status` 时复现 `database is locked`，即 `get_hub()`
  竞态；修复后 8 并发 0 错误。

### 未验证（如实记录）

- 真实账号扫码：需要真人用手机扫码，本轮没有可用的平台账号，因此只验证到「二维码正确生成
  并可读」为止，**未完成真实登录**。验证方法与产物：`.tmp/*_login_qr.png`（已随手清理）。
- 各平台登录成功后的发布/更新链路未在本轮触发（需要真实账号）。

---

## [Unreleased] - 2026-10-28（登录诊断脚本）

> 主题：把本轮手工排查固化成可复用工具，下次接新平台不用再写一次性脚本。

### 新增

- `scripts/login_probe.py`：一条命令完成「开浏览器 → 打开平台登录页 → 等二维码/验证码 →
  导出二维码 PNG → 打印页面标题/登录态/验证码类型」。参数 `--platform`、`--headed`、
  `--wait`、`--out`。用于判断「登录页是否可用」与「二维码是否真的生成」，不代替人扫码。
- `scripts/login_probe.py --check-cookie`：复用本地 profile 复检登录态（登录后验证是否真的
  记住），输出 `check_auth` 结果。
- 门禁第 1 道补 `scripts/` 目录的 compileall：原先只编译 `backed/`，仓库根与 `scripts/` 的
  脚本语法错误漏检。


---

## [Unreleased] - 2026-10-28（真机截图巡检 + 主题失效修复）

> 主题：**按 1440/1024/390 三档视口逐屏截图巡检，修掉 3 个静默缺陷与 4 个窄屏布局缺陷；任务中心改卡片列表，文章库加搜索筛选。**
> 测试：后端 411 → 414；前端 42 → 49。

### 修复：静默缺陷（不报错、界面上却看不出来）

- **Element Plus 主题色板完全失效**：`main.scss` 把 `--el-color-*` 写在 `html {}` 里，而 EP
  默认变量定义在 `:root`（伪类优先级高于类型选择器），自定义色被静默覆盖。实测
  `--el-color-danger` 仍是默认 `#f56c6c`，标签渲染成 `rgb(245,108,108)`。按钮另有
  `.el-button--primary` 显式样式，看上去"像是生效了"，掩盖了问题。改 `:root` 并补齐
  primary/success/warning/danger/error/info 全套 light-3/5/7/8/9 + dark-2。
- **`/tasks` 与 `/tasks/page` 的 `platforms` 是 JSON 字符串**：详情端点早解析，列表端点漏了。
  前端 `(row.platforms || []).join()` 拿到字符串抛 TypeError → Vue 渲染中断 → 任务表「对象」
  列空白、状态标签落到对象列、后续列整体错位。新增 `_task_row()` 统一收口；详情补
  `article_title`，任务可直接跳回文章。
- **`tags.py` 的 config.json 路径算错两层**：`Path(__file__).parent.parent` 得到
  `backed/service/config.json`，既不是 ai/gate/metaweblog 共用的 `backed/config.json`，也不在
  `.gitignore` —— 点一次「标签治理 → 合并」就在仓库里凭空多出一个未跟踪文件。收口
  `_config_path()`。

### UI：窄屏布局与卡片化（截图驱动）

- **`.pane-head` 允许换行 + 标题 `nowrap`**：390px 下「任务中心」「设置」曾被压成每行一个字。
- **任务中心改卡片列表**：原 1166px 表格塞进 264px 视口只剩三列可见。新卡片带状态色左条、
  平台标签、错误摘要、待人工任务就地操作，`role=button` + Enter/Space 键盘可达。
- **文章库加搜索与状态筛选**：搜索框（标题+摘要）+ 状态 chips（全部/草稿/已发布/待审，
  `aria-pressed`）+ 无结果一键清空 + 筛选态明确提示「只作用于已加载的 N 篇」。
- **发布中心统计卡改网格**：宽屏 4 卡 + 操作按钮同排，窄屏 2×2 + 按钮独占一行。
- **AI Provider 价格标签 `nowrap` + 折叠头窄屏换行**：原先「经济/均衡/旗舰」被挤成竖排。
- **设置页窄屏隐藏副标题**，腾出宽度给标题。

### 测试基建

- `el-input` 的 vitest 桩原是无 `v-model` 的裸 `<input />`，被测组件收不到输入值——过滤逻辑
  根本测不到（典型"测试绿但功能未覆盖"）。改为转发 props/emits。

### 验证

- 六道门禁全绿；pytest 414 passed / vitest 49 passed / ruff + eslint 零错误。
- 三档视口（1440/1024/390）× 四视图逐屏截图：所有页面 `scrollWidth == clientWidth`。
- EP 变量实测：primary `#2A5CE0` / danger `#B0382E` / success `#0F7A52` / warning `#8A5510`。
- 回退验证：还原 `deps.py` → Hub 竞态用例红；还原 `article.js` → 分页 3 例红；
  还原 `browser_pool.py` → 池等待用例红；还原 `App.vue` → 侧栏计数 2 例红
  （`published` 平台实例数被当成「已发布 N 篇」，一篇发两个平台会显示 2 篇）。

---

## [Unreleased] - 2026-10-14（第四轮：AI 声明改走平台选项 + 发布多模式）

> 主题：**AI 声明不再写进正文（改用平台自带「AI 辅助」选项）；发布支持三档操作模式**。
> 测试用例 364 → 379（新增 `test_publish_modes.py` 10 例、`test_task_pending_user.py` 4 例；
> 更新 `test_gate.py` 3 例以反映新契约）。

### 变更：AI 声明改走平台选项（不再污染正文）

- `gate.add_aigc_label` 默认**不再向正文追加**「本文由 AI 辅助生成」；标识义务改由平台
  自带的「AI 辅助创作」选项承担（知乎适配器早已内置该开关的自动勾选，本轮接线）。
- `ext.aigc / ai_model / aigc_at` 留痕**保留**（可追溯性不丢）。
- 新增配置 `AI_DECLARATION_IN_TEXT`（默认 false）：无原生 AI 选项的纯 API 平台
  （博客园 MetaWeblog / 掘金 API 等）可置 true 恢复正文声明。
- 前端发布轨新增「勾选平台的『AI 辅助创作』声明」开关（默认开）。

### 新增：三档发布模式（谁来操作）

- `settings[platform].mode`（或请求级 `mode`）：**auto**（默认，全自动发到底）/
  **assist**（打开平台页并预填标题，停在提交前等你改配置/传附件，自己点发布）/
  **manual**（只打开平台新建页，一个字都不自动填，全程自选自填自发）。
- 中断模式（assist/manual）任务落 `waiting_human`，发布实例状态 `pending_user`
  （前端显示「等你确认」）；**resume 对 pending_user 是「标记完成」语义，不重跑**
  ——避免在平台上重复发布。
- 中断时先释放浏览器池锁再等用户（否则同账号其它任务被堵）；纯协议平台自动降级 auto。
- API：`POST /articles/{id}/publish` 增 `mode`（非法值 422）；AI 稿全自动发布仍走人审闸门，
  中断/手动模式由用户在平台上亲自确认承接合规（仍保留高危词扫描）。

===

## [Unreleased] - 2026-10-14（第三轮：知乎回答问题能力 + 两篇真实发布）

> 主题：**新增知乎「回答问题」链路并完成两篇真实上线（专栏文章 + 问题回答）**。
> 测试用例 357 → 364（新增 `test_zhihu_answer_flow.py`，7 用例）。

### 新增：知乎回答问题（`publish_as_answer`）

- 在发布轨的知乎卡片粘贴**问题链接**即进入回答模式（留空仍发专栏文章）：
  `settings.zhihu.question_url` → `publish_single` options 下发 → 适配器分流。
- 真机探针（2026-10-14）钉死的两条关键事实：
  - 问题页「写回答」按钮被 sticky 顶栏拦截，`locator.click` 与 `force` 点击都失败，
    **JS `click()` 是唯一稳定通道**；编辑器出现需自行轮询存在性
    （`_wait_editor(want_text=False)` 在编辑器未出现时也会满足条件，不能用来等开场）。
  - 回答编辑器与专栏编辑器同构（同款 Draft.js `div[data-contents="true"]` +
    「导入→导入文档」+ file input），整套导入/清空/回读机制**原样复用**。
- 沿用既有发布纪律：点发布前回读正文（证不出即拒发，绝不带错内容上线）、
  发布后双通道验证（URL 出现 `/answer/` **或** 按钮变「修改回答」）、失败存现场 debug。
- 回归：`test_zhihu_answer_flow.py` 7 用例（分流 / 双通道验证 / 回读不符拒发 /
  编辑器未开报错 / draft_only 不发布 / 发布未确认报错），不触网；首轮 6 红 → 修夹具后全绿。

### 真实发布（本地实测记录）

- 专栏系列第 2 篇《学 AI 第二步：把活儿交给 AI 之前，先过这 4 道关》：
  https://zhuanlan.zhihu.com/p/2093716695743575672 （任务 `74c0460899`，55s，ok）
- 回答《AI发展这么快，普通人应该从哪里开始学习AI？》：
  https://www.zhihu.com/question/2088198385572526079/answer/2093717357122396372
  （任务 `2640a8aa68`，25s，ok，新链路首次 live 验证）
- 两篇均走 `human_confirmed` 通道（AI 稿人工核验后上线），`ext` 留痕
  `aigc=True / human_confirmed_publish=True`；只读复核确认线上正文含 AIGC 声明
  （标识由发布门禁幂等注入，数据库保留作者原文为有意设计）。

### 待办（报告向用户说明）

- 候选问题 2090776648023013094 上可能有探针期留下的空草稿，需要时可在知乎 App 删除。
- 删除类能力目前全平台缺失（仅「原地更新」）；实施需独立任务 + 二次确认 + 回读验证，待用户指定目标。

===

## [Unreleased] - 2026-10-14（第二轮：真实发布 + 前端重构）

> 主题：**让真实发布真正可用 + AI 配置端点 + 前端按发布工作流整体重构**。
> 测试用例 339 → 355（新增 `test_real_publish_and_ai_config.py`，14 用例 + imports 参数化 1）。

### 修复：测试套件在 Windows 上的偶发整进程崩溃（访问违例）

- 症状：全量 pytest 在机器高负载时偶发 `Windows fatal exception: access violation`
  （实测约 1/5），崩溃点在 sqlite `executescript`。
- 根因（三处叠加）：① `LockedConnection.close()` 未持执行锁——close 落在并发
  execute 中间会把底层连接抽走（use-after-close → AV）；② 事件派发线程
  `stop_dispatcher()` 只置停、不 join、不清线程引用（且残留引用让重启永远 no-op）；
  ③ 测试侧 Hub 只关 worker 线程、不停 scheduler/派发线程，解释器退出期线程仍在碰 sqlite。
- 修复：`close()` 纳入 `_exec_lock`（+ 锁纪律回归测试）；`stop_dispatcher` 改为
  置停 + 唤醒 + join + 允许重启；测试夹具统一改用 `hub.close_all()` 全量回收；
  `Hub.close_all()` 兜住全部子步骤异常（退出零噪声）。
- 验证：修复后 **18 次连续全量运行全绿**（357 passed）。

### 大修复：配置文件路径与文档位置不一致（写作风格档案从未加载）

- `ai.py`/`gate.py`/`ai_providers.py` 的 `ROOT = parent.parent` 解析到
  `backed/service/`，而 config.json 的**文档位置是 `backed/config.json`**
  （`.gitignore`、`api/auth.py` 均按此读）——AI 密钥、`REVIEW_MODEL`、
  `AI_DIRECT_PUBLISH`、`AIGC_GATE` 开关实际从未从文档位置被读到；
  写作风格档案指向不存在的 `backed/service/docs/writing-style.md`，
  **风格档案功能从未生效**（`load_style()` 永远返回空）。
- 修复后统一指向 `backed/config.json` / 仓库根 `docs/writing-style.md`；
  新增路径契约测试把四个模块的 `CONFIG_FILE` 与 `STYLE_FILE` 锁死。
- `service.py` 的博客园 MetaWeblog 令牌保存同样修正到 `backed/config.json`。

### 大修复：AI 写稿此前整体不可用（名称撞车）

- **`providers.py` 与 `providers/` 同名，包优先于模块**——AI 多 Provider 路由模块被
  平台工具包遮蔽：`ai.chat()` 每次调用都在 `_resolve_providers()` 上 AttributeError，
  8 家 AI Provider 路由（DeepSeek/Kimi/GLM/通义/OpenAI/Claude/Ollama）全部失效；
  `GET /ai/providers` 也返回错的（平台工具列表）。
- 修复：`git mv providers.py ai_providers.py` + 4 处 import 点；`/ai/providers` 返回
  AI Provider（掩码），平台外部工具移到 `/ai/tool-providers`；模块导入测试加锁。

### 大修复：AI 稿「永远进草稿箱」的断头路 → 真实发布通道

- 原门禁对 `source=ai` 正式发布一律拒绝，而「人工确认后再发」的通道从未实现，
  `config.example.json` 里设计的 `AI_DIRECT_PUBLISH` 零实现，`PublishIn.live` 是死参数
  ——AI 稿只能进草稿箱，永远发不出去。
- 现在：
  - **human_confirmed**：前端「我已核验」显式确认 → AI 稿可正式上线，
    `ext.human_confirmed_publish/at` 审计留痕；REST/MCP 同步支持该参数。
  - **AI_DIRECT_PUBLISH**（设置页开关）：开启后 AI 稿经安全扫描 + 双模型二审 +
    AIGC 标识后直接上线（自动发布管线场景）。
  - **发布前预检**：确定性拒绝（AI 未确认 / 高危词）在提交前同步 422 并给出可操作
    提示，不再让用户等任务跑完才知道被拒；完整门禁（含 LLM 二审）仍在任务里兜底。
  - 前端错误提示修正：读后端 `{code, message}` 包（此前只读 `detail`，
    门禁拒绝在界面上显示英文 axios 报错）。
- 实测（DEMO 后端 + HTTP）：未确认 422 → 勾选核验发布 ok 且留痕 → 直发开关生效 →
  高危词 422 → 草稿路径不受影响（详见冒烟记录）。

### 新增：AI 配置端点 + 设置界面

- `GET/PUT /ai/config`：7 家 Provider 的密钥（**只回掩码，永不回显明文**）、
  接口地址、默认模型、模型清单、价格级别；`REVIEW_MODEL`（二审模型）、
  `AI_DIRECT_PUBLISH`；白名单合并写 + 原子落盘（tmp+replace），
  `HARBOR_API_TOKEN` 等无关键原样保留；解析失败拒绝写入。
- `POST /ai/config/test`：真实最小调用测连通（ping→pong），支持对未保存字段先测，
  失败返回结构化错误不抛 500。
- 前端「设置 → AI 模型」：Provider 折叠卡片（密钥/地址/模型/级别 + 测试连接 +
  清除密钥）、AI 稿直发开关、二审模型、任务路由说明、写作风格档案状态。

### 前端：按发布工作流整体重构（views 层重建）

- 新壳层：侧栏导航（写作台 / 发布中心 / 任务中心 / 设置）+ AI 就绪与待人工徽标。
- **写作台**：列表 | 编辑器 | **发布轨** 三栏。发布轨 = 平台选择（登录态圆点、
  未登录就地登录、掘金/CSDN 分类记忆）→ 发布方式（默认「立即发布（真实上线）」，
  「仅存草稿」为次选）→ AI 稿合规条（勾选核验 / 直发提示）→ 就地进度与每平台结果
  → 已发布实例状态与单/全平台更新。
- **发布中心**：待人工处置（任务挂起 + 平台草稿待发）、待同步实例一键同步、
  全平台发布记录表。
- **任务中心**：类型/状态筛选 + SSE 实时进度 + 任务详情 + 恢复/放弃。
- **设置**：AI 模型 / 平台账号（登录·检测·登出·诊断·抓取）/ 定时发布 / 通知 Webhook
  / 标签治理，五个面板。
- 移除被取代组件：AppHeader / ManagePanel / PublishWizard / usePublishWizard
  （store 分层与晴空设计 token 保留复用，无新依赖）。

===

## [Unreleased] - 2026-10-14

> 主题：**审查收尾轮 —— 剩余低危项清零 + 本机凭据卫生 + README 损坏修复**。
> 测试用例 333 → 339（新增 `test_review_20261014.py`，6 用例）。

### 修复（审查剩余项）

- **F-09 密钥文件权限**：`crypto.py` 新增 `_restrict_key_file()`——POSIX 保持 chmod 600；
  Windows 改用 `icacls /inheritance:r /grant:r <user>:F` 去掉继承 ACE、只授权当前用户
  （原实现 Windows 上 chmod 静默失败，Fernet 主密钥落盘时 Users 组可读）。
  读取存量密钥时也顺手收紧；收紧失败只打 warning 不阻断密钥加载
  （含 icacls 超时：TimeoutExpired 不是 OSError 子类，已显式兜住）。
- **F-14 站内通知测试隔离**：`_event_notifier` 是模块级环形缓冲，跨测试会串——
  `tests/conftest.py` 增加 autouse 夹具每个测试前清空；新增隔离顺序对锁定该行为。
- **F-27 回滚无操作语义**：`versions.rollback()` 目标版本与当前内容相同时显式返回
  「当前内容与版本 X 相同，无需回滚」——旧实现照常走两处 capture 被哈希去重跳过、
  却返回"已回滚"，pre-rollback/rollback 两条记录全部落空，语义含混。
  短路比对**含 tags**：只改标签的回滚不是 no-op（内容哈希不含 tags，否则标签回不去）。

### 修复（文档）

- **README.md 反引号损坏**：074ce0d「文档打磨」引入——全文 0x60 被替换为
  `\`+`\b` 退格序列，12 处内联代码与 6 对代码围栏全部失效（GitHub/Gitee 上
  代码块不渲染、正文出现游离反斜杠）。已按上下文逐行重建：41 个反斜杠 / 7 个
  退格字节清零，反引号恢复 60 个；发布流程图补回顾围栏。
- 用例数漂移：README/AGENT.md 的 331 → 339。
- review-findings.md 状态同步：补记 F-15/16/17/20/28 已修 + 本轮 F-09/14/27。

### 安全（本机操作，非仓库内容）

- 本仓库 `.git/config` 里 gitcode remote 的明文密码剥离（保留用户名以维持凭据
  查找），验证 credential helper 仍可解析、push 不受影响。
- 全局 `credential.helper` 从 `store`（明文 `~/.git-credentials`，9 条凭据）
  切到 `manager`（Windows 凭据管理器，vault 中已有 gitcode/github 条目）；
  明文文件已删除。**gitcode 密码建议轮换**（曾多处落盘）。

### 环境/部署债清理

- **删除 `backed/requirements.txt`**：uv 迁移后已无任何引用（CI/Docker/脚本均
  用 `uv sync` + `uv.lock`），且内容过期（还写着 `server/mcp_server.py` 的旧路径）。
- **venv 与 lock 对齐**：`uv sync` 移除手工装入的 `web-publish`（git 源、
  全仓零 import 的遗留物）；pip-audit 不再报 "Dependency not found on PyPI"。
- **版本对齐**：`pyproject.toml`/`uv.lock` 的 0.6.5 → **0.7.0**（与 CHANGELOG、
  `api/hub.py` 的 app version 一致；此前元数据落后一个版本）。
- **CI 修复**（`.github/workflows/ci.yml`）：backend job 删掉从未使用的
  pnpm/setup-node 三步（其 `cache-dependency-path: backed/pnpm-lock.yaml`
  指向不存在的文件，会让 CI 直接红）；pip-audit 改为
  `uv run --with pip-audit pip-audit --strict`（原写法往 venv 里 pip install，
  且 `|| true` 与 continue-on-error 双重吞错）。
- **deploy/ 修复**（此前容器根本起不来）：
  - `nginx.conf`：删除 Python 文档字符串行、`limit_req_zone` 移到 http 级
    （原位置 server{} 内 = [emerg] 启动失败）、补 SPA `try_files` 回落、
    `/api/` 反代 + SSE `proxy_buffering off`，`/` 不再整站反代后端；
  - `Dockerfile`：`uvicorn start:app` → `python start.py`（start.py 模块级
    没有 `app`，原 CMD 必 AttributeError）；基础镜像 3.11 → 3.12（对齐 CI/本地）；
  - `Dockerfile.web` + `docker-compose.yml`：web 构建上下文改回仓库根
    （原 context ../web + dockerfile deploy/... 让 Dockerfile 落在上下文之外）；
  - 新增 `.dockerignore`（此前构建上下文会把 `.venv`/`node_modules`/`backed/data`
    全量上传 daemon；并显式排除 `backed/service/data` 登录快照与 `backed/config.json`
    这类含凭据的本地文件，防被 COPY 进镜像）；
  - compose healthcheck `/api/hub/health` → `/health`（前者 404，容器永远 unhealthy）。

---

## [0.7.0] - 2026-10-12

> 主题：**全仓审查 + 修复 0.7.0 拆分引入的运行时缺陷 + 补 AIGC 合规门禁缺口**。
> 测试用例 299 → 330。

### 新增：全仓代码审查与回归测试

审查员逐文件通读 `backed/` 全部 Python 源码（~14,800 行），产出
`review-findings.md`（29 条发现，含 A/B/C/D/E 五类定级）。本轮修复 14 条
关键缺陷，新增 `backed/tests/test_review_20261012.py`（31 用例）锁定所有修复。

### 修复（运行时缺陷）

- **F-29 `Hub._busy_for` 从未定义**：`service.py` 里 5 处 `with self._busy_for(...)`
  但 Hub 从未定义这个方法——只有 `BrowserPoolManager` 有。一运行到登录/检查/验证码
  路径就 `AttributeError`。这是 0.7.0 拆分引入的**必炸点**，现有 298 测试因走
  StubHub/DEMO 模式从未触发。已在 Hub 里补 `_busy_for` 委托。
- **F-05/F-06 `TaskManager._dispatch` 丢参数**：login 的 `timeout`/`on_captcha`、
  refresh 的 `limit` 全部被 API 层接收但 dispatch 硬编码丢弃，`LoginIn.timeout` 传了无效。
  现在从 `task.result` 读出来传给 hub 方法。
- **F-07 路径穿越**：`/accounts/{platform}/check`、`/accounts/{platform}/login` 等端点
  未校验 platform/account，攻击者传 `..` 或 `../../etc` 可以拼到 profile 路径上，
  触发 `shutil.rmtree` 删任意目录。所有账号端点加正则校验 `^[a-zA-Z0-9_-]{1,64}$`。
- **F-18/F-26 AIGC 门禁缺口**：`hub.update()` / `hub.sync_pending()` 完全不过
  `aigc_gate.apply_gate_before_publish`，AI 改写后直接同步上线绕过《AIGC 标识办法》。
  现在 update 也过安全扫描 + AI 源打 AIGC 标识。
- **F-13 占位符误报**：`gate._local_review` 把 `{{ }}` 当占位符，任何 Python f-string /
  Jinja / CSS 技术文档一跑就 reject。移出黑名单，占位符检测只在代码块外的正文做。
- **F-02 连接泄漏**：`_webhook_store.init(conn)` 自建 conn 后不 close，register/list_all
  每次调用泄漏一个 sqlite 连接。加 try/finally。
- **F-03 arity bug**：`ArticleStore.edit` 里 `get_pending_updates(conn, article_id)`
  实际只接受 1 个参数，一旦真被调用就 TypeError。删掉这行。
- **F-12 误发事件**：`_run_task` retry 分支也调 `on_task_complete`，把 retry 当完成发事件。
  只有 fail/human 终态才 emit。
- **F-24 吞中断**：`browser._bw_worker` 捕获 `BaseException`，把 `KeyboardInterrupt` /
  `SystemExit` 当普通异常吞掉。改成 `except Exception`。
- **F-25 死代码**：`BuiltinBrowser.start()` 用 `self.display is False` 判断，
  `ensure_display()` 从不返回 False，Linux 无 X 时不会降级到 headless。改成 `not self.display`。

### 清理

- **F-01 `check_all.ps1` 死锁 bug**：scriptblock 里的 `exit 1` 会终止整个 pwsh 进程
  而不是回到 try/catch，ruff 一红 4 步只跑 1 步，汇总行也打不出来。改成 `throw`。
- **F-10 死代码**：删除 `api/__init__.py::register_routers`（挂 `/api/hub` 但从未被调用，
  实际入口是 `api.hub.register_hub` 挂 `/api/v1/hub`）。
- **F-11 版本号**：`api/hub.py` 独立 app 的 version 从 0.2.0 更新到 0.7.0。
- **ruff F401**：清理 3 处未使用 import（`_webhook_store.time`、`urllib.request`、`crypto.base64`）。

### 新增测试

- **`test_review_20261012.py`（31 用例）** — 锁定本轮所有修复：
  平台名路径穿越防护（6 例）、dispatch 参数传递（2 例）、retry 不发完成事件（2 例）、
  AIGC 门禁 update 路径（2 例）、代码级断言（8 例）、占位符检测（4 例）。
- **`test_gate.py` 更新**：删除 `{{name}}` 占位符回归用例，新增模板语法不误报
  与代码块内 TODO 不误报的回归锁定。

### 门禁

`check_all.ps1` 四道全绿：compileall / ruff / pytest / vite build。


## [0.6.5] - 2026-10-10

> 主题：**给零覆盖的高风险区补测试，并修掉测试挖出的限流绕过漏洞**。
> 测试用例 176 → 278。

### 修复：限流可被一个请求头绕过

`middleware/rate_limiter.py` 的 `_get_client_ip` 无条件采信 `X-Forwarded-For`，
而该请求头由客户端自由填写。任何人每请求换一个 XFF 就拿到一个全新令牌桶，
**限流形同虚设**——而限流是安全边界上的关键一环，此前零测试覆盖。

改为只在**对端本身受信**（本机回环 / `TRUSTED_NETS` 网段）时才采信代理头。
受信判定直接复用 `api/hub.py` 既有约定（`_trusted_networks` / `_peer_is_trusted`），
不另造一套——本机代理部署仍能正确解析 XFF。

### 新增测试

- **`test_gate.py`（61 用例）** — AIGC 合规门禁，此前零直接覆盖。
  它是《人工智能生成合成内容标识办法》的落地实现（显式标识 + 人审闸门），
  错了不是功能 bug 而是合规事故。四道闸门全覆盖：安全扫描（含误阳性排查）、
  AIGC 标识（幂等 / ext 保全 / 损坏 JSON 兜底）、双模型审查（安全扫描优先于
  二审、LLM 失败降级留痕）、发布闸门（AI 源禁裸奔上线）。
- **`test_rate_limiter.py`（24 用例）** — 含限流绕过的回归锁定、跳过路径、
  令牌桶突发/补充/独立键/重置，以及 TRUSTED_NETS 配置容错。
- **`test_human_click.py`（17 用例）** — 贝塞尔采样（steps≤0 除零保护、
  端点精确、控制器外插不发散）、落点夹取（只约束落点，曲线中间点本就该在
  元素外）、候选选择器拼装与降级。

### 顺带纠正

`test_gate.py` 首轮写坏了一条断言（以为 `add_aigc_label` 在文章模型为空时会
回退成别的值）。读实现确认是测试写错而非代码 bug，已改为分别覆盖"参数空则回退
文章模型"与"两者皆空则保持空"两种真实语义。

---

## [0.6.4] - 2026-10-10

> 主题：**清掉全部存量技术债**（tech-debt 台账 #8 + #11），并纠正台账对 #3 的过期描述。
> 无业务行为变更。

### 门禁

- **ruff 门禁从 3 条规则扩到全量卫生规则**（`E4/E7/E9/F`，仅 `E402` 显式豁免）。
  之前 0.6.3 定的"全开只会产出 58 条噪音"在本轮被证伪——**清完之后门禁是绿的**，
  噪音是一次性存量而非持续增量。
- `E402` 的 9 处经逐一读上下文确认是**有意为之的后置 import**（`config/__init__.py`
  先定义 `__all__` 再 re-export；`service.py` 的跨模块 import 必须排在常量之后，
  `:37` 的"导入即注册"尤其不能动）。豁免理由写进 `pyproject.toml` 注释，不是"以后再说"。

### 清理（68 条 → 0）

- F401 死 import 29 处、F541 无占位符 f-string 8 处、E401 单行多导入 1 处：
  走 `ruff --fix` 后**逐文件读 diff 复核**。
- F841 死变量 6 处、F811 重定义 5 处：人工确认无行为风险后手改。
- E702 分号多语句 10 处：手工拆行，保持浏览器操作顺序逐字不变。
- 顺带清掉 `api/hub.py` 里 3 处裸 SQL 已在本轮之前下沉完毕（0.6.1）。

**过程中抓到并修掉一个真 bug**：`ruff --fix` 删掉了 `service.py` 中
`wait_human_captcha` 的唯一引入点——该符号只在 `solve_captcha` 的浏览器分支
用到，测试全绿也照样上线炸。由 F821 当场抓住，并新增
`test_no_undefined_names_anywhere` 把 F821 零命中纳入回归。

### 文档迁移（tech-debt #11）

- `ARCHITECTURE.md` → `docs/architecture/ARCHITECTURE.md`（git 记录为 rename，历史保留）
- `AUDIT.md` → `docs/audit/AUDIT.md`
- 新增 `test_docs_links.py`（16 用例）：相对链接可达性 + 迁移落点固定 +
  **AGENT.md 适配器清单与代码对照**——该清单此前写着"11 个"却列出 5 个不存在的
  文件（douyin/weibo/sf 等），且漏掉 6 个真实存在的，现按 `ADAPTERS` 实际内容重写。

### 台账纠正

- **#3（update/sync 切流引擎）标记为早已完成**：核实发现 `update_single`
  （`service.py:876`，注释写明"R3 门禁：行为逐字不变"）已抽出、`tasks._dispatch`
  已处理 update/sync、`api/hub.py:263-276` 三套入口已合一无 legacy 分支。
  台账写于 2026-09-22 基线，之后代码重构过而台账没跟上。该项从未是待办。
- **#5（共享驱动崩溃自愈）维持不还**：其触发条件是"首次真实崩溃或部署到无人值守
  环境"，均未发生。

版本 0.6.3 → 0.6.4（无运行时行为变更，`uv.lock` 依赖集合不变）。

---

## [0.6.3] - 2026-10-10

> 主题：**依赖管理收口 + Pydantic v2 API 收尾**。无行为变更。

### 移除

- **删 `web/package-lock.json`**：与 `web/pnpm-lock.yaml` 双 lockfile 并存会让本地
  与 CI 装出不同的依赖树。README / README.en / ci.yml / check_all 四处早已一律走
  pnpm，全仓库零 npm 调用，故删 npm 那份（tech-debt 台账 #10）。
  若 pnpm 装出问题，执行 `pnpm install` 会按 `pnpm-lock.yaml` 精确还原。

### 修复

- **`api/hub.py` 4 处 `.dict()` → `.model_dump()`**：`ArticleIn` / `ArticlePatch` /
  `SchedulePatch` / `WebhookIn` 四处调用。`.dict()` 在 Pydantic v2 已弃用、v3 移除，
  而 `pyproject.toml` 写的是 `pydantic>=2.0.0`（允许装 v3）——照现状升级即炸。

### 门禁

- `pyproject.toml` 新增 `filterwarnings`：把 `PydanticDeprecatedSince20` 升级为
  **error**。这类问题编译期查不出、运行时也不炸，只在升级依赖那天爆；
  现在让它在测试里红掉。护栏有效性已反向验证（注入一处 `.dict()` → 测试转红）。

版本 0.6.2 → 0.6.3（无运行时行为变更，故不动 `uv.lock` 依赖集合）。

---

## [0.6.2] - 2026-10-10

### 新增：数据备份（tech-debt 台账 #9）

`docs/design/m5-data.md:61` 当时把备份留给手工 `cp` 并注明"更稳的做法是
`sqlite3 .backup`，M6 可做成脚本"。现在做成脚本了：

- `scripts/backup_data.py`，在 `backed/` 下 `uv run python ../scripts/backup_data.py`
- **在线一致性快照**：走 `sqlite3.Connection.backup()` 而非 `cp`。SQLite 跑 WAL
  且服务常在写，`cp` 拿到的是半截事务副本——能打开但可能缺最近几条写入。
  **不需要停服务**。
- 备份完对副本跑 `PRAGMA integrity_check` 并只读打开校验，跑不通不算备份成功
  （否则"生成了个文件"会被误当成"备份好了"）。
- 备份清单按 `m5-data.md` §4 的敏感分级处置：`hub.db` 可直接备份；
  `profiles/*.auth.json`（session cookie）与两份 `config.json`（明文 API Key）
  属敏感项，落盘权限收紧。
- **拒绝备份到仓库内**：凭据类文件落进仓库就有被 commit 的风险，直接拒绝并
  给出仓库外路径建议，退出码 2。
- 资产缺失时"能备的都备、缺的如实报告"，只有一件都没有才算失败——
  hub.db 缺失往往是数据事故信号，那时更该把还在的凭据备下来而不是整体失败。
- `--keep N` 按天清理，但永远保留最新一份（清理策略不能把备份清空）。
- `--dry-run` / `--include-profiles` / `--json`。
- 16 个用例覆盖：仓库内拒绝、并发写时的一致性快照、副本可读性与完整性、
  敏感文件内容与权限、输出不泄露任何 secret 值、清理策略的边界。

同时把 tech-debt 台账的 T1/T2/#9 标注为已清偿（#1 无版本基线、#2 无一键门禁、
#9 无备份分别由 0.6.1 与本版完成）。

---

## [0.6.1] - 2026-10-10

> 主题：**修隐患 + 补测试门禁**。无新功能；发现并修掉 6 个"上过线但跑不通"的缺陷，
> 然后把 tech-debt 台账 T2 的"一键回归门禁"落地。

### 修复（全部由新增测试先复现，再改源码）

| # | 症状 | 根因 | 位置 |
|---|---|---|---|
| 1 | **后端完全起不来**，`create_app()` 直接 ImportError | `utils/audit.py` 留着 `from sqlalchemy.orm import Session`，而 SQLAlchemy 早就不在依赖里（死 import） | `backed/utils/audit.py` |
| 2 | **MCP Server 起不来**，`python mcp_server.py` 报无 `fastmcp` 模块 | 用 `from fastmcp import FastMCP`，但 `mcp>=1.0` 的导入路径是 `mcp.server.fastmcp`；且 `run(transport="sse", port=)` 不接 `port` 参数 | `mcp_server.py` |
| 3 | **Webhook 注册必 500**，POST `/webhooks` 永远失败 | `c.lastrowid` 写在 `Connection` 上——该属性在 `Cursor` 上 | `backed/service/publishing/events.py` |
| 4 | **定时发布静默失效**：到期任务每 30 秒炸一次且永远不执行 | `_execute` 把入参 `task_row` 写成 `row`（`_tick` 的循环变量名），F821；失败分支自己也引用 `row`，于是 `next_run` 永不更新。空表达式建出的任务 `next_run=NULL` 同样是僵尸 | `backed/service/publishing/scheduler.py` |
| 5 | **回滚不落痕**，历史里查不到 `change_kind='rollback'` 记录 | 回滚时快照的是 `dict(art)`（回滚前内容），与刚写入的 `pre-rollback` 内容哈希相同，被去重逻辑吞掉 | `backed/service/publishing/versions.py` |
| 6 | **`GET /platforms` 返回空数组**，平台列表像不存在 | 适配器"导入即注册"，但注册只发生在 `service.py` 被导入时；先打到 `/platforms` 的进程拿到的注册表是空的 | `backed/service/publishing/adapters/__init__.py`、`backed/api/hub.py`、`mcp_server.py` |

附带：`tags.merge_tags` 合并 `py,Python` → 留下 `Python,Python` 重复标签（去重只护了映射侧）。

### 测试与门禁（tech-debt T2）

- 新增 `backed/tests/`，**159 个用例**：
  - 模块导入层（每个真实模块裸 import，专抓"编译过但装不上"的依赖漏洞）
  - DB 层：schema / 迁移幂等 / `LockedConnection` 6 线程串行化 / 文章与发布 CRUD / 跨表查询
  - 任务引擎：提交、串行消费、验证码→人工、网络错误→重试 3 次→失败、人工恢复、串行性
  - 标签治理：归一化、别名合并、合并去重、智能降级
  - 版本历史：快照去重、回滚、回滚的回滚、行级 diff、裁剪
  - 事件系统：webhook CRUD、HMAC 签名、通知环形缓冲、任务事件映射
  - 调度器：表达式解析 / CRUD / 投递失败推迟 / tick 拾取
  - REST 接线：文章 CRUD、任务生命周期、webhooks、tags、pending-human、platforms 走真实 FastAPI TestClient
- `conftest.py` 把 DB / 事件日志 / webhook 派发线程全关进 pytest 临时目录，测试不碰开发库
- 新增 `scripts/check_all.ps1` 与 `scripts/check_all.sh`：compileall → ruff → pytest → vite build，
  任一步失败即停，末行给 PASS/FAIL
- `pyproject.toml` 新增 `[tool.ruff]`：门禁只查 `E9/F7/F82`（未定义名、运行时错误、语法错误），
  风格与卫生类存量债暂不纳入，避免 58 条噪音把门禁变成没人看的东西
- CI 升级：backend job 增加 ruff + pytest；新增 frontend job 跑 `pnpm build`
- `.gitignore` 补 `backed/service/data/` 与 `*.db-wal` / `*.db-shm`（WAL 边车文件）
- `api/hub.py` 清掉 3 处裸 SQL，下沉到 `db.py` 新增的 `list_pending_human` /
  `list_publications_joined` / `list_failed_jobs`（遵守 AGENT.md §8）
- `uv.lock` 重新解析：此前 lock 与 `pyproject.toml` 已不同步（lock 里还留着
  aiomysql/pymysql/sqlalchemy/greenlet），`uv sync` 一次就掉进"缺依赖起不来"

---

## [0.6.0] - 2026-10-09

### 新增：多模型管理

- **8 个 AI Provider 预置**：DeepSeek / OpenAI / Anthropic / Kimi / 通义千问 / 智谱 / Ollama 本地
- **任务分级路由**：写作用 premium，润色用 standard，标签/摘要/大纲/SEO 用 economy
- **自动故障转移**：某个 Provider 连续失败 3 次冷却 2 分钟，自动切到下一可用
- **按请求指定模型**：任意 AI 调用传 `model="provider:model_name"` 绕过路由
- **前端模型下拉**：AI 写稿弹窗新增 Provider · 模型选择，留空走自动路由
- **CLI `--model` / `--provider`**：命令行指定模型和 Provider
- REST: `GET /ai/providers` · `GET /ai/status` (enriched)

### 新增：事件 / 通知系统

- **Webhook 注册**：URL + 事件过滤 + HMAC 签名，事件异步 POST 推送
- **事件类型**：task.ok / task.failed / task.waiting_human / publish.ok / publish.failed 等 10 种
- **站内通知**：环形缓冲最近 200 条，`GET /notifications` 拉取
- **自动禁用僵尸 Webhook**：连续失败 5 次暂停
- **任务完成自动触发事件**：TaskManager 内建 emit 节点
- REST: `GET/POST/PUT/DELETE /webhooks/{id}` · `POST /webhooks/{id}/test` · `GET /notifications`

### 新增：内容质检

- **可读性评分**：基于句长、段落长度、标题密度、代码占比；0-100 + 等级
- **SEO 评分**：TDK 完整度、关键词分布、H 标签结构、图片 alt、内链数
- **近似重复检测**：MinHash + Jaccard 查重，阈值 35%
- **一键运行**：`content_qa()` 返回总分 + 分项 + 改进建议
- 前端编辑器「视图与 AI」下拉新增「内容质检」条目 + 圆形总分图
- REST: `POST /articles/{id}/qa` · `POST /qa/analyze`

### 新增：MCP Server（FastMCP）

- **16 个 MCP Tool**：覆盖文章 / AI 写稿 / 发布 / 任务 / 质检 / 标签 / Provider
- **stdio + SSE** 两种传输，对接 Claude Code / Cursor / Windsurf
- 文件：`mcp_server.py`（项目根目录，独立运行）

### 前端改善

- AI 写稿弹窗增加模型选择器（按 Provider 分组，标注经济/均衡/旗舰）
- 文章编辑器新增「内容质检」菜单项 + 质检报告弹窗（圆形总分 + 可读性/SEO/重复度 + 改时建议）
- Element Plus 改为全局注册（uniplugin 轻量化），包体略大但构建更稳定

---

## [0.5.0] - 2026-10-09

### 新增：AI 工作流完整工具链

- **AI 翻译**：`ai_translate` — Markdown 文章翻译（保留代码块不动）到 en/ja/ko/fr/de
- **配图提示生成**：`ai_image_prompts` — 从正文提炼 3 个英文文生图 prompt（兼容 SDXL/Flux/DALL·E）
- **大纲提炼**：`ai_outline` — 从 Markdown 内容提取标题层级树
- **SEO 元数据**：`ai_seo` — 自动生成 seo_title / seo_description / slug / keywords
- **文章克隆**：`clone` — 复制已有文章为新草稿
- **写作模板**：4 个预置模板（教程/观点/深度剖析/资讯简评）

### 新增：定时发布调度器

- 守护线程每 30 秒轮询 `scheduled_tasks` 表，投递到 TaskManager
- 支持 once / daily / weekly / cron 四种调度类型
- CLI: `schedule-add` / `schedule-list` / `schedule-del` / `schedule-trigger`

### 新增：文章版本管理

- 改文章前自动快照到 `version_history` 表（sha256 去重）
- 回滚 / 行级 diff / AI 改写润色前自动存版本
- CLI: `versions` / `rollback`

### 新增：标签治理

- `tag_stats` 表 + `alias_map` 同义合并
- 合并 / 重命名 / 别名三步治理 + 词云展示
- CLI: `tags` / `tag-sync` / `tag-rename`

### 前端

- 编辑器「视图与 AI」下拉扩展（翻译/配图/大纲/SEO/克隆/版本历史）
- 新 `VersionDrawer` + `AIToolDialog` 组件
- ManagePanel 新增「定时任务」+「标签治理」标签页

### 文档

- ARCHITECTURE.md 新增 D6-D9 + API 路径总览
- CHANGELOG.md 本条目

---

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.4.1] - 2026-10-09

### 文档打磨

- **README.md 全面重写**：修复所有过时引用（`deplay` → `deploy`、`/api/v1` CRUD 路由、MCP 入口、`markdown-it` → `md-editor-v3`、`server/static` 构建路径）；删除 E2E 测试引用（测试套件已拆除）；更新仓库地址为多镜像表格
- **README.en.md 全面重写**：与中文版内容同步，专注国际开发者视角
- **ARCHITECTURE.md 全面重写**：修复目录结构（不再列举 `xiaohongshu` / `wechat` 等不存在的适配器）、修复 API 路径（删除 `/api/v1/*` 引用）、新增 ADR 设计决策一节（统一任务引擎、Patchright、单实例浏览器、双库合一、异步轮询）
- **CHANGELOG.md 同步更新**

### 浏览器启动修复

- `browser.py` `_find_chrome()` 新增 `/opt/playwright` 搜索根和 `chromium-*/chrome-linux64/chrome` 模式，解决沙箱环境内 Chromium 二进制找不到的问题

### 前端组件打磨

- 字体系统重构：`--font-serif` / `--font-disp` 语义化命名，真正区分展示体与代码块；中英字体栈优化（零外部请求）
- 删除 `App.vue` 中不再使用的 `.fab` 浮动按钮样式残留
- `vite.config.js` 修正 `manualChunks.md` 从 `markdown-it` → `md-editor-v3`；开发代理目标改为 `:8000`（当前默认端口）

## [0.4.0-fix] - 2026-10-09

### 重大清理

- **删除 scaffold 遗留**
  - 移除 `api/v1/` 孤立的 scaffold router
  - 移除 `models/` Pydantic 模型层（无实际依赖）
  - 移除 `schemas/` 校验层（无实际使用）
  - 移除 `repository/` 仓储抽象层（直连 db.py 即可）
  - 移除 `*_service.py` 重复服务层（article_service、account_service、job_service、publication_service、task_service）
  - 合计减少约 800 行无用代码

- **删除幽灵基础设施**
  - 移除 `core/cache.py`（从未被调用，仅定义类）
  - 移除 `core/queue.py`（从未被调用，基于 asyncio.Queue 的空实现）

- **移除 scaffold 遗产**
  - 移除 `events.py`（空事件钩子）
  - 移除 `router.py`（空路由器基础结构）
  - 移除 `config/settings.py`（已统一到 config_unified.py）

- **统一持久化层**
  - 保留 `db.py` 原生 SQLite SQL 作为唯一持久化层
  - 移除 SQLAlchemy ORM 依赖（不再需要 ORM 抽象）
  - 所有表结构定义集中在 `db.py` + `MIGRATIONS` 列表

- **测试清理**
  - 删除 `backed/tests/` 中引用 scaffold 的旧测试文件
  - 待后续补充针对性单元测试与集成测试

### 修复

- **humanize.py**
  - 修复 `human_type` 公式 bug：原公式导致打字速度被 WPS_EM 分母稀释
  - 修复后有效 WPM 从 48-84 提升到配置的 240-420 范围
  - 移除冗余的类封装，改为纯函数

- **observability.py**
  - 删除死代码 `_observe_with_max`（废弃的观察模式）
  - 删除 `M_*` 常量组（从未引用）
  - 删除 monkey-patch 逻辑（破坏函数签名，干扰调试）

- **config_unified.py**
  - `ProductionConfig.secret_key` 改为从 `HARBOR_SECRET_KEY` 环境变量读取
  - `ProductionConfig.database_url` 改为从 `HARBOR_DATABASE_URL` 环境变量读取
  - 缺失时抛出 `ValueError` 而非静默回退到默认值

- **api/hub.py**
  - 移除模块加载时的全局 Hub 副作用
  - 改为惰性单例工厂模式 `get_hub()`
  - 移除导入时的 `from service.publishing.hub import Hub` 全局实例化

- **app/factory.py**
  - `lifespan` 简化为 DB 初始化 + logging 配置
  - 移除 cache/queue 初始化（随幽灵基础设施删除）
  - 移除 scaffold router 注册

- **安全**
  - `api_token` 未配置时自动生成 UUID4
  - 启动时打印警告日志：`Generated api_token=*** — set HARBOR_API_TOKEN in production`
  - 生产模式必须显式指定 token

### 前端修复

- 删除 `App.vue` 中未使用的 `onRefresh` / `refreshing` 状态管理
- 删除 `ArticleList.vue` 中与 ArticleStore 重复的过滤逻辑（单一数据源原则）
- `ArticleEditor.vue` 补全 `is-active-cmd` 样式类（编辑态视觉反馈）

### 重命名

- `deplay/` → `deploy/` （`git mv`，修正拼写错误）

### 依赖更新

- `pyproject.toml`
  - 移除 `sqlalchemy`（不再需要 ORM）
  - 移除 `aiosqlite`（不再需要异步 SQLite）
  - 移除 `aiomysql`（不再需要 MySQL 异步驱动）
  - 移除 `greenlet`（SQLAlchemy 附带依赖）
  - 版本号对齐 `0.4.0-fix`

---

_Template for future entries:_

## [Unreleased]

### Added
### Changed
### Deprecated
### Removed
### Fixed
### Security
