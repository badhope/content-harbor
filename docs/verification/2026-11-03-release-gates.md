# Verification Record — 2026-11-03

Commit: `b3668be` (0.13.1 release cut on top of it)
Machine: Windows 11, Python 3.13.12 (uv-managed), Node 20 / pnpm
Method: all eight automated gates re-run from a clean working tree at release time.

---

## 1. Automated gates (八道门禁)

| Gate | Command | Result |
|---|---|---|
| Compileall | `uv run --no-sync python -m compileall -q .` | OK |
| Ruff | `uv run --no-sync ruff check .` | All checks passed |
| Backend tests | `uv run --no-sync pytest -q --no-header` | **537 passed**, 1 warning |
| Frontend tests | `pnpm -C web test -- --run` | **60 passed** across 9 files; 1 unhandled EPERM from the sandboxed fs shim writing vitest temp files (environment artifact, not project code) |
| Frontend lint | `pnpm -C web lint` | 0 errors, 196 warnings (stylistic, within warning-ceiling policy) |
| Frontend build | `pnpm -C web build` | success, 19.6 s, output to `server/static` |
| Version consistency | `scripts/bump_version.py --check` | 5/5 at `0.13.1` |
| Secret scan | `scripts/scan_secrets.py` | 859 files, 0 findings |

## 2. What this release contains

0.13.1 = the previous `[未发布]` changelog section, cut as a patch release:

- Added: tag-cluster governance UI entry (near-duplicate tags with batch merge),
  publish-rail manual cooldown clearing.
- Fixed: settings tag-governance panel rendering blank (`clusters`/`loadingClusters`
  getters missing from the hub store), `/health` returning 401 behind reverse proxies,
  fetch timeout illusion (background thread kept running after `fetch_pinned` timeout),
  removal of 0.7-era stale screenshots.

## 3. Remaining unverified

Unchanged from the 2026-11-02 record and README 已知限制:

- Real platform publish verification (browser adapters) — static contract probes only.
- Docker compose build/run on this machine (no Docker daemon locally).
- Tauri cross-platform packaging (linker blocked on this machine).
