# 内容港 Harbour · 0.8.0 能力审计与补全报告

> 日期：2026-10-29 ｜ 基线：`badhope/harbour` @ `97b502d`（0.7.0）
> 结论：**定位成立且差异化真实存在；补齐 9 处"文档有、代码无"的缺口，
> 替换 6 处自研半成品，首屏负载降 59%、查重提速 14×、安全扫描提速 42×。**
> 全部改动有回归测试（后端 415 → 451）与前后实测数据。

---

## 一、项目定位：它到底是什么

**一句话**：面向中文技术内容创作者的**自托管发布中台**——
把"写→合规审查→多平台分发→原地更新→人机协作收尾"这条链路做成一个可自己
部署、数据不出本机的服务，AI 与人工都能驱动同一条链路。

**它不是**：不是 CMS（不托管站点）、不是社媒排期工具（不面向海外平台）、
不是浏览器插件（不寄生在用户日常浏览器里）。

**真正的能力内核（五项，逐项验证过是否落地）**

| # | 内核 | 为什么别人难复制 | 0.7.0 是否落地 | 0.8.0 处置 |
|---|---|---|---|---|
| 1 | **反检测浏览器 + 登录态永续**（Patchright + 独立 profile + auth 快照四层保鲜） | 中文平台大多无开放 API，只能做浏览器自动化；而反检测 + 会话保鲜是长期踩坑产物 | 已实现（`browser.py` / `audit` L1-L4） | 保持 |
| 2 | **原地更新而非重复发布**（`publications.edit_url` + `content_hash` 比对） | 竞品只做"再发一遍"，会产生重复内容；本项目的 `post_id/edit_url` 是逐平台逆向出的真值 | 已实现 | 补：不支持 update 的平台不再假装支持（能力矩阵） |
| 3 | **AIGC 合规四道闸门**（安全扫描 → 标识 → 双模型二审 → 人审 in-the-loop） | 国内外同类项目**都没有合规概念**，这是政策驱动的结构性差异 | 已实现 | 补：扫描算法换 AC 自动机，修掉中文语境漏检 |
| 4 | **统一任务引擎 + 人机协作语义**（`waiting_human` + `pending_user` 双语义，防重复发布） | 竞品的"重试"是整批重发；本项目恢复只标记完成，绝不重跑 | 已实现 | 补：指数退避真正生效 |
| 5 | **单机零依赖 + 三入口同源**（SQLite / REST / MCP / CLI 共用 Hub） | 竞品要么是浏览器扩展（无服务端），要么要 Redis+Postgres+Worker | 已实现 | 补：MCP 27 tool、能力矩阵、FTS5 |

**"本地化"这类描述不是优势**——它只是部署形态。真正的优势是上面 2、3、4 三条：
**只有这个项目同时具备"中文平台逆向覆盖 + 合规门禁 + 不重复发布的恢复语义"。**

---

## 二、竞品对标（≥5 个，源码层）

分析方式：GitHub API 目录树 + 关键源码文件逐行核对（README 结论一律回源码验证）。
标注"未验证"的条目代表只读到代码未实机运行。

| 项目 | 形态 | 平台数 | 更新已发布 | 重试/恢复 | 合规 | 关键结论 |
|---|---|---|---|---|---|---|
| **wechatsync/Wechatsync** 6.5k★ | Chrome MV3 扩展 | 公开 19 个适配器 | 接口留了 `update?`，**无一实现** | 发布无重试；SW 被回收任务即丢 | 无 | 内容预处理最强（40 项可配置管线）；但"发布"实为**只存草稿**（19 处 `draftOnly ?? true`），无幂等、无 CI |
| **artipub/artipub** 89★ | Node 库 + CLI（**无 HTTP 服务**） | 3 个 | 有 pid 映射，但**唯一 ID 不写回源文件** → 每次 add 可能重复发 | 无重试、无任务模型 | 无 | AST（mdast）插件模型最干净；"API-first"名不副实 |
| **gitroomhq/postiz-app** 40k★ | NestJS + Next.js + Redis + Temporal | 30+ | 支持 | Temporal 工作流级重试 | 无 | 集成抽象最完整（OAuth/token 刷新/analytics）；重量级，需 Redis+PG，中文平台为零 |
| **inovector/mixpost** 3.9k★ | Laravel + Vue | 3（Twitter/FB/Mastodon） | 支持 | 队列 + `release($retryAfter)` **按平台限流窗口退避** | 无 | 限流建模最专业（见下）；平台覆盖极窄 |
| **halo-dev/halo** 40k★ | Java Spring WebFlux | —（CMS，非发布工具） | — | — | — | 插件扩展点/内容模型/REST 契约设计的参考标杆，非同类竞品 |
| **n8n-io/n8n** 209k★ | TS 工作流引擎 | 400+ 集成 | — | 队列模式 + 错误工作流 | — | 节点凭据抽象与执行引擎参考；作为"通用编排"而非竞品 |

### 2.1 从竞品挖到的、本项目缺失的（已补）

1. **图片转存管线**（Wechatsync `code-adapter.ts:228-319`、Artipub `picUpload.ts`）：
   两家都有"逐平台转存 + 同图去重 + 失败不阻断"，Harbour 之前**完全没有**，
   `md_to_html()` 直接透传外链 → 平台侧 403/裂图。→ **已实现 `media.py`**。
2. **能力矩阵真正接线**：Wechatsync 声明了 `capabilities` 但全仓 grep 无消费点
   （纯装饰）。Harbour 之前也只有文档表格没代码。→ **已实现代码级矩阵并接进
   API/发布/更新/前端置灰**，避免重蹈"声明不消费"。
3. **按平台限流退避**（Mixpost `HasSocialProviderJobRateLimit`）：它把
   `retryAfter` 存进缓存、`release()` 到点再跑。Harbour 之前是"立即重排"。
   → **已实现指数退避**；平台级 retry-after 解析留作后续（需各适配器上报，未做）。
4. **结构化内容变换**（Artipub 用 mdast 而非字符串）：Harbour 仍是"Markdown 字符串
   → HTML → 各适配器正则"。本轮**未改**（改动面大、收益需真实平台验证），
   已在技术债登记。

### 2.2 本项目的优势（逐条验证过）

| 优势 | 证据 |
|---|---|
| **唯一有"发布合规"概念** | `gate.py` 四道闸门 + `test_gate.py` 63 条；竞品 0 条 |
| **恢复语义不重复发布** | `tasks.py` `pending_user` 只标记完成（竞品重试=整批重发） |
| **中文平台覆盖 + 原地更新真值** | 10 适配器，5 个支持 update 且 edit_url 从列表页抓取而非拼接 |
| **单机零外部依赖** | SQLite + 单进程；Postiz 需 Redis+PG+Temporal |
| **三入口同源** | REST / MCP（27 tool）/ CLI 共用同一 Hub 与 TaskManager |

### 2.3 仍存的劣势（诚实登记）

| 劣势 | 与谁比 | 现状 | 计划 |
|---|---|---|---|
| 平台数少（10 vs 30+） | Postiz | 中文平台覆盖深但宽度窄 | 适配器模板已就绪，按需扩 |
| 无多租户/团队协作 | Postiz / Mixpost | 单用户设计 | 明确不做（定位是个人自托管） |
| 内容变换是字符串级 | Artipub | 未改 | 记入技术债，等真实平台反馈 |
| 无平台级 retry-after 上报 | Mixpost | 只有通用指数退避 | 需适配器逐平台补 |
| analytics 采集缺失 | Postix/Mixpost | 只有 `stats` 字段无采集 | 平台 API 差异大，按需 |

---

## 三、逐模块问题清单（发现 → 处置）

### P0 阻断（用户直接感知）

| # | 模块 | 问题 | 证据 | 处置 |
|---|---|---|---|---|
| 1 | 启动脚本 | `uvicorn start:app` 必然 AttributeError（`app` 在 `main()` 内） | `start.sh:5` / `start.bat:4` / `start.ps1:3` vs `start.py` | 改 `app.factory:create_app --factory`，三平台同步 |
| 2 | Tauri | 健康探活打 `/api/hub/health`（实际 `/api/v1/hub/health`） | `lib.rs:27` | 修正 |
| 3 | Tauri | `frontendDist: ../dist` ≠ vite `outDir: ../server/static` | 两处配置 | 修正为 `../../server/static` |
| 4 | 质检 API | `POST /qa/analyze` 只收 query，前端发 JSON → 恒 422 | `api.js:qaAnalyze` vs `articles.py` | body/query 双支持 |
| 5 | 标签 API | 同上（`/tags/suggest`） | 同上 | 同上 |
| 6 | 任务引擎 | "指数退避"是假的：置 pending 后立刻 wake，1 秒打光 3 次 | `tasks.py` 重试分支 | 真退避 + 时间过滤 |
| 7 | 更新链路 | 不支持 update 的平台被塞进循环，每次同步返回假失败 | `service.update` | 能力矩阵预筛 + 显式 `skipped` |

### P1 能力缺失（"文档有、代码无"）

| # | 模块 | 问题 | 处置 |
|---|---|---|---|
| 8 | 媒体 | 无图片转存/去重/托管，外链图在平台侧裂图 | 新增 `media.py` + 5 个端点 + 发布链路改写 |
| 9 | 检索 | `LIKE '%kw%'` 全表扫，无相关度 | FTS5 trigram + 触发器 + 回填 |
| 10 | 列表 | 列表接口返回完整正文（100 篇 × 20KB） | slim 投影（含字数） |
| 11 | 能力矩阵 | 只有 `ARCHITECTURE.md` 表格，代码零消费 | 代码级 `capabilities` + API + UI + 发布/更新分流 |
| 12 | 导入 | 手写 `split("---")` 解析 front-matter；只能导本地文件 | `python-frontmatter` + `trafilatura` 网页导入 |
| 13 | 可观测 | `/metrics` 只有私有 JSON，接不了 Prometheus | `format=prometheus` 标准 exposition |
| 14 | 调度 | 手撸 cron 解析（不支持 L/W/#） | `croniter`，内置保留兜底 |

### P2 性能与工程

| # | 问题 | 数据 | 处置 |
|---|---|---|---|
| 15 | 首屏强拉 md-editor(900KB)+echarts(484KB) | preload 2.56MB | 异步视图 + 去掉固定 chunk → 1.04MB（gzip 349KB） |
| 16 | 桌面版无 gzip | 多下 ~1MB | `GZipMiddleware` |
| 17 | 查重 32×md5/篇 | 200 篇 15.7s | blake2b + 16 置换 + 落表缓存 → 1.1s / 热 0.22s |
| 18 | 高危词逐词扫 + 中文 `\b` 漏检 | 300 词稍慢且漏检 | AC 自动机（41.7×）+ ASCII 边界断言 |
| 19 | 数据目录分裂 | DB 在 APPDATA、日志在安装目录 | 全部跟随 `data_root()` |
| 20 | 门禁编译 .venv | 10.85s | 排除 → 0.3s |

### 发现但**刻意不改**（附理由）

| 项 | 理由 |
|---|---|
| `service.py` 921 行上帝类 | 拆分触及发布主链路，风险 > 收益；已按能力矩阵/媒体/导入把可外移的先外移 |
| `core/permissions.py`、`utils/auth.py`（JWT）未使用 | 单用户自托管无登录概念，保留作未来多用户扩展点；删除会破坏 `core` 公共 API |
| `providers/`（biliup 等外部工具）未接线 | `publish_with_provider` 有定义但无人调用；需真实工具安装后验证，避免假接通 |
| 平台级 retry-after | 需各适配器上报响应头，逐个平台逆向，留作后续 |
| mdast 级内容变换 | 见 §2.3 |

---

## 四、替换清单（自研 → 成熟库，功能只增不减）

| 模块 | 替换前 | 替换后 | 验证 |
|---|---|---|---|
| 多模式字符串匹配 | 逐词 `in` | `pyahocorasick` 2.3.1 | 300 词 41.7×，命中集合一致 |
| 模糊字符串相似 | 无 | `rapidfuzz` 3.14.6 | 新增 `/tags/clusters`，中文不误合并 |
| HTTP 重试 | 无 | `tenacity` 9.2.1 | 429/5xx 重试，4xx 立即失败 |
| cron 解析 | 手写 5 段 | `croniter` 6.2.4 | `L/W/#` 支持，内置兜底保留 |
| front-matter | `split("---",2)` | `python-frontmatter` 1.3.0 | YAML 列表/冒号/正文分隔线 |
| 网页正文提取 | 无 | `trafilatura` 2.3.1 | URL 导入 + SSRF 防护 |
| 指标导出 | 私有 JSON | `prometheus-client` 0.26 | 标准 exposition |
| 全文检索 | `LIKE` | SQLite FTS5（内置） | 相关度排序 + 回填 |

依赖增量：约 +6 个纯 Python 包（含 lxml），未引入 Node/Java 侧新依赖。

---

## 五、实测数据（同机、同数据集）

| 指标 | 0.7.0 | 0.8.0 | 变化 |
|---|---|---|---|
| 后端测试 | 415 passed | **451 passed** | +36 |
| 首屏 modulepreload JS | 2560 KB | **1063 KB** | −58.5% |
| 首屏 gzip | 860 KB | **349 KB** | −59.4% |
| 内容查重（200 篇） | 15.72 s | **1.07 s**（热缓存 0.22 s） | 14.7× |
| 安全扫描（300 词 × 20 次） | 0.130 s | **0.003 s** | 41.7× |
| 门禁 compileall | 10.85 s | **0.3 s** | 36× |
| 文章列表响应 | 含全文 | 仅元信息 + 字数 | 体积随正文线性下降 |

复现命令：
```bash
cd backed && uv run pytest -q          # 451 passed
cd backed && uv run ruff check .       # All checks passed
bash scripts/check_all.sh              # 六道门禁全绿
```

---

## 六、剩余风险与下一步

1. **真机验证缺口**：所有平台适配器的发布行为未在真实账号上回归（无凭据）。
   本轮改动只保证"契约与能力声明一致"，不保证平台页面结构未变。
2. **媒体管线需配 `PUBLIC_BASE_URL`**：未配置时只入库不改写（平台抓不到 127.0.0.1）。
3. **图片上传未做 virus/内容审查**：只校验扩展名/MIME/大小/公网来源。
4. **Postiz 的 Temporal 级编排**未借鉴：当前单机 SQLite 队列足够，多机部署时再评估。
5. **建议下一轮**：平台级 retry-after、mdast 内容变换、适配器真机回归矩阵。
