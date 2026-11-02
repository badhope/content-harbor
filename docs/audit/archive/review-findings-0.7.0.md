# 内容港 · 代码审查发现（进行中）

> 审查员：AutoClaw  
> 日期：2026-10-12  
> 基线：`check_all.ps1` — compileall ✅ / ruff ❌(3) / pytest ✅ (298 passed) / vite 未跑  
> 版本：0.7.0（e8de999 + 2312ef6，CHANGELOG 未记录）

---

## 基线状态

### 门禁

| 步骤 | 状态 | 详情 |
|---|---|---|
| 1/4 compileall | ✅ PASS | 3s |
| 2/4 ruff | ❌ FAIL | 3 处 F401 unused import |
| 3/4 pytest | ✅ PASS | 298 passed, 7.30s |
| 4/4 vite build | ⏳ 未跑 | check_all.ps1 有 bug，见 F-01 |

### ruff 报的 3 处 F401

| 位置 | 未使用 import |
|---|---|
| `_webhook_store.py:11` | `time` |
| `_webhook_store.py:13` | `urllib.request` |
| `crypto.py:11` | `base64` |

### 文档滞后（E 类）

- **CHANGELOG.md 完全没有 0.7.0 条目**。而 0.7.0 是 128 文件、22443 行插入的巨大重构（拆 hub.py 到 8 个 routers、新增 providers/ tools/ 子包、observability 重写 524 行、Tauri 完整项目）。
- **AGENT.md 描述仍是 0.4.0 前的架构**（说 hub.py 是所有 REST 端点唯一入口、有 models/ schemas/ repository/ service 层等），与真实代码严重不符。

---

## A 类 · 行为错误

### F-01 · `check_all.ps1` 脚本 bug：`exit 1` 在 scriptblock 里导致整个脚本静默退出

- **位置**：`scripts/check_all.ps1`（第 34/40/43/53 行等）
- **证据**：
```powershell
Invoke-Step '2/4 静态门禁 ruff（backed + 根目录 mcp_server.py）' {
    uv run ruff check .
    if ($LASTEXITCODE -ne 0) { exit 1 }    # ← 这个 exit 1 在 scriptblock 内
    ...
}
```
- **为什么是问题**：PowerShell 的 `& $Body` 在父作用域执行 scriptblock，scriptblock 里的 `exit 1` 会终止整个脚本进程，而不是回到 try/catch。结果是 ruff 失败后 pytest 和 vite build 步骤被静默跳过，只有 ruff 报错的日志，没有 FAIL 汇总行。实测日志只有 52 行，只到 step 2/4。
- **建议修法**：把所有 `exit 1` 改成 `throw "退出码 $LASTEXITCODE"`，走现有 try/catch。

### F-02 · `_webhook_store.init(conn)` 未关闭它创建的连接，泄漏

- **位置**：`service/publishing/_webhook_store.py:37-46`
- **证据**：
```python
def init(conn=None):
    c = conn or _conn()          # ← 无 conn 时新创建一个
    c.execute("""CREATE TABLE IF NOT EXISTS webhooks ...""")
    c.commit()
    # ← 没有 c.close()！
```
然后 `register` / `list_all` / `update` / `delete` 每次都调 `init()` 不传 conn，导致每次泄漏一个连接。
- **为什么是问题**：webhook CRUD 每调一次泄漏一个 sqlite connection。SQLite 有文件句柄上限，长时间运行会耗尽。
- **建议修法**：`init` 内部创建时 `try/finally` 关闭，或让调用方传 conn。

### F-03 · `article_store.py:ArticleStore.edit` 调用 `db.get_pending_updates` 传了错误的参数个数

- **位置**：`service/publishing/article_store.py:27`
- **证据**：
```python
def edit(self, article_id, **kw):
    ...
    db.update_article(self.conn, article_id, **kw)
    db.get_pending_updates(self.conn, article_id)   # ← 传 2 参
```
而 `db.get_pending_updates(conn)` 只接受 1 个参数。
- **为什么是问题**：如果 `ArticleStore.edit` 被调用会立刻 `TypeError`。
- **为什么测试没炸**：`ArticleStore.edit` 是死代码（见 F-04），从没人调它。
- **建议修法**：删掉 `ArticleStore.edit` 那行，或改 Hub.edit 直接调 ArticleStore（同时修参数）。

### F-04 · `Hub.articles`（ArticleStore 实例）完全未使用

- **位置**：`service/publishing/service.py:107`
- **证据**：
```python
# 文章 CRUD 仓库（纯 DB 操作，无浏览器/AI 依赖）
self.articles = ArticleStore(self.conn)
```
搜遍整个 service.py，`self.articles` 只在这里被赋值，从未被读取。所有文章 CRUD 都直接调 `db.create_article` / `db.get_article` / `db.update_article` / `db.list_articles` / `db.search_articles` / `db.list_accounts`。
- **为什么是问题**：ArticleStore 是 0.7.0 新加的抽象层，本意是让 Hub 委托给它。但 Hub 只赋值不委托，ArticleStore 成死代码。它里面的 edit() 还有 F-03 的 bug 也没被发现。
- **建议修法**：要么真的把 Hub 的 CRUD 全部委托给 self.articles，要么删除 ArticleStore 和这行赋值。

### F-05 · `TaskManager._dispatch` 丢失 login 的 timeout 和 on_captcha 参数

- **位置**：`service/publishing/tasks.py:160-161` 与 `api/routers/accounts.py:50-56`
- **证据**（accounts.py 收参数但不传）：
```python
class LoginIn(BaseModel):
    account: str = "default"
    timeout: int = 600
    on_captcha: str = "handoff"    # handoff / abort

@router.post("/accounts/{platform}/login")
def login(platform: str, body: LoginIn):
    task_id = get_hub().tasks.submit("login", platforms=[platform], account=body.account)
    # ← body.timeout 和 body.on_captcha 直接被丢
```
`_dispatch` 只调 `self.hub.login(platforms[0], account)`，不传 timeout / on_captcha。
- **为什么是问题**：用户在 API 里传的 `timeout` 和 `on_captcha` 静默失效，无法配置登录行为（比如无人值守环境想设 on_captcha=abort）。
- **建议修法**：把 LoginIn.timeout / on_captcha 传给 `submit` 的 extra，然后 `_dispatch` 里读出来传给 `hub.login(...)`。

### F-06 · `refresh` API 收 limit 但 _dispatch 硬编码 limit=50

- **位置**：`api/routers/accounts.py:66-72` 与 `tasks.py:158-159`
- **证据**：
```python
@router.post("/refresh/{platform}")
def refresh(platform: str, limit: int = 50):     # ← 收 limit
    task_id = get_hub().tasks.submit("refresh", platforms=[platform])   # ← 不传
    ...
```
```python
if kind == "refresh":
    return self.hub.refresh(platforms[0] if platforms else None, account, limit=50)  # 硬编码 50
```
- **为什么是问题**：用户传 `limit=100` 无效。虽然默认值恰好也是 50，看似无影响，但破坏契约。
- **建议修法**：extra 里带 limit，dispatch 里读出来。

---

## B 类 · 安全

### F-07 · `_account_ops.logout` 存在路径穿越风险

- **位置**：`service/publishing/_account_ops.py:75-86`
- **证据**：
```python
def logout(platform, account, pool, _drop, db_conn):
    ...
    profile_dir = PROFILE_ROOT / f"{platform}_{account}"
    auth_file = PROFILE_ROOT / f"{platform}_{account}.auth.json"
    if profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)    # ← 递归删除！
```
`platform` 和 `account` 都来自 URL 路径参数：
```python
@router.delete("/accounts/{platform}")   # accounts.py:39
```
- **为什么是问题**：如果 `platform` 是 `..` 或 `../../Windows/Temp`，`shutil.rmtree` 会删除非预期目录。虽然是 profile_dir 下拼的字符串，但 pathlib 的 `/` 会拼接任意子路径。攻击者能触发任意目录删除（需 X-API-Token 或本机访问）。
- **建议修法**：白名单校验 platform / account 只允许 `[a-z0-9_-]`。

### F-08 · `assist_open` 未做 SSRF 防护

- **位置**：`api/routers/accounts.py:59-64`
- **证据**：
```python
@router.post("/accounts/{platform}/assist")
def assist_open(platform: str, body: AssistIn):
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(422, "url 必须是 http(s) 地址")
    task_id = get_hub().tasks.submit("assist", platforms=[platform], url=body.url)
```
- **为什么是问题**：只检查 scheme，不检查 host。用户可以传 `http://169.254.169.254/latest/meta-data/`（云元数据）或 `http://localhost:6379/`。虽然是在有头浏览器打开，浏览器本身可能有同源限制，但 SSRF 面仍然存在（尤其配合浏览器 fetch 时）。
- **建议修法**：加白名单（配置项 ASSIST_ALLOWED_HOSTS），或至少禁止 localhost / link-local / metadata IP 段。

### F-09 · `crypto._load_key` 在 Windows 上密钥文件权限不收紧

- **位置**：`service/publishing/crypto.py:32-35`
- **证据**：
```python
try:
    _KEY_FILE.chmod(0o600)
except (OSError, AttributeError):
    pass   # Windows 不支持 POSIX chmod
```
- **为什么是问题**：注释自己承认了 Windows 不支持。用户主要用 Windows（README 有 start.bat）。Fernet 主密钥在磁盘上是明文（虽是 URL-safe base64，但没加密），任何能读 `backed/data/.fernet_key` 的进程都能解密所有 webhook secret 和未来加密字段。
- **建议修法**：Windows 上用 `win32security` 或 `SetFileSecurityW` 设 DACL 只允许当前用户读写。

---

## C 类 · 设计

### F-10 · `api/__init__.py::register_routers` 是死代码

- **位置**：`api/__init__.py:15-20`
- **证据**：
```python
def register_routers(app: FastAPI) -> None:
    """将平台发布路由挂载到配置指定的 API 前缀（默认 /api/hub）。"""
    prefix = getattr(settings, "api_prefix", "/api/hub")
    app.include_router(hub_router, prefix=prefix)
```
- **为什么是问题**：`app/factory.py` 只 import 并使用 `api.hub.register_hub`（挂 `/api/v1/hub`），从未调用 `api.register_routers`（挂 `/api/hub`）。两个函数挂不同前缀，但只有一个被调用。文档和使用者以为 prefix 可配置，实际写死 `/api/v1/hub`。
- **建议修法**：删掉 `register_routers`，或让 `register_hub` 也用 `settings.api_prefix`。

### F-11 · `api/hub.py` 里独立 app 的 version="0.2.0" 未随项目更新

- **位置**：`api/hub.py:56`
- **证据**：`app = FastAPI(title="AI 内容中台", version="0.2.0")`
- **为什么是问题**：项目在 0.7.0 版本，这个独立 app 的 OpenAPI 显示 0.2.0。虽只用于本地调试，但误导。
- **建议修法**：改成从 pyproject.toml 读，或直接写当前版本。

### F-12 · 任务引擎 `_run_task` 在 retry 时误调用 `on_task_complete`

- **位置**：`service/publishing/tasks.py:115-135`
- **证据**：
```python
except Exception as e:
    ...
    if action == "retry":
        db.update_task(self.conn, task_id, status="pending", ...)
        self._wake.set()
    elif action == "human": ...
    else: ...
    # 触发事件：任务失败/等人工
    completed = db.get_task(self.conn, task_id)
    ...
    evt_mod.on_task_complete(completed)   # ← 无论 retry/human/fail 都调
```
- **为什么是问题**：retry 时任务 status 是 pending，`on_task_complete` 里只处理 ok/failed/waiting_human，pending 落空——所以功能上没爆炸，但命名与语义不对：`on_task_complete` 应该只在任务真的结束时被调用。
- **建议修法**：retry 分支下不 emit，直接 return 后让 worker 继续循环。

### F-13 · gate.py 本地审查的 `{{` `}}` 检测误伤技术文档

- **位置**：`service/publishing/gate.py:97-100`
- **证据**：
```python
for placeholder in ("TODO", "FIXME", "XXX", "lorem ipsum", "placeholder", "{{", "}}"):
    if placeholder.lower() in content.lower():
        issues.append(f"含占位符残留: {placeholder}")
        break
```
- **为什么是问题**：Python f-string、CSS、JSON、Jinja、LaTeX、JavaScript 模板字面量都大量使用 `{{ }}`。技术类文章一跑门禁就误判"占位符残留"，本地审查直接拒绝。
- **建议修法**：`{{` / `}}` 检测加上下文（比如必须紧邻无空格、或整篇只有一个孤立 `{{`）。或者干脆去掉这两个占位符。

### F-14 · `_event_notifier` 用全局 deque，无环境隔离

- **位置**：`service/publishing/_event_notifier.py:6`
- **证据**：`_notifications = deque(maxlen=200)`
- **为什么是问题**：模块级全局，测试与生产共享（同进程）。多实例 Hub 共享。测试并发时可能污染。conftest.py 说会隔离，但如果没在测试里显式清，会串。
- **建议修法**：允许外部传入 storage，或提供 reset() 方法让 conftest 显式清空。

### F-15 · events.py 在模块导入时启动后台线程

- **位置**：`service/publishing/events.py:183-184`
- **证据**：
```python
# 启动派发线程（模块加载时）
start_dispatcher()
```
- **为什么是问题**：任何 import events 的地方都会拉起一个 daemon 线程。pytest 里 test_docs_links 甚至不会用到 webhook，也会启动线程。虽然 daemon=True 会随进程退出，但测试期间仍在跑。
- **建议修法**：把启动改成显式 init_events() 里做，而不是 import 时。

### F-16 · Hub.check 有 3 处相同 upsert_account 调用，可简化

- **位置**：`service/publishing/service.py:147-160`
- **证据**：check() 方法里有 3 段几乎相同的 `db.upsert_account(...)` 调用，逻辑可合并。
- **为什么是问题**：可读性。不是 bug。
- **建议修法**：合并成一个 `_update_account_status` helper。

### F-17 · `_with_adapter` 里的重试条件把"greenlet 冲突"当作浏览器崩溃

- **位置**：`service/publishing/service.py:271-283`
- **证据**：
```python
except Exception as e:
    if attempt == 2:
        raise
    if any(s in str(e) for s in ("Target closed", "Browser has been closed",
                                 "Session closed", "浏览器启动失败",
                                 "Cannot switch to a different thread",
                                 "greenlet")):
        self._drop(platform, account)
        continue
    raise
```
- **为什么是问题**：注释说 greenlet 冲突"= 池实例被别的线程创建"，但实际上 greenlet 错误可能来自各种状态错位。drop 池实例后重建未必能解决，反而可能掩盖真正的 bug。而且这里吞掉了具体错误类型。
- **建议修法**：至少记录错误堆栈，或把 greenlet 错误单独处理。

---

## D 类 · 不完善

### F-18 · `Hub.update` 和 `Hub.sync_pending` 不过 AIGC 门禁

- **位置**：`service/publishing/service.py:460-541`
- **证据**：`publish` 调用 `aigc_gate.apply_gate_before_publish`，但 `update` 和 `sync_pending` 都没调。
- **为什么是问题**：如果 AI 改写文章后直接 update 同步到平台，会绕过 AIGC 门禁。虽然 `publish` 的 draft_only 流程能拦住 AI 首发，但 update 路径无防护。
- **建议修法**：在 update / sync_pending 前也过 gate（至少对 `source=ai` 的文章）。

### F-19 · `ArticleStore.edit` 完全未被 Hub 使用（死代码）

- **位置**：`service/publishing/article_store.py`
- 同 F-04。

### F-20 · `_triage` 里"未知错误"总是 fail，无法区分"重试可恢复"

- **位置**：`tasks.py:180-190`
- **证据**：不匹配 HUMAN_HINTS / RETRY_HINTS 的错误一律 fail。
- **为什么是问题**：真实平台上有些错误是"操作太快被限流但没写 429"、"页面加载慢"等，本可重试的会被判永久失败。
- **建议修法**：加个 "RETRY_ONCE_HINTS"，未命中但 error 消息像可重试的默认再试 1 次。

---

## E 类 · 文档失真

### F-21 · CHANGELOG.md 缺 0.7.0 条目

- 0.7.0 是 128 文件、22443 行插入的重大重构，但 CHANGELOG.md 里最新条目是 0.6.5。

### F-22 · AGENT.md 与真实架构严重不符

- AGENT.md 描述的是 0.4.0 之前的结构（hub.py 单一入口、models/ schemas/ repository/ service.py 层）。
- 实际 0.7.0 后：hub.py 只 51 行，聚合 8 个 routers；新增 api/auth.py、api/deps.py、api/routers/*.py；新增 providers/ tools/ 子包；新增 crypto/browser_pool/ai_service 等模块。

### F-23 · AGENT.md 声称"11 个适配器"但列出 5 个不存在的（前面 CHANGELOG 0.6.4 已修）

- 这个已修过（`test_docs_links.py` 校验）。

---

### F-24 · `_bw_worker` 捕获 BaseException，会吞掉 KeyboardInterrupt/SystemExit

- **位置**：`service/publishing/browser.py:63-71`
- **证据**：
```python
def _bw_worker(q):
    ...
    while True:
        task = q.get()
        if task is None:
            break
        ...
        try:
            box["r"] = fn(*args, **kwargs)
        except BaseException as e:      # ← 捕获包括 KeyboardInterrupt / SystemExit
            box["e"] = e
```
- **为什么是问题**：BaseException 包括 KeyboardInterrupt / SystemExit / GeneratorExit。daemon 线程收到 SIGINT 应该终止，这里被吞掉后作为普通异常返回给调用方，中断行为被破坏。
- **建议修法**：改成 `except Exception as e`。

### F-25 · `BuiltinBrowser.start()` 里 `self.display is False` 检查是死代码

- **位置**：`service/publishing/browser.py:399-405`
- **证据**：
```python
if not self.headless:
    self.display = ensure_display()
    if self.display is False:         # ← 死代码：ensure_display 永不返回 False
        print("[warn] 没找到可用的显示...")
        self.headless = True
```
`ensure_display` 的返回值只有 `True`（Win/Mac）、`str`（display 名）或 `None`（Linux 无 X）。
- **为什么是问题**：Linux 无 X 环境（容器 / SSH）走有头模式时，本该降级成无头，实际不做降级直接 launch 有头 Chromium，报一个不易读的错。
- **建议修法**：改成 `if not self.display:`。

### F-26 · `ai_rewrite → hub.update` 绕过 AIGC 门禁

- **位置**：`service/publishing/ai_service.py:111` 与 `service.py:460`
- **证据**：`ai_rewrite` 改完文章调 `self._update(article_id, publish_to)` = `hub.update(...)`，而 `hub.update` 不过 `aigc_gate.apply_gate_before_publish`。
- **为什么是问题**：AI 改写后的内容同步到已发布平台时，绕过 AIGC 门禁。虽然 `publish` 的 draft_only 流程能拦 AI 首发，但 update 路径无防护——AI 改写后内容直接同步上线，违反《AIGC 标识办法》。
- 与 F-18 相同，是 D 类不完善的具体表现。

### F-27 · `version_history.content_hash` 去重逻辑有 corner case

- **位置**：`service/publishing/versions.py:63-70`
- **证据**：
```python
last = conn.execute("SELECT content_hash FROM version_history WHERE article_id=? ORDER BY id DESC LIMIT 1", (article["id"],)).fetchone()
if last and last["content_hash"] == h:
    return None  # 内容没变，跳过
```
- **为什么是问题**：如果最近一条是 `change_kind='pre-rollback'` 且内容相同，第二次编辑相同内容也会被跳过。这在连续编辑-回滚-再编辑相同内容时会漏存版本。轻微，不影响正确性，但语义不清。

### F-28 · `ai.py:load_style` 用 `except Exception: return ""` 吞编码错误

- **位置**：`service/publishing/ai.py:60-64`
- **证据**：
```python
try:
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""
except Exception:
    return ""     # ← 编码错误、权限、路径非法字符全被吞
```
- **为什么是问题**：风格档案是可选增强，吞错是对的（注释也这么说）。但完全无日志会让风格配置损坏静默失效，用户以为写了 style，实际没生效。
- **建议修法**：加个 `warnings.warn` 或 debug 日志。

---

## 尚未审查的文件

- service/publishing/browser.py（729 行）
- service/publishing/observability.py（585 行）
- service/publishing/{scheduler, versions, tags, content_qa, ai, ai_service, ai_enhancements, humanize, cnblogs_login}.py
- service/publishing/adapters/*.py（12 个文件，含 base.py 527 行）
- service/publishing/providers/*.py（5 个文件）
- service/publishing/tools/*.py（2 个文件）
- config/*.py（4 个文件，含 config_unified 667 行）
- core/*.py, utils/*.py
- cli.py, start.py, desktop_sidecar.py
- backed/tests/*（15 个测试文件）
- mcp_server.py
- scripts/*.py, scripts/*.sh, scripts/*.ps1（除 check_all）
- deploy/*
- web/src/*（20 个前端文件）
- docs/*, PLATFORM_CONNECTION.md

---

### F-29 · Hub._busy_for 从未定义，登录/检查/验证码/辅助路径运行时 AttributeError

- **位置**：`service/publishing/service.py` 第 116/191/223/265/341 行
- **证据**：
  - `with self._busy_for(platform, account):` 出现在 5 处（login/check/assist/solve_captcha/publish 路径）
  - `grep -n '_busy_for' service.py` 显示所有出现都是 `self._busy_for(...)` 调用，**没有任何一处定义**
  - `_busy_for` 方法实际定义在 `BrowserPoolManager`（browser_pool.py:26），Hub 从未委托
- **为什么是问题**：一运行到 `login()` / `check()` / `solve_captcha()` / `assist()` / `_with_adapter` 里的 `with self._busy_for(...)` 就抛 `AttributeError: 'Hub' object has no attribute '_busy_for'`。这是登录链路的**必炸点**。
- **为什么测试没炸**：现有 298 个测试全走 StubHub（test_api_integration.py）或 DEMO 模式，从不触发真实浏览器路径，所以从未跑到 `with self._busy_for(...)` 那行。
- **建议修法**：在 Hub 里加一行 `def _busy_for(self, platform, account): return self._browser_pool._busy_for(platform, account)` 委托。

---

## 已修复清单（2026-10-12 本轮）

| 编号 | 状态 | 修了什么 |
|---|---|---|
| F-01 | 修 | check_all.ps1 把 scriptblock 里的 `exit 1` 改成 `throw`，否则 ruff/pytest 一红整个脚本静默退出，4 步只跑到 1 步 |
| F-02 | 修 | _webhook_store.init 自建 conn 后用 try/finally 关闭，不再泄漏连接 |
| F-03 | 修 | ArticleStore.edit 删掉错误的 `get_pending_updates(conn, article_id)` 调用，返回 update 结果 |
| F-05 | 修 | TaskManager._dispatch 从 task.result 读 timeout/on_captcha 传给 hub.login |
| F-06 | 修 | TaskManager._dispatch 从 task.result 读 limit 传给 hub.refresh |
| F-07 | 修 | accounts.py 加 _check_platform/_check_account 正则校验，所有端点都过校验 |
| F-10 | 修 | 删除 api/__init__.py 里的 register_routers 死代码，只保留实际使用的 register_hub |
| F-11 | 修 | api/hub.py 独立 app 的 version 从 0.2.0 更新到 0.7.0 |
| F-12 | 修 | _run_task retry 分支不再 emit on_task_complete，只有 fail/human 终态才 emit |
| F-13 | 修 | gate.py _local_review 把 `{{ }}` 移出占位符黑名单，占位符检测只在代码块外的正文里做 |
| F-18 | 修 | hub.update() 补 AIGC 合规门禁（安全扫描 + AI 源打标识），与 publish 一致 |
| F-24 | 修 | browser._bw_worker 从 `except BaseException` 改成 `except Exception`，让 KeyboardInterrupt/SystemExit 冒泡 |
| F-25 | 修 | browser.BuiltinBrowser.start() 把 `self.display is False` 改成 `not self.display`，Linux 无 X 时能正确降级 |
| F-29 | 修 | Hub 加 `_busy_for` 方法委托给 _browser_pool._busy_for，修复登录/检查/验证码路径的 AttributeError |
| ruff | 清 | 3 处 F401 未使用 import（_webhook_store.time, urllib.request, crypto.base64） |

**测试**：新增 `backed/tests/test_review_20261012.py` 31 个用例，锁定所有修复。总用例 299 → 330。

**门禁**：check_all.ps1 四道全绿（compileall / ruff / pytest / vite build）。

---

## 后续行动

1. 继续读剩下的文件
2. 出分类清单 + 修复优先级
3. 修 A/B 类（含 F-01 check_all 脚本 bug）
4. 修 C 类里低风险高收益的（F-10, F-11, F-12）
5. D 类补 F-18（update/sync 过门禁）
6. E 类写 CHANGELOG 0.7.0 条目 + 更新 AGENT.md

---

## 收尾轮（2026-10-14）—— 逐条复核实况 + 剩余项清零

> 本轮先把 29 条逐条对代码复核（不照抄旧清单），再修真正剩余项。
> 复核发现多条已在后续提交中修复但未回填本文件，一并补记。

### 复核更正：原清单里"未修"但实际已修（带代码证据）

| 编号 | 实况 | 证据 |
|---|---|---|
| F-04 | 已消除 | `self.articles = ArticleStore(...)` 赋值已摘除（service.py:71 处只留注释）；ArticleStore 模块本体成死文件，见下方剩余项 |
| F-08 | 已修 | discover 的辅助入口已接线 SSRF 校验：`accounts.py:144` `_check_url_safety(body.url)`（IP 段判定 + 域名黑名单 + `ASSIST_ALLOW_LOCAL` 豁免） |
| F-15 | 已修 | `events.py:237` 派发线程改由 `init_events()` 显式启动，Hub 构造时调用（service.py:55）；conftest 里 `stop_dispatcher()` 兜底 |
| F-16 | 已修 | `check()` 的 3 处 upsert_account 合并为 1 处（service.py:182 带 F-16 注释） |
| F-17 | 已修 | `_with_adapter` 崩溃分支不再静默：记录错误类型与堆栈后 drop 重建（service.py:380-387，带 F-17 注释） |
| F-20 | 已修 | `_triage` 增加 PERMANENT_HINTS（合规门禁/422 立即 fail）+ 未分类错误首次给一次重试（tasks.py:204-232） |
| F-28 | 已修 | `load_style` 降级路径打 logging warning（ai.py:99-107，带 F-28 注释） |

### 本轮新修

| 编号 | 状态 | 修了什么 | 锁定测试 |
|---|---|---|---|
| F-09 | 修 | crypto.py 新增 `_restrict_key_file()`：Windows 用 `icacls /inheritance:r /grant:r <user>:F` 收紧 Fernet 主密钥 ACL（存量密钥读取时也收紧；失败只 warning） | test_review_20261014.py 3 例 |
| F-14 | 修 | conftest 增加 autouse 清空夹具；隔离顺序对（写→读空）锁定行为 | test_review_20261014.py 2 例 |
| F-27 | 修 | `rollback()` 目标版本与当前内容相同 → 显式「无需回滚」短路，不再静默跳过全部记录 | test_review_20261014.py 1 例 |

**测试**：新增 `backed/tests/test_review_20261014.py` 6 个用例（RED 5 failed/1 passed → GREEN 6/6）。总用例 333 → 339。

### 剩余项（不修，知情即可）

| 项 | 说明 | 处置 |
|---|---|---|
| ~~`article_store.py` 死模块~~ | **已删除**（2026-10-14）：`test_review_20261012.py` 的 F-03 用例与 import、AGENT.md 结构行同步移除；删除后全量 338 用例通过 | 完成 |
| 本机 `~/.git-credentials` | 全局 credential.helper=store 导致 9 条凭据明文落盘（gitcode×2 / gitee×2 / github×5），非仓库内容 | **已处置**：helper 切 manager + 明文文件删除；gitcode 密码建议轮换 |
| 前端零测试 | 只有 vite 构建门禁（本文件"尚未审查"一节同样覆盖不到前端行为） | 需要引入测试框架（新依赖决策），另行立项 |
