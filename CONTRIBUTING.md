# Contributing to Harbour

Thanks for taking the time. This guide is short on purpose — the rules that matter are
enforced by CI, not by reviewers.

## Before you start

- Bugs and feature ideas: open an issue using one of the templates.
- Security problems: **do not open an issue**. Follow [SECURITY.md](SECURITY.md).
- Read [docs/PROJECT_MANAGEMENT.md](docs/PROJECT_MANAGEMENT.md) for the branch model and
  the definition of done.

## Workflow

```bash
# 1. Fork, then clone your fork
git clone https://github.com/<you>/harbor.git
cd harbor

# 2. Branch from main
git checkout -b fix/csrf-on-webhook-create

# 3. Set up both halves
cd backed && uv sync && cd ..
cd web && pnpm install && cd ..

# 4. Make the change, add a test that fails without it
# 5. Run every gate
bash scripts/check_all.sh

# 6. Commit and push
git push origin fix/csrf-on-webhook-create
```

Then open a pull request against `main` and fill in the template.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/) with an imperative subject.

```
fix(security): reject redirects that resolve to private addresses

The importer validated the initial URL but followed redirects blindly...
```

Types in use: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `ci`.
Scopes: `api`, `web`, `publishing`, `security`, `deploy`, `docs`, `ci`.

Rules:

- Subject line states the **user-visible effect**, not the file you touched.
- Body explains *why*. The diff already shows *what*.
- One logical change per commit. A commit that fixes a bug and renames a module is two
  commits.

## What gets reviewed

A reviewer will check, in this order:

1. **Does it do what it says?** The test must fail before and pass after.
2. **Does it break something else?** Full suite, not a subset.
3. **Is the security surface larger?** New network calls, new file reads, new env vars.
4. **Is the documentation still true?** A behaviour change without a doc change is
   incomplete.
5. **Is it readable six months from now?** Names, module boundaries, comment density.

Style alone is never a reason to block a PR. Correctness and security always are.

## Adding a platform adapter

Adapters live in `backed/service/publishing/adapters/`. Each one declares a capability
matrix — publish, draft, update, list, tags, cover, question — and the UI greys out what
it does not support.

1. Implement the adapter against the base interface.
2. Register it in `adapters/__init__.py`.
3. Add it to `PLATFORM_CONNECTION.md` with its credential fields.
4. Add a contract test; if it needs a browser, add a DOM fixture under
   `backed/tests/fixtures/dom/`.
5. Run `GET /api/v1/hub/adapters/probe` and confirm zero errors for your platform.

Do not claim a capability the platform does not have. A greyed-out button is honest; a
button that fails is not.

## Documentation changes

- Root markdown is limited to: `README.md`, `README.zh-CN.md`, `CHANGELOG.md`,
  `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `LICENSE`, `AGENT.md`,
  `PLATFORM_CONNECTION.md`.
- Long-form articles, audits and design notes go under `docs/`.
- `README.md` and `README.zh-CN.md` must stay structurally parallel: same sections,
  same order, same claims.
- `backed/tests/test_docs_links.py` fails on broken relative links — run it after moving
  any file.

## Code of conduct

Participation implies agreement with [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
