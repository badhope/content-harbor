# -*- coding: utf-8 -*-
"""真实发布端到端验证（GitHub Pages 协议平台）。

不 mock 任何 HTTP：用 Harbor 自己的 GitHubPagesAdapter 发一篇到真仓库，
再回读比对仓库层与站点层，最后原地更新并再次验证。

用法（在仓库根执行）：
    uv run --project backed python scripts/verify_real_publish.py
    uv run --project backed python scripts/verify_real_publish.py --repo owner/repo --keep

默认目标仓库 badhope/harbor-publish-verify；--keep 保留产出（默认删掉，
避免验证脚本往目标仓库堆测试文章）。

凭据只从 ~/.agent-secrets/accounts.json 读取，不落盘、不打印。
产出证据写 .tmp/e2e/publish_real_result.json，供 docs/verification 引用。
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backed"))

import argparse  # noqa: E402

_ap = argparse.ArgumentParser()
_ap.add_argument("--repo", default="badhope/harbor-publish-verify",
                 help="目标仓库 owner/name（需已启用 Pages）")
_ap.add_argument("--keep", action="store_true", help="保留发布的验证文章")
_ARGS = _ap.parse_args()
_OWNER, _REPO = _ARGS.repo.split("/", 1)

# 用验证仓库作为发布目标；token 从凭据库读，不落盘、不打印
CREDS = json.load(open(os.path.expanduser("~/.agent-secrets/accounts.json"), encoding="utf-8"))
TOKEN = CREDS["platforms"]["github"]["accounts"]["badhope"]["token"]
os.environ["GITHUB_TOKEN"] = TOKEN

# 配置写到工作区内的临时文件，**绝不碰用户真实的 config.json**。
# 第一版直接改 backed/config.json 再还原：中途异常退出就会把真实 token 留在
# 磁盘上（密钥扫描当场报了一次），而且并发跑两个验证会互相踩。用
# HARBOR_CONFIG_FILE 指到独立文件，进程结束即丢。
_TMP_CFG = ROOT / ".tmp" / "verify-real-publish" / "config.json"
_TMP_CFG.parent.mkdir(parents=True, exist_ok=True)
_TMP_CFG.write_text(json.dumps({
    "platforms": {"github": {
        "token": TOKEN, "owner": _OWNER, "repo": _REPO,
        "branch": "main", "path_prefix": "",
        "site_base": f"{_OWNER}.github.io/{_REPO}",
    }}
}, ensure_ascii=False, indent=2), encoding="utf-8")
os.environ["HARBOR_CONFIG_FILE"] = str(_TMP_CFG)

from service.publishing.adapters.github_pages import GitHubPagesAdapter  # noqa: E402

STAMP = time.strftime("%Y-%m-%d %H:%M:%S")
ARTICLE = {
    "title": f"Harbor real publish verification {STAMP}",
    "content_md": (
        f"# Harbor real publish verification {STAMP}\n\n"
        "This page was published by the Harbor GitHub Pages adapter as part of a\n"
        "dated, reproducible release verification. It is not a mock: the adapter\n"
        "committed this file through the GitHub Contents API and then read it back\n"
        "to confirm the copy exists on the platform.\n\n"
        f"Verification timestamp: {STAMP}\n\n"
        "If you are reading this on the rendered site, the whole chain worked:\n"
        "adapter -> commit -> Pages build -> public URL.\n"
    ),
    "tags": "verification,harbor",
    "summary": "Dated real publish verification for Harbor 0.15.0",
}

adapter = GitHubPagesAdapter()
results = {}

print("=== 1. check_auth ===")
results["auth"] = adapter.check_auth()
print("auth:", results["auth"])

print("\n=== 2. publish (real commit) ===")
pub = adapter.publish(None, ARTICLE, {})
print(json.dumps(pub, ensure_ascii=False, indent=2))
results["publish"] = pub

print("\n=== 3. read_back (independent GET) ===")
ev = adapter.read_back(pub, ARTICLE)
print(json.dumps({k: (v[:200] if isinstance(v, str) else v) for k, v in ev.items()},
                 ensure_ascii=False, indent=2))
results["readback"] = ev

print("\n=== 4. verify_readback (compare) ===")
ok, ev2 = adapter.verify_readback(pub, ARTICLE)
print("verified:", ok)
print("detail:", ev2.get("detail"))
results["verified"] = ok

print("\n=== 5. list_articles (does it show up?) ===")
items = adapter.list_articles(None, 50)
hit = [i for i in items if i.get("post_id") == pub.get("post_id")]
print("listed:", len(items), "| our post present:", bool(hit))
results["listed"] = bool(hit)

print("\n=== 6. update (in-place) ===")
ARTICLE2 = dict(ARTICLE)
ARTICLE2["content_md"] = ARTICLE["content_md"] + "\n\nUpdated in place by the same adapter.\n"
upd = adapter.update(None, pub, ARTICLE2)
print("update:", upd)
ev3 = adapter.read_back(pub, ARTICLE2)
ok3, ev4 = adapter.verify_readback(pub, ARTICLE2)
print("post-update readback verified:", ok3, "|", ev4.get("detail"))
results["update"] = upd
results["update_verified"] = ok3

# ---- 清理：默认删掉本次发布的验证文章，避免目标仓库堆垃圾 ----
if not _ARGS.keep:
    import urllib.request as _u
    def _api(method, url, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = _u.Request(url, data=data, method=method,
                         headers={"Authorization": "Bearer " + TOKEN,
                                  "User-Agent": "harbor-verify",
                                  "Accept": "application/vnd.github+json",
                                  "Content-Type": "application/json"})
        try:
            return json.loads(_u.urlopen(req, timeout=30).read())
        except Exception as e:
            return {"_err": str(e)[:120]}
    # 用 Contents API 删：拿 sha → DELETE。失败不改变验证结论（文章已经验过了）
    got = _api("GET", f"https://api.github.com/repos/{_OWNER}/{_REPO}/contents/{pub['post_id']}")
    if isinstance(got, dict) and got.get("sha"):
        _api("DELETE", f"https://api.github.com/repos/{_OWNER}/{_REPO}/contents/{pub['post_id']}",
             {"message": "cleanup: verification artifact", "sha": got["sha"], "branch": "main"})
        print("cleanup: removed", pub["post_id"])

# 临时配置用完即删（含 token，不能留在磁盘上）
try:
    _TMP_CFG.unlink(missing_ok=True)
except Exception:
    pass
print("\n=== SUMMARY ===")
print(json.dumps({"auth": results["auth"], "publish_ok": bool(pub.get("post_id")),
                  "readback_found": results["readback"].get("found"),
                  "verified": results["verified"], "listed": results["listed"],
                  "update_verified": results["update_verified"]}, ensure_ascii=False))
_OUT = ROOT / ".tmp" / "verify-real-publish"
_OUT.mkdir(parents=True, exist_ok=True)
(_OUT / "publish_real_result.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
