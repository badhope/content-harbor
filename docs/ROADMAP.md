# Roadmap

Living document. Reviewed monthly; every item states the user-visible outcome, not the
implementation. See [`VERSIONING.md`](VERSIONING.md) for how a version gets cut.

Legend: `[x]` shipped · `[ ]` planned · `[~]` in progress · `[!]` blocked

---

## Shipped

### 0.15.0 — read-back verification (current)

"Success" now means the copy exists on the platform, not that an API returned 201.

- [x] Post-publish read-back for every publication record
      (`publications.verify_state`: verified / failed / unsupported / unverified)
- [x] Protocol read-back: Dev.to (article API) and GitHub Pages (contents API)
- [x] Page read-back: open the published URL, compare title and body against the source
- [x] `POST /publications/{id}/verify` to re-check on demand
- [x] Publish centre shows the read-back state with a "re-check" action
- [x] Real publish verification script + dated record in `docs/verification/`
- [x] CI: docker-smoke and Windows desktop-build jobs added
- [x] MSI + NSIS installers built, installed, launched and uninstalled on Windows

Four real defects were found by running this against a live account, none of which any
mock had caught: future-dated front-matter silently dropped by Jekyll, read-back
recomputing the URL instead of reusing the published one, site suffixes breaking title
comparison, and the editor-only "append detection" rule rejecting valid published pages.

### 0.14.0 — full-loop verification

Every item below was driven by running the product, not by reading the code.

- [x] Rest and CLI coverage: 91 REST endpoints, 32 MCP tools, 34 CLI commands exercised
- [x] SSE reliability: monotonic event ids, 256-event replay ring, `Last-Event-ID` gap
      fill, `resync` when the gap exceeds the window
- [x] Article CRUD now emits SSE events (previously only tasks/publish did)
- [x] MCP `streamable-http` transport (supersedes HTTP+SSE since 2025-03-26)
- [x] Markdown export with YAML front-matter (API + CLI + editor menu)
- [x] Offline tag suggestions with synonym de-duplication
- [x] Real Prometheus histograms (were all zeros)
- [x] Security headers on by default; verified in a real browser with zero CSP violations
- [x] Single-file executable built and smoke-tested (7/7 checks)
- [x] Defect sweep: `/api/info` empty routes, fake AI-ready state, hardcoded editor
      theme, PIL excluded from the bundle, webhook test false success

### 0.12.x — hardening

- [x] Network boundary: public-IP validation, connection pinning, redirect revalidation
- [x] Browser fetch/screenshot restored with Chromium DNS pinning (was disabled)
- [x] MCP SSE loopback default, remote requires bearer token
- [x] Graceful shutdown ordering with connection preservation on failure
- [x] Unified data root for config, database, media, profiles
- [x] Documentation claims aligned with verified behaviour

### 0.10 – 0.11 — platform coverage

- [x] WeChat, GitHub Pages, Dev.to
- [x] Telegraph, Xiaohongshu
- [x] 15 adapters with capability matrix

### 0.8 – 0.9 — foundation

- [x] Media pipeline with content-addressed dedupe
- [x] SQLite FTS5 full-text search
- [x] Platform rate-limit cooldowns
- [x] External MCP bridge
- [x] Service split into mixins

---

## Next

### 0.15.0 — Editorial workflow depth

- [ ] Draft collaboration: comments and suggestion mode on article bodies
- [ ] Publish diff preview: show what will change on each platform before syncing
- [ ] Bulk operations: retag, reschedule, re-publish across a filtered article set
- [ ] RSS and JSON Feed output so the library is subscribable, not just exportable

### 0.16.0 — Publish failure forensics

- [x] Read back the published URL after a successful submit, compare title and body,
      store `publications.verified_at` — shipped in 0.15.0
- [ ] On publish failure, capture a Playwright trace plus screenshot for replay

### 0.13.0 — Verification and packaging

The theme is **"prove it, then ship it"**: turn every documented capability into a
recorded verification, and make the desktop build reproducible.

- [ ] Record real publish verification for the protocol platforms that need no browser
      (cnblogs, Dev.to, GitHub Pages, Telegraph) — `docs/verification/`
- [ ] Reproducible Tauri build: sidecar staging script, CI job on a Windows runner
- [ ] Docker smoke test in CI (compose up, health check, authenticated API call)
- [ ] Regenerate all README screenshots from the current UI
- [ ] Publish `docs/verification/` index and link it from README

**Exit criteria:** every README capability row has either an automated test or a dated
verification record; `docker compose up` is exercised in CI; `tauri build` produces an
artifact on at least one platform.

### 0.14.0 — Editorial workflow depth

- [ ] Draft collaboration: comments and suggestion mode on article bodies
- [ ] Schedule templates: per-platform time slots with timezone handling
- [ ] Publish diff preview: show what will change on each platform before syncing
- [ ] Bulk operations: retag, reschedule, re-publish across a filtered article set

### 0.15.0 — Observability and operations

- [ ] Structured JSON logs with request correlation IDs
- [ ] Prometheus-compatible `/metrics` export
- [ ] Task timeline view in the UI (per-task step history, retries, timings)
- [ ] Backup/restore UI with integrity verification

---

## Backlog (unscheduled)

- [ ] Additional adapters: 掘金 API channel, Substack, Hashnode, Medium
- [ ] Image generation integration for cover art
- [ ] Multi-user mode with per-account permissions
- [ ] Webhook delivery retry with exponential backoff and dead-letter queue
- [ ] Content calendar heatmap

---

## Explicitly out of scope

| Item | Why not |
|---|---|
| SaaS hosting | The product's value is self-hosting; a hosted tier would fork the architecture |
| CAPTCHA solving | Account-ban risk and ToS violation; the product pauses for a human instead |
| Bulk scraping of arbitrary sites | Turns a publishing tool into a scraping proxy; SSRF surface never closes |
| Plugin marketplace | Adds a supply-chain risk surface without solving a user problem yet |
