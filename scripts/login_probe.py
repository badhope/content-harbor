# -*- coding: utf-8 -*-
"""登录诊断：一条命令看清「这个平台的登录页能不能用」。

做三件事：
  1. 用内置浏览器（BuiltinBrowser / patchright）打开平台登录页
  2. 等二维码或验证码出现，导出二维码 PNG（data URI / canvas 都支持）
  3. 打印页面标题、当前 URL、是否已登录、是否检测到验证码

它**不代替人扫码**，也不做任何破解：只回答"二维码有没有真的生成"。
真实登录仍走应用里的「平台账号 → 去登录」。

用法：
    python scripts/login_probe.py --platform juejin
    python scripts/login_probe.py --platform csdn --wait 30 --out .tmp/csdn.png
    python scripts/login_probe.py --platform juejin --check-cookie   # 复检已保存的登录态

退出码：0 = 登录页可用（已登录或二维码已导出）；1 = 打不开或没等到二维码。
"""

import argparse
import base64
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backed"))

QR_JS = """() => {
  for (const el of document.querySelectorAll('img,canvas')) {
    const r = el.getBoundingClientRect();
    if (r.width < 100 || r.height < 100) continue;
    if (el.tagName === 'CANVAS') {
      try { return el.toDataURL('image/png'); } catch (e) { /* tainted */ }
      continue;
    }
    const src = el.getAttribute('src') || '';
    if (src.startsWith('data:image') && src.length > 800) return src;
  }
  return null;
}"""


def main() -> int:
    ap = argparse.ArgumentParser(description="平台登录页诊断（只读，不代替人扫码）")
    ap.add_argument("--platform", required=True, help="适配器 id，如 juejin / csdn / zhihu")
    ap.add_argument("--account", default="default")
    ap.add_argument("--headed", action="store_true", help="显示浏览器窗口（默认无头）")
    ap.add_argument("--wait", type=int, default=20, help="等二维码的最长秒数")
    ap.add_argument("--out", default="", help="二维码 PNG 输出路径")
    ap.add_argument("--check-cookie", action="store_true",
                    help="只复检本地 profile 的登录态，不重新打开登录页")
    args = ap.parse_args()

    from service.publishing.adapters.base import get_adapter
    from service.publishing import browser as B

    ad = get_adapter(args.platform)
    out = {"platform": args.platform, "login_url": ad.login_url,
           "needs_browser": ad.needs_browser}

    if not ad.needs_browser:
        try:
            ok = ad.check_auth(None)
            err = ""
        except Exception as e:      # aqg: top-level boundary（缺配置属正常态，不应抛堆栈）
            ok, err = False, f"{type(e).__name__}: {e}"
        print(json.dumps({**out, "mode": "api-only", "credential_ok": ok, "error": err},
                         ensure_ascii=False, indent=1))
        return 0 if ok else 1

    br = B.BuiltinBrowser(args.platform, args.account, headless=not args.headed)
    try:
        br.start()
        page = br.new_page()

        if args.check_cookie:
            target = ad.home_url or ad.login_url
            page.goto(target, timeout=60000, wait_until="domcontentloaded")
            ok = ad.check_auth(page)
            print(json.dumps({**out, "mode": "check-cookie", "target": target,
                              "logined": bool(ok)}, ensure_ascii=False, indent=1))
            return 0 if ok else 1

        page.goto(ad.login_url, timeout=60000, wait_until="domcontentloaded")
        qr_path = None
        waited = 0
        for i in range(max(1, args.wait)):
            waited = i + 1
            if ad.check_auth(page):
                break
            data = page.evaluate(QR_JS)
            if data and data.startswith("data:image"):
                qr_path = args.out or str(
                    REPO / ".tmp" / f"{args.platform}_login_qr.png")
                Path(qr_path).parent.mkdir(parents=True, exist_ok=True)
                Path(qr_path).write_bytes(base64.b64decode(data.split(",", 1)[1]))
                break
            import time
            time.sleep(1)

        out.update({
            "page_title": page.title(),
            "already_logged_in": bool(ad.check_auth(page)),
            "captcha": B.CaptchaPolicy.detect(page),
            "qr_png": qr_path,
            "waited_s": waited,
        })
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if (qr_path or out["already_logged_in"]) else 1
    except Exception as e:      # aqg: top-level boundary（CLI 入口，错误要打印而非堆栈）
        print(json.dumps({**out, "error": f"{type(e).__name__}: {e}"},
                         ensure_ascii=False, indent=1))
        return 1
    finally:
        try:
            br.close()
        except Exception:       # aqg: top-level boundary（关闭失败不掩盖上文结果）
            pass


if __name__ == "__main__":
    sys.exit(main())
