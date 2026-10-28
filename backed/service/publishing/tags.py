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
    return Path(__file__).resolve().parents[2] / "config.json"


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
    """标签归一化：去空白 -> 小写 -> alias 替换。"""
    tag = tag.strip()
    if not tag:
        return ""
    tag_lower = tag.lower()
    # 同义合并
    if alias_map:
        for alias, canonical in alias_map.items():
            if tag_lower == alias.lower():
                return canonical
    # 首字母大写保形（"python" -> "Python"）但不大写中文
    if tag.isascii() and tag == tag.lower():
        tag = tag.capitalize()
    return tag


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


def suggest_for_article(conn, title, content_md, model=None):
    """基于本地 tag_stats 高频 + AI 兜底，推荐标签。优先本地（快、省 AI 调用）。"""
    from service.publishing import ai as ai_mod
    # 本地：取 Top 20 高频
    hot = list_tags(conn, limit=20)
    hot_tags = [t["tag"] for t in hot]

    # AI 推荐：让模型从推荐结果里挑
    try:
        raw = ai_mod.chat([{"role": "user", "content":
            f"为这篇文章推荐 3-5 个中文技术标签，只输出逗号分隔的标签，不要解释。\n"
            f"已有热门标签（优先选）: {','.join(hot_tags)}\n"
            f"标题: {title or ''}\n\n{(content_md or '')[:2000]}"}],
            max_tokens=100, model=model)
        tags = [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()]
        return ",".join(tags[:5])
    except Exception:
        return ",".join(hot_tags[:5])


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
