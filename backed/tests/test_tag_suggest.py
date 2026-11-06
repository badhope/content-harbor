# -*- coding: utf-8 -*-
"""标签推荐：AI 不可用时不能返回空串。

回归：无热门标签 + AI 失败时 ``suggest_for_article`` 返回 ""，前端表现为
"点了推荐标签什么都没发生"。
"""
import sqlite3

import service.publishing.tags as tags


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_offline_suggest_hits_known_terms():
    out = tags._local_suggest("Kubernetes 部署实践", "k8s docker 容器与 redis 缓存", [])
    assert "kubernetes" in out
    assert "docker" in out
    assert len(out) >= 3


def test_offline_suggest_does_not_mix_go_and_golang():
    out = tags._local_suggest("Golang 微服务", "go 语言性能好", [])
    assert "golang" in out
    # go 不该作为独立标签再出现一次
    assert out.count("go") == 0 and out.count("GO") == 0


def test_offline_suggest_empty_input_returns_empty():
    assert tags._local_suggest("", "", []) == []


def test_suggest_falls_back_when_ai_raises(monkeypatch):
    import service.publishing.ai as ai_mod
    monkeypatch.setattr(ai_mod, "chat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no ai")))
    out = tags.suggest_for_article(_conn(), "Docker 实战", "容器镜像与 compose", [])
    assert out.strip(), "AI 不可用时必须走本地兜底，不能返回空串"
    assert "docker" in out


def test_suggest_uses_ai_result_when_available(monkeypatch):
    import service.publishing.ai as ai_mod
    monkeypatch.setattr(ai_mod, "chat", lambda *a, **k: "性能优化, 缓存")
    out = tags.suggest_for_article(_conn(), "标题", "正文", [])
    assert out == "性能优化,缓存"
