#!/usr/bin/env python3
"""重新生成 README 使用的界面截图。

前置：后端跑在 127.0.0.1:8000，前端 dev server 跑在 127.0.0.1:5173
（或传入 --base 指向已构建的站点）。

产物写入 docs/screenshots/，文件名与 README 引用一致：
    01-write.png      写作台（三栏：文章库 / 编辑器 / 发布轨）
    02-publish.png    发布中心（统计 + 图表）
    03-tasks.png      任务中心
    04-settings.png   设置（AI 模型）
    05-mobile.png     移动端布局（375x812）
    06-platforms.png  平台账号矩阵

用法：
    uv run --project backed python scripts/capture_screenshots.py
    uv run --project backed python scripts/capture_screenshots.py --base http://127.0.0.1:5173
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from patchright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

VIEWS = (
    ("settings", "04-settings"),
    ("tasks", "03-tasks"),
    ("publish", "02-publish"),
)


def _launch(pw):
    kwargs: dict = {"headless": True, "args": ["--no-sandbox"]}
    for candidate in EDGE_CANDIDATES:
        if Path(candidate).exists():
            kwargs["executable_path"] = candidate
            break
    return pw.chromium.launch(**kwargs)


def _click_view(page, view: str, label: str) -> bool:
    """Click a left-nav entry, tolerating icon-only mobile layout."""
    selectors = (
        f'[data-view="{view}"]',
        f'[data-nav="{view}"]',
        f'a:has-text("{label}")',
        f'button:has-text("{label}")',
    )
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() and locator.is_visible():
                locator.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 README 截图")
    parser.add_argument("--base", default="http://127.0.0.1:5173")
    parser.add_argument("--wait", type=float, default=2.5, help="每次切页后的等待秒数")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    with sync_playwright() as pw:
        browser = _launch(pw)
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            device_scale_factor=2,
        )
        page = ctx.new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        page.goto(args.base, wait_until="domcontentloaded", timeout=45000)
        time.sleep(args.wait + 1)
        page.screenshot(path=str(OUT / "01-write.png"))
        print("  ok 01-write.png")

        for view, name in VIEWS:
            if not _click_view(page, view, {"settings": "设置", "tasks": "任务中心",
                                            "publish": "发布中心"}[view]):
                print(f"  warn 未能切到 {view}，跳过 {name}", file=sys.stderr)
                continue
            time.sleep(args.wait)
            page.screenshot(path=str(OUT / f"{name}.png"))
            print(f"  ok {name}.png")

        # 平台账号矩阵：15 张卡是项目最核心的对外展示，单独截一张
        if _click_view(page, "settings", "设置"):
            time.sleep(args.wait)
            tabs = page.locator(".el-tabs__item")
            if tabs.count() > 1:
                tabs.nth(1).click()
                time.sleep(args.wait + 1)
                page.screenshot(path=str(OUT / "06-platforms.png"))
                print("  ok 06-platforms.png")
            # 标签治理：README 引用了 07-tags.png，但旧脚本从没生成过它，
            # 文件一直停留在早期版本。按文本找 tab，不按序号硬点（tab 顺序会变）。
            tag_tab = page.locator('.el-tabs__item:has-text("标签")').first
            try:
                if tag_tab.count() and tag_tab.is_visible():
                    tag_tab.click()
                    time.sleep(args.wait + 1.5)   # 聚类要等接口返回
                    page.screenshot(path=str(OUT / "07-tags.png"))
                    print("  ok 07-tags.png")
                else:
                    print("  warn 未找到标签治理 tab，跳过 07-tags.png", file=sys.stderr)
            except Exception as e:   # aqg: top-level boundary（截图失败不该中断整批）
                print(f"  warn 标签治理截图失败：{e}", file=sys.stderr)

        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(args.base, wait_until="domcontentloaded", timeout=45000)
        time.sleep(args.wait)
        page.screenshot(path=str(OUT / "05-mobile.png"))
        print("  ok 05-mobile.png")

        browser.close()

    if errors:
        print(f"\n控制台错误 {len(errors)} 条：", file=sys.stderr)
        for item in errors[:10]:
            print(f"  {item[:160]}", file=sys.stderr)
        return 1
    print("\n完成：截图已写入 docs/screenshots/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
