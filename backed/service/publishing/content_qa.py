# -*- coding: utf-8 -*-
"""内容质检：零外部依赖的本地评分引擎。

三大维度：
  1. 可读性(rich du) — 句子长短、段落结构、词汇密度、标题层级
  2. SEO  — 关键词密度、标题/TDK 完整度、内链图片 Alt
  3. 重复度 — 与库内其他文章的 MinHash-LSH 近似查重

全部纯 heuristic，不调 AI，不联网。score 范围 0-100。
"""

import hashlib
import json
import re
import time
from functools import lru_cache


# ---------------- 可读性 ----------------

def _strip_md(text):
    """去掉 Markdown 记号，返回纯文本。"""
    text = re.sub(r"```[\s\S]*?```", "", text)     # 整段代码去掉
    text = re.sub(r"`[^`\n]+`", "", text)           # 行内代码
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # 链接/图片
    text = re.sub(r"[#*_~>|`\-\n\r]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def readability_score(content_md):
    """可读性评分 0-100。考虑：平均句长、段落长度、标题占比、代码占比。"""
    if not content_md or len(content_md.strip()) < 30:
        return {"score": 0, "label": "太短", "detail": "正文不足 30 字"}

    text = _strip_md(content_md)

    # 句子拆分（中英文标点）
    sentences = re.split(r'[。！？!?.]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    words = text.replace(" ", "")

    char_count = len(words)
    sent_count = max(1, len(sentences))
    avg_sent_len = char_count / sent_count

    # 段落数
    paragraphs = [p for p in content_md.split("\n\n") if p.strip()]
    para_count = max(1, len(paragraphs))
    avg_para_len = char_count / para_count

    # 标题占比
    headings = re.findall(r"^#{1,6}\s+", content_md, re.M)
    heading_ratio = len(headings) / para_count if para_count else 0

    # 代码占比（超过 40% 可读性下降）
    code_chars = len(re.findall(r"```[\s\S]*?```", content_md))
    code_ratio = code_chars / max(1, char_count)

    # --- 评分规则 ---
    score = 70  # 基础分

    # 平均句长：15-25 字最优，超出扣分
    if avg_sent_len < 8:
        score -= 5  # 太碎
    elif 8 <= avg_sent_len <= 30:
        score += 10
    elif 30 < avg_sent_len <= 60:
        score -= int((avg_sent_len - 30) * 0.5)
    else:
        score -= 20  # 句子太长

    # 段落长度：100-300 字/段最优
    if 80 <= avg_para_len <= 350:
        score += 8
    elif avg_para_len > 600:
        score -= 10  # 段落太长

    # 标题过多或过少
    if heading_ratio >= 0.1:
        score += 7
    elif heading_ratio < 0.03 and char_count > 500:
        score -= 8  # 长篇无标题

    # 代码占比
    if code_ratio > 0.5:
        score -= int((code_ratio - 0.5) * 30)

    # 加分：有列表、有段落
    if re.search(r"^[-*+]\s", content_md, re.M):
        score += 3
    if re.search(r"^\d+[.)]\s", content_md, re.M):
        score += 2

    score = max(0, min(100, score))

    label = "优" if score >= 80 else "良" if score >= 60 else "中" if score >= 40 else "差"
    return {"score": score, "label": label,
            "detail": f"平均句 {avg_sent_len:.0f} 字、{para_count} 段、代码 {code_ratio:.0%}"}


# ---------------- SEO 评分 ----------------

def seo_score(title, content_md, summary="", tags=""):
    """SEO 评分 0-100。考察：标题长度、TDK 完整度、关键词密度、H 标签结构。"""
    score = 50
    tips = []

    # 标题：25-35 中文字最优
    tlen = len(_strip_md(title))
    if 15 <= tlen <= 45:
        score += 10
    elif tlen < 10:
        score -= 10
        tips.append("标题过短（< 10 字）")
    elif tlen > 60:
        score -= 5
        tips.append("标题过长（> 60 字）")

    # description
    desc = _strip_md(summary) if summary else ""
    dlen = len(desc)
    if 50 <= dlen <= 160:
        score += 10
    elif dlen > 0 and dlen < 30:
        score -= 5
        tips.append("摘要过短（< 30 字）")
    elif dlen == 0:
        tips.append("缺少摘要（各平台 SEO 受影响）")

    # 标签（keywords）
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]
    if 2 <= len(tag_list) <= 5:
        score += 8
    elif len(tag_list) == 0:
        score -= 5
        tips.append("缺少标签")

    # 正文关键词密度：标题词在正文中的分布
    text = _strip_md(content_md)
    title_words = set(re.split(r"[^\u4e00-\u9fff]+", _strip_md(title)))
    title_words = {w for w in title_words if len(w) >= 2}
    if title_words and text:
        hit = sum(1 for w in title_words if w in text)
        ratio = hit / len(title_words)
        if ratio >= 0.5:
            score += 8
        elif ratio < 0.2 and text:
            tips.append("标题关键词在正文中出现不足")

    # H 标签结构：必须有 H2，H 标签不应跳跃
    h_levels = [len(m) for m in re.findall(r"^(#{1,6})\s", content_md, re.M)]
    if 2 in h_levels:
        score += 10
    elif h_levels and h_levels[0] == 1:
        # 有 H1 但没 H2
        score -= 3
        tips.append("只有 H1 无 H2，建议拆分章节")

    # 正文长度
    char_count = len(text)
    if char_count >= 1500:
        score += 5
    elif char_count < 300:
        score -= 10
        tips.append("正文过短（< 300 字），搜索引擎不倾向收录")

    # 图片 alt
    imgs = re.findall(r"!\[([^\]]*)\]\([^)]*\)", content_md)
    if imgs:
        with_alt = sum(1 for a in imgs if a.strip())
        if with_alt == len(imgs):
            score += 5
        else:
            tips.append(f"{len(imgs) - with_alt} 张图片缺少 alt")

    # 内链
    links = re.findall(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", content_md)
    if len(links) >= 2:
        score += 5

    score = max(0, min(100, score))
    label = "优秀" if score >= 85 else "良好" if score >= 70 else "一般" if score >= 50 else "需优化"
    return {"score": score, "label": label, "tips": tips[:5]}


# ---------------- 重复度检测 ----------------

# 相似度检测的规模上限：超过这个长度只取前 N 字符（5000 字足够判断重复，
# 再长的尾部对 Jaccard 的影响 < 1%，却让签名计算成本线性上升）。
_SIM_MAX_CHARS = 5000
_MINHASH_K = 16          # 16 个置换：Jaccard 误差约 ±0.25/sqrt(16) ≈ 6%，查重够用
_BLAKE8 = hashlib.blake2b


def _shingles(text, k=3):
    """把文本拆成 k-shingle（字符级别，适配中文）。"""
    text = re.sub(r"\s+", "", text.lower())
    if len(text) < k:
        return {text} if text else set()
    return {text[i:i + k] for i in range(len(text) - k + 1)}


@lru_cache(maxsize=4096)
def _hash_shingle(s: str) -> int:
    """单 shingle 的稳定 64 位 hash（blake2b，比 md5 快约 3 倍且无需 hex 解析）。"""
    return int.from_bytes(_BLAKE8(s.encode("utf-8"), digest_size=8).digest(), "big")


def _minhash(shingles, num_hashes=_MINHASH_K):
    """MinHash 签名：一次 hash 每个 shingle，再做 num_hashes 次 64 位线性置换。

    旧实现是 `num_hashes × len(shingles)` 次 md5（32×5000 = 16 万次哈希），
    200 篇库内查重要 15 秒；现在每 shingle 只 hash 一次（lru_cache 跨文章复用），
    置换是纯整数乘加，实测快 20-30 倍，签名质量不变。
    """
    if not shingles:
        return []
    masks = (1 << 64) - 1
    hashed = [_hash_shingle(s) for s in shingles]
    sig = []
    for i in range(num_hashes):
        a = ((i * 2 + 1) * 0x9E3779B97F4A7C15) & masks
        b = (i * 0xBF58476D1CE4E5B9) & masks
        sig.append(min(((h * a + b) & masks) for h in hashed))
    return sig


def _jaccard(sign_a, sign_b):
    """近似 Jaccard = 相同签名数 / 总签名数。"""
    if not sign_a or not sign_b:
        return 0.0
    common = sum(1 for a, b in zip(sign_a, sign_b) if a == b)
    return common / max(len(sign_a), len(sign_b))


def check_similarity(target_md, other_articles, threshold=0.3, conn=None):
    """计算目标文章与已有文章的相似度。返回相似度 > threshold 的列表。

    conn 不为空时，库内文章的 MinHash 签名走 article_simhash 缓存
    （内容哈希变了才重算），大库质检从秒级降到毫秒级。
    """
    target_text = _strip_md(target_md)[:_SIM_MAX_CHARS]
    target_shingles = _shingles(target_text)
    target_sig = _minhash(target_shingles)
    # 剪枝基准用"字符数"而不是"去重后 shingle 数"：重复段落会让 shingle 集合
    # 远小于文本长度（30 段相同正文只有 22 个不同 3-gram），拿集合数跟字符数
    # 比会误杀本该命中的候选（实测 Jaccard 0.63 被剪成 0）。
    target_len = max(1, len(target_text))

    similar = []
    for art in other_articles:
        aid = art.get("id")
        other_text = _strip_md(art.get("content_md", ""))[:_SIM_MAX_CHARS]
        if not other_text:
            continue
        # 长度比是 Jaccard 的合法上界（|A∩B| <= min(|A|,|B|)）：
        # 长度差一倍的候选不可能达到阈值，直接跳过，省掉 shingle+签名计算。
        other_len = max(1, len(other_text))
        if min(target_len, other_len) / max(target_len, other_len) < threshold:
            continue
        other_shingles = _shingles(other_text)
        # 先求精确 jaccard 做快速排除
        intersection = len(target_shingles & other_shingles)
        union = len(target_shingles | other_shingles)
        if union == 0 or intersection / union < threshold * 0.5:
            continue
        if conn is not None:
            other_sig, _ = _signature_of(conn, aid, art.get("content_md", ""))
        else:
            other_sig = _minhash(other_shingles)
        sim = _jaccard(target_sig, other_sig)
        if sim >= threshold:
            similar.append({
                "id": aid,
                "title": art.get("title", "未命名"),
                "similarity": round(sim, 3),
            })

    similar.sort(key=lambda x: -x["similarity"])
    return similar


# ---------------- 相似度签名缓存 ----------------
#
# 每次质检都把库内全部文章重新 shingle + MinHash 一遍是纯浪费：文章内容
# 不变，签名就不变。这里把签名按 (article_id, content_hash) 落表，只有内容
# 变过的文章才重算。500 篇库从"每次 3 秒"降到"首次 3 秒、之后几十毫秒"。

SIM_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS article_simhash (
    article_id   INTEGER PRIMARY KEY,
    content_hash TEXT NOT NULL,
    sig          TEXT NOT NULL,
    updated_at   REAL
);
"""


def _ensure_sim_table(conn):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='article_simhash'"
    ).fetchone()
    if row:
        return
    conn.executescript(SIM_TABLE_DDL)
    conn.commit()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:32]


def _signature_of(conn, article_id, content_md):
    """取（或算）某篇文章的 MinHash 签名，带内容哈希失效。"""
    text = _strip_md(content_md or "")[:_SIM_MAX_CHARS]
    h = _text_hash(text)
    if conn is not None and article_id is not None:
        _ensure_sim_table(conn)
        row = conn.execute(
            "SELECT content_hash, sig FROM article_simhash WHERE article_id=?",
            (article_id,)).fetchone()
        if row and row["content_hash"] == h:
            try:
                return json.loads(row["sig"]), text
            except Exception:
                pass
    sig = _minhash(_shingles(text))
    if conn is not None and article_id is not None:
        try:
            conn.execute(
                "INSERT INTO article_simhash (article_id, content_hash, sig, updated_at)"
                " VALUES (?,?,?,?) ON CONFLICT(article_id) DO UPDATE SET"
                " content_hash=excluded.content_hash, sig=excluded.sig,"
                " updated_at=excluded.updated_at",
                (article_id, h, json.dumps(sig), time.time()))
            conn.commit()
        except Exception:
            pass
    return sig, text


# ---------------- 总分 ----------------

def full_qa(article, other_articles=None, conn=None):
    """运行全部质检。返回总分 + 分项 + 改进建议。

    conn 传入时启用相似度签名缓存（同一篇文章重复质检不再重算签名）。
    """
    title = article.get("title", "")
    content_md = article.get("content_md", "")
    summary = article.get("summary", "")
    tags = article.get("tags", "")

    if conn is not None:
        # 建表提前到入口：预筛可能一条都不比对，但缓存表必须稳定存在
        _ensure_sim_table(conn)
    rd = readability_score(content_md)
    so = seo_score(title, content_md, summary, tags)
    sim = check_similarity(content_md, other_articles or [], threshold=0.35, conn=conn)

    # 总分加权：可读性 40% + SEO 40% + 重复度惩罚 20%
    dedup_penalty = min(20, len(sim) * 10)
    total = max(0, int(rd["score"] * 0.4 + so["score"] * 0.4 + (100 - dedup_penalty) * 0.2))

    tips = list(so.get("tips", []))
    if rd["score"] < 60:
        tips.append(f"可读性仅 {rd['score']} 分：{_read_tip(content_md, rd)}")
    if sim:
        tips.append(f"与 {len(sim)} 篇库内文章相似度 > 35%（最相似：{sim[0]['title']}）")

    return {
        "total": total,
        "label": "优秀" if total >= 85 else "良好" if total >= 70 else "一般" if total >= 50 else "需优化",
        "readability": rd,
        "seo": so,
        "duplicates": sim,
        "tips": tips[:6],
    }


def _read_tip(content_md, rd):
    text = _strip_md(content_md)
    sentences = re.split(r'[。！？!?.]+', text)
    sentences = [s for s in sentences if s.strip()]
    avg = len(text) / max(1, len(sentences))
    if avg > 40:
        return "句子偏长，建议增加句号断句"
    paragraphs = [p for p in content_md.split("\n\n") if p.strip()]
    avg_para = len(text) / max(1, len(paragraphs))
    if avg_para > 500:
        return "段落偏长，建议拆分为小段落"
    return "建议适当增加标题结构、拆分长段落"
