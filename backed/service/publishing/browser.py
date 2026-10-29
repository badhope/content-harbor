# -*- coding: utf-8 -*-
"""内置浏览器：程序自带 Chromium，登录态存在本地 profile，扫码一次长期有效。

跟"浏览器插件"路线的本质区别：
  - 插件方案：寄生在你日常用的 Chrome/Edge 里，浏览器一关就断
  - 本方案：程序自己开一个带独立 profile 的 Chromium，登录态存盘，
            可以后台常驻、可以被服务进程托管、可以给 AI 24 小时调用

每个平台一个独立 profile 目录，互不串味，也方便单独重登。

关于验证码/人机识别的立场（重要，别绕）：
  我们做的是**降低触发率 + 可交接的人工兜底**，不是对抗式破解。
  理由很简单：滑块/点选类验证码是服务端下发、前端 JS 加密上报轨迹的，
  纯脚本"硬解"的成功率随时会被风控升级归零，而且账号说封就封。
  真正稳的三层在下面 CaptchaPolicy 里写着了：
    1. 能走官方 API/令牌的绝不模拟浏览器（博客园、掘金都有）
    2. 登录态持久化，把"过验证"从每天一次压到一次性
    3. 反检测，让自动化浏览器看起来就是普通浏览器，从源头少弹验证
  剩下那点必须人工的，就交给 watchdog 暂停 + 人工过完自动续跑。
"""

import atexit
import json
import os
import queue as queue_mod
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from patchright.sync_api import sync_playwright   # 反检测 fork，替代手写 STEALTH_JS


# ---------------------------------------------------------------------------
# 专属浏览器线程（2026-09-22 存量体检定音之笔）
#
# playwright sync API 的 greenlet 与 asyncio"运行中循环"状态都绑定线程：
#   1) 连接的 dispatcher 挂起后，所在线程的 get_running_loop() 泄漏为真
#      → 同线程再起第二个 sync_playwright 直接报 "inside the asyncio loop"
#      （FastAPI 线程池复用线程 → zhihu/juejin check 500 的元凶）
#   2) 实例跨线程使用报 "Cannot switch to a different thread"
#      （池实例被 check 线程建、run 线程用 → E2E 发布失败的元凶）
# 对策：全局唯一后台线程串行执行一切 playwright 操作——greenlet 永远同线程，
# sync_playwright.__enter__ 一生只跑一次（首跑时线程上下文必然干净）。
# ---------------------------------------------------------------------------
_BW_QUEUE = None
_BW_THREAD = None
_BW_IDENT = [0]
_BW_LOCK = threading.Lock()


def _bw_worker(q):
    _BW_IDENT[0] = threading.get_ident()
    while True:
        task = q.get()
        if task is None:
            break
        fn, args, kwargs, box, ev = task
        try:
            box["r"] = fn(*args, **kwargs)
        except Exception as e:
            # 只捕 Exception，不捕 BaseException：
            # KeyboardInterrupt / SystemExit 属于中断，
            # 不应作为普通异常回传给调用方，否则 daemon 线程里 Ctrl-C 会被吞掉。
            box["e"] = e
        finally:
            ev.set()


def browser_thread_run(fn, *args, **kwargs):
    """在专属浏览器线程执行 fn；已在该线程则直接执行（防自锁死）。

    未捕获异常原样重抛给调用方线程；线程 daemon 化随进程退出。"""
    global _BW_QUEUE, _BW_THREAD
    if threading.get_ident() == _BW_IDENT[0]:
        return fn(*args, **kwargs)
    with _BW_LOCK:
        if _BW_THREAD is None or not _BW_THREAD.is_alive():
            _BW_QUEUE = queue_mod.Queue()
            _BW_THREAD = threading.Thread(target=_bw_worker, args=(_BW_QUEUE,),
                                           daemon=True, name="browser-worker")
            _BW_THREAD.start()
    box, ev = {}, threading.Event()
    _BW_QUEUE.put((fn, args, kwargs, box, ev))
    ev.wait()   # 登录需等扫码（最长 10 分钟），不设超时
    if "e" in box:
        raise box["e"]
    return box.get("r")


# ---------------------------------------------------------------------------
# 进程级共享 Playwright 驱动（2026-09-22 存量体检第二刀）
#
# 每实例一个 sync_playwright 的旧结构有死穴：第一个实例的 dispatcher 挂起后
# 所在线程的 get_running_loop() 泄漏为真，同线程起第二个 driver 直接报
# "inside the asyncio loop"（worker 线程把它从偶发变成必现：csdn 过、zhihu 炸）。
# 对策：驱动单例，一生只 __enter__ 一次（首跑上下文必然干净）；所有实例共享，
# launch_persistent_context 天然支持多上下文。关闭实例只关上下文、不动驱动。
# ---------------------------------------------------------------------------
_SHARED_PW = None
_SHARED_LOCK = threading.Lock()


def _get_shared_pw():
    global _SHARED_PW
    with _SHARED_LOCK:
        if _SHARED_PW is None:
            _SHARED_PW = sync_playwright().start()
        return _SHARED_PW


def _stop_shared_pw(*_a):
    global _SHARED_PW
    with _SHARED_LOCK:
        pw, _SHARED_PW = _SHARED_PW, None
    if pw is not None:
        try:
            pw.stop()
        except Exception:
            pass


# 注册顺序决定 LIFO：Hub.close_all（Hub 构造时注册，更晚）先关上下文，
# 这里最后停驱动，正好是正确顺序
atexit.register(_stop_shared_pw)


# ---------------------------------------------------------------------------
# 虚拟显示：Linux 服务器/容器里没有 X Server，有头浏览器（登录必须用）起不来。
# 这里自动兜一层 Xvfb，让"扫码登录"在云主机上也能用。
# ---------------------------------------------------------------------------
_XVFB = None


def _sock_path(disp):
    try:
        n = disp.split(":")[-1].split(".")[0]
        return Path(f"/tmp/.X11-unix/X{n}")
    except Exception:
        return None


def _xvfb_running(disp):
    """有没有 Xvfb 进程正在服务这个 display。"""
    try:
        r = subprocess.run(["pgrep", "-f", f"Xvfb {disp}"],
                           capture_output=True, text=True, timeout=5)
        return bool(r.stdout.strip())
    except Exception:
        return False


def _display_alive(disp):
    """DISPLAY 环境变量常常是"僵尸值"——容器里被塞了 :0，或者上次 Xvfb 死了
    但 /tmp/.X11-unix/X99 的 socket 文件还留着。两种情况都会让有头浏览器直接崩。

    所以按"进程在 + 真能连上"双重确认，socket 存在与否不作为判据。
    """
    if not disp:
        return False
    # 1) xdpyinfo 真连一次，最可靠
    if shutil.which("xdpyinfo"):
        try:
            r = subprocess.run(["xdpyinfo", "-display", disp],
                               capture_output=True, timeout=6)
            if r.returncode == 0:
                return True
        except Exception:
            pass
        # 连不上，但如果有 Xvfb 进程刚起来可能还没就绪，再给一次机会
        if _xvfb_running(disp):
            time.sleep(1.2)
            try:
                r = subprocess.run(["xdpyinfo", "-display", disp],
                                   capture_output=True, timeout=6)
                return r.returncode == 0
            except Exception:
                pass
        return False
    # 2) 没有 xdpyinfo，退而求其次看进程
    return _xvfb_running(disp)


def _reap_dead_socket(disp):
    """清掉残留的死 socket，否则 Xvfb 会认为该 display 已被占用而启动失败。"""
    sock = _sock_path(disp)
    if sock and sock.exists() and not _xvfb_running(disp):
        try:
            sock.unlink()
            return True
        except Exception:
            pass
    return False


def ensure_display():
    """拿到一个**真的能用**的 DISPLAY，不行就自己拉一个 Xvfb。

    踩过的坑（别改回去）：
      - 容器里 DISPLAY 常是僵尸值 :0，盲信它 → 有头浏览器崩
      - Xvfb 进程死了但 /tmp/.X11-unix/Xn 还在 → 新 Xvfb 起不来 → 全部候选跳过
        → 静默降级成无头 → 验证码永远过不去，还查不出为什么
    """
    global _XVFB
    if sys.platform not in ("linux", "linux2"):
        # Win/mac 有真桌面：返回 True 表示"有头可用"（不需要 DISPLAY/Xvfb）
        # 旧逻辑返回 os.environ.get("DISPLAY")，在 Windows 上 DISPLAY 未设 → None →
        # BuiltinBrowser.start() 误判有头不可用，降级无头，扫码窗口弹不出来
        return True

    # 先看当前 DISPLAY 是不是真活着
    cur = os.environ.get("DISPLAY")
    if cur and _display_alive(cur):
        return cur
    if cur:
        os.environ.pop("DISPLAY", None)        # 僵尸值，扔掉

    if not shutil.which("Xvfb"):
        return None                            # 没装，调用方自己降级

    # 号段给宽一点，并且先清死 socket，避免被上次残留卡死
    for num in list(range(99, 79, -1)) + list(range(120, 140)):
        disp = f":{num}"
        if _xvfb_running(disp):                # 有人在服务，直接复用
            if _display_alive(disp):
                os.environ["DISPLAY"] = disp
                return disp
            continue
        _reap_dead_socket(disp)                # 清残留，否则启动必失败
        try:
            p = subprocess.Popen(
                ["Xvfb", disp, "-screen", "0", "1600x1000x24", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.5)
            if p.poll() is not None:           # 秒退 = 起来失败
                continue
            if _display_alive(disp):
                _XVFB = p
                os.environ["DISPLAY"] = disp
                return disp
            p.terminate()                      # 起来了但连不上，换一个
        except Exception:
            continue
    return None


def stop_display():
    global _XVFB
    if _XVFB:
        try:
            _XVFB.terminate()
        except Exception:
            pass
        _XVFB = None

ROOT = Path(__file__).resolve().parent.parent
def _profile_root() -> Path:
    from service.publishing.db import data_root
    d = data_root() / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


PROFILE_ROOT = _profile_root()
# 调试/验证码产物与 DB 同源（见 observability._data_dir 的说明）
_DATA_ROOT = _profile_root().parent
DEBUG_DIR = _DATA_ROOT / "debug"
CAPTCHA_DIR = _DATA_ROOT / "captcha"


def _find_chrome():
    """定位可用的 Chromium 可执行文件。

    patchright 的 chromium 需要单独 install（国内网络常失败），
    这里允许复用预装的 playwright chromium——patchright 与 playwright
    同源（CDP 协议兼容），直接指定 executable_path 驱动即可。
    """
    import glob
    home = Path.home() / ".cache" / "ms-playwright"
    for root in (Path("/opt/vm/preinstall/ms-playwright"),
                 Path("/opt/playwright"),
                 home):
        for pat in ("chromium-*/chrome-linux/chrome",
                    "chromium-*/chrome-linux64/chrome",
                    "chromium_headless_shell-*/chrome-linux64/chrome-headless-shell",
                    "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"):
            hits = sorted(glob.glob(str(root / pat)))
            if hits:
                return hits[-1]
    return None


_CHROME_BIN = _find_chrome()

# 可切换浏览器通道：某些平台的前端 SPA 在 patchright 自带 chromium 下整页不渲染
# （2026-09 实测：开源中国写作页、博客园用户中心均为 0 input / body 空），
# 换系统 Edge 同 profile 正常。用 HUB_BROWSER_CHANNEL=msedge|chrome 覆盖；
# 不设置 = 维持原行为（自带 chromium）。
CHANNEL_BINARIES = {
    "msedge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
}


def resolve_channel_executable(channel: str):
    """把通道名解析成本机可执行文件路径；未设置/未知/文件不存在一律返回 None。

    返回 None 表示"按默认方式启动"——绝不静默换浏览器，也绝不因为通道没配好
    就让发布链路起不来。
    """
    name = (channel or "").strip().lower()
    if not name:
        return None
    for cand in CHANNEL_BINARIES.get(name, []):
        if Path(cand).exists():
            return cand
    return None



UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 启动参数：只留容器/服务器必需项；反检测 hack 全删（patchright 内置处理）
LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    "--window-size=1440,900",
]

# 注：TLS/JA3 指纹在 Python 侧无法直接覆写（握手在 Chromium 内部）。
# patchright fork 内置了 TLS 指纹与自动化痕迹处理——这正是换它的原因；
# 极端对抗（JA3/JA4 全抹平）需要商业反检测浏览器层，超出本工具范围。


# ---------------------------------------------------------------------------
# 验证码策略
# ---------------------------------------------------------------------------
class CaptchaPolicy:
    """验证码处置策略。

    顺序很重要：先想办法**不遇到**，再想办法**遇到一次就完事**，
    最后才是遇到时怎么办。
    """
    # 各平台验证码特征，命中就交给对应策略
    PATTERNS = {
        "geetest":      ["geetest", "gt-", "极验"],
        "aliyun":       ["nc_", "aliyuncs.com/captcha", "阿里云验证"],
        "tencent":      ["tcaptcha", "腾讯验证"],
        "hcaptcha":     ["hcaptcha"],
        "recaptcha":    ["recaptcha", "grecaptcha"],
        "slide":        ["slider", "滑块", "拖动滑块", "按住滑块"],
        "sms":          ["短信验证", "获取验证码", "手机验证"],
        "manual_risk":  ["安全验证", "确认您不是机器人", "CertifyId", "访问验证", "人机验证"],
    }

    # 哪些平台纯协议就能发，压根不用开浏览器 —— 这是最彻底的"绕过"
    API_FIRST = {
        "cnblogs": "MetaWeblog XML-RPC，只需访问令牌，零验证码",
        "juejin":  "有内容 OpenAPI，但需申请；默认仍走浏览器",
    }

    # 各类验证码里"需要点的那个东西"的选择器，按平台优先级排
    CHECK_HINTS = [
        # 阿里云（博客园用的就是这个，"确认您不是机器人"）
        ".aliyun-captcha-checkbox", "#aliyunCaptcha .checkbox",
        "[class*='aliyunCaptcha'] [class*='checkbox']",
        # 极验
        ".geetest_radar_tip", ".geetest_btn", "[class*='geetest'] [class*='btn']",
        # 腾讯
        ".tcaptcha-checkbox", "#tcaptcha_iframe", "[class*='tcaptcha'] [class*='checkbox']",
        # reCAPTCHA（recaptcha.net 国内镜像）
        ".recaptcha-checkbox-border", "#recaptcha-anchor",
        # 通用
        "input[type=checkbox][class*=captcha]",
        "[class*='captcha'] [class*='checkbox']",
        "[class*='verify'] [class*='checkbox']",
    ]
    # 滑块相关
    SLIDER_HINTS = [
        ".btn_slide", ".nc_iconfont.btn_slide", "[class*='slider-btn']",
        "[class*='slide-btn']", "[class*='slider'] [class*='btn']",
        ".geetest_slider_button", "[class*='sliderButton']",
    ]

    def __init__(self):
        self.paused = False
        self.hit = None      # 最近一次命中的验证码类型

    @classmethod
    def detect(cls, page):
        """扫页面 DOM/iframe，判断当前是不是卡在验证码上。

        2026-09-21 重构（实测教训）：旧版用 HTML 关键词 grep（"nc_" 等），
        CSDN 编辑器页的普通内容（async_with 之类锚点）就能误命中，
        导致发布成功被误记成"验证码拦截"。改为 DOM 结构检测优先，
        文本只保留强特异性词且必须配合可见的验证码容器。
        """
        # 1) DOM 结构检测：真验证码有明确的容器/触发元素（可靠，零误报来源）
        try:
            dom_hints = [
                "#aliyunCaptcha", "[id*='aliyunCaptcha']", ".nc-container",
                "[class*='aliyunCaptcha-container']", "[class*='captcha-verify']",
                ".geetest_panel_box", ".geetest_box", ".geetest_window",
                "#tcaptcha_iframe_dy", "[class*='tcaptcha-']",
                ".recaptcha-checkbox-checked", ".hcaptcha-box",
            ]
            for sel in dom_hints:
                if page.locator(sel).count():
                    return "dom_captcha"
        except Exception:
            pass
        # 2) iframe 检测：验证码几乎都在独立 iframe 里
        try:
            for fr in page.frames:
                u = (fr.url or "").lower()
                if any(s in u for s in ("captcha", "verify?", "geetest", "tcaptcha")):
                    return "iframe_captcha"
        except Exception:
            pass
        return None

    @classmethod
    def detections(cls, page):
        hits = []
        try:
            html = (page.content() or "").lower()
            for kind, keys in cls.PATTERNS.items():
                if any(k.lower() in html for k in keys):
                    hits.append(kind)
        except Exception:
            pass
        return hits

    # ---------------- 半自动处置 ----------------

    @classmethod
    def _find(cls, page, hints, action="点击"):
        """在主文档和各 iframe 里找一个存在的目标。返回 (locator, frame_url)。"""
        for f in [page] + list(page.frames):
            try:
                for sel in hints:
                    loc = f.locator(sel)
                    if loc.count():
                        return loc.first, (getattr(f, "url", "") or "main")
            except Exception:
                continue
        return None, None

    @classmethod
    def try_auto_pass(cls, page, timeout=25):
        """半自动尝试过掉验证。

        注意定位：这不是"破解"，是在**验证码本身简单**（纯复选框）时省你一次手点。
        需要在服务端做行为判定的（滑块、点选、reCAPTCHA 打分），大概率过不去，
        过不去就交给人工 —— 见 wait_human_captcha。

        返回 (passed: bool, detail: str)
        """
        from service.publishing import humanize
        kind = cls.detect(page)
        if not kind:
            return True, "没有验证码"

        detail = []

        # 1) 先试纯复选框 —— 这类最可能自动过
        loc, where = cls._find(page, cls.CHECK_HINTS)
        if loc:
            try:
                box = loc.bounding_box()
                if box:
                    cx = box["x"] + box["width"] / 2
                    cy = box["y"] + box["height"] / 2
                    # 带轨迹地移动过去再点，别用 locator.click
                    humanize.human_click(page, x=cx, y=cy)
                    detail.append("已拟人点击复选框")
                    # 等判定结果
                    for _ in range(int(timeout / 1.5)):
                        time.sleep(1.5)
                        if not cls.detect(page):
                            return True, "；".join(detail) + " → 通过"
                    detail.append("点了但没通过")
                else:
                    detail.append("复选框不可见")
            except Exception as e:
                detail.append(f"点复选框异常:{type(e).__name__}")

        # 2) 再试滑块 —— 拖一把，成不成都算尽力
        if kind in ("slide", "aliyun", "geetest", "tencent", "iframe_captcha"):
            sl, _ = cls._find(page, cls.SLIDER_HINTS)
            if sl:
                try:
                    box = sl.bounding_box()
                    track = None
                    # 找轨道宽度：滑块容器通常有个 track
                    for sel in ("[class*='track']", "[class*='nc_scale']",
                                "[class*='slider-track']", "[class*='bg']"):
                        t = page.locator(sel)
                        if t.count() and t.first.bounding_box():
                            track = t.first.bounding_box()
                            break
                    dist = (track["width"] - box["width"] - 6) if track else 260
                    star_x = box["x"] + box["width"] / 2
                    star_y = box["y"] + box["height"] / 2
                    page.mouse.move(star_x, star_y)
                    humanize.human_pause(0.2, 0.4)
                    humanize.slide_to(page, dist, y_offset=int(star_y))
                    detail.append(f"已拖动滑块 {int(dist)}px")
                    for _ in range(int(timeout / 1.5)):
                        time.sleep(1.5)
                        if not cls.detect(page):
                            return True, "；".join(detail) + " → 通过"
                    detail.append("拖了但判定未通过")
                except Exception as e:
                    detail.append(f"拖滑块异常:{type(e).__name__}")

        return False, ("；".join(detail) if detail else f"没找到可操作元素（类型 {kind}）")


def dump_captcha(page, platform, reason="unknown"):
    """把卡验证码时的现场存下来：截图 + HTML。
    一是给人工过验证时看着方便，二是扩平台时能拿来分析风控长什么样。"""
    CAPTCHA_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    shot = CAPTCHA_DIR / f"{platform}_{reason}_{ts}.png"
    try:
        page.screenshot(path=str(shot), full_page=False)
    except Exception:
        shot = None
    html_path = CAPTCHA_DIR / f"{platform}_{reason}_{ts}.html"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception:
        html_path = None
    return {"screenshot": str(shot) if shot else "", "html": str(html_path) if html_path else ""}


class BuiltinBrowser:
    """一个平台一个实例，用完 close()。"""

    def __init__(self, platform, account="default", headless=False, slow_mo=0,
                 proxy=None):
        self.platform = platform
        self.account = account
        self.headless = headless
        self.slow_mo = slow_mo
        self.proxy = proxy
        self.profile_dir = PROFILE_ROOT / f"{platform}_{account}"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        # 登录快照：存 profile 同级的独立 json（Python 同步写盘，不赌 chromium
        # 关窗前的 cookie 刷盘时序——2026-09-22 掘金扫码成功却丢态的教训）
        self.auth_file = PROFILE_ROOT / f"{platform}_{account}.auth.json"
        # 有些平台风控认 UA 和 profile 里上次的 UA 必须一致，
        # 存一份在 profile 里，避免新旧 UA 打架反而更可疑
        self._stamp = self.profile_dir / ".ua_stamp"
        self._pw = None
        self._ctx = None

    def _ua(self):
        # 首次用固定 UA；之后一直复用，别换
        if self._stamp.exists():
            try:
                return self._stamp.read_text(encoding="utf-8").strip() or UA
            except Exception:
                pass
        self._stamp.write_text(UA, encoding="utf-8")
        return UA

    def start(self):
        if self._ctx:
            return self._ctx
        # 有头模式在 Linux 上需要 X Server，没有就自动起 Xvfb
        if not self.headless:
            self.display = ensure_display()
            # ensure_display 返回 True（Win/Mac）、str（display 名）或 None（Linux 无 X），
            # 永不会返回 False。因此判断要用 `not self.display` 而不是 `is False`：
            # 旧写法 `is False` 是死代码，Linux 无 X 时会尝试 headful launch 导致报错不可读。
            if not self.display:
                print("[warn] 没找到可用的显示（DISPLAY/Xvfb），有头模式不可用，"
                      "已降级为无头。登录请在有桌面的机器上做，或装：apt install xvfb")
                self.headless = True
        self._pw = _get_shared_pw()   # 进程级共享驱动，绝不每实例起一个
        opts = dict(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=LAUNCH_ARGS,
            ignore_default_args=["--enable-automation", "--use-mock-keychain"],
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=self._ua(),
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
                "sec-ch-ua-platform": '"Windows"',
            },
        )
        if self.proxy:
            opts["proxy"] = self.proxy
        # 通道覆盖优先于自带 chromium；解析不到就回落原逻辑（保持默认行为不变）
        channel_bin = resolve_channel_executable(
            os.environ.get("HUB_BROWSER_CHANNEL", ""))
        try:
            if channel_bin:
                opts["executable_path"] = channel_bin
            elif _CHROME_BIN:
                opts["executable_path"] = str(_CHROME_BIN)
            self._ctx = self._pw.chromium.launch_persistent_context(**opts)
        except Exception as e:
            # 最常见的翻车：同一个 profile 被另一个实例占着（比如正在扫码登录）。
            # 注意：共享驱动绝不能在这里 stop（一个 profile 占用不该杀全局驱动）
            self._pw = None
            msg = f"浏览器启动失败: {str(e)[:160]}"
            if "ProcessSingleton" in str(e) or "SingletonLock" in str(e) or "user data dir" in str(e):
                msg = (f"profile 被占用（{self.platform}/{self.account} 的浏览器正在别处运行，"
                       f"可能正在扫码登录）。等它结束再试，或删 {self.profile_dir} 重建")
            raise RuntimeError(msg) from e
        self._import_auth()
        return self._ctx

    def _import_auth(self):
        """把登录快照合回浏览器：即使 profile 的 cookie 刷盘丢了，扫码态也还在。"""
        if not self.auth_file.exists():
            return
        try:
            data = json.loads(self.auth_file.read_text(encoding="utf-8"))
            cks = data.get("cookies") or []
            if cks:
                self._ctx.add_cookies(cks)
        except Exception:
            pass  # 快照坏了不拦启动，最多回到 profile 自身状态

    # 主流平台的会话 cookie 名（关门前据此判断"该不该刷新快照"：
    # 有会话迹象=刷新保鲜；无会话且已有快照=别拿游客态覆盖好快照）
    SESSION_COOKIE_NAMES = {
        "sessionid", "sessionid_ss", "sid_tt", "sid_guard",
        "z_c0", "sessdata", "username", "userinfo", "authencation",
        "inlogin", "cnblogin",
    }

    def _has_session_cookie(self):
        try:
            for c in self._ctx.cookies():
                n = (c.get("name") or "").lower()
                if n in self.SESSION_COOKIE_NAMES or "session" in n:
                    return True
        except Exception:
            pass
        return False

    def export_auth(self):
        """把当前登录态快照到独立文件。原子写（tmp + os.replace）：
        写一半断电/被杀也不会留下半截损坏的快照（2026-09-22 永续登录加固）。"""
        if not self._ctx:
            return None
        data = self._ctx.storage_state()          # dict：cookies + localStorage
        tmp = self.auth_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.auth_file)
        return str(self.auth_file)

    @property
    def ctx(self):
        return self.start()

    def new_page(self):
        page = self.ctx.new_page()
        # 鼠标轨迹微抖动：纯瞬移点击是行为指纹里最明显的一条
        try:
            page.add_init_script("""
              (() => {
                let seed = Date.now() % 1000;
                const rnd = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
                const move = (el, x, y) => el.dispatchEvent(new MouseEvent('mousemove', {
                  clientX: x, clientY: y, bubbles: true
                }));
                document.addEventListener('click', (e) => {
                  for (let i = 0; i < 3; i++) {
                    move(document, e.clientX - rnd() * 6, e.clientY - rnd() * 6);
                  }
                }, true);
              })();
            """)
        except Exception:
            pass
        return page

    def close(self):
        try:
            if self._ctx:
                # 关窗保鲜：有会话 cookie（或从未存过快照）就刷新快照——
                # 会话轮换（平台 rotate sid）后快照永远跟着最新态走，
                # 覆盖每次正常关窗与池淘汰（"一次登录一直记住"的关键一环）
                if self._has_session_cookie() or not self.auth_file.exists():
                    self.export_auth()
        except Exception:
            pass
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:
            pass
        # 共享驱动不停（别的实例可能还在用）；进程退出由 _stop_shared_pw 收尾
        self._ctx = self._pw = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *a):
        self.close()


def wait_human_captcha(page, platform, timeout=300, poll=2, try_auto=True):
    """卡在验证码上时的处置：先尝试半自动，不行再把窗口交给人工。

    顺序是有讲究的：
      1. 先试 try_auto_pass —— 纯复选框那种经常能一次过，省你手动一步
      2. 过不了就转人工：窗口已经开着，你直接在里面点/拖/选就行
      3. 你过完，这里轮询发现验证码消失，自动接着往下跑

    返回 (ok, info)：ok=True 验证已通过；ok=False 超时或页面被关。
    这是有意为之的"半自动"——不硬解，只交接。
    """
    kind = CaptchaPolicy.detect(page) or "unknown"
    files = dump_captcha(page, platform, kind)

    # ---- 第一步：半自动尝试 ----
    if try_auto:
        print(f"\n>>> 检测到验证码（{kind}），先试一次半自动…")
        passed, detail = CaptchaPolicy.try_auto_pass(page, timeout=min(20, timeout))
        if passed:
            print(f">>> 半自动通过：{detail}")
            return True, {"kind": kind, "mode": "auto", "detail": detail, **files}
        print(f">>> 半自动没通过（{detail}），转人工。")

    # ---- 第二步：交人工 ----
    print(f"\n{'=' * 62}")
    print(f"  【需要人工】{platform} 弹了验证码（类型：{kind}）")
    print(f"  现场截图：{files['screenshot']}")
    print("  浏览器窗口已经开着，请在里面手动完成验证（点/拖/选）。")
    print(f"  过完这里会自动继续，最长等 {timeout} 秒。")
    if CaptchaPolicy.API_FIRST.get(platform):
        print(f"  小提示：{platform} 其实可以走 {CaptchaPolicy.API_FIRST[platform]}，")
        print("          走那条路压根不会有验证码，有空可以考虑。")
    print(f"{'=' * 62}\n")

    deadline = time.time() + timeout
    last_try = 0
    while time.time() < deadline:
        time.sleep(poll)
        try:
            if page.is_closed():
                return False, {"reason": "页面被关闭", "kind": kind, **files}
            if not CaptchaPolicy.detect(page):
                waited = round(timeout - (deadline - time.time()))
                return True, {"kind": kind, "mode": "human",
                              "waited": waited, **files}
        except Exception:
            pass
        # 每 20 秒再自动试一次（有的验证码是动态刷新的，重试可能过）
        if try_auto and time.time() - last_try > 20:
            last_try = time.time()
            try:
                p, _ = CaptchaPolicy.try_auto_pass(page, timeout=6)
                if p:
                    return True, {"kind": kind, "mode": "auto-retry", **files}
            except Exception:
                pass
    return False, {"reason": "等待人工验证超时", "kind": kind, **files}


def ensure_login(browser, login_url, check_fn, timeout=300, poll=3,
                 on_captcha="handoff"):
    """
    确认登录态。没登录就开个有头窗口让用户扫码/过验证，循环等到成功。

    check_fn(page) -> bool，由各平台适配器实现。

    on_captcha 决定遇到验证码怎么办：
      "handoff"  暂停，把窗口交给人工，过完自动续跑（默认，最稳）
      "skip"     不管验证码，只看登录态（适合你人在旁边、本来就要手动点）
      "abort"    一遇到验证码立刻放弃并报错（适合无人值守批处理）
    """
    page = browser.new_page()
    page.goto(login_url, timeout=60000, wait_until="domcontentloaded")

    if check_fn(page):
        page.close()
        return True, "已登录（复用本地登录态）"

    if browser.headless:
        page.close()
        return False, "未登录，且当前是无头模式没法扫码。请加 --headed 跑一次完成登录"

    handled = False
    print(f"\n>>> 请在弹出的浏览器里登录【{browser.platform}】，"
          f"最多等 {timeout} 秒…")
    if CaptchaPolicy.API_FIRST.get(browser.platform):
        print(f">>> 提示：{browser.platform} 可以走 {CaptchaPolicy.API_FIRST[browser.platform]}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll)

        # 先看有没有弹验证码
        kind = CaptchaPolicy.detect(page) if not handled else None
        if kind:
            if on_captcha == "abort":
                page.close()
                return False, f"遇到验证码（{kind}），按配置放弃"
            if on_captcha == "handoff":
                print(f">>> 检测到验证码（{kind}），切换人工模式…")
                ok, info = wait_human_captcha(page, browser.platform,
                                              timeout=max(60, int(deadline - time.time())))
                handled = True
                if not ok:
                    page.close()
                    return False, f"人工验证未完成：{info.get('reason', '')}"
                print(">>> 验证已通过，继续等待登录态…")

        try:
            if check_fn(page):
                page.close()
                return True, "登录成功，登录态已保存到本地"
        except Exception:
            pass
    page.close()
    return False, "等待登录超时"


def dump_dom(page, tag="debug"):
    """把当前页面 HTML 存下来——扩新平台时靠它看真实结构，比猜选择器快十倍。"""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f"{tag}_{int(time.time())}.html"
    path.write_text(page.content(), encoding="utf-8")
    return path
