# 港 Harbour —— 桌面 + Web 双形态 AI 内容发布中枢

一个后台管所有内容平台：知乎 / 掘金 / CSDN / B站 / 博客园 / 头条 / 开源中国 / SegmentFault / 51CTO / MetaWeblog 自建博客 / 微信公众号 / GitHub Pages / Dev.to / Telegraph / 小红书。
提供 AI 写稿、多平台发布、定时任务和人工审核等功能；各平台能力不同，实际发布前请先验证登录态并试发草稿。

**当前限制**：浏览器抓取与截图暂因安全问题禁用；平台适配器的静态探针不代表所有平台都已真实发布验证。

## 形态对照

| | Web 形态 | 桌面形态 |
|---|---|---|
| 前端 | Vue 3 + Vite 浏览器 | Vue 3 + Tauri 系统 WebView |
| 后端 | FastAPI + uvicorn 手动启 | 由 Tauri 内嵌启动，对外不可见 |
| 访问 | http://localhost:8000 | Tauri 应用与后端 sidecar（打包尚待验证） |

桌面形态基于 Tauri 2.0，复用 100% 前端代码与后端代码——不写两套业务逻辑。

## 快速开始

### 桌面端（推荐）

```bash
cd web
pnpm install
pnpm run tauri dev
```
桌面启动时，Tauri 自动以 sidecar 模式启动 `backed/` Python 后端，前端自动连接
本地后端，不需要手动开命令行窗口。

### 桌面端构建

桌面端需要为目标平台构建并准备后端 sidecar。当前仓库的打包链路尚未完成跨平台验证，不能保证单文件安装或自动更新可用。Windows 构建可先检查 `scripts/build_sidecar.ps1` 和 MSVC 工具链，再运行 `pnpm --dir web tauri build`。

### Web 开发

```bash
# 终端 1：前端
cd web
pnpm install
pnpm dev          # http://localhost:5173

# 终端 2：后端
cd backed
uv sync            # 首次安装依赖
uv run uvicorn app.factory:create_app --factory --host 127.0.0.1 --port 8000 --reload
```
浏览器打开 http://localhost:5173（vite 代理自动转发 API 到 :8000）。

## 核心能力

| 能力 | 说明 |
|---|---|
| 一键多投 | 选平台 → 发布 → 全平台同步，间隔 8-20s 防风控 |
| 回答问题 | 知乎：发布轨粘贴问题链接即写回答（留空则发专栏文章），回读+发布双验证 |
| 原地更新 | 改一处，已发布文章全平台同步，不产生重复内容 |
| 定时发布 | 一次性 / 每天 / 每周 / cron，到期自动投递 |
| AI 写稿 | 7 个内置 Provider 路由，支持配置扩展与故障转移 |
| AI 改写/润色 | 改完自动标记待同步，点一下全平台更新 |
| AIGC 合规 | 四道闸门：安全扫描 → AIGC 标识 → 双模型审查 → 人审 in-the-loop |
| 人工兜底 | 验证码/风控/登录失效 → 任务自动暂停，人工处理完点恢复 |
| 版本管理 | 每次改前自动快照，误改一键回滚 |
| 标签治理 | 同义合并 / 别名映射 / 词云 / 智能推荐 |
| 内容质检 | 可读性 + SEO + 近似重复检测，一键出报告 |
| MCP Server | 32 个 Tool（另可桥接外部 MCP Server）；stdio 默认可用，SSE 默认只监听回环，远程 SSE 必须配置 Bearer Token |
| 媒体资产 | 图片内容寻址去重、Markdown 改写、本地托管（配 `PUBLIC_BASE_URL` 后平台可直接抓取） |
| 全文检索 | SQLite FTS5（trigram，中文免分词）+ 相关度排序 |
| 网页导入 | 粘贴 URL 直接抓正文转 Markdown（trafilatura），带 SSRF 防护 |
| 能力矩阵 | 每个平台声明 publish/draft/update/list/tags/cover/question，前端按此置灰 |
| Webhook | 任务完成/失败/等人工 10 种事件，HMAC 签名，自动禁僵尸 |
| SSE 实时流 | 前端 EventSource 长连接，任务进度实时推送 |

## 配置

编辑 `backed/config.json`（首次启动自动生成模板）或用环境变量：

| 变量 | 说明 | 默认 |
|---|---|---|
| `HARBOUR_API_TOKEN` | REST 鉴权 Token（优先于 config.json） | 开发回环可自动生成；生产/Docker 必须显式设置 |
| `SECRET_KEY` / `HARBOUR_SECRET_KEY` | 应用签名/JWT 配置（生产必须显式设置） | 不使用公开默认值 |
| `TRUSTED_NETS` | 免 API Token 的客户端网段（谨慎配置） | 仅回环 |
| `TRUSTED_PROXIES` | 允许采信 X-Forwarded-For 的反向代理网段 | 空 |
| `CORS_ORIGINS` | 跨域白名单 | 同源 |
| `DEMO=1` | 演示模式（不开浏览器不调外部 API） | 0 |
| `AI_API_KEY` | LLM API 密钥 | 空 |
| `AI_BASE_URL` | LLM 端点 | deepseek |
| `FERNET_KEY` | Webhook secret 加密主密钥 | 自动生成 |
| `PUBLIC_BASE_URL` | 媒体/图片对外基地址（如 `https://harbour.example.com`） | 空（只入库不改写） |

## 登录平台

1. 打开 Web UI → 点平台名 → 点「登录」
2. 弹出有头浏览器窗口（Linux 服务器自动起 Xvfb）
3. 扫码/输密码/过验证码（系统先试半自动，过不了转人工）
4. 登录态存本地 profile，之后长期复用，不用每次重登

纯协议平台（博客园 MetaWeblog、51CTO、自建 MetaWeblog、Dev.to、GitHub Pages、微信公众号、Telegraph）按 `PLATFORM_CONNECTION.md` 配置凭据；掘金主适配器仍走浏览器登录，不要把独立 API 工具当作已接入发布通道。

## 发布流程

```
AI 写稿 / 手工编辑 → 保存 → 选平台 → 发布
                                    │
                    ┌───────────────┤
                    │               │
              draft_only=False  draft_only=True
                    │               │
              过 AIGC 门禁      直接进草稿
                    │               │
              AI 源→拒(须 draft)  等人确认
              人写源→放行            │
                    │               ▼
              平台 API / 浏览器   管理页点「确认发布」
                    │
              成功 → publications 落库
              失败 → Triage 分流（重试/人工/永久失败）
```
## 测试

```bash
# 一键门禁（六道全绿才能提交）
pwsh -File scripts/check_all.ps1      # Windows
bash scripts/check_all.sh            # Linux/macOS

# 单跑
cd backed
uv run pytest -q                     # 当前源码约 506 个展开用例，以命令输出为准
cd ..
pnpm --dir web test                  # 当前源码约 62 个展开用例，以命令输出为准（含 a11y 静态门禁）
pnpm --dir web lint                  # 前端 eslint
uv run ruff check .                  # 静态
```
## 安全部署边界

- 生产或 Docker 部署必须显式设置 `HARBOUR_API_TOKEN`、`SECRET_KEY`、`ALLOWED_HOSTS` 和 CORS 白名单；不要依赖自动生成的临时 Token。
- `TRUSTED_NETS` 是免 Token 的客户端网段，不是代理网段；只有 `TRUSTED_PROXIES` 决定是否采信 `X-Forwarded-For`。
- MCP SSE 默认只绑定 `127.0.0.1`；需要远程访问时必须设置 `HARBOUR_MCP_TOKEN`，并通过 TLS 反向代理暴露。
- 浏览器操控和网页导入会校验目标地址，但当前尚未消除 DNS 重绑定风险；不要在不受信任的网络环境中开放这些功能。
- 当前没有自动更新、签名发布清单或可复现的跨平台 Tauri release；Tauri/sidecar 仍需按目标平台构建并验收。

## 备份

```bash
cd backed
uv run python ../scripts/backup_data.py              # 到 ~/harbour-backups
uv run python ../scripts/backup_data.py --dry-run    # 只看计划
```
走 `sqlite3.Connection.backup()` 做在线一致性快照，不用停服务。
拒绝备份到仓库内（防凭据被 commit）。

## 架构

详见 [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md)。

## 文档

- [架构详解](docs/architecture/ARCHITECTURE.md)
- [0.8.0 能力审计与补全报告](docs/audit/CAPABILITY_AUDIT_2026-10-29.md)
- [审计报告](docs/audit/AUDIT.md)
- [技术债台账](docs/design/tech-debt.md)
- [平台接入指南](docs/PLATFORM_PUBLISH_GUIDE.md)
- [MCP 接入](PLATFORM_CONNECTION.md)

## License

MIT — 见 [LICENSE](LICENSE)。
