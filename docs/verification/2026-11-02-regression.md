# Verification Record — 2026-11-02

Commit: `106ade0` (plus working-tree changes described below)
Machine: Windows 10 19044, Python 3.14.9, Node 20, system Edge 156
Method: live backend on `127.0.0.1:8000`, Vite dev server on `127.0.0.1:5173`,
UI driven by Patchright through the system Edge binary.

---

## 1. Automated gates

| Gate | Command | Result |
|---|---|---|
| Backend tests | `uv run --no-sync pytest -q` | **537 passed**, 1 Starlette deprecation warning; three consecutive clean runs |
| Ruff | `uv run ruff check .` | All checks passed |
| Frontend tests | `pnpm test --run` | **66 passed** across 10 files |
| Frontend lint | `pnpm lint` | 0 errors, 194 warnings (stylistic) |
| Frontend build | `pnpm build` | success, 4374 modules, 16.6 s |
| Version consistency | `scripts/bump_version.py --check` | 5/5 at `0.13.0` |
| Secret scan | `scripts/scan_secrets.py` | 848 files, 0 findings |

## 2. Runtime — service assembly

```
GET /health              -> {"status":"ok","version":"v1","service":"内容港 Harbour"}
GET /api/v1/hub/platforms -> 15 adapters
GET /api/v1/hub/status    -> articles=1, publications=0, accounts=1
GET /api/v1/hub/adapters/probe
    static: checked=15, errors=0, warnings=0
    dom:    juejin 2/2 selectors matched
```

## 3. UI walkthrough (screenshots regenerated from this run)

| View | Evidence | Result |
|---|---|---|
| Write workspace | `docs/screenshots/01-write.png` | Three-column layout renders, article list, editor empty-state, publish rail |
| Publish center | `docs/screenshots/02-publish.png` | Four stat cards, two charts, empty-state table |
| Task center | `docs/screenshots/03-tasks.png` | Task list with status grouping |
| Settings / AI | `docs/screenshots/04-settings.png` | Seven providers listed, gate status, routing chips |
| Mobile 375×812 | `docs/screenshots/05-mobile.png` | Icon rail collapses, tabs scroll |

**Console errors during the full walkthrough: 0.**

### Defect found and fixed during this walkthrough

At 375 px width the AIGC explanation text wrapped character-by-character and overflowed
its container, because `el-form-item` kept a fixed 150 px label column. Fixed in
`web/src/components/settings/AIPanel.vue` by stacking label and content vertically under
`max-width: 767px`. Re-captured screenshot confirms the text now flows normally.

## 4. Browser fetch — restored capability

Previously `browser_fetch` / `browser_screenshot` raised unconditionally, because the
browser cannot pin sockets at the Python layer. Now the target host is resolved and
validated, then pinned inside Chromium via `--host-resolver-rules`, and a request-level
guard aborts any sub-resource or redirect that resolves to a non-public address.

| Check | Command | Result |
|---|---|---|
| Real fetch | `browser_fetch(hub, 'test', 'https://example.com')` | title `Example Domain`, 911 chars of text |
| Loopback | `http://127.0.0.1:8000/` | blocked — `不是公网地址` |
| Cloud metadata | `http://169.254.169.254/latest/` | blocked |
| Non-HTTP scheme | `file:///C:/Windows/win.ini` | blocked — `仅支持 http/https` |
| IPv6 loopback | `http://[::1]:80/` | blocked |
| Private LAN | `http://192.168.1.1/` | blocked |
| Pinned rule content | `_pinned_resolver_rules` unit test | all validated IPs embedded, single flag |
| Request guard | `_guard_requests` unit test | private-host sub-resource aborted, public + data: allowed |

## 5. Article lifecycle — including delete

Before this run, articles could be created, edited and versioned but **never deleted**
through the API or the UI. That gap is closed.

| Check | Method | Result |
|---|---|---|
| Create | `POST /articles` | 200, id returned |
| Read | `GET /articles/{id}` | 200 |
| Update | `PUT /articles/{id}` | 200 |
| Quality report | `POST /articles/{id}/qa` | 200 |
| Schedule create | `POST /schedules` | 200 |
| Delete | `DELETE /articles/{id}` | 200, `{"articles":1,"publications":0,"versions":0}` |
| Get after delete | `GET /articles/{id}` | 404 |
| Delete again | `DELETE /articles/{id}` | 404 (not a silent success) |
| UI delete button | hover list item → click trash → confirm | Row removed, toast `已删除`, 0 console errors |
| Cascade | insert version + publication, then delete | all three tables report 1 row removed |
| Isolation | delete one of two articles | the other still readable |

## 6. Endpoint smoke

All reachable with the server running:

```
200  GET /articles/page?limit=5
200  GET /articles/search/{keyword}
200  GET /tags
200  GET /schedules
200  GET /webhooks
200  GET /tasks
200  GET /status
200  GET /metrics
200  GET /platforms
200  GET /adapters/probe
200  GET /ai/status
200  GET /ai/providers
200  GET /ai/templates
200  GET /ai/gate
200  GET /ai/config
200  GET /publications
200  GET /pending-human
200  GET /health
405  GET /browser/{platform}/fetch   (POST-only route, expected)
```

## 7. Defects found and fixed during this pass

| Defect | Impact | Fix | Regression test |
|---|---|---|---|
| `/health` returned 401 for non-loopback sources | Docker healthcheck / k8s probe always failed; dependants never started | health paths exempt from the token check, business routes unchanged | `test_health_auth_boundary.py` (3 cases; removing the exemption makes 2 fail) |
| Tag governance panel rendered blank | Whole settings tab unusable, no console error | expose `clusters` / `loadingClusters` getters on the hub store | `tag.test.js` (3 cases; removing the getters makes 2 fail) |
| `fetch_pinned` timeout was cosmetic | Caller believed the request never happened; the worker thread still connected afterwards | cancellation event checked before every network step | `test_cancelled_flag_alone_blocks_connection` (fails when the check is removed) |
| Flaky `test_dns_timeout_prevents_late_connection` | Intermittent red in CI; masked the real timeout bug | fixed the underlying cancellation bug; widened the post-timeout wait | 12 consecutive runs, 0 failures |

## 8. Not verified in this record

These remain open and must not be described as working:

- **Real platform publishing.** No live credentials were used. Adapter behaviour against
  current platform DOM is unverified beyond the juejin static probe.
- **Docker / compose.** Docker daemon and the Compose plugin are unavailable on this
  machine. Gateway auth and SSE end-to-end are untested.
- **Tauri desktop build.** `cargo check` fails locally with a linker error
  (`link.exe: extra operand`); sidecar staging is scripted but unproven.
- **MCP remote SSE.** Loopback default and token requirement are unit-tested; a real
  remote deployment is untested.
