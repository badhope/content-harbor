# -*- coding: utf-8 -*-
"""标签治理回归：归一化、别名合并、同义合并、热门排序。

`tags.py` 三个写操作（merge/rename/add_alias）都会读写 `_config_path()`
返回的 config.json；测试里把该函数指向临时目录，否则跑一次测试就在
仓库里留下真实配置文件。
"""

import json
from pathlib import Path

import pytest

from service.publishing import db, tags


@pytest.fixture
def tag_env(sandbox, monkeypatch, tmp_path):
    """指向临时配置目录的 tags 模块状态 + 一篇带标签的文章。"""
    cfg_dir = tmp_path / "svc"
    cfg_dir.mkdir(parents=True)
    monkeypatch.setattr(tags, "_config_path", lambda: cfg_dir / "config.json")
    conn = db.connect(sandbox["db_file"])
    yield {"conn": conn, "config": tmp_path / "svc" / "config.json"}
    conn.close()


def _alias_map(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))[tags.ALIAS_MAP_KEY]


def test_normalize_capitalizes_ascii_only():
    assert tags._normalize("python") == "Python"
    assert tags._normalize("PYTHON") == "PYTHON"   # 已是大小写混合，保形
    assert tags._normalize("  python  ") == "Python"
    assert tags._normalize("异步编程") == "异步编程"  # 中文不动
    assert tags._normalize("   ") == ""


def test_normalize_applies_alias_map():
    m = {"py": "Python"}
    assert tags._normalize("Py", m) == "Python"
    assert tags._normalize("python", m) == "Python"


def test_guess_category():
    assert tags._guess_category("python") == "语言"
    assert tags._guess_category("FastAPI") == "框架"
    assert tags._guess_category("大模型") == "领域"
    assert tags._guess_category("摸鱼学") == "其他"


def test_alias_map_roundtrip(tmp_path):
    p = tmp_path / "config.json"
    tags._save_alias_map(p, {"py": "Python"})
    assert tags._load_alias_map(p) == {"py": "Python"}
    # 文件损坏/不存在时静默返回空，不能让治理功能把接口打挂
    (tmp_path / "bad.json").write_text("{oops", encoding="utf-8")
    assert tags._load_alias_map(tmp_path / "bad.json") == {}
    assert tags._load_alias_map(tmp_path / "nope.json") == {}


def test_sync_counts_and_normalizes(tag_env):
    conn = tag_env["conn"]
    db.create_article(conn, "A", "x", tags="python,Python,异步")
    db.create_article(conn, "B", "y", tags="Python , 数据库")
    out = tags.sync_from_articles(conn)
    assert out["synced"] == 3   # 去重后的标签种类
    assert out["total"] == 5    # 标签出现总次数
    rows = {r["tag"]: r["count"] for r in tags.list_tags(conn)}
    assert rows["Python"] == 3
    assert rows["异步"] == 1
    assert rows["数据库"] == 1


def test_sync_is_rebuild_not_accumulate(tag_env):
    conn = tag_env["conn"]
    db.create_article(conn, "A", "x", tags="python")
    tags.sync_from_articles(conn)
    tags.sync_from_articles(conn)
    assert {r["tag"]: r["count"] for r in tags.list_tags(conn)} == {"Python": 1}


def test_list_tags_orders_by_count_and_filters(tag_env):
    conn = tag_env["conn"]
    db.create_article(conn, "A", "x", tags="python")
    db.create_article(conn, "B", "y", tags="python,python2,python3")
    db.create_article(conn, "C", "z", tags="sql,mysql")
    tags.sync_from_articles(conn)
    top = tags.list_tags(conn)
    assert top[0]["tag"] == "Python"
    assert top[0]["count"] == 2
    langs = {r["tag"] for r in tags.list_tags(conn, category="语言")}
    assert langs == {"Python", "Sql"}          # mysql 归「领域」不归「语言」
    assert "Mysql" in {r["tag"] for r in tags.list_tags(conn, category="领域")}
    assert tags.list_tags(conn, category="不存在的类") == []


def test_merge_tags_rewrites_articles_and_records_alias(tag_env):
    conn = tag_env["conn"]
    db.create_article(conn, "A", "x", tags="py,Python")
    db.create_article(conn, "B", "y", tags="py, SQL")

    out = tags.merge_tags(conn, "py", "Python")
    assert out["affected"] == 2
    assert db.get_article(conn, 1)["tags"] == "Python"
    assert db.get_article(conn, 2)["tags"] == "Python,SQL"
    assert _alias_map(tag_env["config"])["py"] == "Python"

    # 别名落库后，重新同步应把别名归并到标准名
    tags.sync_from_articles(conn)
    assert {r["tag"] for r in tags.list_tags(conn)} == {"Python", "SQL"}


def test_merge_tags_deduplicates(tag_env):
    """回归：合并后不得留下 "python,python" 这种重复标签。"""
    conn = tag_env["conn"]
    db.create_article(conn, "A", "x", tags="py,python")
    tags.merge_tags(conn, "py", "python")
    assert db.get_article(conn, 1)["tags"] == "python"


def test_rename_tag_is_merge(tag_env):
    conn = tag_env["conn"]
    db.create_article(conn, "A", "x", tags="python")
    out = tags.rename_tag(conn, "python", "Python")
    assert out["from"] == "python" and out["to"] == "Python"
    assert db.get_article(conn, 1)["tags"] == "Python"


def test_add_alias(tag_env):
    conn = tag_env["conn"]
    db.create_article(conn, "A", "x", tags="golang")
    tags.add_alias(conn, "go", "Golang")
    assert _alias_map(tag_env["config"])["go"] == "Golang"
    db.create_article(conn, "B", "y", tags="go")
    tags.sync_from_articles(conn)
    counts = {r["tag"]: r["count"] for r in tags.list_tags(conn)}
    assert counts == {"Golang": 2}


def test_trending(tag_env):
    conn = tag_env["conn"]
    db.create_article(conn, "A", "x", tags="python,python")
    tags.sync_from_articles(conn)
    assert tags.trending(conn)[0]["tag"] == "Python"


def test_suggest_falls_back_to_local_hot_when_ai_unavailable(tag_env, monkeypatch):
    """AI 不可用时不得抛错，退化成本地高频标签（离线也要能推荐）。"""
    conn = tag_env["conn"]
    db.create_article(conn, "A", "x", tags="python, sql, redis")
    tags.sync_from_articles(conn)

    def boom(*a, **kw):
        raise RuntimeError("no api key")

    monkeypatch.setattr("service.publishing.ai.chat", boom)
    # 同分标签按 tag ASC 排序：Python / Redis / Sql（count 都是 1）
    assert tags.suggest_for_article(conn, "标题", "正文") == "Python,Redis,Sql"


def test_alias_map_lives_next_to_real_config(tag_env, monkeypatch):
    """别名映射必须落在 backed/config.json。

    回归：曾用 Path(__file__).parent.parent 算成 backed/service/config.json，
    该路径既不是其它模块读的配置，也不在 .gitignore 里——点一次「合并」
    就在仓库里凭空多出一个未跟踪文件。
    """
    monkeypatch.undo()
    real = tags._config_path()
    assert real.parent.name == "backed", f"应为 backed/config.json，实际 {real}"
    assert real.name == "config.json"
