# Verification Record — 2026-11-04

Commit: cut as 0.13.2 on top of `624214e` (0.13.1).
Machine: Windows 11, Python 3.13.14 (venv), Node 22 / pnpm 12, Edge channel for browser work.
Method: all eight automated gates re-run on the dirty release tree, then functional
verification of the new publish-settings feature against the live dev instance
(backend :8000 serving the freshly built frontend).

---

## 1. Automated gates (八道门禁)

| Gate | Command | Result |
|---|---|---|
| Compileall | `python -m compileall -q -x '(\.venv\|node_modules\|__pycache__)' .` | OK |
| Ruff | `ruff check .` (+ `mcp_server.py` E9,F7,F82) | All checks passed |
| Backend tests | `pytest -q --no-header -p no:cacheprovider` | **537 passed**, 1 warning |
| Frontend tests | `pnpm -C web test` | **66 passed** across 10 files |
| Frontend lint | `pnpm -C web lint` | 0 errors, 202 warnings (stylistic, within ceiling) |
| Frontend build | `pnpm -C web build` | success, 16.6 s, output to `server/static` |
| Version consistency | `scripts/bump_version.py --check` | 5/5 at `0.13.2` |
| Secret scan | `scripts/scan_secrets.py` | 2803 files, 0 findings |

## 2. Functional verification (this release's feature: 发布设置点满)

- API round-trip: `PUT /api/v1/hub/articles/12` with `{"ai_model": "...", "source": "ai"}`
  → read-back reflects both fields → restored to original values. (New `ArticlePatch`
  fields actually persist; this was impossible before.)
- Write view (screenshot `.tmp/acct/verify_write_settings.png`, served build on :8000):
  editor shows the new 发布设置 block with cover preview + AI-assist select,
  badge "✓ 已点满"; publish rail shows "✓ 已点满：封面、标签、摘要、AI 辅助标识".
- Seeded 3 governance seed articles (#10/#11/#12) with cover/tags/summary/ai_model all
  set; `hub_seed_result.json` re-read confirms `all_set: true` per article.
- Real publish chain smoke (draft_only=true, nothing went live): juejin draft
  `7704153929698082870` (publications #1), CSDN draft (publications #2, no post id —
  see remaining), bilibili draft `aid=387763` (publications #3).

## 3. What this release contains

0.13.2 = the `[0.13.2]` changelog section: editor publish-settings block (cover upload/
URL + preview, AI-assist select, 4-item fill meter), publish-rail missing-settings gate
with confirm-before-publish, `ai_model`/`source` now editable via the articles API,
local-draft restore keeps cover/ai_model, and the tag-alias test-isolation regression fix.

## 4. Remaining unverified / known platform limits

- Live (non-draft) publishing was **not** re-verified end-to-end: juejin's publish API
  has been sealed since 2026-09 (frontend "确定并发布" only issues `article_draft/update`,
  adapter degrades to draft + `pending_human`); bilibili deprecated the legacy article
  editor (draft `aid=387763` must be finished in the new editor); the CSDN draft-save
  path returns no `articleId`, so its draft could not be re-opened programmatically.
- Docker compose build/run on this machine (no Docker daemon locally).
- Tauri cross-platform packaging (linker blocked on this machine).
