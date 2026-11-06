# 安全审计 — 2026-11-07

> 范围：0.14.0 的 HTTP API、鉴权、出站请求、文件落盘、打包产物。
> 方法：43 项黑盒攻击（真实 HTTP 请求，不是读代码推断）+ 源码复核。
> 结论：**发现 8 项真实缺陷，全部修复并各自配有会红的回归测试。**

## 一、缺陷清单

| # | 级别 | 缺陷 | 影响 | 修复 | 验证 |
|---|---|---|---|---|---|
| 1 | **Critical** | `webhook` 注册完全无目标校验（`api/routers/webhooks.py:32`） | 可注册 `http://169.254.169.254/latest/meta-data/`、`http://127.0.0.1:6379/`。webhook 是服务端主动发起的 POST，等于把标准 SSRF 原语交给任何能调 API 的人 | 注册/更新/投递三道校验。注册时对"域名还没上线"（DNS 失败）放行——自建站常先配 webhook 再部署；投递时再严格解析，防 DNS rebinding | 实测 `POST /webhooks` 对 10 类内网目标全部 422；投递阶段 `_webhook_target_allowed` 拒绝且不发出请求 |
| 2 | **Critical** | 500 响应泄漏完整 traceback | 响应体含本地绝对路径、源码行号、完整调用栈、上游响应片段。攻击者据此摸清依赖版本与目录结构 | 根因是 `FastAPI(debug=settings.debug)` 让 Starlette 的 ServerErrorMiddleware 绕过统一 JSON 处理器。改为 debug 恒关；异常处理器只回固定文案，细节进日志 | 实测 `/articles/999999/qa` 与 `/ai/write` 的 500 响应体只剩 `{"code":500,"message":"服务器内部错误"}` |
| 3 | **High** | `Server` 响应头删不掉 | 中间件里 `del response.headers["Server"]` 无效——uvicorn 在中间件之后才写协议层，实测仍返回 `server: uvicorn` | 三个启动入口（`start.py` / `desktop_sidecar.py` / `cli serve`）统一 `server_header=False` | `curl -I /health` 已无 Server 头 |
| 4 | **High** | 登录态快照 `*.auth.json` 无权限收紧 | 里面是平台账号**明文 cookie**，能直接冒用登录态；默认权限继承父目录 | 写盘后调 `crypto._restrict_key_file`（POSIX 600 / Windows icacls 清继承只授权当前用户） | `icacls` 实测只剩 `DESKTOP-xxx\Admin:(F)` |
| 5 | **High** | `hub.db` / `-wal` / `-shm` 权限为 `-rw-r--r--` | 含全部文章正文与发布记录，同机其他用户可读 | 建库时对三个文件做同样收紧 | 同上 |
| 6 | **High** | AI provider 的 `base_url` 可指向任意地址 | 它决定 `Authorization: Bearer <key>` 发往哪个主机。允许任意值 = 拿到配置写权限的人可把密钥静默改道到自己的服务器，同时构成 SSRF | 补校验：仅 http/https、禁带凭据 URL、字面量 IP 判公网性、域名解析到内网则拒。**放行回环**（Ollama / LM Studio 必须可用） | 5 类危险目标全部拒绝，3 类合法目标（含 `127.0.0.1:11434`）全部通过 |
| 7 | **Medium** | token 比较用 `!=`（非常量时间） | 按首个不同字节短路，可做时序侧信道逐字节还原 token | 改 `hmac.compare_digest` | 源码级断言锁住 |
| 8 | **Medium** | 首次启动迁移旧配置时整份复制 | 把开发机的 `api_token` 带进新数据目录——**所有安装共用同一 token**，等于没有鉴权 | 迁移后剔除凭据字段；不含凭据时保持逐字节不变（既有契约） | 新装 data 目录的 config.json 已无 api_token；跑完全量测试后 `backed/config.json` md5 不变 |

## 二、攻击测试覆盖（43 项，全部防御生效）

| 类别 | 项数 | 结果 |
|---|---|---|
| SSRF — `articles/import` | 10 | 全部拦截（本机端口 / cloud metadata / 内网 A 段 / IPv6 回环 / `0.0.0.0` / `file:` / `gopher:` / `dict:`） |
| SSRF — `media/ingest` | 6 | 全部拦截 |
| SSRF — `browser/fetch` | 5 | 全部拦截 |
| SSRF — `webhook` 注册与投递 | 2 + 1 | 全部拦截（修复前 2 项裸奔） |
| 路径穿越 — `media/{name}` | 6 | 全部 404（相对 / URL 编码 / Windows 绝对 / UNC / NTFS ADS / 短文件名） |
| 路径穿越 — `import_md` | 3 | 全部拒绝 |
| SQL 注入 — FTS5 搜索 | 2 | 不崩溃、表未受损 |
| 上传校验 | 3 | HTML 伪装 / PE 伪装 / 空文件全部 413/400 |
| 越权读取 | 1 | 404 |
| 响应头与 CORS | 3 | 安全头齐全、无 Server 头、恶意 Origin 不放行 |

## 三、已验证的既有防护（本次复核未发现问题的部分）

- **URL 安全模块**（`url_safety.py`）：公网 IP 白名单含 IPv4-mapped、CGNAT、回环、链路本地；`fetch_pinned` 做连接钉扎（防 DNS rebinding）与重定向重校验。
- **媒体读取**：严格 `<64位hex>.<白名单扩展名>`，无法用 `..` 或大小写绕过。
- **MCP 子进程**：`subprocess.run([bin, *args])` 列表形式、环境变量白名单（仅 PATH + 一个 token）、有超时。
- **前端 XSS**：全仓无 `v-html`；实测 `<img onerror>` / `<script>` / `javascript:` 链接在 md-editor 预览里全部被转义，4 个 payload 均未执行。
- **Fernet 密钥**：环境变量优先，文件权限收紧，解密失败平滑降级。
- **SQLite 并发**：`LockedConnection` 用 RLock 串行化同连接跨线程访问；WAL + `busy_timeout=30000` 处理跨连接锁。

## 四、已知但未修（如实说明）

| 项 | 说明 | 为什么不修 |
|---|---|---|
| `/media/{name}` 公开可读 | 内容寻址 + `Cache-Control: public`，知道 hash 就能读图 | **设计使然**：平台侧要能抓取图片。私有部署应放在反向代理的访问控制之后，或改用 `PUBLIC_BASE_URL` 指向受控域名 |
| 静态 token 无 scope / 过期 / 轮换 | 单一 token 即全权限 | 需要引入多用户与权限模型，属产品决策（ROADMAP 0.16+），不是本轮能安全拍板的 |
| 单机 SQLite 无多租户隔离 | 所有数据在同一库 | 与"自托管单用户"定位一致；多用户见 ROADMAP |
| `MarkdownIt(html=True)` 保留原始 HTML | 发布到平台时用户的 HTML 会原样保留 | 这是发布器的必要能力（用户可能写 `<video>` 等）；预览侧已转义，风险在后端渲染路径，见下方建议 |

## 五、给你的建议（部署侧）

1. **公网部署必须设 `HARBOR_API_TOKEN`**：不设时仅回环免鉴权；`TRUSTED_NETS` 保持空，`TRUSTED_PROXIES` 只填你自己的反代网段。
2. **反向代理加一层访问控制**：`/media/` 若含私有图片，用 nginx `auth_basic` 或 IP 白名单包住。
3. **数据库与快照放在加密盘**：权限已收紧，但磁盘被物理取走时仍可读。
4. **webhook 推内网需显式开关**：`allow_private: true`，默认关闭是刻意的。
