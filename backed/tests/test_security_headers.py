# -*- coding: utf-8 -*-
"""安全响应头必须默认存在。

回归：``middleware_security_headers_enabled`` 默认 False，实测默认部署的
/health 一个安全头都没有——自托管用户裸奔在点击劫持与 MIME 嗅探下。
"""
from fastapi.testclient import TestClient


def _client(monkeypatch):
    monkeypatch.setenv("HARBOR_API_TOKEN", "test-token")
    from app.factory import create_app
    return TestClient(create_app())


def test_security_headers_present_by_default(monkeypatch):
    with _client(monkeypatch) as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"
        assert "content-security-policy" in {k.lower() for k in r.headers}
        assert "permissions-policy" in {k.lower() for k in r.headers}


def test_csp_does_not_allow_arbitrary_script_origins(monkeypatch):
    with _client(monkeypatch) as c:
        csp = c.get("/health").headers.get("content-security-policy", "")
        assert "frame-ancestors 'none'" in csp
        assert "script-src 'self'" in csp
        assert "unsafe-eval" not in csp


def test_server_header_not_leaked(monkeypatch):
    with _client(monkeypatch) as c:
        r = c.get("/health")
        assert "server" not in {k.lower() for k in r.headers}
