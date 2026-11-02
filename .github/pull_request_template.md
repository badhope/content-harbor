## What this changes

<!-- One paragraph. State the user-visible effect, not the files touched. -->

## Why

<!-- Link the issue. If there is no issue, explain the problem this solves. -->

Closes #

## Type

- [ ] Bug fix
- [ ] New capability
- [ ] Security fix
- [ ] Documentation
- [ ] Refactor (no behaviour change)
- [ ] Tooling / CI

## Gates run

Paste the result of `bash scripts/check_all.sh` (or `pwsh -File scripts/check_all.ps1`).
A PR that says "tests pass" without output will be asked to re-run.

```
$ bash scripts/check_all.sh
...
```

- [ ] Backend tests
- [ ] Ruff
- [ ] Version consistency
- [ ] Secret scan
- [ ] Frontend lint
- [ ] Frontend tests
- [ ] Frontend build

## Checklist

- [ ] A test fails without this change and passes with it
- [ ] Documentation affected by this change is updated in the same commit
- [ ] Screenshots regenerated if the UI changed (`scripts/capture_screenshots.py`)
- [ ] No token, cookie, credential or personal path in the diff
- [ ] If this changes a documented capability, the README wording was checked against
      what the code actually does

## Screenshots / evidence

<!-- For UI changes: before and after. For behaviour changes: the command and its output. -->
