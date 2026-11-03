# Harbor Project Management

> Audience: maintainers. This is the operating manual for the repository — how work is
> planned, how quality is gated, how the five remotes stay in sync, and what "done"
> means. For version numbering and release steps see [`VERSIONING.md`](VERSIONING.md).

## 1. Product definition

**One sentence.** Harbor is a self-hosted publishing hub that writes with AI and ships
one article to fifteen Chinese and international platforms from a single screen, keeping
every published copy in sync.

**Who it is for**

| Persona | What they need | What Harbor gives them |
|---|---|---|
| Chinese tech writer publishing on 5+ platforms | Write once, avoid re-pasting | Multi-platform publish with per-platform capability matrix |
| Indie developer running a blog farm | Content pipeline without SaaS lock-in | Self-hosted SQLite + local browser profiles, no vendor account |
| Team maintaining a content operation | Auditability and human control | AIGC gate, human-review pause/resume, version snapshots, webhooks |
| AI agent integrator | Programmatic publishing | 32 MCP tools over stdio, REST API, SSE task stream |

**Non-goals.** Not a CMS, not a social scheduler, not a hosting platform. It does not
scrape at scale, does not defeat CAPTCHAs, and does not store platform passwords.

**Success criteria for a release**

1. A new user can go from clone to first draft in under 10 minutes following the README.
2. Every capability listed in the README maps to a passing test or a documented manual
   verification recorded in `docs/verification/`.
3. No documented claim is broader than what the code does.

## 2. Repository layout contract

| Path | Owns | Rule |
|---|---|---|
| `backed/` | FastAPI service, adapters, scheduler, MCP bridge | Domain logic only; no HTTP concerns in adapters |
| `web/` | Vue 3 SPA + Tauri shell | Presentation only; no business rules |
| `deploy/` | Dockerfile, compose, nginx | Must build from repo root context |
| `docs/` | Architecture, guides, audits, articles | Any `.md` not belonging to the root contract lives here |
| `scripts/` | Dev/ops tooling | Each script has a `--help` and is referenced by a doc |
| root | README, CHANGELOG, LICENSE, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT | Exactly these seven, nothing else |

Root-level markdown beyond the seven above is a layout violation. Long-form articles,
audit snapshots and design notes belong under `docs/`.

## 3. Branch model

Trunk-based. `main` is the only long-lived branch.

```
main ──●──●──●──●──●──●──►   always green, always deployable
        \    /
         ●──●   short-lived topic branch (≤ 3 days)
```

| Branch | Purpose | Lifetime | Merge rule |
|---|---|---|---|
| `main` | Trunk | permanent | Protected; CI must pass |
| `feat/<slug>` | New capability | ≤ 3 days | Squash-merge via PR |
| `fix/<slug>` | Defect / security | ≤ 2 days | Squash-merge via PR |
| `docs/<slug>` | Documentation only | ≤ 1 day | Squash-merge via PR |
| `chore/<slug>` | Tooling, deps | ≤ 1 day | Squash-merge via PR |

**Rules**

- Never commit directly to `main` for anything larger than a typo.
- Delete the branch after merge — local and remote.
- No `develop`, no `release/*`, no long-lived environment branches. Tags carry releases.

## 4. Definition of Done

A change is done only when **all** of the following hold. Partial completion is
reported as partial, never as done.

- [ ] Behaviour is covered by a test that fails without the change.
- [ ] `uv run --project backed ruff check backed` passes.
- [ ] `uv run --project backed pytest -q` passes (full suite, not a subset).
- [ ] `pnpm --dir web test` and `pnpm --dir web lint` pass; `pnpm --dir web build` succeeds.
- [ ] Version consistency check passes (`scripts/bump_version.py --check`).
- [ ] No secret, token, credential or personal path added to the diff.
- [ ] Documentation touched by the change is updated in the same commit.
- [ ] If the UI changed, screenshots in `docs/screenshots/` are regenerated.
- [ ] Commit message states the user-visible effect.

## 5. Quality gates

Three layers, cheapest first. A change must clear all three before it reaches `main`.

| Layer | What it catches | Where it runs | Blocking |
|---|---|---|---|
| **L1 static** | syntax, import errors, undefined names, lint | pre-commit + CI | yes |
| **L2 test** | behaviour regressions | CI | yes |
| **L3 smoke** | the app actually boots and serves | CI + release checklist | yes |

`scripts/check_all.sh` (POSIX) and `scripts/check_all.ps1` (Windows) run all three layers
locally and mirror CI exactly. If local and CI disagree, CI wins and the script is fixed.

### What is deliberately *not* gated

- `pip-audit` runs in report-only mode: a fresh CVE in a transitive dependency must not
  block an unrelated bugfix, but it is surfaced on every run.
- ESLint runs with a warning ceiling rather than zero warnings, because the existing Vue
  templates have stylistic warnings that carry no correctness signal. Errors always fail.

## 6. Remote repository matrix

Five remotes, one canonical. **GitCode is the primary**; the rest are mirrors with their
own history.

| # | Host | Repository | Role | Extra commits to preserve |
|---|---|---|---|---|
| 1 | GitCode | `badhope/harbor` | **Primary** — canonical `main`, tags, releases | — |
| 2 | Gitee | `badhope/harbor` | Mirror (China) | `remove-ci-workflow-on-mirror` |
| 3 | GitHub | `badhope/harbor` | Mirror (international, primary GitHub) | dependabot bumps |
| 4 | GitHub | `x33834/harbor` | Mirror (personal) | `remove CI workflow on mirror` |
| 5 | GitHub | `Morningstar202604/harbor` | Mirror (personal) | `remove CI workflow on mirror` |

### Why mirrors diverge

Each mirror carries exactly one local commit that strips `.github/workflows/ci.yml`,
because GitHub Actions on a mirror account would run duplicate jobs and burn that
account's minutes. Gitee mirrors the same decision. **These commits are intentional and
must be preserved.**

### Sync procedure

Never `--force` a mirror. Instead, merge `main` into the mirror's branch:

```bash
# 1. Add the mirror as a remote once
git remote add gitee https://gitee.com/badhope/harbor.git

# 2. Fetch and merge (not rebase) the mirror's own commit
git fetch gitee main
git checkout -B mirror-sync gitee/main
git merge main --no-edit          # keeps the "remove CI" commit in history

# 3. Push the merge commit
git push gitee mirror-sync:main

# 4. Return to trunk
git checkout main && git branch -D mirror-sync
```

Repeat per mirror. The result: identical source trees, each mirror retaining its own
CI-removal commit.

### README policy across mirrors

All five READMEs must be byte-identical. The only permitted difference is the CI badge
target, which is omitted entirely on mirrors that have no workflows. Repository
descriptions are set through each host's API and must match `docs/REPO_METADATA.md`.

## 7. Issue and PR workflow

- **Bug report** — must include: version (`/health`), OS, reproduction steps, expected vs
  actual, relevant log excerpt with secrets redacted.
- **Feature request** — must include: the persona, the job to be done, and what the user
  does today instead.
- **PR** — must reference an issue, describe the user-visible change, and list the gates
  that were run. Draft PRs are allowed; they must not be merged.
- Review SLA: first response within 72 hours. Security reports follow `SECURITY.md`
  instead and are handled privately.

## 8. Verification record

Capability claims need evidence. `docs/verification/` holds dated records of manual
checks that cannot be automated (real platform logins, Tauri packaging, Docker deploys).
Each record states: what was checked, on which commit, on which machine, with what
result, and what remains unverified.

```
docs/verification/
  2026-11-02-ui-walkthrough.md
  2026-11-02-browser-fetch.md
  2026-11-02-regression.md
```

A capability is **verified** only if such a record exists and is linked from the README
or the relevant guide. Otherwise it is **experimental** and must be labelled as such.

## 9. Technical debt policy

`docs/design/tech-debt.md` is the ledger. Rules:

1. Every deliberate shortcut gets an entry with an owner and a trigger condition.
2. Debt that blocks a release is fixed before the release, not after.
3. Debt older than two minor versions is either scheduled or explicitly accepted with a
   written rationale.
4. Removing debt is a valid standalone PR — it does not need a feature attached.

## 10. Cadence

| Activity | Frequency | Output |
|---|---|---|
| Dependency review | Monthly | Patch bumps merged; majors evaluated |
| Screenshot refresh | Every UI-affecting release | `docs/screenshots/*` |
| Verification sweep | Every minor release | `docs/verification/<date>-*.md` |
| Mirror sync | Every release | All five remotes at the same tree |
| Roadmap review | Monthly | `docs/ROADMAP.md` updated |
