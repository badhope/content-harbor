# Versioning & Release Policy

Harbor follows [Semantic Versioning 2.0.0](https://semver.org/). This document is the
single source of truth for how versions are chosen, cut, documented and published.

## Version format

```
MAJOR.MINOR.PATCH
  0  . 13 . 0
```

| Segment | Bumped when | Example trigger |
|---|---|---|
| **MAJOR** | A change breaks existing user-facing behaviour or stored data | Removing a platform adapter; changing the REST path; an irreversible DB migration |
| **MINOR** | New capability added, fully backwards compatible | New platform adapter; new MCP tool; new settings surface |
| **PATCH** | Bug fix, security fix, documentation or performance work only | SSRF bypass fix; corrected README claim; dependency patch |

### Pre-1.0 rule

While `MAJOR == 0`, the public API is still stabilising. MINOR bumps may include
breaking changes, but each one **must** be called out under a `### Breaking` heading in
`CHANGELOG.md`. Once `1.0.0` ships, strict SemVer applies.

## Where the version lives

The version is duplicated in five manifests. All five must agree before a tag is cut —
CI enforces this (`.github/workflows/ci.yml`, job `version-consistency`).

| File | Field |
|---|---|
| `backed/pyproject.toml` | `project.version` |
| `web/package.json` | `version` |
| `web/src-tauri/Cargo.toml` | `package.version` |
| `web/src-tauri/tauri.conf.json` | `version` |
| `backed/config/config_unified.py` | `project_version` |

Use `scripts/bump_version.py` to update all five atomically:

```bash
uv run --project backed python scripts/bump_version.py 0.13.0
```

## Changelog discipline

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

- Work in progress accumulates under `## [Unreleased]`.
- On release, rename that heading to `## [x.y.z] - YYYY-MM-DD` and open a fresh
  `## [Unreleased]` block above it.
- Entry headings use the same six categories: `Added`, `Changed`, `Deprecated`,
  `Removed`, `Fixed`, `Security`.
- Every entry states what a **user** observes, not which function was edited.

## Release checklist

Run top to bottom. Do not skip a step because "it passed last time".

1. **Freeze scope** — no new commits on `main` except release fixes.
2. **Verify green** — `bash scripts/check_all.sh` (or `pwsh -File scripts/check_all.ps1`)
   passes: backend tests, ruff, frontend lint, frontend tests, frontend build, version
   consistency, secret scan.
3. **Bump version** — `uv run --project backed python scripts/bump_version.py X.Y.Z`.
4. **Write the changelog** — move `[Unreleased]` content under the new version heading,
   add the release date, open a new empty `[Unreleased]`.
5. **Refresh screenshots** — if the UI changed, regenerate `docs/screenshots/*` with
   `uv run --project backed python scripts/capture_screenshots.py` and commit them.
6. **Commit** — `chore(release): vX.Y.Z`.
7. **Tag** — annotated tag, never lightweight:
   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z"
   ```
8. **Push** — `git push origin main && git push origin vX.Y.Z`.
9. **Sync mirrors** — push `main` and the tag to Gitee and all three GitHub mirrors
   (see `docs/PROJECT_MANAGEMENT.md` for the remote matrix).
10. **Publish release notes** — create the release on GitHub (primary) from the tag,
    reusing the changelog section verbatim.
11. **Post-release check** — verify the release page, README badge and mirror READMEs
    all show the new version.

## Hotfix path

A security or data-loss fix does not wait for the next planned minor.

1. Branch `fix/<slug>` from the release tag.
2. Fix + regression test that fails before and passes after.
3. Bump PATCH only.
4. Full `check_all` run.
5. Merge to `main`, tag, push, sync mirrors — same as above.

## What must never happen

- A tag without a matching `CHANGELOG.md` section.
- A release whose README still advertises an older version.
- Pushing to a mirror with `--force` (mirrors carry their own commits; see
  `docs/PROJECT_MANAGEMENT.md`).
- Claiming a capability in README that no test or manual verification covers.
