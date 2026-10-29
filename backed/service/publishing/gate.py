# -*- coding: utf-8 -*-
"""AIGC 合规门禁 + 双模型审查 + 发布闸门。

对标 2026 国内 AIGC 标识法规（《人工智能生成合成内容标识办法》）与
OWASP Agentic Top10「输出不可信 / 工具滥用 / 目标劫持」的落地最小集。

四道闸门，按顺序过：
  1. 内容安全扫描 —— 高危词命中直接拒发（防 AIGC 生成敏感内容被自动发布）
  2. AIGC 标识 —— AI 源文章强制在 ext 标 aigc:true + 模型名，发布时
     平台若支持元数据则携带，至少入库可追溯（法规要求"显式标识"）
  3. 双模型审查 —— 配了 REVIEW_MODEL 用第二模型审一遍（事实/口径/合规），
     未配则降级本地 heuristic 审查（确定性检查，不依赖外网）
  4. 发布闸门 —— source=ai 的文章正式发布（非 draft_only）需人工确认
     （human_confirmed=True，前端显式勾选并留痕）或用户开启 AI_DIRECT_PUBLISH；
     否则必须走 draft_only 草稿 + 人工确认（人审 in-the-loop，防 AI 内容裸奔上线）

设计原则：
  - 纯本地 heuristic 是底线，保证无网/无第二模型时门禁仍生效
  - REVIEW_MODEL 可选，配了才走 LLM 二审，不增加硬性依赖
  - 门禁失败不抛异常阻塞入库，而是把文章标成 review + 拒发，留给人工
"""

import json
import re
import threading
import time
from pathlib import Path

import ahocorasick


# config.json 的文档位置是 backed/config.json（与 .gitignore / api/auth.py 一致；
# 2026-10-14 修复：旧代码 parent.parent 指到 backed/service/，门禁开关读不到文档位置）
ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "config.json"

# 高危词（保守起量，宁多勿漏；可按需扩展）
DANGEROUS_PATTERNS = [
    # 政治敏感（合规红线）
    "法轮功", "六四", "藏独", "疆独", "港独", "台独", "反共",
    # 违法内容
    "枪支交易", "毒品制造", "代孕中介", "赌博网站",
    # 恶意
    "恶意软件下载", "病毒木马", "钓鱼链接",
]
# 正则型（带语境的）
DANGEROUS_REGEX = [
    # 色情站。不能用 \b：Python 的 \w 含中日韩文字，"看PORNHUB网站" 前后都是
    # 词字符，\b 不成立 → 中文语境下的命中全部漏掉（实测）。改用 ASCII 边界断言。
    r"(?i)(?<![a-z0-9])(pornhub|xvideos|xhamster)(?![a-z0-9])",
    r"(?i)(get|click|buy)\s+(drug|pills?)\s+online",
    r"破解\s*(卡密|激活码|序列号)\s*出售",
]

# AIGC 标识文案（可选：仅当 AI_DECLARATION_IN_TEXT=true 时追加进正文）。
# 2026-10-14 起默认**不写进正文**——标识义务改由平台自带的「AI 辅助」选项承担
# （知乎发布设置里的 AI 辅助开关，见 adapters/zhihu._fill_publish_settings）。
# 无原生选项的平台（博客园 MetaWeblog / 掘金 API 等）可把该开关打开恢复正文声明。
AIGC_NOTICE = "\n\n---\n> 本文由 AI 辅助生成，发布前已经过合规审查与人工确认。"


def _cfg(key, default=None):
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
    except Exception:
        data = {}
    return data.get(key, default)


def is_enabled() -> bool:
    """门禁开关。默认开；可经 config.json "AIGC_GATE": false 关闭（不推荐）。"""
    v = _cfg("AIGC_GATE", True)
    return str(v).lower() not in ("false", "0", "no")


def is_ai_direct_publish() -> bool:
    """AI 稿直发开关。默认 false（先草稿+人工确认）。

    config.json "AI_DIRECT_PUBLISH": true 时，AI 源文章经
    安全扫描 + 二审 + AIGC 标识后可直接正式发布，不再要求人工确认。
    """
    v = _cfg("AI_DIRECT_PUBLISH", False)
    return str(v).lower() in ("true", "1", "yes")


def is_ai_sourced(article: dict) -> bool:
    return article.get("source") == "ai"


_automaton = None
_automaton_lock = threading.Lock()


def _get_automaton():
    """Aho-Corasick 自动机：一次线性扫描命中所有模式。

    旧实现是 `for w in DANGEROUS_PATTERNS: if w in text`，词表每加一个词就
    多扫全文一遍。合规词表只会越来越长，这个成本会线性叠加到每次发布上。
    Aho-Corasick 预处理一次，之后 O(len(text) + 命中数)。
    """
    global _automaton
    if _automaton is not None:
        return _automaton
    with _automaton_lock:
        if _automaton is None:
            auto = ahocorasick.Automaton()
            for idx, w in enumerate(DANGEROUS_PATTERNS):
                auto.add_word(w.lower(), (idx, w))
            auto.make_automaton()
            _automaton = auto
    return _automaton


def reload_patterns() -> None:
    """词表变更后重建自动机（配置热加载 / 测试用）。"""
    global _automaton
    with _automaton_lock:
        _automaton = None


def scan_dangerous(content: str, title: str = "") -> list:
    """内容安全扫描。返回命中的高危项列表（空 = 安全）。大小写不敏感。"""
    text = f"{title}\n{content}"
    hits = []
    for _, (_idx, original) in _get_automaton().iter(text.lower()):
        if original not in hits:
            hits.append(original)
    for pat in DANGEROUS_REGEX:
        if re.search(pat, text, re.I):
            hits.append(f"regex:{pat[:30]}")
    return hits


def is_declaration_in_text() -> bool:
    """正文是否追加 AI 声明。默认关（2026-10-14 用户要求：改走平台「AI 辅助」选项）。

    无原生 AI 选项的平台（纯 API 发布）可置 config.json
    "AI_DECLARATION_IN_TEXT": true 恢复正文声明。
    """
    v = _cfg("AI_DECLARATION_IN_TEXT", False)
    return str(v).lower() in ("true", "1", "yes")


def add_aigc_label(article: dict, model: str = "") -> dict:
    """给 AI 源文章打标识：ext 留痕（必须，可追溯）＋ 可选正文声明。

    正文声明受 AI_DECLARATION_IN_TEXT 控制，默认不加（平台原生选项负责显式标识）。
    返回更新后的 article dict。
    """
    ext = {}
    if article.get("ext"):
        try:
            ext = json.loads(article["ext"]) if isinstance(article["ext"], str) else dict(article["ext"])
        except Exception:
            ext = {}
    ext["aigc"] = True
    ext["ai_model"] = model or article.get("ai_model", "")
    ext["aigc_at"] = time.time()
    article["ext"] = json.dumps(ext, ensure_ascii=False)

    if is_declaration_in_text():
        content = article.get("content_md", "")
        # 幂等：已有标识就不重复加
        if "本文由 AI 辅助生成" not in content:
            article["content_md"] = content.rstrip() + AIGC_NOTICE
    return article


# ---------------------------------------------------------------------------
# 双模型审查
# ---------------------------------------------------------------------------
def _local_review(title: str, content: str) -> dict:
    """本地 heuristic 二审（无外网也能跑，作为底线）。

    查：代码块是否标语言 / 是否含占位符 / 篇幅是否过短 / 是否含 TODO 残留。

    占位符检测只在代码块**外**的正文里找：代码块里的 ``{{ }}``
    常是合法的 Jinja / Python f-string / JSON / CSS，旧实现一律命中就误报，
    技术文档一跑就 reject。
    """
    issues = []
    if len(content.strip()) < 200:
        issues.append("篇幅过短（<200字），可能未完成")
    # 代码块语言标注（2026-09-21 修复：原正则把闭栏 ``` 也当开栏检查，
    # 导致所有带代码块的文章都被误判"未标语言"——闭栏永远不带语言）
    in_block = False
    prose_lines = []     # 代码块外的正文行，占位符只在这里找
    for line in content.split("\n"):
        s = line.strip()
        if s.startswith("```"):
            if not in_block:
                if s == "```":          # 开栏且裸栏 = 未标语言
                    issues.append("存在未标语言的代码块")
                    break
                in_block = True
            else:
                in_block = False        # 闭栏
            continue
        if not in_block:
            prose_lines.append(line)
    prose = "\n".join(prose_lines).lower()
    # 占位符残留（仅正文）
    for placeholder in ("TODO", "FIXME", "lorem ipsum", "placeholder"):
        if placeholder.lower() in prose:
            issues.append(f"含占位符残留: {placeholder}")
            break
    # XXX 单独处理：避免命中 xxx 邮箱、路径变量等
    if re.search(r"\bXXX+\b", content, re.I):
        issues.append("含占位符残留: XXX")
    ok = not issues
    return {"verdict": "pass" if ok else "reject",
            "issues": issues, "reviewer": "local-heuristic"}


def dual_model_review(article: dict) -> dict:
    """双模型审查。配了 REVIEW_MODEL 走 LLM 二审，否则本地 heuristic。

    返回 {verdict: pass/reject, issues: [...], reviewer, detail}。
    """
    # 安全扫描永远先跑，门禁开时
    if is_enabled():
        hits = scan_dangerous(article.get("content_md", ""), article.get("title", ""))
        if hits:
            return {"verdict": "reject", "issues": [f"高危内容: {hits}"],
                    "reviewer": "safety-scan"}

    review_model = _cfg("REVIEW_MODEL", "").strip()
    if review_model:
        try:
            from service.publishing import ai as ai_mod
            prompt = (f"你是内容合规审查员。审查下面文章是否可安全发布："
                      f"事实是否有明显错误、口径是否稳妥、是否含敏感/违法/恶意内容。"
                      f"只输出 JSON: {{\"verdict\":\"pass\"|\"reject\",\"issues\":[...]}}\n\n"
                      f"标题: {article.get('title','')}\n\n{article.get('content_md','')[:6000]}")
            raw = ai_mod.chat([{"role": "user", "content": prompt}], model=review_model,
                              temperature=0.0, max_tokens=500)
            m = re.search(r"\{[\s\S]*\}", raw)
            parsed = json.loads(m.group(0)) if m else {"verdict": "pass", "issues": []}
            parsed["reviewer"] = f"dual-model:{review_model}"
            return parsed
        except Exception as e:
            # LLM 二审失败不阻塞，降级本地并记录
            base = _local_review(article.get("title", ""), article.get("content_md", ""))
            base["issues"].append(f"dual-model 审查失败已降级本地: {str(e)[:120]}")
            base["reviewer"] = "fallback-local"
            return base

    return _local_review(article.get("title", ""), article.get("content_md", ""))


# ---------------------------------------------------------------------------
# 发布闸门
# ---------------------------------------------------------------------------
class GateError(Exception):
    """门禁拒绝发布。"""


def gate_publish(article: dict, draft_only: bool = False,
                 human_confirmed: bool = False) -> dict:
    """发布前强制过闸。

    AI 源文章的正式发布（draft_only=False）需要满足其一：
      - human_confirmed=True：人工核验（前端「确认上线」显式勾选，留痕 ext）
      - config.json "AI_DIRECT_PUBLISH": true：用户明确开启直发
    两者都没有 → 拒（人审 in-the-loop，防 AI 内容裸奔上线）。

    返回 {approved, issues, aigc_labeled, review, human_confirmed}。
    """
    if not is_enabled():
        return {"approved": True, "issues": [], "aigc_labeled": False,
                "review": {"verdict": "skipped", "issues": [], "reviewer": "gate-off"},
                "human_confirmed": human_confirmed}

    review = dual_model_review(article)
    ai_src = is_ai_sourced(article)
    allow_live = draft_only or not ai_src or human_confirmed or is_ai_direct_publish()
    approved = (review["verdict"] == "pass") and allow_live
    issues = list(review["issues"])

    # AI 源正式发布且未确认、未开直发 -> 人审闸门拦
    if review["verdict"] == "pass" and ai_src and not draft_only \
            and not human_confirmed and not is_ai_direct_publish():
        approved = False
        issues.append("AI 生成内容默认须先发草稿并经人工确认后才能正式上线"
                      "（前端勾选「我已核验」确认，或在设置开启 AI 稿直发）")

    return {"approved": approved, "issues": issues, "aigc_labeled": ai_src,
            "review": review, "human_confirmed": human_confirmed}


def apply_gate_before_publish(article: dict, draft_only: bool = False,
                              human_confirmed: bool = False) -> dict:
    """一键闸门：过 gate_publish，通过则给 AI 源打标 AIGC 标识。失败抛 GateError。"""
    r = gate_publish(article, draft_only=draft_only, human_confirmed=human_confirmed)
    if not r["approved"]:
        raise GateError("；".join(r["issues"]) or "合规门禁拒绝")
    if r["aigc_labeled"]:
        article = add_aigc_label(article, article.get("ai_model", ""))
    return {"result": r, "article": article}
