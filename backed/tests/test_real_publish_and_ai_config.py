# -*- coding: utf-8 -*-
"""回归测试：真实发布通道 + AI 配置端点（2026-10-14 大改造）。

覆盖：
- R1 providers 名称撞车修复：AI 路由模块（ai_providers）可达，平台工具包（providers/）不受影响
- R2 门禁三态：AI 源默认拒 / human_confirmed 放行并留痕 ext / AI_DIRECT_PUBLISH 放行
- R2b 任务派发把 human_confirmed 透传到 Hub
- R3 AI 配置：掩码读取 / 白名单写回 / 保留无关键 / 清除 key
- R4 连通性测试：协议校验、无 key、成功与失败两条路径（不触网）
"""

import json

import pytest

from service.publishing import ai_providers
from service.publishing import db
from service.publishing import gate
from service.publishing import service as service_mod
from service.publishing.tasks import TaskManager


SAFE_CONTENT = "这是一段超过两百字的正常技术正文，用于通过本地二审。" * 12


# ---------------------------------------------------------------------------
# R1 名称撞车
# ---------------------------------------------------------------------------

def test_config_paths_point_to_documented_locations():
    """配置路径契约（2026-10-14 修复）：config.json 在 backed/，风格档案在仓库 docs/。

    旧代码 parent.parent 解析到 backed/service/，与 .gitignore / api/auth.py /
    README 的文档位置不一致——AI 密钥与门禁开关实际从未从文档位置被读到，
    写作风格档案也从未加载。此测试把路径锁死。
    """
    from pathlib import Path as _P
    from service.publishing import ai as ai_mod
    from service.publishing import ai_config, gate
    from service.publishing import ai_providers

    backed = _P(__file__).resolve().parents[1]
    repo = backed.parent
    for mod in (ai_mod, ai_config, gate, ai_providers):
        assert mod.CONFIG_FILE == backed / "config.json", mod.__name__
    assert ai_mod.STYLE_FILE == repo / "docs" / "writing-style.md"
    assert ai_mod.STYLE_FILE.exists(), "写作风格档案必须真实存在于文档位置"


def test_ai_providers_module_not_shadowed():
    """R1: AI 路由模块与平台工具包各自可达（原同名包遮蔽模块 → 全体 AI 调用炸）。"""
    assert hasattr(ai_providers, "_resolve_providers")
    assert hasattr(ai_providers, "chat")
    assert hasattr(ai_providers, "is_ready")
    import service.publishing.providers as tool_pkg
    assert hasattr(tool_pkg, "resolve_provider")   # 平台包不受改名影响


def test_ai_chat_entry_no_attribute_error(monkeypatch):
    """R1: ai.chat() 入口不再在 _resolve_providers 上 AttributeError。"""
    monkeypatch.delenv("AI_API_KEY", raising=False)
    from service.publishing import ai as ai_mod
    monkeypatch.setattr(ai_mod, "_providers_mod", None)
    # 无任何可用真实 provider（只剩 ollama 兜底）时，应有明确业务错误而非 AttributeError
    try:
        ai_mod.chat([{"role": "user", "content": "hi"}])
    except Exception as e:   # aqg: top-level boundary（捕获任意异常只为断言不再是撞车症状）
        assert not isinstance(e, AttributeError), f"仍是撞车症状: {e!r}"


# ---------------------------------------------------------------------------
# R2 门禁三态
# ---------------------------------------------------------------------------

@pytest.fixture
def gate_cfg(tmp_path, monkeypatch):
    """门禁配置隔离到临时 config.json（绝不碰开发机真实配置）。"""
    cfg = tmp_path / "gate-config.json"
    monkeypatch.setattr(gate, "CONFIG_FILE", cfg)
    return cfg


def _ai_article():
    return {"id": 1, "title": "AI 稿", "content_md": SAFE_CONTENT,
            "summary": "", "source": "ai", "ai_model": "deepseek-chat", "ext": "{}"}


def test_gate_ai_direct_publish_default_rejects(gate_cfg):
    """R2/S2.1: 默认（无确认、未开直发）AI 源正式发布被拒。"""
    r = gate.gate_publish(_ai_article(), draft_only=False)
    assert r["approved"] is False
    assert any("人工确认" in i for i in r["issues"])


def test_gate_human_confirmed_allows(gate_cfg):
    """R2/S2.2: human_confirmed=True 放行。"""
    r = gate.gate_publish(_ai_article(), draft_only=False, human_confirmed=True)
    assert r["approved"] is True
    assert r["human_confirmed"] is True


def test_gate_ai_direct_publish_flag_allows(gate_cfg):
    """R2: AI_DIRECT_PUBLISH=true 放行（用户明确开启直发）。"""
    gate_cfg.write_text(json.dumps({"AI_DIRECT_PUBLISH": True}), encoding="utf-8")
    r = gate.gate_publish(_ai_article(), draft_only=False)
    assert r["approved"] is True


def test_gate_draft_only_still_allows_ai(gate_cfg):
    """R2: 草稿路径不受影响。"""
    r = gate.gate_publish(_ai_article(), draft_only=True)
    assert r["approved"] is True


# ---------------------------------------------------------------------------
# R2b 服务层留痕 + 派发透传
# ---------------------------------------------------------------------------

@pytest.fixture
def demo_hub(sandbox, monkeypatch, gate_cfg):
    """DEMO 模式 Hub（不碰浏览器/外部 API），门禁配置隔离到临时文件。"""
    monkeypatch.setenv("DEMO", "1")
    hub = service_mod.Hub()
    yield hub
    # 完整回收（含 scheduler 守护线程），防解释器退出期线程碰 sqlite
    hub.close_all()
    try:
        hub.conn.close()
    except Exception:   # aqg: top-level boundary（测试夹具清理，不得影响用例结论）
        pass


def test_service_publish_human_confirmed_stamps_ext(demo_hub):
    """R2/S2.2: 人工确认后正式发布，ext 留下 human_confirmed_at 审计痕迹。"""
    aid = db.create_article(demo_hub.conn, "AI 稿", SAFE_CONTENT, source="ai")
    results = demo_hub.publish(aid, ["zhihu"], human_confirmed=True)
    assert results and results[0].get("ok") is True
    art = dict(db.get_article(demo_hub.conn, aid))
    ext = json.loads(art.get("ext") or "{}")
    assert ext.get("human_confirmed_publish") is True
    assert isinstance(ext.get("human_confirmed_at"), float)


def test_service_publish_unconfirmed_ai_rejected_422(demo_hub):
    """R2/S2.1: 未确认的 AI 稿正式发布 → GateError（HTTP 层映射 422），发布记录不产生。

    I5 起 service 层抛领域异常而非 HTTPException：MCP/CLI 同一路径也能识别业务错误；
    422 由 app/exceptions.py 的 GateError handler 保证（见 test_audit_20261027）。
    """
    from service.publishing.gate import GateError
    aid = db.create_article(demo_hub.conn, "AI 稿2", SAFE_CONTENT, source="ai")
    with pytest.raises(GateError):
        demo_hub.publish(aid, ["zhihu"])


def test_dispatch_passes_human_confirmed():
    """R2b: 任务派发把 extra.human_confirmed 透传给 hub.publish。"""
    class _StubHub:
        def __init__(self):
            self.calls = []

        def publish(self, *a, **kw):
            self.calls.append((a, kw))
            return []

    class _Shell:
        pass

    shell = _Shell()
    shell.hub = _StubHub()
    task = {"article_id": 1, "platforms": '["zhihu"]', "account": "default",
            "draft_only": 0, "result": '{"human_confirmed": true}'}
    TaskManager._dispatch(shell, "publish", task)
    assert shell.hub.calls[0][1].get("human_confirmed") is True


def test_publish_endpoint_preflight_rejects_unconfirmed_ai(sandbox, monkeypatch, gate_cfg):
    """端口预检：未确认 AI 稿 → 提交前同步 422（不落任务）；确认后 → 200 入队。

    背景：发布走任务引擎是异步的，若拒绝全留给任务，前端要到任务跑完才知道被拒；
    确定性拒绝（AI 未确认 / 高危词）必须在提交前就地 422。
    """
    from fastapi.testclient import TestClient
    from app.factory import create_app

    monkeypatch.setenv("DEMO", "1")
    hub = service_mod.Hub()
    import api.deps as hub_deps
    hub_deps._hub_instance = hub
    try:
        c = TestClient(create_app())
        aid = db.create_article(hub.conn, "AI 预检", SAFE_CONTENT, source="ai")
        r = c.post(f"/api/v1/hub/articles/{aid}/publish",
                   json={"platforms": ["zhihu"], "draft_only": False})
        assert r.status_code == 422
        body = r.json()
        # 项目自定义异常包是 {code, message, success, data}，不是 FastAPI 默认 detail
        msg = body.get("message") or body.get("detail") or json.dumps(body, ensure_ascii=False)
        assert "人工确认" in msg, msg

        r2 = c.post(f"/api/v1/hub/articles/{aid}/publish",
                    json={"platforms": ["zhihu"], "draft_only": False,
                          "human_confirmed": True})
        assert r2.status_code == 200
        assert r2.json().get("task_id")
    finally:
        hub.close_all()   # 停 scheduler/worker 等守护线程（防测试进程退出期 AV）
        try:
            hub.conn.close()
        except Exception:   # aqg: top-level boundary（测试夹具清理，同上）
            pass
        hub_deps._hub_instance = None


# ---------------------------------------------------------------------------
# R3 AI 配置读写
# ---------------------------------------------------------------------------

@pytest.fixture
def ai_cfg_file(tmp_path, monkeypatch):
    """ai_config 与 gate 的 CONFIG_FILE 都指到临时文件；清掉可能存在的 env key。"""
    from service.publishing import ai_config
    cfg = tmp_path / "ai-config.json"
    cfg.write_text(json.dumps({"HARBOR_API_TOKEN": "secret-token-keepme"}),
                   encoding="utf-8")
    monkeypatch.setattr(ai_config, "CONFIG_FILE", cfg)
    monkeypatch.setattr(gate, "CONFIG_FILE", cfg)
    for env in ("AI_API_KEY", "OPENAI_API_KEY", "KIMI_API_KEY", "QWEN_API_KEY",
                "GLM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return cfg


def test_ai_config_mask_and_roundtrip(ai_cfg_file):
    """R3/S3.1+S3.2: 掩码读取、白名单写回、无关键保留、清除语义。"""
    from service.publishing import ai_config

    out = ai_config.update_ai_config({
        "providers": {"deepseek": {"api_key": "sk-1234567890abcdef",
                                   "default_model": "deepseek-reasoner"}},
        "review_model": "glm-4-air",
        "ai_direct_publish": True,
    })
    # 写入结果：掩码，不含完整 key
    ds = next(p for p in out["providers"] if p["key"] == "deepseek")
    assert ds["key_masked"] == "sk-1\u2026cdef"
    assert ds["key_set"] is True
    assert "sk-1234567890abcdef" not in json.dumps(out, ensure_ascii=False)
    assert out["review"]["ai_direct_publish"] is True
    # 文件：无关键保留 + 明文 key 确实落盘（本地文件本来就是明文存储面）
    raw = json.loads(ai_cfg_file.read_text(encoding="utf-8"))
    assert raw["HARBOR_API_TOKEN"] == "secret-token-keepme"
    assert raw["providers"]["deepseek"]["api_key"] == "sk-1234567890abcdef"
    assert raw["AI_DIRECT_PUBLISH"] is True
    assert raw["REVIEW_MODEL"] == "glm-4-air"

    # 清除：空串删 key
    out2 = ai_config.update_ai_config({"providers": {"deepseek": {"api_key": ""}}})
    ds2 = next(p for p in out2["providers"] if p["key"] == "deepseek")
    assert ds2["key_set"] is False
    raw2 = json.loads(ai_cfg_file.read_text(encoding="utf-8"))
    assert "api_key" not in raw2["providers"]["deepseek"]


def test_ai_config_rejects_broken_json(tmp_path, monkeypatch):
    """R3: config.json 解析失败时拒绝写入（不写坏文件）。"""
    from service.publishing import ai_config
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(ai_config, "CONFIG_FILE", bad)
    with pytest.raises(ValueError):
        ai_config.update_ai_config({"ai_direct_publish": True})
    assert bad.read_text(encoding="utf-8") == "{ not json"   # 未被覆盖


# ---------------------------------------------------------------------------
# R4 连通性测试（离线模拟，不触网）
# ---------------------------------------------------------------------------

def test_test_provider_success(monkeypatch):
    """R4: 成功路径返回 ok + 延迟 + 模型名。"""
    from service.publishing import ai_config
    monkeypatch.setattr(ai_providers, "chat_with_provider",
                        lambda p, m, **kw: ("pong", p["default_model"]))
    r = ai_config.test_provider("deepseek", api_key="sk-test")
    assert r["ok"] is True and r["model"] == "deepseek-chat"


def test_test_provider_errors_are_data_not_exceptions(monkeypatch):
    """R4/S4.1: 连接失败返回 ok=false 结构化错误，不抛（端点据此返 200 数据体）。"""
    from service.publishing import ai_config

    def _boom(p, m, **kw):
        raise ai_providers.ProviderError("连接被拒绝")

    monkeypatch.setattr(ai_providers, "chat_with_provider", _boom)
    r = ai_config.test_provider("deepseek", api_key="sk-test")
    assert r["ok"] is False and "连接被拒绝" in r["error"]

    r2 = ai_config.test_provider("deepseek", base_url="ftp://x", api_key="k")
    assert r2["ok"] is False and "协议" in r2["error"]

    r3 = ai_config.test_provider("deepseek")
    assert r3["ok"] is False and "API Key" in r3["error"]
