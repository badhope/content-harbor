# Security Policy

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private advisory flow:

<https://github.com/badhope/harbor/security/advisories/new>

If that is unavailable, email the maintainer listed on the repository profile with the
subject `[harbour security]` and encrypt nothing sensitive in the first message — say
what you found and ask for a channel.

Please include:

- Affected version (`GET /health` or the git tag)
- The request, file path or configuration that triggers it
- Impact: what an attacker gains, and from what position (network, local user,
  malicious platform page)
- A minimal reproduction if you have one

## What to expect

| Stage | Target |
|---|---|
| Acknowledgement | 72 hours |
| Initial assessment (severity + affected versions) | 7 days |
| Fix or mitigation for critical issues | 30 days |
| Public disclosure | After a fixed release ships, coordinated with you |

Credit is given in the release notes unless you ask to stay anonymous.

## Supported versions

Only the latest release receives security fixes. There is no long-term support branch —
the project is pre-1.0 and the maintenance cost of backporting outweighs the benefit.

## Security model

Harbour runs on your machine and holds platform session cookies, API tokens and article
content. The threat model assumes:

- **The host is trusted.** Anyone with filesystem access to `HUB_DATA_DIR` can read the
  SQLite database and browser profiles. Protect it with OS permissions.
- **The network is not.** The HTTP surface may be exposed to a LAN or the internet, so
  every route is authenticated outside loopback.
- **Platform pages are not.** A malicious page loaded in the browser channel could try to
  reach internal services; the browser channel therefore pins the target host to a
  validated public IP and re-checks every sub-resource.

### Boundaries that are enforced

| Boundary | Mechanism |
|---|---|
| REST authentication | `HARBOUR_API_TOKEN`; loopback exemption controlled by `TRUSTED_NETS` |
| Reverse proxy trust | `X-Forwarded-For` honoured only from `TRUSTED_PROXIES` |
| Outbound HTTP (importer, media) | public-IP validation, connection pinning, per-hop redirect revalidation, size and concurrency caps |
| Browser channel | Chromium `--host-resolver-rules` pinning + request-level guard |
| Markdown import | confined to the import root, `.md`/`.markdown` only, size cap, sensitive names rejected |
| External MCP servers | child process gets a minimal environment allowlist, never the parent's secrets |
| MCP SSE | loopback bind by default; remote requires `HARBOUR_MCP_TOKEN` |
| Secrets at rest | webhook secrets encrypted with `FERNET_KEY`; session cookies in local profiles only |
| Production startup | fails fast when `HARBOUR_API_TOKEN` / `SECRET_KEY` are missing |

### Boundaries that are *not* enforced

Be aware of these before deploying on an untrusted network:

- **No rate limiting per account.** There is a token-bucket limiter, but it is global,
  not per user.
- **No audit trail of who changed what.** Single-user product; the HTTP audit log records
  requests, not identity.
- **Browser profiles are not encrypted at rest.** They are ordinary Chromium profile
  directories.
- **MCP tools run with the app's full authority.** A connected agent can publish, delete
  and reconfigure. Scope tokens and MCP access accordingly.

## Handling credentials in this repository

- Never commit `.env`, `config.json`, cookies, profiles or screenshots containing tokens.
- `scripts/scan_secrets.py` runs in CI and blocks known token prefixes, PEM headers and
  high-entropy assignments.
- If a credential is committed, treat it as public: rotate it first, then rewrite history.
  Deleting the commit is not enough.
