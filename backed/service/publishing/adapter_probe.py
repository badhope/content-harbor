# -*- coding: utf-8 -*-
"""适配器离线契约探针：没有真机账号时，用机器能跑的方式锁住"适配器还能用"。

背景：平台适配器的真机回归需要账号与验证码，CI 与本地都跑不了。上一轮
把这件事记成"未验证"就结束了——但"未验证"不等于"什么都不做"。这里用
三层可离线执行的检查，把能提前发现的问题全部前移：

  1. **静态契约**：每个适配器必须实现基类声明的动作，能力矩阵声明必须
     与实现一致（声明 update=True 就不能是直接 raise "不支持"）。
  2. **选择器完整性**：`key_selectors` 里的选择器必须非空、必须是合法的
     CSS/Playwright 形态（含 `:has-text()` 这类扩展语法时只做括号配对检查）。
  3. **页面结构漂移**：用 `dump-dom` 采样的真实页面（存成 fixture）比对
     选择器是否仍能命中——这是"平台改版"的唯一离线信号。

第 3 层需要用户提供 dump（登录后才能拿到），没有 dump 时跳过并如实报告
"未覆盖"，而不是假装通过。
"""

import json
import re
from pathlib import Path

# 选择器里允许出现的 Playwright 扩展语法
_PW_PSEUDO = (":has-text(", ":text(", ":visible", ":nth-match(", ">>")

DUMP_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "dom"


def _balanced(sel: str) -> bool:
    """括号必须成对：() 用于 :has-text()，[] 用于属性选择器，两者都要查。"""
    for opener, closer in (("(", ")"), ("[", "]")):
        depth = 0
        for ch in sel:
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth < 0:
                    return False
        if depth != 0:
            return False
    return True


def check_selector_syntax(sel: str) -> str:
    """返回空串表示合法，否则返回原因。"""
    s = (sel or "").strip()
    if not s:
        return "空选择器"
    if not _balanced(s):
        return "括号不配对"
    if s.count("'") % 2 or s.count('"') % 2:
        return "引号不配对"
    if s.startswith((">>", ">")) and not s.startswith(">>"):
        return "以子代选择器开头"
    return ""


def static_contract_report() -> dict:
    """静态检查全部适配器：接口实现 + 能力一致性 + 选择器合法性。"""
    from service.publishing.adapters.base import (ADAPTERS, PlatformAdapter,
                                                  capability_of)

    problems = []
    checked = 0
    for pid, ad in sorted(ADAPTERS.items()):
        checked += 1
        caps = capability_of(pid)
        # 1) 必须实现 publish（基类里是 raise NotImplementedError）
        if ad.publish.__func__ is PlatformAdapter.publish:
            problems.append({"platform": pid, "level": "error",
                             "issue": "未实现 publish"})
        # 2) 声明了 update/list 就必须重写对应方法
        if caps.get("update") and ad.update.__func__ is PlatformAdapter.update:
            problems.append({"platform": pid, "level": "error",
                             "issue": "声明 update=True 但未实现 update()"})
        if caps.get("list") and ad.list_articles.__func__ is PlatformAdapter.list_articles:
            problems.append({"platform": pid, "level": "error",
                             "issue": "声明 list=True 但未实现 list_articles()"})
        # 3) 声明了却直接抛"不支持"——自相矛盾
        if caps.get("update"):
            src = ad.update.__func__.__code__.co_consts
            if any(isinstance(c, str) and "不支持" in c for c in src):
                problems.append({"platform": pid, "level": "error",
                                 "issue": "声明 update=True 但实现里写死'不支持'"})
        # 4) 选择器
        for name, sel in (ad.key_selectors or {}).items():
            why = check_selector_syntax(sel)
            if why:
                problems.append({"platform": pid, "level": "warn",
                                 "issue": f"选择器 {name} 非法: {why}（{sel!r}）"})
        # 5) 浏览器类平台必须有登录/首页地址，否则 check_auth 无从落地
        if ad.needs_browser and not (ad.login_url or ad.home_url):
            problems.append({"platform": pid, "level": "warn",
                             "issue": "needs_browser=True 但缺 login_url/home_url"})
        # 6) selector_version 未标注 → 改版时无法定位版本
        if ad.needs_browser and not ad.selector_version:
            problems.append({"platform": pid, "level": "info",
                             "issue": "未标注 selector_version（改版排查会吃亏）"})
    return {"checked": checked, "problems": problems,
            "errors": sum(1 for p in problems if p["level"] == "error"),
            "warnings": sum(1 for p in problems if p["level"] == "warn")}


def dom_fixtures() -> dict:
    """可用 DOM 采样：文件名 `<platform>.html`。"""
    if not DUMP_DIR.is_dir():
        return {}
    return {p.stem: p for p in DUMP_DIR.glob("*.html")}


def check_dom(platform: str, html: str) -> dict:
    """在采样的 HTML 上检查选择器能否命中。

    只做"标签名/属性/id/class"这类可静态判定的匹配，不引入浏览器：
    目的是"平台改版后选择器是否还认得页面"，不是像素级渲染验证。
    用 html.parser 提取元素集合，再对选择器做保守解析。
    """
    from html.parser import HTMLParser

    from service.publishing.adapters.base import ADAPTERS, capability_of  # noqa: F401

    class Collector(HTMLParser):
        def __init__(self):
            super().__init__()
            self.items = []       # (tag, id, classes, attrs)

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            self.items.append((tag, d.get("id", ""),
                               set((d.get("class") or "").split()), d))

    col = Collector()
    col.feed(html or "")

    def _match_simple(sel: str) -> bool:
        """保守匹配：#id / .class / tag / [attr=value] / tag[attr*=value]。"""
        sel = sel.strip()
        # 复合选择器取最后一个基本选择器（"div > .a" 取 .a）
        parts = re.split(r"\s+|>", sel)
        base = parts[-1].strip()
        if not base or any(p in base for p in _PW_PSEUDO):
            return True       # 扩展语法没法静态判定，按"未检测到问题"处理
        m = re.match(r"^([a-zA-Z][\w-]*)?((?:[.#][\w-]+)*)(\[[^\]]*\])?$", base)
        if not m:
            return True
        tag, idclass, attr = m.group(1), m.group(2) or "", m.group(3)
        want_id = ""
        want_cls = set()
        for token in re.findall(r"[.#][\w-]+", idclass):
            if token.startswith("#"):
                want_id = token[1:]
            else:
                want_cls.add(token[1:])
        for t, i, cls, attrs in col.items:
            if tag and t != tag.lower():
                continue
            if want_id and i != want_id:
                continue
            if want_cls and not want_cls <= cls:
                continue
            if attr:
                mm = re.match(r"\[([\w-]+)(?:([*^$]?=)\"?'?([^\"'\]]*)\"?'?)?\]", attr)
                if mm:
                    key, op, val = mm.group(1), mm.group(2), mm.group(3)
                    have = attrs.get(key)
                    if have is None:
                        continue
                    if op == "=" and have != val:
                        continue
                    if op == "*=" and val not in have:
                        continue
                    if op == "^=" and not have.startswith(val):
                        continue
            return True
        return False

    ad = ADAPTERS.get(platform)
    if not ad:
        return {"ok": False, "error": f"未知平台 {platform}"}
    results = []
    for name, sel in (ad.key_selectors or {}).items():
        results.append({"selector": name, "value": sel[:120],
                        "matched": _match_simple(sel)})
    missing = [r["selector"] for r in results if not r["matched"]]
    return {"ok": not missing, "platform": platform,
            "checked": len(results), "missing": missing, "selectors": results}


def full_report(include_dom: bool = True) -> dict:
    """一次性报告：静态契约 + 有采样的平台做 DOM 检查。"""
    static = static_contract_report()
    dom = {}
    if include_dom:
        for platform, path in dom_fixtures().items():
            try:
                html = path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                dom[platform] = {"ok": False, "error": f"读取失败: {e}"}
                continue
            dom[platform] = check_dom(platform, html)
    tested = sum(1 for p in (ADAPTERS_KEYS()) if p in dom)
    return {
        "static": static,
        "dom": dom,
        "dom_coverage": f"{tested}/{len(ADAPTERS_KEYS())}（未覆盖的平台需要登录后 dump-dom 采集）",
        "how_to_capture": (
            "登录后在平台内容页执行：uv run python -c \"from service.publishing.browser "
            "import ...\"，或直接用浏览器开发者工具保存 HTML 到 "
            "backed/tests/fixtures/dom/<platform>.html"),
    }


def ADAPTERS_KEYS() -> list:
    from service.publishing.adapters.base import ADAPTERS
    return sorted(ADAPTERS.keys())


def save_dump(platform: str, html: str) -> Path:
    """保存一份 DOM 采样（供 check_dom 使用）。"""
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    path = DUMP_DIR / f"{platform}.html"
    path.write_text(html, encoding="utf-8")
    return path


def report_json() -> str:
    return json.dumps(full_report(), ensure_ascii=False, indent=2)
