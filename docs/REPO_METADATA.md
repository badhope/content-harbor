# Repository Metadata

Canonical copy for every host. Update this file first, then apply it through each
platform's API. Descriptions must be identical across all five remotes.

## Short description (≤ 120 chars, used by all hosts)

```
Self-hosted AI publishing hub — write once, ship to 15 platforms, keep every copy in sync.
```

Chinese variant (Gitee / GitCode description field):

```
自托管 AI 内容发布中枢：一次写作，分发到 15 个平台，已发布内容保持同步。
```

## Topics / tags

```
ai-writing  content-publishing  self-hosted  fastapi  vue3  mcp
multi-platform  playwright  sqlite  tauri  automation  chinese-content
```

## Homepage

```
https://badhope.github.io/harbor/
```

## Applying the metadata

### GitHub (three repositories)

```bash
gh api -X PATCH repos/badhope/harbor \
  -f description="Self-hosted AI publishing hub — write once, ship to 15 platforms, keep every copy in sync." \
  -f homepage="https://badhope.github.io/harbor/" \
  -f 'topics[]=ai-writing' -f 'topics[]=content-publishing' \
  -f 'topics[]=self-hosted' -f 'topics[]=fastapi' -f 'topics[]=vue3' \
  -f 'topics[]=mcp' -f 'topics[]=multi-platform' -f 'topics[]=playwright' \
  -f 'topics[]=sqlite' -f 'topics[]=tauri' -f 'topics[]=automation'
```

Repeat for `x33834/harbor` and `Morningstar202604/harbor`.

### Gitee

Gitee's API uses `PATCH /api/v5/repos/{owner}/{repo}` with `name`, `description`,
`homepage`, `path`. Topics are not exposed through the v5 API; set them in the web UI.

### GitCode

GitCode mirrors the GitLab v5 shape for repository updates:
`PUT /api/v5/repos/{owner}/{repo}`. Verify field names against the live API before
running — this host changes shape more often than the other two.
