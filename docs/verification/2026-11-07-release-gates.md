# Verification Record — 2026-11-07 (0.15.0)

Commit: cut as 0.15.0 on top of `8bec923` (0.14.2).
Machine: Windows 10 19044 (x64), Python 3.14.9, uv 0.13.6, Node 24.20.0,
pnpm 9.15.9 (CI-matching), Rust 1.99.1, Edge for browser work.
Method: every claim below was produced by running the product, not by reading code.
Where a check could not run on this machine, that is stated as a limitation rather
than skipped silently.

---

## 1. Automated gates (八道门禁)

| Gate | Command | Result |
|---|---|---|
| Compileall | `python -m compileall -q -x '(\.venv\|node_modules\|__pycache__)' .` | OK |
| Ruff | `ruff check .` (+ `mcp_server.py` E9,F7,F82) | All checks passed |
| Backend tests | `pytest -q --no-header -p no:cacheprovider` | **675 passed**, 1 warning |
| Frontend lint | `pnpm -C web lint` (pnpm 9.15.9) | 0 errors, 209 warnings (stylistic) |
| Frontend tests | `pnpm -C web test` (pnpm 9.15.9) | **77 passed** across 13 files |
| Frontend build | `pnpm -C web build` | success, output to `server/static` |
| Version consistency | `scripts/bump_version.py --check` | 5/5 at `0.15.0` |
| Secret scan | `scripts/scan_secrets.py` | 0 findings |

Backend count moved 651 → 675: +14 for the new publish read-back layer
(`backed/tests/test_publish_readback.py`) and +10 for the title-evidence rules
(`backed/tests/test_title_evidence.py`, one of them parametrized ×7).

## 2. Real publish verification — GitHub Pages (protocol platform, no browser)

Script: `scripts/verify_real_publish.py` (re-runnable; credentials come from the local
secret store and are never written to disk or printed).

Target: `badhope/harbor-publish-verify`, a dedicated public repo with Pages enabled, so
verification never pollutes the product repo.

Run on 2026-11-07, full chain, no HTTP mocking:

```
check_auth        -> True
publish           -> commit 52d72d3 / post_id _posts/2026-11-07-…md
read_back         -> found=True, sha present, title matched
verify_readback   -> True  "回读通过：仓库文件与站点页面都确认存在"
list_articles     -> post appears in the platform listing
update (in place) -> True
post-update readback -> True
```

Independent third check (not through Harbor's own client):

```
GET https://badhope.github.io/harbor-publish-verify/2026/11/07/harbor-real-publish-verification-2026-11-07-14-01-19.html
-> 200
<title>Harbor real publish verification 2026-11-07 14:01:19 | harbor-publish-verify</title>
```

### Two real defects this verification found

Both were invisible to every mock and to repository-level checks:

1. **Future-dated posts silently dropped by Jekyll.** `_front_matter()` wrote
   `datetime.now()` (local, UTC+8). The Pages builder runs in UTC, so a 13:58 local
   timestamp becomes a *future* date; Jekyll's default `future: false` discards the
   post. The commit succeeded, the Contents API read-back succeeded, `list_articles`
   listed it — and the public URL was 404. Fixed by emitting UTC with an explicit
   `+0000` offset, so the front-matter is timezone-independent.
2. **Read-back recomputed the URL instead of reusing the published one.** `_site_url()`
   derives the path from *now*; a read-back after the UTC date rolls over points at a
   directory that does not exist. Fixed by probing `pub["post_url"]` (the URL recorded
   at publish time) first.

A third, softer issue: Pages builds are asynchronous, so a fresh publish is legitimately
404 for tens of seconds. Blind URL retries would report a false failure, so the adapter
now asks `GET /repos/{o}/{r}/pages/builds/latest` and distinguishes `building` (not a
failure, flagged "稍后复核") from `errored` (a real failure).

## 3. Desktop installers — built, installed, launched, uninstalled

| Item | Result |
|---|---|
| `pnpm tauri build` | NSIS `内容港 Harbor_0.14.2_x64-setup.exe` 53.3 MB |
| MSI | `内容港 Harbor_0.14.2_x64_zh-CN.msi` 54.93 MB |
| Silent install (`msiexec /i … /qn`) | exit 0; `harbor.exe` + `harbor-backend.exe` in `C:\Program Files\内容港 Harbor\` |
| Uninstall registry entry | `内容港 Harbor` 0.14.2 present |
| Launch | `harbor.exe` spawned the sidecar automatically |
| Health | `GET 127.0.0.1:60918/api/v1/hub/health` → `{"status":"ok","service":"harbor"}` |
| Data root | `%APPDATA%\com.harbor.hub\` (config.json, hub.db, profiles, tauri-port) |
| Uninstall (`msiexec /x … /qn`) | exit 0; install dir gone; uninstall entry gone |

MSI initially failed with `light.exe` exit code 1. Root cause: `productName` is Chinese
(`内容港 Harbor`) while WiX 3.14 defaults to code page 1252, which cannot encode CJK.
Fixed by declaring `bundle.windows.wix.language = ["zh-CN"]` in `tauri.conf.json`.
This is the failure that had previously been written off as "Tauri packaging unverified".

## 4. CI — the mirrors had been red for two days

`badhope/harbor`, `X33834/harbor` and `Morningstar202604/harbor` all showed `CI: failure`
on every recent run (`8bec923`, `cd748da`, `d26e519`, `4f1c3e8`, `59763ac`).

Root cause, reproduced locally with `npx pnpm@9 install --frozen-lockfile`:

```
ERROR  packages field missing or empty
```

`web/pnpm-workspace.yaml` declared only `allowBuilds`. pnpm 9+ treats the presence of
that file as workspace mode and then requires a `packages` field; without it, install
aborts before lint/test/build ever run. Local runs never caught it because
`web/node_modules` already existed.

Fix: declare `packages: ["."]` (this repo is a single-package workspace) and pin
`packageManager: pnpm@9.15.9` in `web/package.json` so CI and local cannot drift again.
Verified by running the full frontend gate on pnpm 9: install → lint (0 errors) →
77 tests → build.

## 5. Docker — not verified on this machine, and why

`docker` CLI 29.9.2 is installed but no engine can run here:

- No Docker Desktop, no daemon, `docker info` → pipe not found.
- WSL feature and kernel were installed during this session, but WSL 2 cannot boot:
  `HCS_E_HYPERV_NOT_INSTALLED` — this host is itself a VM without nested
  virtualization exposed. That is a host capability limit, not a project defect.
- `deploy/docker-compose.yml` and both Dockerfiles were reviewed and their previously
  known defects (wrong build context, wrong data volume, missing `HUB_DATA_DIR`) were
  fixed in 0.14.2.

Because the engine cannot run here, container verification is executed by the new
`docker-smoke` CI job on `ubuntu-latest` (compose config → build → up → healthcheck
from inside the container → authenticated API call → teardown). This job has not yet
produced a green run at the time of writing this record; the first run after this
commit is the evidence. Nothing in this file claims the container ran locally.

## 6. What is still not verified

- Browser-driven platforms (知乎 / CSDN / 掘金 / B站 / 头条 / 开源中国 / 小红书): these
  need interactive logins and were not driven end-to-end this round. Their adapters
  were exercised only through the offline contract probe.
- `devto` and `telegraph` protocol adapters: implement read-back, covered by unit tests,
  but not published against a live account this round (no API key / telegra.ph is not
  reachable from this network).
- The `desktop-build` CI job (Windows runner) and `docker-smoke` job: first green run
  pending.
