# -*- coding: utf-8 -*-
"""发布模式 + AI 声明改走平台选项（2026-10-14）。

覆盖：
- R1 正文不再追加 AI 声明（ext 留痕保留）；`AI_DECLARATION_IN_TEXT` 开关可恢复
- R1b settings.ai_assist / mode 从 settings → options 全链路透传
- R2 三模式：auto 不中断 / assist 中断到 pending_user / manual 只开页面
- R3 demo 模式任何 mode 都不开浏览器
"""

import json

import pytest

from service.publishing import db
from service.publishing import gate
from service.publishing import service as service_mod

SAFE_CONTENT = "这是一段超过两百字的正常技术正文，用于发布模式测试。" * 12


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """gate/ai 的配置隔离到临时文件（不碰开发机真实 config.json）。"""
    from service.publishing import ai as ai_mod
    from service.publishing import ai_config
    from service.publishing import ai_providers
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    for mod in (gate, ai_mod, ai_config, ai_providers):
        monkeypatch.setattr(mod, "CONFIG_FILE", p)
        if hasattr(mod, "BACKED_ROOT"):
            monkeypatch.setattr(mod, "BACKED_ROOT", tmp_path)
    return p


def _art(source="ai"):
    return {"id": 1, "title": "标题", "content_md": SAFE_CONTENT,
            "summary": "", "source": source, "ai_model": "test-model", "ext": "{}"}


# ---------------------------------------------------------------------------
# R1 声明改走平台选项
# ---------------------------------------------------------------------------

def test_no_declaration_appended_to_body_by_default(cfg):
    """默认：正文一个字都不加；ext 留痕照旧（可追溯性保留）。"""
    r = gate.apply_gate_before_publish(_art(), draft_only=False, human_confirmed=True)
    art = r["article"]
    assert "本文由 AI 辅助生成" not in art["content_md"]
    assert art["content_md"] == SAFE_CONTENT
    ext = json.loads(art["ext"])
    assert ext["aigc"] is True and ext["ai_model"] == "test-model"


def test_declaration_in_text_opt_in(cfg):
    """config 打开 AI_DECLARATION_IN_TEXT 时恢复正文声明（无原生选项平台用）。"""
    cfg.write_text(json.dumps({"AI_DECLARATION_IN_TEXT": True}), encoding="utf-8")
    r = gate.apply_gate_before_publish(_art(), draft_only=False, human_confirmed=True)
    assert "本文由 AI 辅助生成" in r["article"]["content_md"]


def test_manual_draft_only_still_labels_ext(cfg):
    r = gate.apply_gate_before_publish(_art(), draft_only=True)
    ext = json.loads(r["article"]["ext"])
    assert ext["aigc"] is True
    assert "本文由 AI 辅助生成" not in r["article"]["content_md"]


# ---------------------------------------------------------------------------
# R1b settings 透传
# ---------------------------------------------------------------------------

class _Shell:
    pass


def test_publish_single_passes_ai_assist_and_mode(monkeypatch):
    """auto 路径下 settings[platform] 的 ai_assist / mode 必须出现在适配器 options 里。"""
    captured = {}

    class _Ad:
        needs_browser = False
        id = "zhihu"
        name = "知乎"

        def check_auth(self, page):
            return True

        def publish(self, page, article, options=None):
            captured.update(options or {})
            return {"post_id": "1", "post_url": "u", "edit_url": "u/e"}

    monkeypatch.setattr(service_mod, "get_adapter", lambda pf: _Ad())

    class _Hub:
        demo = False
        conn = None
        delay_platform = (0, 0)

        # publish_single 现在会先做平台级内容变换，再进适配器。
        # 这里用真实方法（纯函数，无副作用），只是把宿主换成极简 stub。
        _prepare_article_for_platform = (
            service_mod.Hub._prepare_article_for_platform)
        # 0.16.0 把 options 组装抽成了 _publish_options（发布/更新共用）。
        # stub 里用真实实现（纯函数，无副作用），只换宿主。
        _publish_options = service_mod.Hub._publish_options
        # 0.15.0 起 publish_single 在成功落库后调回读验证。本用例只关心 options
        # 透传；真实方法会打网络，显式 stub 成"平台不支持"，同时锁住
        # "回读不得改变发布返回结构"这条约束。
        def _verify_publication(self, *a, **kw):
            return "回读：平台不支持"

        def _with_adapter(self, platform, account, fn, page_hook=None):
            return fn(_Ad(), None)

        def _publish_with_handoff(self, *a, **kw):   # assist/manual 会分流到这里
            return {"platform": "zhihu", "ok": True, "status": "pending_user",
                    "pending_user": True}

    monkeypatch.setattr(service_mod.db, "add_job", lambda *a, **kw: 1)
    monkeypatch.setattr(service_mod.db, "finish_job", lambda *a, **kw: None)
    monkeypatch.setattr(service_mod.db, "upsert_publication", lambda *a, **kw: None)

    # auto：进适配器，两个字段都要在 options 里
    service_mod.Hub.publish_single(_Hub(), 1, "zhihu", {"title": "t", "content_md": "c"},
                                   settings={"zhihu": {"ai_assist": True, "mode": "auto"}})
    assert captured.get("ai_assist") is True
    assert captured.get("mode") == "auto"

    captured.clear()
    # assist：不进适配器 publish（中断分流），由 _publish_with_handoff 接手
    r = service_mod.Hub.publish_single(_Hub(), 1, "zhihu", {"title": "t", "content_md": "c"},
                                       settings={"zhihu": {"ai_assist": True, "mode": "assist"}})
    assert not captured, "assist 模式不应直接调用适配器 publish"
    assert r.get("pending_user") is True


# ---------------------------------------------------------------------------
# R2 三模式
# ---------------------------------------------------------------------------

@pytest.fixture
def demo_hub(sandbox, monkeypatch, cfg):
    monkeypatch.setenv("DEMO", "1")
    hub = service_mod.Hub()
    yield hub
    hub.close_all()
    try:
        hub.conn.close()
    except Exception:   # aqg: top-level boundary（测试夹具清理，不得影响用例结论）
        pass


def _bare_hub(sandbox):
    """非 demo 的裸 Hub：不跑 __init__（不起任务/调度线程），只挂 db 连接。"""
    hub = service_mod.Hub.__new__(service_mod.Hub)
    hub.conn = db.connect(sandbox["db_file"])
    hub.demo = False
    hub.delay_platform = (0, 0)
    return hub


def test_mode_auto_no_interruption(demo_hub):
    aid = db.create_article(demo_hub.conn, "t", SAFE_CONTENT, source="ai")
    r = demo_hub.publish(aid, ["zhihu"], human_confirmed=True, mode="auto")
    assert r and r[0].get("ok") is True


def test_mode_assist_goes_pending_user(sandbox, monkeypatch, cfg):
    """assist：浏览器打开、标题预填、不点提交 → 落 pending_user 且已释放池锁。"""
    hub = _bare_hub(sandbox)
    seen = {}

    class _Ad:
        id = "zhihu"
        name = "知乎"
        needs_browser = True
        new_url = "https://zhuanlan.zhihu.com/write"

    class _Page:
        url = "https://zhuanlan.zhihu.com/write"
        def goto(self, url, **kw): self.url = url
        def fill(self, sel, val, **kw): seen["filled_title"] = val

    class _Br:
        def new_page(self): return _Page()

    monkeypatch.setattr(service_mod, "get_adapter", lambda pf: _Ad())
    monkeypatch.setattr(service_mod.Hub, "_acquire", lambda self, pf, ac: _Br())
    monkeypatch.setattr(service_mod, "browser_thread_run", lambda fn: fn())
    monkeypatch.setattr(service_mod.db, "add_job", lambda *a, **kw: 1)
    monkeypatch.setattr(service_mod.db, "finish_job", lambda *a, **kw: None)
    monkeypatch.setattr(service_mod.db, "upsert_publication", lambda *a, **kw: None)
    locked = {"held_during_wait": None}

    class _Lock:
        def __enter__(self):
            locked["held_during_wait"] = True
            return self

        def __exit__(self, *a):
            locked["held_during_wait"] = False

    monkeypatch.setattr(service_mod.Hub, "_busy_for", lambda self, pf, ac: _Lock())

    art = {"title": "中断测试标题", "content_md": SAFE_CONTENT, "source": "human", "ext": "{}"}
    r = service_mod.Hub.publish_single(hub, 1, "zhihu", art,
                                       settings={"zhihu": {"mode": "assist"}})
    assert r.get("pending_user") is True
    assert r.get("status") == "pending_user"
    assert seen.get("filled_title") == "中断测试标题"   # assist 预填了标题
    assert locked["held_during_wait"] is False          # 等待前已释放池锁（关键）
    hub.conn.close()


def test_mode_manual_opens_blank_editor(sandbox, monkeypatch, cfg):
    """manual：只开平台新建页，不做任何自动填写。"""
    hub = _bare_hub(sandbox)
    seen = {}

    class _Ad:
        id = "zhihu"
        name = "知乎"
        needs_browser = True
        new_url = "https://zhuanlan.zhihu.com/write"

    class _Page:
        url = "about:blank"
        def goto(self, url, **kw): self.url = url
        def fill(self, sel, val, **kw): seen["filled_title"] = val

    class _Br:
        def new_page(self): return _Page()

    monkeypatch.setattr(service_mod, "get_adapter", lambda pf: _Ad())
    monkeypatch.setattr(service_mod.Hub, "_acquire", lambda self, pf, ac: _Br())
    monkeypatch.setattr(service_mod, "browser_thread_run", lambda fn: fn())
    monkeypatch.setattr(service_mod.db, "add_job", lambda *a, **kw: 1)
    monkeypatch.setattr(service_mod.db, "finish_job", lambda *a, **kw: None)
    monkeypatch.setattr(service_mod.db, "upsert_publication", lambda *a, **kw: None)
    monkeypatch.setattr(service_mod.Hub, "_busy_for",
                        lambda self, pf, ac: __import__("contextlib").nullcontext())

    art = {"title": "不该被填的标题", "content_md": SAFE_CONTENT, "source": "human", "ext": "{}"}
    r = service_mod.Hub.publish_single(hub, 1, "zhihu", art,
                                       settings={"zhihu": {"mode": "manual"}})
    assert r.get("pending_user") is True
    assert "filled_title" not in seen        # manual 一个字都不填
    hub.conn.close()


def test_protocol_platform_falls_back_to_auto(sandbox, monkeypatch, cfg):
    """纯协议平台（needs_browser=False）没有「等人」中间态 → 退化为自动发布。"""
    hub = _bare_hub(sandbox)
    called = {}

    def _fake_auto(self, aid, pf, art, **kw):
        called["settings_mode"] = (kw.get("settings") or {}).get(pf, {}).get("mode")
        return {"platform": pf, "ok": True, "status": "ok"}

    class _Ad:
        needs_browser = False
        id = "cnblogs"
        name = "博客园"

    monkeypatch.setattr(service_mod, "get_adapter", lambda pf: _Ad())
    monkeypatch.setattr(service_mod.Hub, "publish_single", _fake_auto)
    art = {"title": "t", "content_md": SAFE_CONTENT, "source": "human", "ext": "{}"}
    service_mod.Hub._publish_with_handoff(hub, 1, "cnblogs", art,
                                          settings={"cnblogs": {"mode": "assist"}}, mode="assist")
    assert called["settings_mode"] == "auto"    # 已降级为自动
    hub.conn.close()


def test_mode_manual_does_not_prefill(demo_hub):
    """manual 下也不能带 AI 声明进正文（正文恒等于原文）。"""
    aid = db.create_article(demo_hub.conn, "t2", SAFE_CONTENT, source="ai")
    r = demo_hub.publish(aid, ["zhihu"], human_confirmed=True, mode="manual")
    assert r[0].get("ok") is True
    art = dict(db.get_article(demo_hub.conn, aid))
    assert "本文由 AI 辅助生成" not in art["content_md"]


# ---------------------------------------------------------------------------
# R3 demo 短路
# ---------------------------------------------------------------------------

def test_demo_mode_never_opens_browser_any_mode(demo_hub, monkeypatch):
    opened = {"n": 0}
    monkeypatch.setattr(service_mod, "_open_for_user_publish",
                        lambda *a, **kw: opened.__setitem__("n", opened["n"] + 1),
                        raising=False)
    aid = db.create_article(demo_hub.conn, "t3", SAFE_CONTENT)
    for m in ("auto", "assist", "manual"):
        r = demo_hub.publish(aid, ["zhihu"], human_confirmed=True, mode=m)
        assert r[0].get("ok") is True, m
    assert opened["n"] == 0
