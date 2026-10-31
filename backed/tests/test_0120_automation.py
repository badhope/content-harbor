# -*- coding: utf-8 -*-
"""0.12.0 自动化补全回归：bootstrap / auth_sweep / 浏览器操控面 / 带登录态导入。

断言行为而不是断言"mock 被调用"：状态翻转、落库结果、响应契约、密码零痕迹。
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.factory import create_app
from service.publishing import db, importing
from service.publishing.auth_sweep import AuthSweeper, status as sweep_status
from service.publishing.browser_ops import validate_url
from service.publishing.importing import ImportError_


# ---------------- 切片 1：cnblogs bootstrap（凭据零落库） ----------------

@pytest.fixture
def client(sandbox):
    from tests.test_api_integration import StubHub

    hub = StubHub(db.connect(sandbox["db_file"]))
    # 每个用例自己往 stub 上挂 bootstrap_cnblogs
    import api.deps as hub_deps
    hub_deps._hub_instance = hub

    # 内存 job 注册表逐用例清空（模块级状态）
    import api.routers.accounts as acc
    acc._BOOTSTRAP_JOBS.clear()

    with TestClient(create_app()) as c:
        yield c, hub
    hub.conn.close()
    acc._BOOTSTRAP_JOBS.clear()


class TestBootstrap:
    def test_bootstrap_success_writes_result(self, client):
        c, hub = client

        def _ok(username, password, account="default", timeout=600):
            return {"outcome": "success", "metaweblog": {"endpoint": "e", "username": "u", "token": "t"}}
        hub.bootstrap_cnblogs = _ok

        r = c.post("/api/v1/hub/accounts/cnblogs/bootstrap",
                   json={"username": "u", "password": "SECRET"})
        assert r.status_code == 200
        jid = r.json()["job_id"]

        r2 = c.get(f"/api/v1/hub/accounts/cnblogs/bootstrap/{jid}")
        assert r2.status_code == 200
        # 反证：密码绝不回显
        assert "SECRET" not in r2.text
        for _ in range(50):
            r2 = c.get(f"/api/v1/hub/accounts/cnblogs/bootstrap/{jid}")
            if r2.json()["status"] != "running":
                break
            time.sleep(0.1)
        assert r2.json()["status"] == "succeeded"
        assert r2.json()["result"]["outcome"] == "success"
        assert "SECRET" not in r2.text

    def test_bootstrap_failure_reports_error(self, client):
        c, hub = client

        def _fail(username, password, account="default", timeout=600):
            return {"outcome": "fail", "reason": "密码错误"}
        hub.bootstrap_cnblogs = _fail

        r = c.post("/api/v1/hub/accounts/cnblogs/bootstrap",
                   json={"username": "u", "password": "SECRET"})
        jid = r.json()["job_id"]
        j = {}
        for _ in range(50):
            j = c.get(f"/api/v1/hub/accounts/cnblogs/bootstrap/{jid}").json()
            if j["status"] != "running":
                break
            time.sleep(0.1)
        assert j["status"] == "failed"
        assert "密码错误" in j["error"]
        assert "SECRET" not in c.get(f"/api/v1/hub/accounts/cnblogs/bootstrap/{jid}").text

    def test_bootstrap_rejects_empty_credentials(self, client):
        c, _ = client
        r = c.post("/api/v1/hub/accounts/cnblogs/bootstrap",
                   json={"username": "", "password": ""})
        assert r.status_code == 422

    def test_bootstrap_concurrent_409(self, client):
        c, hub = client
        gate = threading.Event()

        def _slow(username, password, account="default", timeout=600):
            gate.wait(timeout=5)
            return {"outcome": "success"}
        hub.bootstrap_cnblogs = _slow

        r1 = c.post("/api/v1/hub/accounts/cnblogs/bootstrap",
                    json={"username": "u", "password": "p"})
        assert r1.status_code == 200
        # 等 job 真正进入 running（线程已启动、还没结束）
        jid = r1.json()["job_id"]
        for _ in range(50):
            if c.get(f"/api/v1/hub/accounts/cnblogs/bootstrap/{jid}").json()["status"] == "running":
                # 线程在 gate 上阻塞 → 仍是 running；但线程可能还没执行到 gate.wait
                break
            time.sleep(0.05)
        r2 = c.post("/api/v1/hub/accounts/cnblogs/bootstrap",
                    json={"username": "u2", "password": "p2"})
        assert r2.status_code == 409
        gate.set()

    def test_bootstrap_job_unknown_404(self, client):
        c, _ = client
        assert c.get("/api/v1/hub/accounts/cnblogs/bootstrap/nope").status_code == 404

    def test_bootstrap_password_never_persisted(self, client, sandbox):
        """密码不进任务 DB / config.json——凭据零痕迹。"""
        c, hub = client
        hub.bootstrap_cnblogs = lambda *a, **kw: {"outcome": "success"}
        r = c.post("/api/v1/hub/accounts/cnblogs/bootstrap",
                   json={"username": "u", "password": "TOPSECRET"})
        assert r.status_code == 200
        time.sleep(0.4)
        # 任务库里查不到
        rows = db.list_tasks(hub.conn, limit=999)
        for t in rows:
            assert "TOPSECRET" not in str(dict(t))
        # config.json 里查不到
        from pathlib import Path
        cfg = Path(__file__).resolve().parents[1] / "config.json"
        if cfg.exists():
            assert "TOPSECRET" not in cfg.read_text(encoding="utf-8")


# ---------------- 切片 2：auth_sweep 登录态巡检 ----------------

class SweepHub:
    """最小 Hub 桩：check 结果可编排，conn 连临时库。"""

    def __init__(self, conn, results):
        self.conn = conn
        self.results = results          # {(platform, account): True/False/raise}
        self.checked = []

    def check(self, platform, account="default"):
        self.checked.append((platform, account))
        r = self.results[(platform, account)]
        if isinstance(r, Exception):
            raise r
        return r

    def _busy_for(self, platform, account):
        import threading
        return threading.Lock()   # 新锁默认未持有：locked() == False → 不跳过


class TestAuthSweep:
    def test_expired_flips_status_and_counts(self, sandbox):
        conn = db.connect(sandbox["db_file"])
        db.upsert_account(conn, "juejin", "default", "-", "logined")
        db.upsert_account(conn, "csdn", "default", "-", "logined")
        hub = SweepHub(conn, {("juejin", "default"): False, ("csdn", "default"): True})
        s = AuthSweeper(hub, interval_min=30)
        s.sweep_once()
        st = {r["platform"]: r["status"] for r in db.list_accounts(conn)}
        assert st["juejin"] == "offline"        # False → 标离线
        assert st["csdn"] == "logined"          # True → 不动
        assert sweep_status()["expired"] >= 1
        assert sweep_status()["checked"] >= 2
        conn.close()

    def test_error_keeps_status_untouched(self, sandbox):
        conn = db.connect(sandbox["db_file"])
        db.upsert_account(conn, "zhihu", "default", "-", "logined")
        hub = SweepHub(conn, {("zhihu", "default"): RuntimeError("浏览器弹不出来")})
        AuthSweeper(hub, interval_min=30).sweep_once()
        st = {r["platform"]: r["status"] for r in db.list_accounts(conn)}
        assert st["zhihu"] == "logined"         # 异常 ≠ 过期，状态不动
        assert sweep_status()["errors"] >= 1
        conn.close()

    def test_skips_non_logined_accounts(self, sandbox):
        conn = db.connect(sandbox["db_file"])
        db.upsert_account(conn, "toutiao", "default", "-", "offline")
        hub = SweepHub(conn, {})
        AuthSweeper(hub, interval_min=30).sweep_once()
        assert hub.checked == []                # offline 账号不巡检
        conn.close()

    def test_interval_zero_starts_no_thread(self, sandbox):
        conn = db.connect(sandbox["db_file"])
        s = AuthSweeper(SweepHub(conn, {}), interval_min=0)
        s.start()
        assert s._thread is None                # 禁用时不启线程
        s.stop()
        conn.close()


# ---------------- 切片 3：浏览器操控面 ----------------

class TestBrowserOps:
    def test_validate_url_rejects_non_http(self):
        with pytest.raises(ValueError):
            validate_url("file:///etc/passwd")
        with pytest.raises(ValueError):
            validate_url("ftp://x.com/a")
        assert validate_url("https://juejin.cn/post/1") == "https://juejin.cn/post/1"

    def test_validate_url_ssrf_blacklist(self):
        for bad in ("http://localhost:8000/api", "http://169.254.169.254/latest/meta-data",
                    "http://evil.localhost/x", "http://metadata.google.internal/"):
            with pytest.raises(ValueError):
                validate_url(bad)

    def test_fetch_endpoint_contract(self, client, monkeypatch):
        c, _ = client
        import service.publishing.browser_ops as bo
        monkeypatch.setattr(bo, "browser_fetch",
                            lambda hub, platform, url, **kw: {
                                "url": url, "title": "T", "text": "正文",
                                "html_len": 10})
        r = c.post("/api/v1/hub/browser/juejin/fetch",
                   json={"url": "https://juejin.cn/post/1"})
        assert r.status_code == 200
        assert r.json()["title"] == "T" and r.json()["text"] == "正文"

    def test_fetch_endpoint_rejects_bad_url(self, client):
        c, _ = client
        r = c.post("/api/v1/hub/browser/juejin/fetch", json={"url": "file:///x"})
        assert r.status_code == 422

    def test_fetch_endpoint_502_on_error(self, client, monkeypatch):
        c, _ = client
        import service.publishing.browser_ops as bo
        def _boom(*a, **kw):
            raise RuntimeError("浏览器起不来")
        monkeypatch.setattr(bo, "browser_fetch", _boom)
        r = c.post("/api/v1/hub/browser/juejin/fetch",
                   json={"url": "https://juejin.cn/post/1"})
        assert r.status_code == 502

    def test_screenshot_endpoint_contract(self, client, monkeypatch, sandbox):
        c, _ = client
        from service.publishing.db import data_root
        import service.publishing.browser_ops as bo
        target = data_root() / "exports" / "screenshots" / "mock.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"png")
        monkeypatch.setattr(bo, "browser_screenshot",
                            lambda hub, platform, url, **kw: {
                                "path": str(target), "url": url, "bytes": 3})
        r = c.post("/api/v1/hub/browser/juejin/screenshot",
                   json={"url": "https://juejin.cn/post/1"})
        assert r.status_code == 200
        out = r.json()
        assert str(data_root()) in out["path"]      # 落盘在 data_root 内
        assert out["bytes"] > 0


# ---------------- 切片 4：带登录态导入 ----------------

class TestImportBrowser:
    def test_browser_import_requires_platform(self, client):
        c, _ = client
        r = c.post("/api/v1/hub/articles/import",
                   json={"url": "https://example.com/a", "browser": True})
        assert r.status_code == 422

    def test_browser_import_creates_article(self, client, monkeypatch):
        c, hub = client
        meta = {"title": "登录墙后", "summary": "s", "tags": "t",
                "sitename": "ex", "author": "a"}
        monkeypatch.setattr(importing, "fetch_url_browser",
                            lambda url, platform, hub_, **kw: (meta, "# 正文\n" * 30))
        r = c.post("/api/v1/hub/articles/import",
                   json={"url": "https://example.com/a", "browser": True,
                         "platform": "juejin"})
        assert r.status_code == 200
        out = r.json()
        assert out["source"] == "browser" and out["title"] == "登录墙后"
        row = hub.conn.execute("SELECT title, source, origin_url FROM articles "
                               "WHERE id=?", (out["id"],)).fetchone()
        assert row["source"] == "import" and row["origin_url"] == "https://example.com/a"

    def test_browser_import_maps_error_to_422(self, client, monkeypatch):
        c, _ = client
        def _fail(url, platform, hub_, **kw):
            raise ImportError_("未能提取到有效正文")
        monkeypatch.setattr(importing, "fetch_url_browser", _fail)
        r = c.post("/api/v1/hub/articles/import",
                   json={"url": "https://example.com/a", "browser": True,
                         "platform": "juejin"})
        assert r.status_code == 422

    def test_fetch_url_browser_wires_want_html(self, sandbox, monkeypatch):
        """browser_fetch(want_html=True) 的结果交同一套抽取后处理。"""
        captured = {}

        def _fake(hub, platform, url, account="default", timeout=60, want_html=False):
            captured["want_html"] = want_html
            return {"url": url, "title": "T", "html": "<html><body><article>"
                    "<h1>标题</h1><p>" + "很长的正文。" * 200 + "</p></article></body></html>"}

        monkeypatch.setattr("service.publishing.browser_ops.browser_fetch", _fake)
        conn = db.connect(sandbox["db_file"])
        import types
        hub_stub = types.SimpleNamespace(conn=conn)
        meta, body = importing.fetch_url_browser("https://example.com/a", "juejin", hub_stub)
        assert captured["want_html"] is True
        assert "很长的正文" in body
        assert meta["title"]
        conn.close()
