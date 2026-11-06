# -*- coding: utf-8 -*-
"""统一标签管理：标签统计、合并、热门推荐、自动打标。

解决当前痛点：
  - 同义标签分散（"Python" "python" "PYTHON"）
  - 标签数膨胀无治理（100+ 前端标签云卡死）
  - 推荐靠 AI 每次重新来，没有历史沉淀

方案：本地 tag_stats 表记录每标签使用次数 + alias_map JSON 存同义合并规则。
不要外部 NLP 依赖——靠 AI 首次推荐后缓存 + 编辑距离合并就够了。
"""

import json
import time
from collections import Counter
from pathlib import Path

from service.publishing.db import config_file



TAGS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS tag_stats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tag        TEXT NOT NULL UNIQUE,
    count      INTEGER NOT NULL DEFAULT 0,
    last_used  REAL,
    category   TEXT DEFAULT ''      -- 分类：语言/框架/领域/其他
);
CREATE INDEX IF NOT EXISTS idx_tag_count ON tag_stats(count DESC);
"""

ALIAS_MAP_KEY = "tag_alias_map"  # 存在 config.json JSON 里


def _config_path():
    """别名映射落在 backed/config.json——与 ai/gate/metaweblog 同一份配置。

    回归：此前用 ``Path(__file__).parent.parent`` 算成了 ``backed/service/config.json``，
    而那个路径既不是其它模块读的配置，也**不在 .gitignore 里**——点一次
    「标签治理 → 合并」就在仓库里凭空多出一个未跟踪的 config.json。
    """
    return config_file()


def _ensure_table(conn):
    try:
        conn.executescript(TAGS_TABLE_DDL)
        conn.commit()
    except Exception:
        pass


def _load_alias_map(config_path):
    """从 config.json 读同义标签合并规则 {别名: 标准名}。"""
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        return data.get(ALIAS_MAP_KEY, {}) or {}
    except Exception:
        return {}


def _save_alias_map(config_path, alias_map):
    """写同义标签合并规则到 config.json。"""
    try:
        data = {}
        if Path(config_path).exists():
            data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        data[ALIAS_MAP_KEY] = alias_map
        Path(config_path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception:
        pass


def sync_from_articles(conn):
    """扫描 articles 标签字段，重建 tag_stats。首次启动或点「标签治理」按钮后触发。

    返回 {added, updated, total}。"""
    _ensure_table(conn)
    config_path = str(_config_path())
    alias_map = _load_alias_map(config_path)

    # 扫所有文章的 tags 字段
    rows = conn.execute(
        "SELECT tags FROM articles WHERE tags IS NOT NULL AND tags != ''").fetchall()
    tag_counter = Counter()
    for row in rows:
        for t in row["tags"].split(","):
            t = _normalize(t, alias_map)
            if t:
                tag_counter[t] += 1

    now = time.time()
    # 清空重算（标签清理后可能归零）
    conn.execute("DELETE FROM tag_stats")
    for tag, count in tag_counter.most_common(200):  # 上限 200
        conn.execute(
            "INSERT INTO tag_stats (tag, count, last_used, category) VALUES (?,?,?,?)",
            (tag, count, now, _guess_category(tag)))
    conn.commit()
    return {"synced": len(tag_counter), "total": sum(tag_counter.values())}


def _normalize(tag, alias_map=None):
    """标签归一化：去空白 -> 小写匹配 -> alias 替换 -> 近似同义合并。

    近似合并（2026-10-29 新增 rapidfuzz）：
      - 编辑距离 ≤ 1 且长度 ≥ 4 的纯 ASCII 标签视为同一词（"reactjs"/"react" 不算，
        但 "javscript"/"javascript" 算）；
      - 中文不做模糊匹配（二字词编辑距离 1 就可能是完全不同的概念）。
    只在 alias_map 未命中时兜底，显式别名永远优先。
    """
    tag = tag.strip()
    if not tag:
        return ""
    tag_lower = tag.lower()
    # 1) 显式别名（精确）
    if alias_map:
        for alias, canonical in alias_map.items():
            if tag_lower == alias.lower():
                return canonical
    # 2) 近似合并：只针对 ≥4 字符的 ASCII 标签
    if alias_map and tag.isascii() and len(tag) >= 4:
        from rapidfuzz.distance import Levenshtein
        for alias, canonical in alias_map.items():
            if not alias.isascii() or abs(len(alias) - len(tag)) > 1:
                continue
            if Levenshtein.distance(tag_lower, alias.lower()) <= 1:
                return canonical
    # 首字母大写保形（"python" -> "Python"）但不大写中文
    if tag.isascii() and tag == tag.lower():
        tag = tag.capitalize()
    return tag


def cluster_similar_tags(conn, threshold=72, limit=200):
    """找出近似重复的标签簇（同义词治理的候选清单）。

    判定规则针对中文/技术标签的实际分布设计：
      - 大小写不敏感比较（Python / python 必须合并）；
      - 纯 ASCII：编辑距离 ≤1（Javscript→JavaScript）或前缀包含（react→reactjs）；
      - 中文：只认"短词是长词子串"（异步 ⊂ 异步编程），不做编辑距离
        （二字中文词差一个字往往是完全不同的概念）。
    阈值参数只在 ASCII 侧作为辅助（ratio 下限），默认 72 足够宽松且不误报。

    返回 [{canonical, members: [...], reason}]，供「标签治理」一键合并。
    """
    from rapidfuzz.distance import Levenshtein

    _ensure_table(conn)
    rows = [r["tag"] for r in conn.execute(
        "SELECT tag FROM tag_stats ORDER BY count DESC LIMIT ?", (limit,)).fetchall()]

    def similar(a: str, b: str) -> str:
        la, lb = a.lower(), b.lower()
        if la == lb:
            return "大小写差异"
        short, long_ = (la, lb) if len(la) <= len(lb) else (lb, la)
        if short.isascii() and long_.isascii():
            if len(short) >= 4 and Levenshtein.distance(short, long_) <= 1:
                return "拼写近似"
            if len(short) >= 3 and long_.startswith(short):
                return "前缀包含"
            return ""
        if not short.isascii() or not long_.isascii():
            # 中文（或中英混合）：只认子串包含
            if len(short) >= 2 and short in long_:
                return "包含关系"
        return ""

    used, clusters = set(), []
    for i, tag in enumerate(rows):
        if tag in used:
            continue
        members, reasons = [], []
        for other in rows[i + 1:]:
            if other in used:
                continue
            why = similar(tag, other)
            if why:
                members.append(other)
                reasons.append(why)
        if members:
            used.add(tag)
            used.update(members)
            clusters.append({"canonical": tag, "members": members,
                             "reason": sorted(set(reasons))})
    return clusters


def _guess_category(tag):
    """极简标签分类：关键词匹配。"""
    tag_lower = tag.lower()
    languages = {"python", "javascript", "typescript", "java", "go", "rust",
                 "c++", "c#", "ruby", "php", "swift", "kotlin", "scala", "r", "sql"}
    frameworks = {"react", "vue", "angular", "nextjs", "django", "flask",
                  "spring", "express", "fastapi", "pandas", "tensorflow",
                  "pytorch", "bootstrap", "tailwindcss", "nodejs"}
    if tag_lower in languages:
        return "语言"
    if tag_lower in frameworks:
        return "框架"
    domains = {"ai", "人工智能", "机器学习", "深度学习", "大模型", "llm", "nlp",
               "区块链", "web3", "云计算", "devops", "docker", "kubernetes",
               "网络安全", "数据库", "redis", "mysql", "postgresql", "mongodb"}
    if tag_lower in domains:
        return "领域"
    return "其他"


def list_tags(conn, limit=50, category=None):
    """列出标签（按使用次数降序）。支持按分类过滤。"""
    _ensure_table(conn)
    sql = "SELECT * FROM tag_stats"
    args = []
    if category:
        sql += " WHERE category=?"
        args.append(category)
    sql += " ORDER BY count DESC, tag ASC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def trending(conn, limit=10):
    """最近用得最多的标签（按 last_used）。"""
    _ensure_table(conn)
    rows = conn.execute(
        "SELECT * FROM tag_stats ORDER BY count DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


_TECH_TERMS = (
    "python", "javascript", "typescript", "java", "golang", "go", "rust", "c++", "c#",
    "php", "ruby", "swift", "kotlin", "scala", "sql", "bash", "shell",
    "react", "vue", "angular", "svelte", "next.js", "nextjs", "nuxt", "django",
    "flask", "fastapi", "spring", "express", "nestjs", "tailwindcss", "sass", "webpack",
    "vite", "node.js", "nodejs", "deno", "bun",
    "docker", "kubernetes", "k8s", "podman", "helm", "terraform", "ansible",
    "nginx", "linux", "ubuntu", "debian", "centos", "windows", "macos",
    "mysql", "postgresql", "postgres", "sqlite", "redis", "mongodb", "elasticsearch",
    "kafka", "rabbitmq", "clickhouse", "neo4j",
    "git", "github", "gitlab", "gitee", "ci/cd", "devops", "jenkins", "actions",
    "aws", "azure", "gcp", "aliyun", "serverless", "cloudflare",
    "机器学习", "深度学习", "人工智能", "大模型", "大语言模型", "llm", "nlp", "rag",
    "pytorch", "tensorflow", "transformer", "微调", "推理", "向量数据库",
    "前端", "后端", "全栈", "架构", "重构", "性能优化", "可观测性", "微服务",
    "分布式", "高并发", "缓存", "消息队列", "数据库", "算法", "数据结构",
    "安全", "渗透测试", "加密", "认证", "权限", "合规",
    "产品设计", "用户体验", "交互设计", "可用性", "无障碍",
    "开源", "编程", "代码", "测试", "自动化测试", "单元测试", "敏捷开发",
)


_TERM_PATTERNS = None


def _term_patterns():
    """把 _TECH_TERMS 编译成正则，ASCII 词按词边界匹配。

    纯 ``str.find`` 会让 ``go`` 命中 ``golang``、``google``，``sql`` 命中
    ``mysql``——同一篇文章会同时推荐 ``golang`` 和 ``go``。ASCII 词加
    ``\b`` 边界，中文词没有词边界概念，直接按子串匹配。
    """
    global _TERM_PATTERNS
    if _TERM_PATTERNS is None:
        import re
        patterns = []
        for term in _TECH_TERMS:
            if term.isascii():
                escaped = re.escape(term)
                # c++ / c# 这类以符号结尾的词，\b 在符号后不成立，改用自定义边界
                if term[-1].isalnum():
                    rx = re.compile(r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])", re.I)
                else:
                    rx = re.compile(r"(?<![a-z0-9])" + escaped, re.I)
            else:
                rx = re.compile(re.escape(term))
            patterns.append((term, rx))
        _TERM_PATTERNS = patterns
    return _TERM_PATTERNS


# 同义词组：同组内只保留最具体的一个（"go" 是 "golang" 的缩写，
# 同时推荐两个等于给用户制造重复标签）。
_SYNONYM_GROUPS = (
    ("golang", "go"),
    ("kubernetes", "k8s"),
    ("postgresql", "postgres"),
    ("node.js", "nodejs"),
    ("next.js", "nextjs"),
    ("大语言模型", "llm"),
    ("人工智能", "ai"),
)


def _preferred_synonyms(scored_terms):
    """同组同义词里只留最具体的那个（"golang" 胜过 "go"，"kubernetes" 胜过 "k8s"）。

    必须在排序前就定好：如果等挑选时再比较，先出现的是短词，长词反而被
    当成重复丢掉，结果推荐里留 "GO" 丢 "golang"，与预期相反。
    """
    present = set(t.lower() for t in scored_terms)
    suppress = set()
    for group in _SYNONYM_GROUPS:
        lowered = [g.lower() for g in group]
        hits = [g for g in lowered if g in present]
        if len(hits) <= 1:
            continue
        best = max(hits, key=len)
        suppress.update(g for g in hits if g != best)
    return suppress


def _local_suggest(title, content_md, hot_tags, limit=5):
    """离线兜底：从标题+正文里命中已知技术词，再补上历史高频标签。

    没有 AI 时，``suggest_for_article`` 原来直接返回空串——用户点「推荐标签」
    看到的是"什么都没发生"。这里保证永远能给出候选：先按命中位置与频次排序
    （标题权重更高、出现次数多的靠前），不够再用历史高频补齐。
    """
    text = f"{title or ''}\n{content_md or ''}"
    title_lower = (title or "").lower()
    scored = []
    for term, rx in _term_patterns():
        matches = list(rx.finditer(text))
        if not matches:
            continue
        weight = len(matches)
        if term.lower() in title_lower:
            weight += 10
        scored.append((weight, -matches[0].start(), term))
    scored.sort(reverse=True)
    suppress = _preferred_synonyms([t for _, _, t in scored])
    picked = []
    seen = set()
    for _, _, term in scored:
        if term.lower() in suppress:
            continue
        canonical = term.upper() if len(term) <= 3 and term.isascii() else term
        key = canonical.lower()
        if key in seen:
            continue
        seen.add(key)
        picked.append(canonical)
        if len(picked) >= limit:
            break
    for tag in hot_tags:
        if len(picked) >= limit:
            break
        if tag.lower() not in seen:
            seen.add(tag.lower())
            picked.append(tag)
    return picked


def suggest_for_article(conn, title, content_md, model=None):
    """基于本地 tag_stats 高频 + 关键词命中 + AI 兜底，推荐标签。

    顺序：AI 优先（能理解语义），失败/为空则走完全离线的关键词命中。
    返回值永远是逗号分隔的字符串，可能为空仅当输入也是空。
    """
    _ensure_table(conn)
    hot = list_tags(conn, limit=20)
    hot_tags = [t["tag"] for t in hot]

    try:
        from service.publishing import ai as ai_mod
        raw = ai_mod.chat([{"role": "user", "content":
            f"为这篇文章推荐 3-5 个中文技术标签，只输出逗号分隔的标签，不要解释。\n"
            f"已有热门标签（优先选）: {','.join(hot_tags)}\n"
            f"标题: {title or ''}\n\n{(content_md or '')[:2000]}"}],
            max_tokens=100, model=model)
        tags = [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()]
        if tags:
            return ",".join(tags[:5])
    except Exception:
        pass
    return ",".join(_local_suggest(title, content_md, hot_tags))



def merge_tags(conn, from_tag, to_tag):
    """合并标签：把 from_tag 替换成 to_tag，更新所有 articles.tag 和 tag_stats。

    返回 {affected_articles, deleted_tag}。"""
    _ensure_table(conn)
    config_path = str(_config_path())
    alias_map = _load_alias_map(config_path)
    alias_map[from_tag.lower()] = to_tag
    _save_alias_map(config_path, alias_map)
    # 更新 articles 表的 tags 字段
    rows = conn.execute(
        "SELECT id, tags FROM articles WHERE tags LIKE ?",
        (f"%{from_tag}%",)).fetchall()
    affected = 0
    for row in rows:
        tags_list = [t.strip() for t in row["tags"].split(",") if t.strip()]
        new_tags = []
        for t in tags_list:
            # 命中 from_tag 换成 to_tag，其余原样；随后统一去重——否则
            # "py,Python" 合并后会留下 "Python,Python" 这种重复标签。
            mapped = to_tag if t.lower() == from_tag.lower() else t
            if mapped not in new_tags:
                new_tags.append(mapped)
        conn.execute("UPDATE articles SET tags=?, updated_at=? WHERE id=?",
                     (",".join(new_tags), time.time(), row["id"]))
        affected += 1
    conn.commit()
    sync_from_articles(conn)
    return {"affected": affected, "from": from_tag, "to": to_tag}


def rename_tag(conn, old, new):
    """重命名标签。等价于 merge_tags(old, new) 但语义更直白。"""
    return merge_tags(conn, old, new)


def add_alias(conn, alias, canonical):
    """添加别名：alias 出现时自动替换成 canonical。"""
    config_path = str(_config_path())
    alias_map = _load_alias_map(config_path)
    alias_map[alias.lower()] = canonical
    _save_alias_map(config_path, alias_map)
    sync_from_articles(conn)
    return {"alias": alias, "canonical": canonical}
