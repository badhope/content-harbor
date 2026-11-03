import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from service.publishing.service import Hub


def test_gateway_does_not_inject_shared_backend_token():
    root = Path(__file__).resolve().parents[2]
    nginx = (root / "deploy/nginx.conf").read_text(encoding="utf-8")
    assert "nginx.conf.template" not in (root / "deploy/Dockerfile.web").read_text(encoding="utf-8")
    compose = (root / "deploy/docker-compose.yml").read_text(encoding="utf-8")
    assert '${HARBOR_API_TOKEN}' not in nginx
    assert 'proxy_set_header X-API-Token $http_x_api_token;' in nginx
    assert '127.0.0.1:${WEB_PORT:-3000}:80' in compose


def test_shutdown_timeout_keeps_database_open():
    conn = Mock()
    hub = Hub.__new__(Hub)
    hub._close_lock = threading.Lock()
    hub._closed = False
    hub.scheduler = SimpleNamespace(stop=Mock(return_value=True))
    hub.tasks = SimpleNamespace(close=Mock(return_value=False))
    hub._browser_pool = SimpleNamespace(close_all_browsers=Mock())
    hub.conn = conn
    with pytest.raises(RuntimeError, match="任务线程未停止"):
        hub.close_all()
    conn.close.assert_not_called()
    hub._browser_pool.close_all_browsers.assert_not_called()
    assert not hub._closed


def test_forged_xff_does_not_grant_trusted_access(monkeypatch):
    """客户端自带 XFF 前缀不能被当作真实来源，末跳才是代理追加的地址。"""
    import api.auth as auth_mod

    class Request:
        client = type("C", (), {"host": "172.18.0.5"})()
        headers = {"x-forwarded-for": "127.0.0.1, 203.0.113.9"}

    monkeypatch.setenv("TRUSTED_PROXIES", "172.16.0.0/12")
    assert str(auth_mod.get_client_ip(Request())) == "203.0.113.9"
    assert auth_mod.is_trusted(Request()) is False
