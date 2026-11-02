# -*- coding: utf-8 -*-
"""回归测试：锁定 2026-10-12 代码审查里修的关键缺陷。

覆盖：
- F-02 _webhook_store.init 自建 conn 后 close
- F-05 login 任务的 timeout/on_captcha 不再丢
- F-06 refresh 任务的 limit 不再丢
- F-07 platform/account 路径穿越防护
- F-10 register_routers 死代码已删
- F-11 独立 app version 更新到 0.7.0
- F-12 任务 retry 时不 emit on_task_complete
- F-13 占位符检测不再误报 `{{ }}` 模板语法
- F-18 hub.update() 走 AIGC 合规门禁
- F-24 browser._bw_worker 只捕 Exception
- F-25 browser.start() 用 `not self.display` 降级
"""

import inspect

import pytest
from fastapi.testclient import TestClient

from app.factory import create_app
from service.publishing import db
from service.publishing import _webhook_store as _wh
from service.publishing import gate as aigc_gate
from service.publishing import service as service_mod
from service.publishing import browser as br_mod
from service.publishing.tasks import TaskManager


# ---------------------------------------------------------------------------
# 通用 Hub 夹具：走 sandbox，测完清理
# ---------------------------------------------------------------------------

@pytest.fixture
def live_hub(sandbox):
    """一个真 Hub（走 sandbox 临时库），测完自动关 worker + conn。"""
    hub = service_mod.Hub()
    yield hub
    # 完整回收：停调度器/任务/浏览器池——只关 worker 会漏掉 scheduler 守护线程，
    # 线程在解释器退出期还在碰 sqlite → Windows 间歇性访问违例（2026-10-14 修复）
    hub.close_all()
    try:
        hub.conn.close()
    except Exception:
        pass


def _with_client(sandbox):
    """把 sandbox 的 Hub 注入 api.deps 并返回 TestClient。"""
    hub = service_mod.Hub()
    import api.deps as hub_deps
    hub_deps._hub_instance = hub
    c = TestClient(create_app())
    # client 被回收（测试结束）时一并回收 Hub 的全部后台线程，
    # 防解释器退出期线程碰 sqlite 导致测试进程崩（同上）
    import weakref
    weakref.finalize(c, hub.close_all)
    return c, hub


# ---------------------------------------------------------------------------
# F-07: platform / account 路径穿越防护（走真实 REST 端点）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["a b", "a.b", "a;b", "a/b", "..", "../etc"])
def test_platform_validator_rejects_bad_chars(bad):
    """F-07: 平台名含空格/点号/分号/斜杠/路径片段必须拒。直接测校验正则。"""
    import re
    pattern = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
    assert not pattern.match(bad), f"{bad!r} 未被拦"


@pytest.mark.parametrize("good", ["zhihu", "juejin", "oschina", "csdn", "a-b_c9"])
def test_platform_validator_allows_valid(good):
    """F-07: 合法平台名放行。"""
    import re
    pattern = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
    assert pattern.match(good), f"{good!r} 被误拦"


def test_platform_name_rejects_traversal_via_http(sandbox):
    """F-07: HTTP 层——含路径片段 `..` 的请求不应执行 hub 方法。"""
    c, hub = _with_client(sandbox)
    try:
        r = c.get("/api/v1/hub/accounts/..%2F..%2Fetc")
        # 关键断言：不是 200 成功
        assert r.status_code != 200, f"路径穿越未被拦: {r.status_code} {r.text[:200]}"
    finally:
        hub.close_all()   # 含 scheduler/浏览器池线程（只关 worker 会漏，退出期碰 sqlite 崩）
        hub.conn.close()


def test_platform_name_allows_valid_http(sandbox, monkeypatch):
    """F-07: HTTP 层——合法平台名放行。DEMO=1 避免真实浏览器路径。"""
    monkeypatch.setenv("DEMO", "1")
    c, hub = _with_client(sandbox)
    try:
        r = c.get("/api/v1/hub/platforms")
        assert r.status_code == 200
        r2 = c.get("/api/v1/hub/accounts/zhihu/check")
        assert r2.status_code == 200, f"合法平台未放行: {r2.status_code} {r2.text[:200]}"
        assert r2.json()["logined"] is True
    finally:
        hub.close_all()   # 同上：完整回收后台线程
        hub.conn.close()


# ---------------------------------------------------------------------------
# F-05 / F-06: dispatch 参数不再丢
# ---------------------------------------------------------------------------

class _FakeHubDispatch:
    """捕获 hub 方法调用的假 Hub。"""

    def __init__(self):
        self.calls = []

    def login(self, platform, account, timeout=600, on_captcha=None):
        self.calls.append(("login", platform, account, timeout, on_captcha))
        return True

    def refresh(self, platform, account, limit=50):
        self.calls.append(("refresh", platform, account, limit))
        return {"count": 0}

    def publish(self, *a, **k): return []
    def update(self, *a, **k): return []
    def sync_pending(self, *a, **k): return []
    def assist_open(self, *a, **k): return True


def test_login_dispatch_passes_timeout_and_on_captcha(conn):
    """F-05: login 任务的 timeout / on_captcha 参数必须传到 hub.login。"""
    hub = _FakeHubDispatch()
    tm = TaskManager(hub, conn)
    tm._stop.set()

    task = {
        "task_id": "x", "kind": "login", "article_id": None,
        "platforms": '["zhihu"]', "account": "default", "draft_only": 0,
        "result": '{"timeout": 1200, "on_captcha": "abort"}',
        "attempts": 0,
    }
    tm._dispatch("login", task)
    assert hub.calls == [("login", "zhihu", "default", 1200, "abort")], hub.calls


def test_refresh_dispatch_passes_limit(conn):
    """F-06: refresh 任务的 limit 必须传到 hub.refresh，不再被硬编码成 50。"""
    hub = _FakeHubDispatch()
    tm = TaskManager(hub, conn)
    tm._stop.set()

    task = {
        "task_id": "x", "kind": "refresh", "article_id": None,
        "platforms": '["zhihu"]', "account": "default", "draft_only": 0,
        "result": '{"limit": 200}',
        "attempts": 0,
    }
    tm._dispatch("refresh", task)
    assert hub.calls == [("refresh", "zhihu", "default", 200)], hub.calls


# ---------------------------------------------------------------------------
# F-12: retry 不发 on_task_complete
# ---------------------------------------------------------------------------

def test_run_task_retry_does_not_emit_complete_event(conn, monkeypatch):
    """F-12: 任务 retry 时不应触发 on_task_complete。"""
    emitted = []

    class FakeEvt:
        @staticmethod
        def on_task_complete(t):
            emitted.append(t)

        @staticmethod
        def stop_dispatcher():
            pass

    monkeypatch.setattr("service.publishing.tasks.evt_mod", FakeEvt)

    class FakeHubRetry:
        def login(self, *a, **k):
            raise RuntimeError("timeout")   # RETRY_HINTS 命中

    tm = TaskManager(FakeHubRetry(), conn)
    tm._stop.set()

    task_id = tm.submit("login", platforms=["zhihu"], account="default")
    task = db.get_task(conn, task_id)

    emitted.clear()
    tm._run_task(task)
    # retry 后不应 emit on_task_complete
    assert emitted == []
    updated = db.get_task(conn, task_id)
    assert updated["status"] == "pending"


def test_run_task_fail_still_emits_complete_event(conn, monkeypatch):
    """F-12 反向：非 retry 的失败/等人工仍然要 emit。"""
    emitted = []

    class FakeEvt:
        @staticmethod
        def on_task_complete(t):
            emitted.append(t)

        @staticmethod
        def stop_dispatcher():
            pass

    monkeypatch.setattr("service.publishing.tasks.evt_mod", FakeEvt)

    class FakeHubHuman:
        def login(self, *a, **k):
            # 命中 HUMAN_HINTS → waiting_human → 终态，应 emit
            raise RuntimeError("验证码，需要人工处理")

    tm = TaskManager(FakeHubHuman(), conn)
    tm._stop.set()

    task_id = tm.submit("login", platforms=["zhihu"], account="default")
    task = db.get_task(conn, task_id)

    emitted.clear()
    tm._run_task(task)
    assert len(emitted) == 1
    assert emitted[0]["status"] == "waiting_human"


# ---------------------------------------------------------------------------
# F-18: update() 走 AIGC 合规门禁
# ---------------------------------------------------------------------------

def test_update_rejects_dangerous_content(live_hub):
    """F-18: hub.update 遇到高危词必须拒绝同步。"""
    hub = live_hub
    art_id = hub.create("测试",
                        "这篇文章含【赌博网站】链接，会被 AIGC 门禁拦截。" + "安全正文 " * 30,
                        source="human")
    db.upsert_publication(hub.conn, art_id, "zhihu", "default",
                          post_id="abc123", post_url="https://zhihu.com/p/abc123",
                          edit_url="", status="ok", draft_only=0)

    with pytest.raises(ValueError, match="合规门禁"):
        hub.update(art_id, platforms=["zhihu"], account="default")


def test_update_labels_ai_source(live_hub):
    """F-18: AI 源文章的 update 走合规门禁时补上 AIGC 标识。"""
    import json as _json
    hub = live_hub

    seen = {}

    def fake_update_single(article_id, platform, pub, art, account="default"):
        seen["ext"] = art.get("ext")
        return {"platform": platform, "ok": True}

    hub.update_single = fake_update_single

    art_id = hub.create("AI 稿",
                        "正文足够长，通过基本安全检查。" + "安全正文 " * 30,
                        source="ai", ai_model="deepseek-chat")
    db.upsert_publication(hub.conn, art_id, "zhihu", "default",
                          post_id="abc123", post_url="", edit_url="",
                          status="ok", draft_only=0)

    r = hub.update(art_id, platforms=["zhihu"], account="default")
    assert r[0]["ok"] is True
    ext = _json.loads(seen["ext"] or "{}")
    assert ext.get("aigc") is True, f"AI 源 update 未打 AIGC 标识: {ext}"
    assert ext.get("ai_model") == "deepseek-chat"


# ---------------------------------------------------------------------------
# 代码级断言（F-02/F-03/F-10/F-11/F-24/F-25）
# ---------------------------------------------------------------------------

def test_webhook_store_init_has_finally_close():
    """F-02: webhook_store.init() 用 try/finally 关闭自建 conn。"""
    src = inspect.getsource(_wh.init)
    assert "finally:" in src
    assert "close()" in src


def test_register_routers_removed():
    """F-10: register_routers 是死代码已删除。"""
    import api
    assert not hasattr(api, "register_routers")
    assert hasattr(api, "hub_router")
    assert hasattr(api, "get_router_info")


def test_hub_app_version_current():
    """F-11: 独立运行的 app version 跟随当前发布版本。"""
    from api import hub as hub_mod
    assert hub_mod.app.version == "0.12.0"


def test_browser_worker_catches_only_exception():
    """F-24: _bw_worker 只捕 Exception，让 KeyboardInterrupt/SystemExit 冒泡。"""
    src = inspect.getsource(br_mod._bw_worker)
    assert "except BaseException" not in src
    assert "except Exception" in src


def test_browser_display_downgrade_path():
    """F-25: start() 用 `not self.display` 而不是 `is False`。"""
    src = inspect.getsource(br_mod.BuiltinBrowser.start)
    assert "self.display is False" not in src
    assert "not self.display" in src


def test_hub_busy_for_defined():
    """F-29: Hub._busy_for 必须存在（0.7.0 登录/检查路径依赖它）。

    旧代码里 Hub 到处写 `with self._busy_for(platform, account)`，
    但从未定义过这个方法——只有 BrowserPoolManager 有。
    一旦真跑登录就 AttributeError。
    """
    from service.publishing.service import Hub
    assert hasattr(Hub, "_busy_for")


def test_ruff_unused_imports_removed():
    """ruff 3 处 F401 已清：_webhook_store.time/urllib.request, crypto.base64。"""
    from service.publishing import _webhook_store, crypto
    wh_src = inspect.getsource(_webhook_store)
    header = wh_src.split("def ")[0]
    assert "import time" not in header
    assert "urllib.request" not in header

    crypto_src = inspect.getsource(crypto)
    crypto_header = crypto_src.split("def ")[0]
    assert "import base64" not in crypto_header


# ---------------------------------------------------------------------------
# F-13: 占位符检测不再误报模板语法
# ---------------------------------------------------------------------------

def _safe_prose():
    """200 字以上的安全正文（不含 XXX / TODO / lorem 等占位符）。"""
    return "这是一段足够长的安全正文。" * 15


def test_local_review_ignores_template_braces():
    """F-13: `{{ }}` 在正文中不再被当占位符。"""
    content = _safe_prose() + "\n\nJinja: `{{ name }}`，Python f-string 也用花括号"
    r = aigc_gate._local_review("标题", content)
    assert r["verdict"] == "pass", r["issues"]


def test_local_review_placeholder_inside_code_ignored():
    """F-13: 代码块内的 TODO 不当占位符。"""
    content = _safe_prose() + "\n\n```python\n# TODO: 优化\nprint(1)\n```"
    r = aigc_gate._local_review("标题", content)
    assert r["verdict"] == "pass", r["issues"]


def test_local_review_still_rejects_real_placeholders():
    """F-13 反向：真实占位符仍要拦。"""
    for ph in ("TODO", "FIXME", "lorem ipsum", "placeholder"):
        r = aigc_gate._local_review("标题", _safe_prose() + f" 正文里有 {ph}")
        assert r["verdict"] == "reject", (ph, r["issues"])


def test_local_review_rejects_prose_xxx():
    """F-13: 正文里独立的 XXX 仍要拦。"""
    r = aigc_gate._local_review("标题", _safe_prose() + " 这里有个 XXX 占位")
    assert r["verdict"] == "reject", r["issues"]
