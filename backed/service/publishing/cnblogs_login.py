# -*- coding: utf-8 -*-
"""博客园「浏览器登录 → 自动抠 MetaWeblog 令牌」引导模块。

为什么需要它（用户痛点）：
  博客园的发布走 MetaWeblog 协议，但协议要三样东西：
    endpoint = https://rpc.cnblogs.com/metaweblog/<博客地址名>
    username = 登录用户名（不是邮箱，也不是昵称）
    token    = 「访问令牌」（密码登录已取消，填密码必挂）
  这三样都在「设置 → 其他设置」里，且需要先登录才能看到/开启。
  之前的做法是"让用户把这三样贴给我"——这才是用户说的"方法错了"。
  正确做法：拿账号+密码用浏览器登一次，登录态存盘，再从设置页把三样自动抠出来写进 config.json。
  之后发布走纯协议，再也不开浏览器、再也不碰验证码。

验证码现实（别绕）：
  博客园登录叠了 阿里「智能验证」复选框 + Google reCAPTCHA(invisible)。
  前者是纯复选框，拟人点一下即可；后者是风险评分，环境干净时常静默放行，
  但反复尝试会被升级成「图片拼图」——那一步脚本解不了，只能交人工点一次。
  登录态持久化，所以"过验证"是一次性的，不是每次都来。
"""

import json
import time
from pathlib import Path

from service.publishing import humanize  # noqa


ROOT = Path(__file__).resolve().parent.parent
SIGNIN = "https://account.cnblogs.com/signin"
SETTINGS = "https://i.cnblogs.com/settings"


# ---------------------------------------------------------------------------
# 页面元素定位（已对照 account.cnblogs.com/signin 真实 DOM 校准）
# ---------------------------------------------------------------------------
ALIYUN_HINTS = [
    "#aliyunCaptcha-checkbox-icon",
    "#aliyunCaptcha-checkbox-background",
    ".aliyun-captcha-checkbox",
    "[class*=aliyunCaptcha] [class*=checkbox]",
]
RECAP_ANCHOR_HINTS = [
    "#recaptcha-anchor",
    ".recaptcha-checkbox-border",
    "label[for=recaptcha-anchor]",
]


def _visible_inputs(page):
    """返回 (用户名输入框, 密码输入框)，只认可见的。"""
    user = pw = None
    for loc in page.locator("input").all():
        try:
            if not loc.is_visible():
                continue
            t = (loc.get_attribute("type") or "text").lower()
            ph = (loc.get_attribute("placeholder") or "").lower()
            if t == "password":
                pw = loc
            elif t in ("text", "email", "tel") or "用户名" in ph or "邮箱" in ph:
                if user is None:
                    user = loc
        except Exception:
            continue
    return user, pw


def _click_aliyun(page):
    """主文档 + 各 iframe 里找阿里验证码复选框，拟人点。"""
    for fr in [page] + list(page.frames):
        for sel in ALIYUN_HINTS:
            try:
                loc = fr.locator(sel)
                if loc.count():
                    b = loc.first.bounding_box()
                    if b:
                        humanize.human_click(
                            page, x=b["x"] + b["width"] / 2,
                            y=b["y"] + b["height"] / 2)
                        return True
            except Exception:
                continue
    return False


def _click_recaptcha_checkbox(page):
    """reCAPTCHA 挑战帧里若有「我不是机器人」复选框就点。"""
    for fr in page.frames:
        if "api2" not in (fr.url or ""):
            continue
        for sel in RECAP_ANCHOR_HINTS:
            try:
                loc = fr.locator(sel)
                if loc.count():
                    loc.first.click()
                    return True
            except Exception:
                continue
    return False


def _body(page):
    try:
        return (page.inner_text("body") or "").replace("\n", " ")
    except Exception:
        return ""


def _has_bframe(page):
    return any("bframe" in (f.url or "") for f in page.frames)


def login_browser(br, username, password, on_captcha="handoff", timeout=600):
    """用浏览器登录。返回 dict：{outcome, ...}。

    outcome 取值：
      success           登录成功（已跳转到 i.cnblogs.com），cookies 已存盘
      wrong_creds      服务端明确「用户名或密码错误」
      no_user          服务端「用户名不存在」
      captcha_image    弹了 reCAPTCHA 图片拼图，脚本解不了，需人工点一次
      timeout          等了 timeout 秒仍没结果
      error            异常

    on_captcha:
      handoff  遇到图片拼图时返回 captcha_image（调用方拿着窗口交人工）
      abort    直接放弃
    """
    ctx = br.ctx
    page = br.new_page()
    out = {"outcome": "error"}
    try:
        page.goto(SIGNIN, timeout=60000, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("input", state="visible", timeout=20000)
        except Exception:
            out = {"outcome": "error", "detail": "登录表单未加载"}
            return out
        time.sleep(1.2)

        u, p = _visible_inputs(page)
        if not u or not p:
            out = {"outcome": "error", "detail": "找不到用户名/密码输入框"}
            return out

        # 填凭据
        u.click()
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
        page.keyboard.type(username, delay=45)
        p.click()
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
        page.keyboard.type(password, delay=45)
        out["typed"] = {"username": u.input_value()[:3] + "***", "password": "***"}
        time.sleep(0.4)

        page.locator("button:has-text('登')").first.click()
        time.sleep(2)

        deadline = time.time() + timeout
        while time.time() < deadline:
            text = _body(page)
            url = page.url
            if "i.cnblogs.com" in url:
                # 存 cookies
                try:
                    cookies = ctx.cookies()
                    br.profile_dir.mkdir(parents=True, exist_ok=True)
                    (br.profile_dir / "cookies.json").write_text(
                        json.dumps(cookies, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                    out["cookies_saved"] = True
                except Exception as e:
                    out["cookies_err"] = str(e)
                out["outcome"] = "success"
                return out
            if "用户名或密码错误" in text:
                out["outcome"] = "wrong_creds"
                return out
            if "用户名不存在" in text:
                out["outcome"] = "no_user"
                return out
            # 阿里验证码
            if "确认您不是机器人" in text or "请完成安全验证" in text:
                _click_aliyun(page)
                time.sleep(3.5)
                continue
            # reCAPTCHA 图片拼图
            if _has_bframe(page):
                if on_captcha == "abort":
                    out = {"outcome": "captcha_image", "aborted": True}
                    return out
                # 先试有没有可点的复选框
                if _click_recaptcha_checkbox(page):
                    time.sleep(4)
                    continue
                out = {"outcome": "captcha_image",
                       "detail": "reCAPTCHA 图片拼图，需人工点一次；"
                                 "窗口已开着，点完后自动继续"}
                return out
            time.sleep(1)

        out = {"outcome": "timeout", "final_url": page.url,
               "final_text": _body(page)[:160]}
        return out
    except Exception as e:
        out = {"outcome": "error", "detail": f"{type(e).__name__}: {e}"}
        return out
    finally:
        try:
            page.close()
        except Exception:
            pass


def extract_metaweblog(page, save_to=None, login_username=None):
    """登录后从「设置 → 其他设置」抠 MetaWeblog 三件套。

    login_username：登录时用的用户名。MetaWeblog 的 username 是它，**不是**
    blogApp（博客地址名可以自定义改，跟登录名不一定一致），两者别混。

    返回 dict：{endpoint, username, token}（缺哪样标 None），或 {"found": False, ...}
    能抠到就直接写进 save_to(config.json)；抠不到则提示人工复制的 URL。
    """
    page.goto(SETTINGS, timeout=60000, wait_until="domcontentloaded")
    time.sleep(2.5)

    # 设置页可能有多个 tab，当前 tab 没有就点带"其他"字样的 tab
    try:
        for loc in page.locator("a,button,[role=tab]").all():
            try:
                if loc.is_visible() and "其他" in (loc.inner_text() or ""):
                    loc.click()
                    time.sleep(1.5)
                    break
            except Exception:
                continue
    except Exception:
        pass

    html = page.content()

    res = {"found": False, "endpoint": None, "token": None, "blogapp": None,
           "username": login_username}

    # endpoint 形如 https://rpc.cnblogs.com/metaweblog/<博客地址名>
    import re
    m = re.search(r"https://rpc\.cnblogs\.com/metaweblog/[A-Za-z0-9_\-]+", html)
    if m:
        res["endpoint"] = m.group(0)
        res["blogapp"] = m.group(0).rsplit("/", 1)[-1]
        res["found"] = True

    # 访问令牌：只认 readonly 输入框 / name|id|placeholder 带 token 的，
    # 裸 textarea / class 模糊匹配会误抓搜索框之类
    sels = ("input[readonly]", "textarea[readonly]",
            "input[name*=token i]", "input[id*=token i]",
            "input[placeholder*=令牌]", "input[placeholder*=token i]")
    for sel in sels:
        try:
            for loc in page.locator(sel).all():
                val = (loc.input_value() or "").strip()
                if len(val) >= 8:
                    res["token"] = val
                    res["found"] = True
                    break
        except Exception:
            continue
        if res["token"]:
            break

    # 还没有就试着把「允许 MetaWeblog」勾上，再抠一次
    if not res["token"]:
        try:
            for loc in page.locator("input[type=checkbox]").all():
                try:
                    box = loc.bounding_box()
                    label = page.evaluate(
                        """(el) => {
                            const lb = el.closest('label, .mat-checkbox, li, div');
                            return lb ? lb.innerText : '';
                        }""", loc.element_handle())
                    if box and label and ("MetaWeblog" in label or "客户端" in label):
                        if not loc.is_checked():
                            loc.click()
                            time.sleep(2)
                        break
                except Exception:
                    continue
        except Exception:
            pass
        html2 = page.content()
        m2 = re.search(r"https://rpc\.cnblogs\.com/metaweblog/[A-Za-z0-9_\-]+", html2)
        if m2:
            res["endpoint"] = m2.group(0)
            res["blogapp"] = m2.group(0).rsplit("/", 1)[-1]
            res["found"] = True

    # 写 config.json：username 优先用登录名，blogApp 只是 endpoint 里那段
    if save_to and res.get("endpoint") and res.get("token"):
        cfg = {}
        if Path(save_to).exists():
            try:
                cfg = json.loads(Path(save_to).read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        cfg.setdefault("platforms", {})
        cfg["platforms"]["cnblogs"] = {
            "endpoint": res["endpoint"],
            "username": login_username or res.get("blogapp") or "",
            "token": res["token"],
        }
        Path(save_to).write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        res["config_written"] = str(save_to)

    if not res["found"]:
        res["manual_url"] = SETTINGS
        res["hint"] = ("自动抠取失败，请手动到该页开启「允许 MetaWeblog 博客客户端访问」"
                       "并复制 endpoint 与访问令牌，贴进 config.json 的 platforms.cnblogs")
    return res


if __name__ == "__main__":
    raise SystemExit("请使用 backed.cli bootstrap-cnblogs，密码不会通过命令行参数传递")
