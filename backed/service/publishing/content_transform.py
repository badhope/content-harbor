# -*- coding: utf-8 -*-
"""按平台改写内容的 AST 层（Markdown token 级，不做字符串正则）。

为什么需要这一层（对标 Artipub 的 mdast 插件 / Wechatsync 的 preprocessConfig）：

    各平台对 Markdown 的容忍度不同，但差异只有几类：
      - 知乎/B站/头条：标题由编辑器单独填写，正文里再带一个 H1 会重复；
      - 所有平台：正文图片地址要换成平台可访问的地址；
      - 掘金/思否：标签走 API 参数，正文里不该留 "标签：xxx" 这种行；
      - 公众号类：外链要剥离。
    过去这些靠各适配器里的字符串 replace，遇到 "同一段文字出现在代码块里"
    就会误伤（代码里的 `# 标题` 被当标题删掉）。这里用 markdown-it-py 的
    token 流做变换——代码块是独立的 fence token，天然不会误伤正文规则。

设计取舍：
  - 只做**结构化、可测试**的改写；不做 Markdown → 平台富文本的渲染（那是
    适配器的事，且各平台差异太大）。
  - 变换函数都是纯函数（输入 str → 输出 str + 报告），便于逐条单测。
"""

import re

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": True}).enable("table")


def _token_lines(text: str) -> list:
    """解析成 token 列表（每次调用独立，无状态）。"""
    return _md.parse(text)


def strip_leading_h1(md_text: str) -> tuple[str, dict]:
    """去掉正文开头的第一个 H1（标题已在平台标题框里填过）。

    只处理**文档开头**（允许前面有空行）的第一个 heading_open h1，
    正文中间的 H1 保留——那是作者的分节意图。
    代码块里的 `# xxx` 不会被识别为 heading，天然安全。
    """
    tokens = _token_lines(md_text)
    if not tokens:
        return md_text, {"removed": False}
    idx = None
    for i, t in enumerate(tokens):
        if t.type == "heading_open":
            if t.tag == "h1":
                idx = i
            break
        if t.type in ("paragraph_open", "fence", "code_block", "bullet_list_open",
                      "ordered_list_open", "blockquote_open", "table_open",
                      "html_block", "hr"):
            break          # 正文已经开始，开头的 H1 不存在
    if idx is None:
        return md_text, {"removed": False}
    # heading_open 自带行范围 [start, end)；heading_close 的 map 是 None，
    # 不能用它（第一版就是这样写成永远 False 的）。
    start_line, end_line = (tokens[idx].map or (None, None))
    if start_line is None or end_line is None:
        return md_text, {"removed": False}
    lines = md_text.splitlines(keepends=True)
    removed_text = "".join(lines[start_line:end_line]).strip()
    out = "".join(lines[:start_line] + lines[end_line:]).lstrip("\n")
    return out, {"removed": True, "title": removed_text.lstrip("# ").strip()}


def rewrite_image_urls(md_text: str, mapper) -> tuple[str, dict]:
    """把图片地址交给 mapper 处理（可以本地化、加前缀、加签名）。

    mapper(src) -> new_src。返回 (新文本, {"total","changed","failed"})。
    只改 image token 的 src，不会碰正文里长得像 URL 的普通文本。
    """
    tokens = _token_lines(md_text)
    stats = {"total": 0, "changed": 0, "failed": 0}
    edits = []
    for t in tokens:
        if t.type != "inline" or not t.children:
            continue
        for child in t.children:
            if child.type != "image":
                continue
            stats["total"] += 1
            src = child.attrGet("src") or ""
            try:
                new = mapper(src)
            except Exception:
                stats["failed"] += 1
                continue
            if new and new != src:
                stats["changed"] += 1
                edits.append((src, new))
    out = md_text
    # 替换用最保守的方式：只替换 `](src)` / `](src "title")` 形态
    for src, new in edits:
        out = out.replace(f"]({src})", f"]({new})")
        out = out.replace(f"]({src} ", f"]({new} ")
    return out, stats


def strip_tag_line(md_text: str, labels=("标签", "tags", "Tags")) -> tuple[str, dict]:
    """删掉正文里的「标签：a, b」这类行（标签由 API 参数传，不该出现在正文）。

    只在段落 token 里按行判断，代码块内不处理（fence 是独立 token）。
    """
    tokens = _token_lines(md_text)
    target_lines = set()
    for t in tokens:
        if t.type != "paragraph_open" or not t.map:
            continue
        start, end = t.map
        if end - start != 1:
            continue           # 多行段落里可能夹着正文，不动
        line_idx = start
        lines = md_text.splitlines()
        if line_idx >= len(lines):
            continue
        line = lines[line_idx]
        for label in labels:
            if re.match(rf"^\s*{re.escape(label)}\s*[:：]", line):
                target_lines.add(line_idx)
                break
    if not target_lines:
        return md_text, {"removed": 0}
    kept = [ln for i, ln in enumerate(md_text.splitlines(keepends=True))
            if i not in target_lines]
    return "".join(kept), {"removed": len(target_lines)}


def prepare_for_platform(md_text: str, platform: str,
                         image_mapper=None) -> tuple[str, dict]:
    """按平台把 Markdown 调成"平台侧最省事"的形态。

    规则表刻意保持小而明确（每条都能单测）：
      - 所有平台：图片 URL 交给 mapper（未提供则跳过）
      - 知乎/头条/B站/开源中国/思否：去掉开头重复的 H1
      - 掘金/思否：去掉正文里的标签行
    """
    report = {"platform": platform, "steps": []}
    out = md_text or ""

    if image_mapper is not None:
        out, st = rewrite_image_urls(out, image_mapper)
        report["steps"].append({"step": "images", **st})

    if platform in ("zhihu", "toutiao", "bilibili", "oschina", "segmentfault"):
        out, st = strip_leading_h1(out)
        if st.get("removed"):
            report["steps"].append({"step": "strip_h1", "title": st.get("title", "")})

    if platform in ("juejin", "segmentfault"):
        out, st = strip_tag_line(out)
        if st.get("removed"):
            report["steps"].append({"step": "strip_tags_line", **st})

    return out, report
