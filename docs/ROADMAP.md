# Roadmap

Living document. Reviewed monthly; every item states the user-visible outcome, not the
implementation. See [`VERSIONING.md`](VERSIONING.md) for how a version gets cut.

Legend: `[x]` shipped · `[ ]` planned · `[~]` in progress · `[!]` blocked

---

## Shipped

### 0.12.x — hardening (current)

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
