# -*- coding: utf-8 -*-
"""多模型 Provider 注册表 + 任务分级路由。

解决的核心问题：原来只有一套全局 AI 配置（DeepSeek），无法：
  - 按任务分级（标签/摘要用便宜快速的模型，写作用高质量的旗舰）
  - 切换模型调用方指定（写稿用 Claude，润义用 Kimi）
  - 多 Provider 主备切换（主 Provider 挂了自动备援）

设计：
  - Provider = (name, base_url, api_key, 可用模型列表, 价格级别)
  - 价格级别：economy（便宜快）/ standard（均衡）/ premium（旗舰质量）
  - 任务分级路由：按 AI 任务名自动匹配最合适的 Provider
  - 前端 GET /ai/providers 列出所有可用 Provider 和模型，下拉选择
  - 任意 AI 调用指定 model="provider:model_name" 格式走指定，不指定时按分级路由
"""

import json
import os
import threading
import time
from pathlib import Path

from service.publishing.db import config_file

import requests as _req

# config.json 的文档位置是 backed/config.json（2026-10-14 路径修复，同 gate.py）
ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = config_file()

# Provider 价格级别：决定默认路由
PRICE_LEVELS = {"economy": 0, "standard": 1, "premium": 2}

DEFAULT_PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "AI_API_KEY",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
        "price_level": "economy",
        "timeout": 180,
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "models": ["gpt-4o-mini", "gpt-4o", "o1-mini"],
        "default_model": "gpt-4o-mini",
        "price_level": "standard",
        "timeout": 180,
    },
    "anthropic": {
        "name": "Anthropic (OpenAI-compat proxy)",
        "base_url": "",   # 需要配置 ANTHROPIC_BASE_URL 或通过 config.json 指定
        "api_key_env": "ANTHROPIC_API_KEY",
        "models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514"],
        "default_model": "claude-sonnet-4-20250514",
        "price_level": "premium",
        "timeout": 240,
    },
    "kimi": {
        "name": "Moonshot Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "KIMI_API_KEY",
        "models": ["moonshot-v1-auto", "moonshot-v1-8k", "moonshot-v1-32k"],
        "default_model": "moonshot-v1-auto",
        "price_level": "standard",
        "timeout": 180,
    },
    "qwen": {
        "name": "通义千问 (DashScope)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "QWEN_API_KEY",
        "models": ["qwen-turbo", "qwen-plus", "qwen-max"],
        "default_model": "qwen-plus",
        "price_level": "economy",
        "timeout": 180,
    },
    "glm": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "GLM_API_KEY",
        "models": ["glm-4-flash", "glm-4-air", "glm-4-plus"],
        "default_model": "glm-4-air",
        "price_level": "economy",
        "timeout": 180,
    },
    "ollama": {
        "name": "本地 Ollama",
        "base_url": "http://localhost:11434/v1",
        "api_key_env": "ollama",  # Ollama 不需要 key，但有这个 env 就启用
        "models": ["llama3.1", "qwen2.5", "deepseek-r1"],
        "default_model": "qwen2.5",
        "price_level": "economy",
        "timeout": 300,
    },
}

# 任务分级路由：按任务复杂度自动选择价格级别
# economy → standard → premium，写作用 premium，标签/摘要用 economy
TASK_ROUTING = {
    "write": "premium",          # 写稿：质量优先
    "rewrite": "premium",        # 改写：质量优先
    "polish": "standard",        # 润色：均衡
    "summarize": "economy",      # 摘要：便宜快速
    "tags": "economy",           # 标签：便宜快速
    "translate": "standard",     # 翻译：均衡
    "image_prompts": "economy",  # 配图提示：便宜快速
    "outline": "economy",        # 大纲：便宜快速
    "seo": "economy",            # SEO：便宜快速
}

# 全局强制覆盖：环境变量 PROVIDER_ECONOMY=deepseek 强制所有 economy 任务走 deepseek
ENV_LEVEL_OVERRIDE = {
    "economy": os.environ.get("PROVIDER_ECONOMY", "").strip(),
    "standard": os.environ.get("PROVIDER_STANDARD", "").strip(),
    "premium": os.environ.get("PROVIDER_PREMIUM", "").strip(),
}


class ProviderError(Exception):
    pass


class _ProviderState:
    """Provider 运行时状态：失败计数 + 冷却时间，自动备援。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.fail_count = {}       # provider_key -> 连续失败次数
        self.cool_until = {}       # provider_key -> 冷却截止时间
        self.last_used = {}        # provider_key -> 上次使用时间

    def is_cooling(self, key):
        until = self.cool_until.get(key, 0)
        return until > time.time()

    def record_fail(self, key):
        with self._lock:
            self.fail_count[key] = self.fail_count.get(key, 0) + 1
            if self.fail_count[key] >= 3:
                self.cool_until[key] = time.time() + 120  # 冷却 2 分钟

    def record_ok(self, key):
        with self._lock:
            self.fail_count[key] = 0
            self.last_used[key] = time.time()


_state = _ProviderState()


def _load_file_cfg():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _resolve_providers():
    """解析 Provider 配置：环境变量 + config.json 覆盖默认值。"""
    cfg = _load_file_cfg()
    providers_cfg = cfg.get("providers", {})

    result = {}
    for key, p in DEFAULT_PROVIDERS.items():
        override = providers_cfg.get(key, {})
        api_key = os.environ.get(p["api_key_env"], "").strip()
        if not api_key and p["api_key_env"] != "ollama":
            # 有 api_key_env 但不是 ollama，才需要 key
            api_key_override = override.get("api_key", "").strip()
            if api_key_override:
                api_key = api_key_override

        # Ollama 不需要 key，其他必须有 key
        if not api_key and key != "ollama":
            continue

        base_url = override.get("base_url", "").strip() or p["base_url"]
        if not base_url:
            continue

        result[key] = {
            "key": key,
            "name": override.get("name", p["name"]),
            "base_url": base_url.rstrip("/"),
            "api_key": api_key or "ollama",
            "models": override.get("models", p["models"]),
            "default_model": override.get("default_model", p["default_model"]),
            "price_level": override.get("price_level", p["price_level"]),
            "timeout": override.get("timeout", p["timeout"]),
        }

    # 自定义 Provider：config.json 里 providers.<key> 不在 DEFAULT_PROVIDERS 的
    for key, override in providers_cfg.items():
        if key in result or key in DEFAULT_PROVIDERS:
            continue
        api_key = os.environ.get(override.get("api_key_env", f"{key.upper()}_API_KEY"), "").strip()
        base_url = override.get("base_url", "").strip()
        if not base_url:
            continue
        if not api_key and key != "ollama":
            continue
        result[key] = {
            "key": key,
            "name": override.get("name", key),
            "base_url": base_url.rstrip("/"),
            "api_key": api_key or "ollama",
            "models": override.get("models", []),
            "default_model": override.get("default_model", override.get("models", [""])[0]),
            "price_level": override.get("price_level", "standard"),
            "timeout": override.get("timeout", 180),
        }

    return result


def list_providers():
    """返回已配置的 Provider 列表（masked api_key）。"""
    providers = _resolve_providers()
    return [{
        "key": p["key"],
        "name": p["name"],
        "base_url": p["base_url"],
        "models": p["models"],
        "default_model": p["default_model"],
        "price_level": p["price_level"],
        "has_key": bool(p["api_key"]),
        "cooling": _state.is_cooling(p["key"]),
    } for p in providers.values()]


def _select_provider_for_level(level, preferred_key=None):
    """按价格级别选 Provider，冷却中的跳过。preferred_key 优先。"""
    providers = _resolve_providers()
    if not providers:
        raise ProviderError("没有可用的 AI Provider。配置环境变量 AI_API_KEY 或 config.json")

    # 强制覆盖
    forced = ENV_LEVEL_OVERRIDE.get(level, "")
    if forced and forced in providers and not _state.is_cooling(forced):
        return providers[forced], providers[forced]["default_model"]

    # 指定优先
    if preferred_key and preferred_key in providers:
        if not _state.is_cooling(preferred_key):
            return providers[preferred_key], providers[preferred_key]["default_model"]

    # 找该级别的 Provider，排除冷却中的
    candidates = [p for p in providers.values()
                  if p["price_level"] == level and not _state.is_cooling(p["key"])]
    if not candidates:
        # 降级：标准 → premium 都找不到（冷却全部）→ 选任意非冷却
        candidates = [p for p in providers.values() if not _state.is_cooling(p["key"])]
    if not candidates:
        # 全部冷却 → 选冷却最快结束的
        providers_list = list(providers.values())
        providers_list.sort(key=lambda p: _state.cool_until.get(p["key"], 0))
        return providers_list[0], providers_list[0]["default_model"]

    # 按上次使用排序：轮流使用
    candidates.sort(key=lambda p: _state.last_used.get(p["key"], 0))
    return candidates[0], candidates[0]["default_model"]


def resolve_model(model_hint=None, task="standard", preferred_provider=None):
    """解析最终使用的 Provider + model。

    model_hint 格式：
      - None → 按 task 级别路由
      - "provider:model_name" → 指定
      - "model_name"（无冒号）→ 第一个有该模型的 Provider
    """
    if not model_hint:
        provider, model = _select_provider_for_level(
            TASK_ROUTING.get(task, "standard"), preferred_provider)
        return provider, model

    if ":" in model_hint:
        pkey, _, mname = model_hint.partition(":")
        provs = _resolve_providers()
        if pkey in provs:
            prov = provs[pkey]
            if mname in prov["models"] or not prov["models"]:
                return prov, mname
            return prov, prov["default_model"]

    # 只有 model_name，找有该模型的 Provider
    provs = _resolve_providers()
    for p in provs.values():
        if model_hint in p["models"]:
            return p, model_hint

    # 都没找到，走默认路由
    return _select_provider_for_level(TASK_ROUTING.get(task, "standard"))


def chat_with_provider(provider, messages, model=None, temperature=0.7, max_tokens=4096, **kw):
    """指定 Provider 发送 chat 请求。返回 (response_text, model_used)。"""
    actual_model = model or provider["default_model"]
    try:
        r = _req.post(
            f"{provider['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {provider['api_key']}",
                     "Content-Type": "application/json"},
            json={"model": actual_model, "messages": messages,
                  "temperature": temperature, "max_tokens": max_tokens, **kw},
            timeout=provider["timeout"],
        )
    except Exception as e:
        _state.record_fail(provider["key"])
        raise ProviderError(f"调不通 {provider['name']}（{provider['base_url']}）：{e}")

    if r.status_code != 200:
        _state.record_fail(provider["key"])
        raise ProviderError(f"{provider['name']} 返回 {r.status_code}: {r.text[:200]}")

    try:
        data = r.json()
        text = data["choices"][0]["message"]["content"].strip()
    except Exception:
        _state.record_fail(provider["key"])
        raise ProviderError(f"{provider['name']} 返回结构异常")

    _state.record_ok(provider["key"])
    return text, actual_model


def chat(messages, model=None, task="standard", temperature=0.7, max_tokens=4096,
         preferred_provider=None, **kw):
    """高级 chat 接口：自动选择 Provider + 模型。返回 dict(text, model, provider, level)。"""
    provider, actual_model = resolve_model(model, task, preferred_provider)
    text, used_model = chat_with_provider(
        provider, messages, model=actual_model,
        temperature=temperature, max_tokens=max_tokens, **kw)
    return {
        "text": text,
        "model": used_model,
        "provider": provider["key"],
        "level": provider["price_level"],
    }


# ---------------- 便捷：和 ai.py 兼容的兼容层 ----------------

def legacy_chat(messages, model=None, temperature=0.7, max_tokens=4096):
    """兼容 ai.py 的旧 chat 签名：返回纯文本。内部走模型路由。"""
    result = chat(messages, model=model or _legacy_model(), temperature=temperature,
                  max_tokens=max_tokens)
    return result["text"]


def _legacy_model():
    """回退到旧配置的模型名。"""
    return os.environ.get("AI_MODEL", "").strip() or \
           _load_file_cfg().get("AI_MODEL", "deepseek-chat")


def is_ready(probe_local=False):
    """是否存在可用的 Provider 配置。

    语义是"有配置可尝试"，不是"服务一定连得上"。本地 Ollama 不需要密钥，
    所以永远会被解析出来——如果直接拿这个结果当"AI 就绪"展示，用户会看到
    绿色的「AI 就绪」，实际点一下 AI 功能就连接被拒（本机实测就是这种状态）。

    需要真实可用性时传 ``probe_local=True``：对 Ollama 这类本地且无密钥的
    provider 做一次短超时探活，连不上就不算 ready。远程 provider 不做探活
    （每次请求都打一次外部 API 既慢又费额度），按"已配置"计。
    """
    providers = _resolve_providers()
    if not providers:
        return False
    if not probe_local:
        return True
    for key, p in providers.items():
        if key != "ollama" and p.get("api_key") not in ("", "ollama"):
            return True  # 远程 provider 有真密钥，视为可用
        if _probe_local_ollama(p.get("base_url", "")):
            return True
    return False


def _probe_local_ollama(base_url, timeout=0.8):
    """本地 Ollama 是否在监听。只连本机回环地址，避免误把远程当本地。"""
    if not base_url:
        return False
    try:
        from urllib.parse import urlparse
        import urllib.request
        parsed = urlparse(base_url)
        if parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            return False
        url = base_url.rstrip("/") + "/models"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except Exception:
        return False

