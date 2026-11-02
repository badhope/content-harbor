# -*- coding: utf-8 -*-
"""api_token 引导行为：环境变量优先 / 自动生成落盘 / 失败收敛 / 既有值不动。

README 的环境变量表一直承诺「api_token 未配置时自动生成 UUID」，但重构后
auth 只读 config.json、空 token 直接放行，承诺落空。本文件把承诺变回机器判定。
"""

import json
import uuid

import pytest

import api.auth as auth_mod


@pytest.fixture()
def sandbox_root(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_mod, "ROOT", tmp_path)
    monkeypatch.setattr(auth_mod, "_api_token_cache", None)
    monkeypatch.delenv("HARBOUR_API_TOKEN", raising=False)
    return tmp_path


def test_env_var_wins_without_persist(sandbox_root, monkeypatch):
    monkeypatch.setenv("HARBOUR_API_TOKEN", "env-token-1")
    assert auth_mod._resolve_token() == "env-token-1"
    assert not (sandbox_root / "config.json").exists()


def test_autogenerate_persists_uuid(sandbox_root):
    tok = auth_mod._resolve_token()
    uuid.UUID(tok)
    cfg = json.loads((sandbox_root / "config.json").read_text(encoding="utf-8"))
    assert cfg["api_token"] == tok
    assert auth_mod._resolve_token() == tok


def test_autogenerate_preserves_existing_keys(sandbox_root):
    cfg_path = sandbox_root / "config.json"
    cfg_path.write_text(json.dumps({"ai_api_key": "k", "note": "保持不变"}),
                        encoding="utf-8")
    tok = auth_mod._resolve_token()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg["api_token"] == tok
    assert cfg["ai_api_key"] == "k" and cfg["note"] == "保持不变"


def test_existing_token_returned_untouched(sandbox_root):
    cfg_path = sandbox_root / "config.json"
    raw = json.dumps({"api_token": "keep-me", "note": "x"}, indent=2)
    cfg_path.write_text(raw, encoding="utf-8")
    assert auth_mod._resolve_token() == "keep-me"
    assert cfg_path.read_text(encoding="utf-8") == raw


def test_generation_failure_falls_back_to_random(sandbox_root):
    (sandbox_root / "config.json").mkdir()
    tok = auth_mod._resolve_token()
    assert tok and len(tok) >= 16
