# Verification records

Dated evidence for claims that cannot be proven by an automated test alone — real
publishing against a live platform, installers actually installed and launched,
deployment paths actually exercised. Every file states the commit, the machine, the
commands, and what was **not** verified.

Read these as "someone ran this and recorded the output", not as a capability brochure.
A capability row in the README is only trustworthy when it links here or has a test.

| Date | Version | Covers |
|---|---|---|
| [2026-11-07](2026-11-07-release-gates.md) | 0.15.0 | Real GitHub Pages publish + read-back (found 2 real defects), MSI/NSIS install & launch, CI root cause, Docker limitation |
| [2026-11-04](2026-11-04-release-gates.md) | 0.13.2 | Eight gates re-run, publish-settings feature round-trip, real draft chain |
| [2026-11-03](2026-11-03-release-gates.md) | 0.13.0 | Release gates for the repository-hygiene/versioning release |
| [2026-11-02](2026-11-02-regression.md) | — | Broad regression sweep across REST/CLI/MCP surfaces |

## How to reproduce the 0.15.0 publish verification

```bash
# target repo must exist and have Pages enabled (Settings -> Pages -> main / root)
uv run --project backed python scripts/verify_real_publish.py \
    --repo your-name/your-verify-repo
```

The script publishes a dated article, reads it back through an independent GET,
compares title and body, checks the public URL, updates it in place, verifies again,
then removes the artifact unless `--keep` is passed. Credentials are read from the
local secret store and never printed.

## What belongs here vs. in tests

- **tests** — behaviour that must not regress, runs on every commit, no network.
- **verification records** — one-off, dated proof against real external systems where
  automation is impossible or too costly (live platform accounts, OS installers,
  container runtimes). These go stale; the date is part of the meaning.
