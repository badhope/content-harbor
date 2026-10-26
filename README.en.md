# Harbor — Self-hosted AI Content Hub

> **Write once, publish everywhere.** Your articles live in your own database; an AI (or any script) manages the full lifecycle — write, edit, publish, update, and list across 10 platforms.

<p>
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="python" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <img alt="platforms" src="https://img.shields.io/badge/platforms-10-blueviolet">
  <img alt="fastapi" src="https://img.shields.io/badge/API-FastAPI-teal">
</p>

[中文](README.md) · English

## Highlights

- **One-click multi-platform publishing** — Juejin / CSDN / Cnblogs / Zhihu / SegmentFault / Bilibili / Toutiao / OSChina / 51CTO / your own blog (WordPress/Typecho / any MetaWeblog-compatible platform).
- **In-place update, not re-post** — edits reopen each platform's editor and modify the original article; URL, comments and likes stay intact.
- **AI takes full control** — clean REST API; Claude / Cursor / any language can create, publish, fetch and update articles.
- **Fully self-hosted** — article DB, login sessions and browser profiles all live on your machine. No third-party SaaS.
- **Captcha-free channels** — Cnblogs / 51CTO / self-hosted blogs use the MetaWeblog XML-RPC protocol: configure credentials once, no browser, no verification.
- **Built-in anti-detection browser** — Patchright (a Playwright fork) with per-platform persistent profiles. Scan a QR code once, stay logged in permanently.

## Quick Start

The backend lives in `backed/` (FastAPI, SQLite by default, zero external dependencies).

```bash
git clone https://gitcode.com/badhope/content-harbor.git
cd content-harbor

# Start the backend (only requires uv)
./start.sh          # Linux / macOS
start.bat           # Windows
# or manually:
cd backed && uv sync && uv run uvicorn app.factory:create_app --host 0.0.0.0 --port 8000 --reload
```

- API docs: http://localhost:8000/api/v1/hub/docs
- Deployment: see `deploy/` (docker-compose + Dockerfile + Nginx reverse proxy)

Web UI: build the frontend once (`cd web && pnpm install && pnpm build`, output goes to `server/static/`), then the backend serves it at the root URL.

## Supported Platforms (10)

| Platform | Method | Publish | Update | List |
|---|---|---|---|---|
| Juejin | browser + page API | ✅ | ✅ | ✅ |
| CSDN | browser | ✅ | ✅ | ✅ |
| Cnblogs | MetaWeblog | ✅ | ✅ | ✅ |
| 51CTO | MetaWeblog | ✅ | ✅ | ✅ |
| Self-hosted blog | MetaWeblog | ✅ | ✅ | ✅ |
| Zhihu | browser UI | ✅ | ✅ | — |
| SegmentFault | browser UI | ✅ | ✅ | — |
| Bilibili column | browser UI | ✅ | ✅ | — |
| Toutiao | browser UI | ✅ | — | — |
| OSChina | browser UI | ✅ | — | — |

## Architecture

```
       AI / scripts  ── REST API ──>  business layer (articles / publications / tasks)
                                         │
                         built-in Chromium, one persistent profile per platform (Patchright)
                                         │
                         adapters: juejin / csdn / cnblogs / zhihu / sf /
                                   bilibili / toutiao / oschina / metaweblog / 51cto
                                         │
                         publish · list · in-place update
```

All long-running tasks (publish / update / login / sync / refresh) go into a single SQLite-backed task queue, consumed by a dedicated background thread. Tasks survive process restarts; `waiting_human` tasks can be resumed.

## REST API Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/hub/status` | Overview |
| GET | `/api/v1/hub/platforms` | List supported platforms |
| GET/POST | `/api/v1/hub/articles` | List / create articles |
| GET/PUT | `/api/v1/hub/articles/{id}` | Read / update article |
| POST | `/api/v1/hub/articles/{id}/publish` | Publish (returns `task_id`, poll async) |
| POST | `/api/v1/hub/articles/{id}/update` | In-place update (returns `task_id`) |
| POST | `/api/v1/hub/sync/pending` | Push all pending updates (returns `task_id`) |
| POST | `/api/v1/hub/refresh/{platform}` | Fetch platform articles (returns `task_id`) |
| GET/PUT | `/api/v1/hub/tasks` / `/api/v1/hub/tasks/{task_id}` | Task list / detail |
| POST | `/api/v1/hub/tasks/{task_id}/resume` | Resume a `waiting_human` task |
| GET/POST | `/api/v1/hub/accounts` | Account status and login |
| GET/POST | `/api/v1/hub/ai/write` | AI draft + auto-publish |
| GET | `/api/v1/hub/metrics` | Real-time counters and histograms |

Interactive API docs: http://localhost:8000/api/v1/hub/docs

## AI Writing

```bash
export AI_API_KEY=sk-xxx
export AI_BASE_URL=https://api.deepseek.com/v1
export AI_MODEL=deepseek-chat

# Draft + publish in one shot
curl -X POST http://localhost:8000/api/v1/hub/ai/write \
  -H 'Content-Type: application/json' \
  -d '{"topic":"How to build an AI content pipeline","words":2000}'
```

AI drafting supports any OpenAI-compatible model: DeepSeek, Doubao, Tongyi, Kimi, Zhipu, Ollama.

## Captcha Strategy (3 Layers)

The honest truth: **"bypass captcha" is a trap**. Server-side captchas (Aliyun / Geetest / Tencent) encrypt mouse trajectory and environment fingerprints client-side — script "breaks" die on the next platform风控 upgrade and get accounts banned.

Our approach: **avoid > permanently solve once > hand off to human**

1. **L1 — Use official channels where possible**: Cnblogs / 51CTO / self-hosted blogs via MetaWeblog = **zero captcha**.
2. **L2 — Anti-detection + persistent sessions**: Patchright (Playwright fork) hides 8 automation fingerprints; logins persist on disk under `backed/data/profiles/`, turning "daily captcha" into "one-time captcha".
3. **L3 — Semi-auto + human handoff**: If a captcha does appear, the browser window stays open; you solve it manually, and automation resumes automatically 2 seconds after it clears.

For fully unattended operation, stick to L1 platforms.

## Adding a New Platform (~150 LOC)

Create `backed/service/publishing/adapters/xxx.py`:

```python
from service.publishing.adapters.base import PlatformAdapter, register

@register
class XxxAdapter(PlatformAdapter):
    id, name = "xxx", "Platform Name"
    login_url, home_url, list_url, new_url = ...
    needs_browser = True

    def check_auth(self, page) -> bool: ...
    def list_articles(self, page, limit=50): ...
    def publish(self, page, article, options): ...
    def update(self, page, pub, article) -> bool: ...
```

Then `from service.publishing.adapters import xxx` in `service.py` — registered.

## Repo Mirrors

| Platform | URL |
|---|---|
| **GitCode (primary)** | <https://gitcode.com/badhope/content-harbor> |
| Gitee | <https://gitee.com/badhope/content-harbor> |
| GitHub | <https://github.com/X33834/content-harbor-fixed> |

## License

[MIT](LICENSE) © 2026 badhope
