# -*- coding: utf-8 -*-
"""AI 就绪判定：有配置 != 服务在跑。

背景：Ollama 不需要密钥，``_resolve_providers`` 永远会把它解析出来，于是
``is_ready()`` 恒为 True。UI 上绿点「AI 就绪」+ 卡片「可用」，用户点一下
AI 功能却连接被拒（本机实测：11434 端口无监听）。这组测试锁住真实语义。
"""
import service.publishing.ai_providers as prov


def test_ready_without_probe_counts_config_only(monkeypatch):
    monkeypatch.setattr(prov, "_resolve_providers", lambda: {"ollama": {"api_key": "ollama", "base_url": "http://localhost:11434/v1"}})
    assert prov.is_ready() is True


def test_ready_with_probe_fails_when_local_service_down(monkeypatch):
    monkeypatch.setattr(prov, "_resolve_providers", lambda: {"ollama": {"api_key": "ollama", "base_url": "http://localhost:11434/v1"}})
    monkeypatch.setattr(prov, "_probe_local_ollama", lambda url, timeout=0.8: False)
    assert prov.is_ready(probe_local=True) is False


def test_ready_with_probe_passes_when_local_service_up(monkeypatch):
    monkeypatch.setattr(prov, "_resolve_providers", lambda: {"ollama": {"api_key": "ollama", "base_url": "http://localhost:11434/v1"}})
    monkeypatch.setattr(prov, "_probe_local_ollama", lambda url, timeout=0.8: True)
    assert prov.is_ready(probe_local=True) is True


def test_remote_provider_with_real_key_counts_ready_without_probe(monkeypatch):
    monkeypatch.setattr(prov, "_resolve_providers", lambda: {
        "deepseek": {"api_key": "sk-realkey", "base_url": "https://api.deepseek.com/v1"},
    })
    monkeypatch.setattr(prov, "_probe_local_ollama", lambda url, timeout=0.8: False)
    assert prov.is_ready(probe_local=True) is True


def test_no_providers_is_not_ready(monkeypatch):
    monkeypatch.setattr(prov, "_resolve_providers", lambda: {})
    assert prov.is_ready() is False
    assert prov.is_ready(probe_local=True) is False


def test_probe_rejects_non_loopback_host():
    assert prov._probe_local_ollama("https://api.deepseek.com/v1") is False
    assert prov._probe_local_ollama("") is False


def test_list_providers_marks_ollama_unavailable_when_down(monkeypatch):
    """/ai/status 与 /ai/config 的口径必须一致：都不能把未运行的 Ollama 说成可用。"""
    monkeypatch.setattr(prov, "_resolve_providers", lambda: {
        "ollama": {"key": "ollama", "name": "Ollama", "base_url": "http://localhost:11434/v1",
                   "models": [], "default_model": "", "price_level": "economy",
                   "api_key": "ollama"},
    })
    monkeypatch.setattr(prov, "_probe_local_ollama", lambda url, timeout=0.8: False)
    row = prov.list_providers()[0]
    assert row["available"] is False
    assert row["requires_local_service"] is True


def test_list_providers_remote_provider_is_available(monkeypatch):
    monkeypatch.setattr(prov, "_resolve_providers", lambda: {
        "deepseek": {"key": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                     "models": [], "default_model": "deepseek-chat", "price_level": "economy",
                     "api_key": "sk-x"},
    })
    row = prov.list_providers()[0]
    assert row["available"] is True
    assert row["requires_local_service"] is False
