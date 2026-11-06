# -*- coding: utf-8 -*-
"""Markdown 导出：内容必须能真的带出去。

定位是自托管、数据属于用户；只进不出等于把用户锁死。
"""
import datetime

from api.routers.articles import build_markdown_export, _safe_filename


ARTICLE = {
    "id": 7,
    "title": "Kubernetes 部署: 实战",
    "summary": "一篇讲滚动更新的文章",
    "tags": "Kubernetes,Docker",
    "status": "published",
    "cover": "https://example.com/cover.png",
    "origin_url": "",
    "content_md": "# 正文\n\n滚动更新。",
    "created_at": 1700000000,
    "updated_at": 1700003600,
}


def test_export_has_valid_frontmatter():
    out = build_markdown_export(ARTICLE)
    assert out.startswith("---\n")
    body = out.split("---\n", 2)[1]
    assert 'title: "Kubernetes 部署: 实战"' in body
    assert "tags:\n  - Kubernetes\n  - Docker" in body
    assert "harbor_id: 7" in body
    assert "status: published" in body


def test_export_body_is_verbatim_markdown():
    out = build_markdown_export(ARTICLE)
    assert out.endswith("# 正文\n\n滚动更新。")
    # 正文不应被 HTML 转义或重新排版
    assert "<p>" not in out and "&lt;" not in out


def test_export_dates_are_iso():
    out = build_markdown_export(ARTICLE)
    iso = datetime.datetime.fromtimestamp(1700000000).isoformat(timespec="seconds")
    assert f"created_at: {iso}" in out


def test_export_without_tags_omits_block():
    art = dict(ARTICLE, tags="")
    out = build_markdown_export(art)
    assert "tags:" not in out


def test_safe_filename_strips_path_chars():
    assert _safe_filename(r'a/b\c:d*e?f"g<h>i|j') == "a-b-c-d-e-f-g-h-i-j"
    assert _safe_filename("   ") == "article"
    assert len(_safe_filename("长" * 200)) == 80
