# -*- coding: utf-8 -*-
"""拟人化操作 + 人机验证辅助。

先说清楚定位，免得跑偏：
  这里是**降低触发率**和**把简单人机验证交给人工/半自动**，
  不是"破解验证码"。理由很实在：
    - 阿里云/极验/腾讯这类验证码的判定逻辑在服务端，前端 JS 加密，
      靠逆向硬解，平台一次风控升级就全废，还可能直接封号。
    - 而"少触发 + 触发一次就一劳永逸"是工程上更稳、更省事的路。

三层策略（配合 browser.CaptchaPolicy 使用）：
  L1 能不模拟就不模拟 —— 有官方 API/令牌的平台走协议（博客园 MetaWeblog）
  L2 反检测 + 持久登录 —— 让浏览器看起来正常，登录态存盘，长期复用
  L3 拟人操作 + 人工交接 —— 输入带节奏、鼠标带轨迹；
                          真弹了验证就暂停把窗口交给人工，过完自动续跑
"""

import math
import random
import time


def gaussian_delay(center, spread, min_ms=0.0, max_ms=5.0):
    """高斯分布延迟（秒）。比 uniform 更接近真人——真人反应时间不是均匀散开，
    而是集中在某个均值附近、偶发长尾。

    center=期望延迟，spread=标准差（越大越"随意"）。clip 防止出现极端 outlier。
    对标 2026 行为指纹检测：uniform 序列熵偏高会被风控识别，高斯更贴近 HMM 行为模型。
    """
    val = random.gauss(center, spread)
    val = max(min_ms, min(max_ms, val))
    return val


def human_pause(a=0.4, b=1.4):
    """行为停顿。内部用高斯分布模拟真人"犹豫/思考"的节奏，比原来 uniform 更真实。
    a/b 仍作为分布的期望上下界，兼容旧调用。"""
    center = (a + b) / 2
    spread = (b - a) / 4
    time.sleep(gaussian_delay(center, spread, min_ms=0.05, max_ms=8.0))




def human_mouse_move(page, x, y, steps=None):
    """把鼠标"走"过去，而不是瞬移。中间带一点横向抖动，
    因为贝塞尔直线轨迹也是可识别的（真人手会画弧）。"""
    try:
        cur = page.evaluate("() => ({x: window.__lastX || 0, y: window.__lastY || 0})")
        sx, sy = cur.get("x", 0), cur.get("y", 0)
    except Exception:
        sx, sy = 0, 0

    dist = math.hypot(x - sx, y - sy)
    steps = steps or max(12, min(60, int(dist / 12)))
    # 控制点偏移，制造弧度
    cx = (sx + x) / 2 + random.uniform(-dist * 0.12, dist * 0.12)
    cy = (sy + y) / 2 + random.uniform(-dist * 0.12, dist * 0.12)

    for i in range(steps + 1):
        t = i / steps
        # 二次贝塞尔
        px = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t ** 2 * x
        py = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t ** 2 * y
        # 高斯噪声抖动（比 uniform 更接近人手微颤的自然分布）
        px += random.gauss(0, 1.2)
        py += random.gauss(0, 1.2)
        page.mouse.move(px, py)
        # 速度先快后慢，像真人临近目标会减速（加高斯抖动）
        time.sleep(max(0.004, 0.02 * (1.35 - t) + random.gauss(0, 0.004)))

    try:
        page.evaluate(f"() => {{ window.__lastX = {x}; window.__lastY = {y}; }}")
    except Exception:
        pass


def human_click(page, selector=None, x=None, y=None):
    """拟人点击：先移动过去，停顿一下再按下去。"""
    if selector:
        box = page.locator(selector).first.bounding_box()
        if box:
            x = box["x"] + box["width"] * random.uniform(0.35, 0.65)
            y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
    if x is None or y is None:
        raise ValueError("human_click 需要 selector 或 x/y")

    human_mouse_move(page, x, y)
    human_pause(0.1, 0.35)
    page.mouse.down()
    human_pause(0.05, 0.16)
    page.mouse.up()


def slide_to(page, distance, y_offset=0, steps=None, overshoot=True):
    """把滑块拖过去。这不是"破解" —— 是给**人工确认过**的场景用的：
    有的验证码只要拖到位就过，有的必须人工。拖完仍失败就交人工。

    要点：变速（先快后慢）、轻微 y 抖动、末端回弹。
    """
    steps = steps or random.randint(28, 45)
    # 获取当前鼠标位置（通过 localStorage 而非私有属性 _x/_y）
    try:
        cur = page.evaluate("() => ({x: window.__lastMouseX || 0, y: window.__lastMouseY || 0})")
        start_x, start_y = cur.get("x", 0), cur.get("y", y_offset)
    except Exception:
        start_x, start_y = 100, y_offset

    page.mouse.move(start_x, start_y)
    human_pause(0.1, 0.3)
    page.mouse.down()
    human_pause(0.1, 0.28)

    travelled = 0
    for i in range(1, steps + 1):
        t = i / steps
        # ease-out：越接近越慢
        target = distance * (1 - (1 - t) ** 2.2)
        travelled = target
        page.mouse.move(start_x + travelled,
                        start_y + random.uniform(-1.2, 1.2))
        time.sleep(max(0.006, 0.028 * (1.4 - t)))

    if overshoot:   # 冲过去一点再拉回来，真人特征
        page.mouse.move(start_x + distance + random.uniform(3, 7), start_y)
        human_pause(0.08, 0.2)
        page.mouse.move(start_x + distance, start_y)

    human_pause(0.15, 0.4)
    page.mouse.up()
    return True
