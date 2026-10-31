# 平台连接与发布指南

## 总览

15 个平台适配器分三类，连接方式完全不同：

| 类型 | 平台 | 连接方式 | 登录态有效期 |
|------|------|----------|-------------|
| 浏览器（扫码） | csdn / juejin / zhihu / segmentfault / bilibili / toutiao / oschina / xiaohongshu | `uv run python -m backed.cli login --platform <名>` 扫码一次，登录态存本地 profile | 长期（数周~数月，视平台） |
| 纯协议（config.json 填凭据） | cnblogs / metaweblog / 51cto / devto / github / wechat / telegraph | 填 `config.json` 的 `platforms.<名>` 字段，免扫码 | 永不过期（token 类凭据失效才需换） |

统一发布入口：`cd backed && uv run python -m backed.cli publish --id <文章ID> --platforms <逗号分隔>`。

每个平台的凭据字段一句话说明见应用内「设置 → 平台账号」卡片提示；能力矩阵（支持发布/草稿/更新/列表/标签/封面）由代码级 `CAPABILITIES` 声明，`GET /api/v1/hub/platforms` 原样下发。

---

## 一、cnblogs（推荐优先接，最简单）

### 1. 获取三件套

去博客园网页：

1. 登录 https://www.cnblogs.com
2. 右上角头像 → **管理 → 设置 → 其他设置**
3. 勾选 **「允许 MetaWeblog 博客客户端访问」**
4. 保存后页面出现三项，逐项抄进 `config.json`：

```json
{
  "platforms": {
    "cnblogs": {
      "endpoint": "https://rpc.cnblogs.com/metaweblog/<你的博客地址名>",
      "username": "<MetaWeblog 登录名（不是邮箱、不是昵称）>",
      "token": "<MetaWeblog 访问令牌（不是登录密码）>"
    }
  }
}
```

> **坑**：endpoint 里那段是**博客地址名**（`https://www.cnblogs.com/<这段>/`），不是数字用户 ID。填错的表现是 500，不是 404。

### 2. 验证连接

```bash
cd backed && uv run python -m backed.cli check --platform cnblogs
# 输出 ✓ 已登录 即凭据有效
```

### 3. 发布

```bash
cd backed && uv run python -m backed.cli publish --id 4 --platforms cnblogs --draft
# 先草稿；确认无误后去掉 --draft 正式发布
```

### 自动抠令牌（替代手贴）

```bash
python cli.py bootstrap-cnblogs --username <登录名> --password <登录密码>
# 有头浏览器登录一次，自动把 endpoint/username/token 写进 config.json
```

---

## 二、浏览器平台（8 个）

### 1. 首次登录（每个平台各做一次）

```bash
cd backed && uv run python -m backed.cli login --platform juejin --headed
# 弹出有头 Chromium，扫码/输密码登录
# 登录态存到 data/profiles/<platform>/<account>/，之后 publish 自动复用
```

| 平台 | 登录方式 | 注意事项 |
|------|---------|---------|
| csdn | 扫码 | 验证码策略：L1 反检测 → L2 持久化 → L3 半自动 |
| juejin | 扫码 | `API_FIRST` 标注有 OpenAPI，但默认走浏览器 |
| zhihu | 扫码 | 编辑器 UI 注入，发布后 URL 带 `/p/<id>` |
| segmentfault | 扫码 | 草稿 API + 写作页点发布 |
| bilibili | 扫码 | 草稿 API（FormData + csrf），正式发布需选分区 |
| toutiao | 扫码 | 编辑器 UI 注入，发布后 URL 在 `article` 路径 |
| oschina | 扫码 | UEditor iframe 注入，发布后 URL 在 `/blog/` 路径 |
| xiaohongshu | App 扫码 | 小红书 Creator 页发布；选择器未实机验证（见适配器 `selector_version`），不支持原地更新（平台限制） |

### 2. 检查登录态

```bash
cd backed && uv run python -m backed.cli check --platform juejin
# ✓ 已登录 / ✗ 未登录
```

### 3. 发布

```bash
cd backed && uv run python -m backed.cli publish --id 4 --platforms juejin,csdn --draft
# 多平台逗号分隔；--draft 只发草稿
```

### 4. 排查

```bash
python cli.py diagnose --platform <名>
# 输出该平台的登录页、是否需要浏览器、验证码策略、推荐接入方式
```

### 5. 验证码专项

```bash
python cli.py solve-captcha --platform <名> --wait 180
# 打开页面就地处理验证码：先半自动点，不行暂停等人工
```

---

## 三、纯 API 平台（devto / github / wechat / telegraph）

凭据填进 `config.json` 的 `platforms` 段（应用内「设置 → 平台账号」也有每平台提示）：

```json
{
  "platforms": {
    "devto":     { "api_key": "<dev.to/settings/extensions 生成>" },
    "github":    { "token": "<PAT，勾 repo 权限>", "owner": "<用户/组织>", "repo": "<仓库名>" },
    "wechat":    { "appid": "<公众号 appid>", "secret": "<公众号 secret，IP 需加白名单>" },
    "telegraph": { "token": "", "author_name": "Harbour" }
  }
}
```

- **telegraph 免注册**：token 留空即可，首次发布会自动创建账号并把 token 回写进 config.json。
- **wechat**：个人订阅号默认只能进草稿箱（API 返回 48001 时如实降级并提示）。
- **github**：发布即 commit 到仓库 Markdown 源；同 path 再 PUT = 原地更新。

验证：`uv run python -m backed.cli check --platform devto`（其余同理）。

---

## 四、端到端示例（AI 写稿 → 多平台发布）

```bash
# 1. AI 写 3000 字并入库（不发布）
python cli.py ai-write --topic "用 Python 实现一个轻量级任务调度器" --words 3000
# 输出：{"id": 5, ...}

# 2. 人工确认内容无误后，发布到多个平台（草稿）
cd backed && uv run python -m backed.cli publish --id 5 --platforms cnblogs,juejin,csdn --draft

# 3. 确认后正式发布
cd backed && uv run python -m backed.cli publish --id 5 --platforms cnblogs,juejin,csdn
```

> **合规门禁**：AI 源文章发布时自动强制 `draft_only=True`（人审闸门）。想正式上线须单独调 `publish(draft_only=False)`。

---

## 五、REST API（不跑 CLI 时）

服务起在 `127.0.0.1:8800`，关键端点：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/v1/hub/health` | GET | 服务健康检查（db/events_log/ai_ready） |
| `/api/v1/hub/platforms` | GET | 平台清单 + 代码级能力矩阵 |
| `/api/v1/hub/articles` | POST | 新建文章 |
| `/api/v1/hub/articles/{id}/publish` | POST | 发布 `{"platforms":["cnblogs"],"draft_only":false}` |
| `/api/v1/hub/ai/write` | POST | AI 写稿 `{"topic":"...","words":3000}` |
| `/api/v1/hub/ai/gate` | POST | 合规门禁检查 |
| `/api/v1/hub/metrics` | GET | Prometheus 格式指标 |
| `/api/v1/hub/jobs` | GET | 发布任务列表 |

---

## 六、首次接入检查单

1. `GET /api/v1/hub/health` 全绿；
2. 纯协议平台先填 `config.json`（见第三节），`check --platform <名>` 返回 ✓；
3. 浏览器平台逐个 `login --headed` 扫码（见第二节），扫码完点「检测」确认登录态；
4. 发一篇草稿到目标平台，回读确认无误后再正式发布。
