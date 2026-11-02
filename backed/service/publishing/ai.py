# -*- coding: utf-8 -*-
"""AI 写稿模块：文章从哪来。

走 OpenAI 兼容协议，所以 DeepSeek / 通义 / 豆包 / Kimi / 智谱 / 本地 Ollama
全都能用——换家只改环境变量，代码一行不动。

配置（环境变量或 config.json）：
    AI_API_KEY    必填
    AI_BASE_URL   默认 https://api.deepseek.com/v1
    AI_MODEL      默认 deepseek-chat
"""

import json
import os
from pathlib import Path

from service.publishing.db import config_file

import requests
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)

# 路径基准（2026-10-14 修复）：config.json 的文档位置是 backed/config.json
# （.gitignore 与 api/auth.py 都按此读）；旧代码用 parent.parent 指出 backed/service/，
# 导致 AI 密钥/gate 开关从没被文档位置的配置读到过。
BACKED_ROOT = Path(__file__).resolve().parents[2]      # backed/
REPO_ROOT = Path(__file__).resolve().parents[3]        # 仓库根（写作风格档案在 docs/）
CONFIG_FILE = config_file()

DEFAULT_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
TIMEOUT = 180

# 写作风格档案：改这个文件就改了全平台产出风格，不用动代码。
# 覆盖方式：环境变量 WRITING_STYLE_FILE 指向另一份档案。
STYLE_FILE = REPO_ROOT / "docs" / "writing-style.md"
STYLE_MARK = "【写作风格档案】"
# 篇幅兜底：AI 返回正文短于 要求字数 × 该比例 时自动续写一轮（只续一轮，
# 防止无止境拉长；比例 0.6 是"明显偷工"与"风格偏短但合格"的分界）。
MIN_COMPLETENESS_RATIO = 0.6
MAX_EXPAND_ROUNDS = 1

# 多 Provider 路由（可选升级）：
# 如果配置了 providers（环境变量 AI_API_KEY 以外还配了别的），
# write_article / rewrite / polish 等函数接受 model="provider:model" 指定模型和 Provider。
# 不指定时 fallback 到旧的单 Provider 逻辑（向下兼容）。
_providers_mod = None
def _get_providers():
    """惰性加载 AI 多 Provider 路由模块（避免循环导入）。

    注意：模块名是 ai_providers（原 providers.py 与平台工具包 providers/ 同名，
    Python 里包优先于模块 → 旧名永远解析到平台工具包，_resolve_providers
    必 AttributeError，AI 写稿整体不可用。2026-10-14 改名修复）。
    """
    global _providers_mod
    if _providers_mod is None:
        from service.publishing import ai_providers as p
        _providers_mod = p
    return _providers_mod


def _cfg(key, default=None):
    return os.environ.get(key) or _file_cfg().get(key) or default


def _load_dotenv():
    """零依赖 .env 加载：backed/.env 里的 KEY=VALUE 作为最底层兜底。
    不覆盖已存在的真实环境变量，不覆盖 config.json 已解析的值。"""
    env_file = BACKED_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _file_cfg():
    _load_dotenv()
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


class AIError(Exception):
    pass


SYSTEM_WRITE = """你是资深技术作者，写给中文开发者看。
要求：
1. 有观点、有细节，不写正确的废话
2. 代码块必须标语言，能跑
3. 结构清晰：问题 → 方案 → 踩坑 → 结论
4. 口语化，别端着，但别用感叹号堆情绪
5. 直接输出 Markdown 正文，不要包裹 ```markdown 代码块
"""


def style_file() -> Path:
    """风格档案路径：环境变量优先，其次 docs/writing-style.md。"""
    env = os.environ.get("WRITING_STYLE_FILE", "").strip()
    return Path(env) if env else STYLE_FILE


def load_style() -> str:
    """读写作风格档案。文件不存在/读失败一律降级为空串（不让风格配置拖垮写稿）。"""
    import logging
    p = style_file()
    # 风格档案是可选增强，读失败（编码/权限/路径含非法字符）不连带写稿失败。
    # F-28 改进：降级时打 warning 日志，避免用户以为配了 style 实际没生效。
    try:
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""
    except Exception as e:
        logging.getLogger(__name__).warning(
            "写作风格档案读取失败（降级为无档案）: %s: %s", p, e)
        return ""


def system_write(extra_style: str = "") -> str:
    """system 提示 = 基础要求 + 风格档案 + 单次调用的追加要求（追加不覆盖）。"""
    parts = [SYSTEM_WRITE.strip()]
    style = load_style()
    if style:
        parts.append("%s\n%s" % (STYLE_MARK, style))
    if extra_style and extra_style.strip():
        parts.append("【本次追加要求】\n%s" % extra_style.strip())
    return "\n\n".join(parts)


def chat(messages, model=None, temperature=0.7, max_tokens=4096, task="standard",
         preferred_provider=None):
    """chat 兼容旧接口，并升级到多 Provider。

    优先级：
      1. model="provider:model" 格式 → 走 providers 模块指定路由
      2. providers 模块检测到多 Provider 配置 → 按 task 级别路由
      3. fallback 到旧的单 Provider 逻辑（环境变量 / config.json）
    """
    # 判断是否可以走多 Provider 路由
    prov_mod = _get_providers()
    has_multi = len(prov_mod._resolve_providers()) > 1
    has_explicit = model and (":" in model or preferred_provider)

    if has_multi or has_explicit:
        try:
            result = prov_mod.chat(messages, model=model, task=task,
                                   temperature=temperature, max_tokens=max_tokens,
                                   preferred_provider=preferred_provider)
            return result["text"]
        except Exception as e:
            # 多 Provider 失败不立即 fallback（避免无限循环），直接报错
            if has_explicit:
                raise AIError(f"指定的 Provider/模型不可用: {e}")
            # 多 Provider 路由失败，fallback 到旧逻辑

    # 旧路径（单 Provider fallback，向下兼容）
    api_key = _cfg("AI_API_KEY")
    if not api_key:
        raise AIError("没配 AI_API_KEY。export AI_API_KEY=xxx 或写进 config.json")

    base = _cfg("AI_BASE_URL", DEFAULT_BASE).rstrip("/")
    actual_model = model or _cfg("AI_MODEL", DEFAULT_MODEL)

    try:
        r = _post_chat(base, api_key, actual_model, messages, temperature, max_tokens)
    except Exception as e:
        raise AIError(f"调不通 AI 接口（{base}）：{e}")

    if r.status_code != 200:
        raise AIError(f"AI 接口返回 {r.status_code}: {r.text[:200]}")

    data = r.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        raise AIError(f"返回结构异常: {json.dumps(data, ensure_ascii=False)[:200]}")


class _RetryableHTTP(Exception):
    """可重试的瞬时错误（429/5xx/网络抖动），与参数错误等确定性失败分开。"""


@retry(
    retry=retry_if_exception_type((_RetryableHTTP, requests.ConnectionError,
                                   requests.Timeout)),
    wait=wait_exponential(multiplier=1, min=1, max=12),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _post_chat(base, api_key, model, messages, temperature, max_tokens):
    """单次 LLM 调用，带指数退避重试（tenacity）。

    只重试瞬时错误：429（限流）、5xx（上游故障）、连接/超时。
    4xx（密钥错、模型名不存在、参数非法）立刻抛——重试三次只是把同一个
    错误重复三遍，还把用户的等待时间拉长三倍。
    """
    r = requests.post(f"{base}/chat/completions",
                      headers={"Authorization": f"Bearer {api_key}",
                               "Content-Type": "application/json"},
                      json={"model": model, "messages": messages,
                            "temperature": temperature,
                            "max_tokens": max_tokens},
                      timeout=TIMEOUT)
    if r.status_code == 429 or 500 <= r.status_code < 600:
        raise _RetryableHTTP(f"HTTP {r.status_code}: {r.text[:120]}")
    return r


def _split_title(raw: str, topic: str):
    """拆出「标题：」行。AI 没按格式输出时，标题退回 topic、正文整段保留。"""
    if "标题：" not in raw:
        return topic, raw
    _, _, rest = raw.partition("标题：")
    line, _, body = rest.partition("\n")
    return (line.strip() or topic), body.lstrip("\n")


def write_article(topic, style="", words=2000, tags_hint="", model=None, preferred_provider=None):
    """写一篇完整的文章，返回结构化结果，可直接入库。

    篇幅兜底：正文明显短于要求时自动续写一轮（MAX_EXPAND_ROUNDS），
    避免"要 2000 字结果给 600 字"的半成品流进发布链路。

    model: "provider:model_name" 指定。不指定时走 premium 级别路由。
    """
    prompt = f"""写主题为「{topic}」的技术文章，目标 {words} 字左右。
{f'建议涉及：{tags_hint}' if tags_hint else ''}

先输出一行标题（以「标题：」开头），然后空一行，再输出 Markdown 正文。"""
    raw = chat([{"role": "system", "content": system_write(style)},
                {"role": "user", "content": prompt}],
               model=model, task="write", preferred_provider=preferred_provider)
    title, body = _split_title(raw, topic)

    floor = int(words * MIN_COMPLETENESS_RATIO)
    for _ in range(MAX_EXPAND_ROUNDS):
        if len(body.strip()) >= floor:
            break
        more = chat([
            {"role": "system", "content": system_write(style)},
            {"role": "user", "content":
                f"继续扩写下面这篇文章，补足到 {words} 字左右。只输出新增的 Markdown "
                f"正文段落，不要重复已有内容，不要输出标题行。\n\n---\n\n{body[:6000]}"},
        ], model=model, task="write", preferred_provider=preferred_provider)
        if not more.strip():
            break
        body = body.rstrip() + "\n\n" + more.strip()

    actual_model = model or _cfg("AI_MODEL", DEFAULT_MODEL)
    return {"title": title, "content_md": body,
            "summary": summarize(body, model=model, preferred_provider=preferred_provider),
            "tags": suggest_tags(title, body, model=model, preferred_provider=preferred_provider),
            "source": "ai", "ai_model": actual_model,
            "style_source": str(style_file()) if load_style() else ""}


def rewrite(content_md, instruction, model=None, preferred_provider=None):
    """按指令改写，比如"改成更口语""补充一个踩坑章节"。"""
    return chat([{"role": "system", "content": system_write()},
                 {"role": "user", "content": f"按这个要求改写下面这篇文章：{instruction}\n\n---\n\n{content_md}"}],
                model=model, task="rewrite", preferred_provider=preferred_provider)


def polish(content_md, model=None, preferred_provider=None):
    """润色：修错别字、顺语句、统一代码块语言标识，不改原意。"""
    return rewrite(content_md, "润色：修错别字和病句、统一代码块语言标识、理顺结构，不要改变原意和篇幅",
                   model=model, preferred_provider=preferred_provider)


def summarize(content_md, model=None, preferred_provider=None):
    """生成 50-150 字摘要，各平台发布时用。"""
    try:
        return chat([{"role": "user", "content":
            f"为下面这篇文章写一段 50-150 字的摘要，突出价值，不要复述标题：\n\n{content_md[:6000]}"}],
            max_tokens=300, model=model, task="summarize", preferred_provider=preferred_provider)
    except Exception:
        return content_md[:100].replace("\n", " ")


def suggest_tags(title, content_md, model=None, preferred_provider=None):
    """推荐 3-5 个标签，逗号分隔。"""
    try:
        raw = chat([{"role": "user", "content":
            f"为这篇文章推荐 3-5 个中文技术标签，只输出逗号分隔的标签，不要解释：\n"
            f"标题：{title}\n\n{content_md[:3000]}"}], max_tokens=100, model=model,
            task="tags", preferred_provider=preferred_provider)
        tags = [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()]
        return ",".join(tags[:5])
    except Exception:
        return ""


def is_ready():
    # 兼容 providers 模块：如果 providers 有配置也认为 ready
    cfg_ready = bool(_cfg("AI_API_KEY"))
    if cfg_ready:
        return True
    try:
        prov_mod = _get_providers()
        return prov_mod.is_ready()
    except Exception:
        return False
