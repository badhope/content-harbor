# -*- coding: utf-8 -*-
"""探活路径必须免鉴权，业务路径必须鉴权。

容器编排与负载均衡器在服务未就绪时没有 Token，如果 /health 也要 Token，
Docker healthcheck 永远 401 → 容器永远 unhealthy → 依赖它的 web 服务永远不启动。
这组用例把这条边界锁死，避免以后"顺手统一鉴权"时把探活也一起收进去。
"""

import pytest
from fastapi.testclient import TestClient

from app.factory import create_app

TOKEN = "test-placeholder-token"   # 占位值，故意低熵，避免被密钥扫描命中
UNTRUSTED = ("10.1.2.3", 12345)   # 模拟来自 nginx 容器的非回环来源


@pytest.fixture()
def client(monkeypatch):
    """conftest 的 autouse 夹具把 _api_token 桩成空串（让其它 REST 测试免鉴权），
    这里必须覆盖回来，否则测不到鉴权边界。"""
    import api.auth as auth
    monkeypatch.setattr(auth, "_api_token", lambda: TOKEN)
    app = create_app()
    yield TestClient(app, client=UNTRUSTED)


def test_health_is_reachable_without_token(client):
    """探活端点免鉴权：编排器的 probe 不带 Token。"""
    assert client.get("/health").status_code == 200


def test_business_routes_still_require_token(client):
    """业务路由不能因为探活放行而一起放开。"""
    assert client.get("/api/v1/hub/platforms").status_code == 401
    assert client.get("/api/v1/hub/platforms",
                      headers={"X-API-Token": "wrong"}).status_code == 401
    assert client.get("/api/v1/hub/platforms",
                      headers={"X-API-Token": TOKEN}).status_code == 200


def test_health_response_carries_no_business_data(client):
    """探活放行不等于泄露：返回体只有进程状态字段。"""
    body = client.get("/health").json()
    assert body.get("status") == "ok"
    assert "articles" not in body
    assert "accounts" not in body
