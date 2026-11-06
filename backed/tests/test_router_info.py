# -*- coding: utf-8 -*-
""" /api/info 必须真的列出路由。

回归：FastAPI 0.141 起 include_router 插入 _IncludedRouter 包装，
``getattr(route, "path", "")`` 全部取空，/api/info 返回 91 条空记录。
"""
from api import get_router_info


def test_router_info_lists_real_paths():
    info = get_router_info()
    paths = [r["path"] for r in info["routes"]]
    assert len(paths) > 50, f"路由数异常少: {len(paths)}"
    assert all(p.startswith("/api/v1/hub") for p in paths)
    assert any(p.endswith("/articles") for p in paths)
    assert any(p.endswith("/tags/suggest") for p in paths)


def test_router_info_entries_have_methods_and_names():
    info = get_router_info()
    for r in info["routes"]:
        assert r["methods"], f"缺 methods: {r}"
        assert r["name"], f"缺 name: {r}"
    assert info["count"] == len(info["routes"])
