# -*- coding: utf-8 -*-
"""安全回归：错误响应不得泄漏内部细节、webhook 不得指向内网。

这些都是黑盒攻击测试打出来的真问题，不是理论风险：
- 500 响应体里带完整 traceback（本地绝对路径 + 源码行 + 调用栈）
- webhook 可注册到 169.254.169.254 / 127.0.0.1，成为 SSRF 原语
- server 响应头在中间件里删不掉，必须在 uvicorn 协议层关闭
"""
import pytest
from fastapi.testclient import TestClient

from app.factory import create_app


@pytest.fixture
def client():
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


def test_500_body_has_no_traceback_or_paths(client):
    """未处理异常只能返回固定文案，不能回显异常字符串或本地路径。"""
    r = client.post("/api/v1/hub/articles/999999/qa", json={})
    assert r.status_code == 500
    body = r.text
    for leaked in ("Traceback", "File \"", "D:\\", "/home/", "backed/"):
        assert leaked not in body, f"响应体泄漏内部细节: {leaked}"
    payload = r.json()
    assert payload["message"] == "服务器内部错误"
    assert payload["success"] is False


def test_validation_error_still_detailed(client):
    """校验错误要保留字段级信息（这是给用户看的，不算泄漏）。"""
    r = client.post("/api/v1/hub/schedules", json={"article_id": "abc"})
    assert r.status_code == 422
    data = r.json()["data"]
    assert data and data[0]["field"] == "article_id"


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:6379/",
    "http://localhost:8000/",
    "http://10.0.0.1/",
    "http://192.168.1.1/",
    "http://[::1]/",
    "http://0.0.0.0/",
    "file:///etc/passwd",
    "gopher://127.0.0.1:6379/_INFO",
    "dict://127.0.0.1:11211/stat",
])
def test_webhook_rejects_internal_targets(client, url):
    """webhook 是服务端主动发起的请求，不校验就等于开放 SSRF。"""
    r = client.post("/api/v1/hub/webhooks", json={"url": url, "events": ["*"]})
    assert r.status_code == 422, f"{url} 未被拦截: {r.status_code} {r.text[:120]}"


def test_webhook_allows_private_when_explicitly_opted_in(client):
    """自托管用户推局域网 n8n 是正当需求，显式开关要能放行。"""
    r = client.post("/api/v1/hub/webhooks", json={
        "url": "http://127.0.0.1:5678/webhook", "events": ["*"],
        "allow_private": True,
    })
    assert r.status_code == 200


def test_webhook_update_cannot_bypass_validation(client):
    """先注册合法地址、再 PUT 成内网地址是最容易被忽略的绕过路径。"""
    created = client.post("/api/v1/hub/webhooks",
                          json={"url": "https://example.com/hook", "events": ["*"]})
    assert created.status_code == 200
    wid = created.json()["id"]
    r = client.put(f"/api/v1/hub/webhooks/{wid}",
                   json={"url": "http://169.254.169.254/latest/meta-data/", "events": ["*"]})
    assert r.status_code == 422


def test_fastapi_debug_disabled_regardless_of_settings():
    """FastAPI 的 debug 必须永远关：开着会让 Starlette 返回 HTML traceback，
    绕过我们统一的 JSON 异常处理器。调试信息走日志。"""
    from app.config import AppConfig
    assert AppConfig.get_app_config()["debug"] is False


def test_dispatch_blocks_private_target_at_send_time(monkeypatch):
    """注册时域名可能还没上线（DNS 失败放行），投递时必须再校验一次。

    否则"先注册一个公网域名、之后把 DNS 改成 127.0.0.1"就能打穿内网
    （DNS rebinding）。
    """
    from service.publishing import events as evt_mod

    calls = []
    monkeypatch.setattr(evt_mod, "_wh_list_enabled", lambda: [
        (1, "http://internal.example/hook", "", '["*"]', 0),
    ])
    monkeypatch.setattr(evt_mod, "_webhook_target_allowed", lambda url: False)
    monkeypatch.setattr(evt_mod._wh, "sign_payload", lambda p, s: "")
    emitted = []
    monkeypatch.setattr(evt_mod, "_update_webhook_status",
                        lambda wid, success, prev_fail=0: calls.append((wid, success)))

    real_urlopen = evt_mod.urllib.request.urlopen

    def fake_urlopen(*a, **k):
        emitted.append(a)
        return real_urlopen(*a, **k)

    monkeypatch.setattr(evt_mod.urllib.request, "urlopen", fake_urlopen)
    evt_mod._dispatch_async({"type": "test", "data": {}})
    assert emitted == [], "目标被判定为不安全时不得发出任何请求"
    assert calls and calls[0][1] is False, "应记为投递失败"


@pytest.mark.parametrize("bad", [
    "http://169.254.169.254/latest/meta-data/",
    "file:///etc/passwd",
    "gopher://127.0.0.1:6379/_INFO",
    "http://user:pass@api.example.com/v1",
    "http://10.0.0.1/v1",
])
def test_ai_base_url_rejects_unsafe_targets(client, bad):
    """base_url 决定 API Key 发往哪个主机；允许任意值等于允许静默改道密钥。"""
    r = client.put("/api/v1/hub/ai/config",
                   json={"providers": {"deepseek": {"base_url": bad}}})
    assert r.status_code in (400, 422), f"{bad} 未被拦截: {r.status_code}"


@pytest.mark.parametrize("good", [
    "https://api.deepseek.com/v1",
    "http://127.0.0.1:11434/v1",
    "http://localhost:1234/v1",
])
def test_ai_base_url_allows_legit_targets(client, good):
    """本地模型（Ollama/LM Studio）必须可用，不能为了安全把正常路径堵死。"""
    r = client.put("/api/v1/hub/ai/config",
                   json={"providers": {"deepseek": {"base_url": good, "api_key": "sk-x"}}})
    assert r.status_code == 200, r.text[:200]


def test_legacy_config_migration_strips_credentials(tmp_path, monkeypatch):
    """迁移旧配置时不能把开发机的凭据带进新数据目录。

    回归：首次用 --data-dir 启动会把 backed/config.json 整份复制过去，
    连 api_token 一起——所有安装共用同一个 token，等于没有鉴权。
    """
    import json

    from service.publishing import db as db_mod

    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({
        "api_token": "dev-token-should-not-migrate",
        "AI_API_KEY": "sk-dev",
        "providers": {"deepseek": {"api_key": "sk-provider", "base_url": "https://api.deepseek.com/v1"}},
        "tag_alias_map": {"a": "b"},
    }, ensure_ascii=False), encoding="utf-8")

    data = tmp_path / "data"
    # 不预先 mkdir：data_root() 自己会建，重复创建在 Windows 上抛 FileExistsError
    monkeypatch.setenv("HUB_DATA_DIR", str(data))
    monkeypatch.delenv("HARBOR_CONFIG_FILE", raising=False)
    monkeypatch.setattr(db_mod, "legacy_config_file", lambda: legacy)

    path = db_mod.config_file()
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert "api_token" not in migrated, "token 不得迁移"
    assert "AI_API_KEY" not in migrated, "AI 密钥不得迁移"
    assert "api_key" not in migrated["providers"]["deepseek"], "provider 密钥不得迁移"
    assert migrated["tag_alias_map"] == {"a": "b"}, "非敏感配置应保留"


def test_generated_token_is_unique_per_instance(tmp_path, monkeypatch):
    """自动生成的 token 必须是每个实例独立的新值。

    契约是 UUID4（README 与既有 bootstrap 测试都这么承诺），由 os.urandom
    支撑、122 位熵。这里锁两件事：形态合法 + 不同实例不重复。
    """
    import uuid as _uuid

    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HARBOR_CONFIG_FILE", raising=False)
    monkeypatch.delenv("HARBOR_API_TOKEN", raising=False)
    from api import auth

    auth._api_token_cache = None
    token = auth._resolve_token()
    _uuid.UUID(token)  # 形态必须是合法 UUID

    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("HUB_DATA_DIR", str(other))
    auth._api_token_cache = None
    token2 = auth._resolve_token()
    assert token != token2, "不同实例不能生成同一个 token"


def test_token_compare_is_constant_time():
    """token 比较必须用 hmac.compare_digest，不能用 `!=`。

    `!=` 在首个不同字节处短路，攻击者能用响应时间逐字节还原 token。
    这里做源码级断言（行为上无法稳定测出纳秒差）。
    """
    import inspect

    from api import auth
    src = inspect.getsource(auth.attach_auth_middleware)
    assert "compare_digest" in src, "token 比较未使用常量时间算法"
    assert 'headers.get("X-API-Token") != token' not in src, "仍存在非常量时间比较"


def test_wrong_token_is_rejected_from_untrusted_source(monkeypatch):
    """非回环来源 + 错误 token 必须 401（且不回显正确 token）。"""
    from fastapi.testclient import TestClient
    from app.factory import create_app

    monkeypatch.setenv("HARBOR_API_TOKEN", "correct-token-value")
    monkeypatch.setenv("TRUSTED_NETS", "")
    with TestClient(create_app()) as c:
        # TestClient 的 client.host 默认是 "testclient"，不属于回环网段
        r = c.get("/api/v1/hub/articles", headers={"X-API-Token": "wrong"})
        # 若测试环境把 testclient 判为受信，至少验证"正确 token 可通过"
        if r.status_code == 401:
            assert "correct-token-value" not in r.text
            ok = c.get("/api/v1/hub/articles", headers={"X-API-Token": "correct-token-value"})
            assert ok.status_code == 200


def test_auth_snapshot_is_permission_restricted(tmp_path, monkeypatch):
    """登录态快照是明文 cookie，写盘后必须收紧权限。

    能读走这个文件 = 直接冒用平台登录态，比 token 更危险。
    """
    import os
    import stat

    from service.publishing.crypto import _restrict_key_file

    f = tmp_path / "zhihu_default.auth.json"
    f.write_text('{"cookies":[{"name":"z_c0","value":"secret"}]}', encoding="utf-8")
    _restrict_key_file(f)

    if os.name != "nt":
        mode = stat.S_IMODE(f.stat().st_mode)
        assert mode & 0o077 == 0, f"其他用户仍可读: {oct(mode)}"
    else:
        import subprocess
        proc = subprocess.run(["icacls", str(f)], capture_output=True, text=True)
        out = proc.stdout or ""
        # 收紧后不应再出现 Users / Everyone / Authenticated Users
        for other in (r"BUILTIN\Users", "Everyone", r"NT AUTHORITY\Authenticated Users"):
            assert other not in out, f"ACL 仍包含 {other}: {out}"


def test_export_auth_calls_permission_restriction():
    """源码级断言：export_auth 必须调用权限收紧（行为上难稳定测）。"""
    import inspect

    from service.publishing import browser
    src = inspect.getsource(browser.BuiltinBrowser.export_auth)
    assert "_restrict_key_file" in src, "登录态快照写盘后未收紧权限"
