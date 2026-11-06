# -*- coding: utf-8 -*-
"""AI 配置读写信封：掩码输出 + 白名单原子写 config.json + 连通性测试。

安全约定（AI 密钥是最高敏感面）：
  - 读取一律掩码（sk-a…wxyz），永不回显完整密钥；
  - 写入只允许白名单键（providers.* / REVIEW_MODEL / AI_DIRECT_PUBLISH），
    其余键（HARBOR_API_TOKEN / FERNET_KEY 等）原样保留；
  - 原子写（tmp + os.replace），写前 JSON 解析失败直接拒绝，绝不写坏文件；
  - 连通性测试允许对"还没保存的字段"先测（draft 覆盖），永远返回 {ok, error} 不抛。
"""

import json
import os
import re
import time
from pathlib import Path

from service.publishing.db import config_file
from urllib.parse import urlparse

from service.publishing import ai_providers

# config.json 的文档位置是 backed/config.json（2026-10-14 路径修复，同 gate.py）
ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = config_file()

# 可写白名单
TOP_LEVEL_KEYS = {"REVIEW_MODEL", "AI_DIRECT_PUBLISH"}
PROVIDER_FIELDS = {"api_key", "base_url", "name", "models", "default_model",
                   "price_level", "timeout"}
_KEY_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def mask_key(key: str) -> str:
    """密钥掩码：只露前 4 后 4，短密钥全遮。"""
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 8:
        return "****"
    return f"{k[:4]}\u2026{k[-4:]}"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))  # 解析失败抛异常，上层拒绝写
    if not isinstance(data, dict):
        raise ValueError("config.json 顶层必须是 JSON 对象")
    return data


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    # 与 F-09 同策略：config.json 里存着各 Provider 明文密钥，落盘后收紧 ACL/权限
    try:
        from service.publishing.crypto import _restrict_key_file
        _restrict_key_file(path)
    except Exception:   # aqg: top-level boundary（权限收紧失败不阻断配置写入）
        pass


def _env_key_of(key: str) -> str:
    """该 provider 对应的环境变量密钥（ollama 不需要 key）。"""
    p = ai_providers.DEFAULT_PROVIDERS.get(key) or {}
    env_name = (p.get("api_key_env") or "").strip()
    if not env_name or env_name == "ollama":
        return ""
    return os.environ.get(env_name, "").strip()


def get_ai_config() -> dict:
    """AI 配置全貌（掩码，给设置页用，永不含完整密钥）。"""
    from service.publishing import gate as gate_mod
    from service.publishing import ai as ai_mod

    try:
        cfg = _read_json(CONFIG_FILE)
    except Exception:   # aqg: top-level boundary（读配置失败降级为空，设置页显示未配置）
        cfg = {}
    prov_overrides = cfg.get("providers", {}) or {}
    if not isinstance(prov_overrides, dict):
        prov_overrides = {}
    resolved = ai_providers._resolve_providers()

    def _entry(key: str, name: str, base_url: str, models, default_model,
               price_level: str) -> dict:
        ov = prov_overrides.get(key, {}) or {}
        env_key = _env_key_of(key)
        file_key = (ov.get("api_key") or "").strip()
        effective = env_key or file_key
        live = resolved.get(key) or {}
        # Ollama 不需要密钥，光看配置永远是 available——本机没起服务时
        # 卡片显示「可用」而点开就连接被拒。这里补一次真实探活。
        live_ok = key in resolved
        if live_ok and key == "ollama":
            live_ok = ai_providers._probe_local_ollama(
                live.get("base_url") or ov.get("base_url") or base_url)
        return {
            "key": key,
            "name": ov.get("name", name),
            "base_url": live.get("base_url") or ov.get("base_url") or base_url,
            "models": ov.get("models", models),
            "default_model": ov.get("default_model", default_model),
            "price_level": ov.get("price_level", price_level),
            "key_set": bool(effective) or key == "ollama",
            "key_masked": mask_key(effective) if key != "ollama" else "(无需密钥)",
            "key_source": ("env" if env_key else
                           "config" if file_key else
                           "builtin" if key == "ollama" else "none"),
            "available": live_ok,
            "requires_local_service": key == "ollama",
        }

    items = [
        _entry(key, p["name"], p["base_url"], p["models"], p["default_model"],
               p["price_level"])
        for key, p in ai_providers.DEFAULT_PROVIDERS.items()
    ]
    # config.json 里的自定义 provider（不在默认表）
    for key, ov in prov_overrides.items():
        if key in ai_providers.DEFAULT_PROVIDERS or not isinstance(ov, dict):
            continue
        env_key = os.environ.get(ov.get("api_key_env", f"{key.upper()}_API_KEY"), "").strip()
        file_key = (ov.get("api_key") or "").strip()
        effective = env_key or file_key
        items.append({
            "key": key, "name": ov.get("name", key),
            "base_url": (ov.get("base_url") or "").rstrip("/"),
            "models": ov.get("models", []),
            "default_model": ov.get("default_model", ""),
            "price_level": ov.get("price_level", "standard"),
            "key_set": bool(effective), "key_masked": mask_key(effective),
            "key_source": "env" if env_key else ("config" if file_key else "none"),
            "available": key in resolved, "custom": True,
        })

    legacy_key = os.environ.get("AI_API_KEY", "").strip() or (cfg.get("AI_API_KEY") or "").strip()
    return {
        "providers": items,
        "routing": ai_providers.TASK_ROUTING,
        "review": {
            "model": cfg.get("REVIEW_MODEL", ""),
            "ai_direct_publish": gate_mod.is_ai_direct_publish(),
            "gate_enabled": gate_mod.is_enabled(),
            "dangerous_terms": len(gate_mod.DANGEROUS_PATTERNS),
        },
        "legacy": {
            "base_url": cfg.get("AI_BASE_URL", ""),
            "model": cfg.get("AI_MODEL", ""),
            "api_key_set": bool(legacy_key),
            "api_key_masked": mask_key(legacy_key),
        },
        "style": {
            "file": str(ai_mod.style_file()),
            "loaded": bool(ai_mod.load_style()),
        },
        "ready": bool(resolved),
    }


def update_ai_config(patch: dict) -> dict:
    """白名单合并写。

    patch 形状：{providers: {key: {field: value}}, review_model?, ai_direct_publish?}
    api_key 语义：字段缺省 = 不改；"" = 清除；其它 = 设置。
    返回写入后的 get_ai_config()（掩码）。
    """
    try:
        cfg = _read_json(CONFIG_FILE)
    except Exception as e:   # aqg: top-level boundary（解析失败转 ValueError，拒绝写入防写坏）
        raise ValueError(f"config.json 解析失败，拒绝写入（防写坏）: {e}")

    provs = cfg.get("providers")
    if not isinstance(provs, dict):
        provs = {}
    changed = False

    for key, fields in (patch.get("providers") or {}).items():
        if not _KEY_RE.match(key or ""):
            raise ValueError(f"非法 provider key: {key!r}")
        if not isinstance(fields, dict):
            continue
        target = provs.get(key)
        if not isinstance(target, dict):
            target = {}
        for f, v in fields.items():
            if f not in PROVIDER_FIELDS:
                continue
            if f == "api_key":
                v = ("" if v is None else str(v)).strip()
                if v == "":
                    target.pop("api_key", None)
                else:
                    target["api_key"] = v
            elif v is None:
                target.pop(f, None)
            else:
                target[f] = v
            changed = True
        if target:
            provs[key] = target
        else:
            provs.pop(key, None)

    if "review_model" in patch and patch["review_model"] is not None:
        v = str(patch["review_model"]).strip()
        if v:
            cfg["REVIEW_MODEL"] = v
        else:
            cfg.pop("REVIEW_MODEL", None)
        changed = True
    if "ai_direct_publish" in patch and patch["ai_direct_publish"] is not None:
        cfg["AI_DIRECT_PUBLISH"] = bool(patch["ai_direct_publish"])
        changed = True

    if provs:
        cfg["providers"] = provs
    elif "providers" in cfg:
        cfg.pop("providers", None)
    if changed:
        _atomic_write(CONFIG_FILE, cfg)
    return get_ai_config()


def test_provider(provider: str, model: str = "", base_url: str = "",
                  api_key: str = "") -> dict:
    """对指定 provider 做真实最小 chat 调用（ping→pong），校验连通/鉴权/模型名。

    允许对未保存的 draft 字段先测：base_url/api_key 传了就用传的。
    永远返回 {ok, ...}，不抛异常。
    """
    resolved = ai_providers._resolve_providers()
    base = dict(resolved.get(provider) or
                ai_providers.DEFAULT_PROVIDERS.get(provider) or {})
    p = {
        "key": provider,
        "name": base.get("name", provider),
        "base_url": (base_url or base.get("base_url", "")).rstrip("/"),
        "api_key": (api_key or base.get("api_key", "") or "").strip(),
        "default_model": model or base.get("default_model", ""),
        "timeout": min(int(base.get("timeout", 60) or 60), 60),
    }
    if not p["base_url"]:
        # 未传 draft 且无解析结果（如 Anthropic 需要代理地址）
        return {"ok": False, "error": "base_url 为空，请先填写接口地址"}
    scheme = urlparse(p["base_url"]).scheme
    if scheme not in ("http", "https"):
        return {"ok": False, "error": f"base_url 协议不支持: {scheme or '(空)'}"}
    if not p["api_key"] and provider != "ollama":
        return {"ok": False, "error": "未配置 API Key"}
    if provider == "ollama" and not p["api_key"]:
        p["api_key"] = "ollama"   # Ollama 不校验 key，占位

    t0 = time.time()
    try:
        text, used = ai_providers.chat_with_provider(
            p, [{"role": "user", "content": "ping，只回复 pong"}],
            max_tokens=8, temperature=0)
    except Exception as e:   # aqg: top-level boundary（连通测试失败转结构化错误，端点不抛 500）
        return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                "error": str(e)[:300]}
    return {"ok": True, "latency_ms": int((time.time() - t0) * 1000),
            "model": used, "reply": (text or "")[:60]}
