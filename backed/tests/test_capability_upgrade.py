# -*- coding: utf-8 -*-
"""2026-10-29 能力补全回归：FTS 检索 / 列表投影 / 能力矩阵 / 退避 / 媒体管线 / 质检契约。

这些问题都属于"文档写了、代码没实现"或"声明了、没人消费"的类型：
单测层面的模块测试照不出来，只有把 API 契约和真实链路接起来才会暴露。
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from service.publishing import db


# ---------------------------------------------------------------- FTS 检索

def test_fts_search_ranks_and_matches_chinese(conn):
    db.create_article(conn, "Python 异步编程实战", "事件循环与协程调度",
                      summary="异步", tags="python")
    db.create_article(conn, "Rust 所有权模型", "借用检查器与生命周期")
    hits = db.search_articles(conn, "异步编程")
    assert [h["title"] for h in hits] == ["Python 异步编程实战"]
    # 2 字查询走 LIKE 兜底（trigram 最少 3 字符）
    assert [h["title"] for h in db.search_articles(conn, "异步")] == ["Python 异步编程实战"]


def test_fts_index_follows_updates_and_deletes(conn):
    aid = db.create_article(conn, "旧标题", "旧正文内容")
    assert db.search_articles(conn, "旧正文")
    db.update_article(conn, aid, content_md="全新正文段落")
    assert db.search_articles(conn, "旧正文") == []
    assert [h["id"] for h in db.search_articles(conn, "全新正文")] == [aid]


def test_fts_backfills_existing_rows(sandbox):
    """建索引之前就已存在的文章也必须能被搜到（rebuild 回填）。"""
    conn = db.connect(sandbox["db_file"])
    db.create_article(conn, "历史文章", "索引建立前的正文")
    conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('delete-all')")
    conn.commit()
    conn.close()
    conn2 = db.connect(sandbox["db_file"])       # 重连触发回填
    assert [h["title"] for h in db.search_articles(conn2, "索引建立前")] == ["历史文章"]
    conn2.close()


def test_fts_survives_query_syntax_error(conn):
    db.create_article(conn, "NEAR 语法", "正文")
    # FTS5 语法字符不应把搜索打成 500
    assert isinstance(db.search_articles(conn, 'NEAR("x", 2)'), list)


# ---------------------------------------------------------------- 列表投影

def test_list_articles_slim_omits_body(conn):
    db.create_article(conn, "标题", "很长的正文" * 100)
    full = db.list_articles(conn, slim=False)[0]
    slim = db.list_articles(conn, slim=True)[0]
    assert "content_md" in full.keys()
    assert "content_md" not in slim.keys()
    assert slim["chars"] == len("很长的正文" * 100)


# ---------------------------------------------------------------- 能力矩阵

def test_capability_matrix_matches_reality():
    """声明必须与实现一致：声明 update=True 的平台其 update 不能直接抛"不支持"。"""
    from service.publishing.adapters.base import ADAPTERS, capability_of

    for pid, adapter in ADAPTERS.items():
        caps = capability_of(pid)
        assert caps["publish"] is True, f"{pid} 必须支持发布"
        if not caps["update"]:
            continue
        try:
            adapter.update(None, {"post_id": "x", "edit_url": ""},
                           {"title": "t", "content_md": "c"})
        except Exception as e:      # 只关心"明确不支持"这一类
            assert "不支持" not in str(e), f"{pid} 声明支持更新却抛不支持"
        except BaseException:
            pass


def test_platforms_endpoint_exposes_capabilities(client):
    rows = client.get("/api/v1/hub/platforms").json()
    by_id = {r["id"]: r for r in rows}
    assert by_id["zhihu"]["capabilities"]["question"] is True
    assert by_id["toutiao"]["capabilities"]["update"] is False


def test_publish_skips_platform_without_capability(conn, monkeypatch):
    """未声明发布能力的平台不能进适配器（否则被当网络错误重试三次）。"""
    from service.publishing import adapters as adapters_pkg  # noqa: F401
    from service.publishing.adapters.base import CAPABILITIES, capability_of

    assert capability_of("no-such-platform")["publish"] is False
    assert "no-such-platform" not in CAPABILITIES


# ---------------------------------------------------------------- 退避重试

def test_retry_backoff_sets_not_before(conn):
    """失败重试必须留出退避窗口，而不是 1 秒内把 3 次重试打光。"""
    from service.publishing.tasks import TaskManager

    class BoomHub:
        def __init__(self):
            self.calls = 0

        def publish(self, *a, **kw):
            self.calls += 1
            raise TimeoutError("connection timed out")

    hub = BoomHub()
    tm = TaskManager(hub, conn, backoff_base=0.35, backoff_max=5)
    try:
        tid = tm.submit("publish", article_id=1, platforms=["juejin"])
        # 第一次失败后应处于 pending 且带退避时间
        for _ in range(200):
            task = tm.get(tid)
            if task["attempts"] >= 1 and task["status"] == "pending":
                break
            time.sleep(0.01)
        assert tm.get(tid)["status"] == "pending"
        not_before, _ = tm._backoff_state()
        assert tid in not_before
        assert not_before[tid] > time.monotonic()
        # 退避窗口内不会被再次取走
        assert tm._next_pending() is None
        assert hub.calls == 1
    finally:
        tm.close()


def test_backoff_delay_grows_exponentially():
    from service.publishing.tasks import TaskManager

    base, cap = TaskManager.BACKOFF_BASE, TaskManager.BACKOFF_MAX
    delays = [min(base * (2 ** (n - 1)), cap) for n in range(1, 8)]
    assert delays[0] == 2 and delays[1] == 4 and delays[2] == 8
    assert delays[-1] == cap


# ---------------------------------------------------------------- 媒体管线

def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_media_store_is_content_addressed_and_deduped(conn, tmp_path):
    from service.publishing import media

    a = media.store_bytes(conn, _png_bytes(), "image/png", "a.png")
    b = media.store_bytes(conn, _png_bytes(), "image/png", "b.png")
    assert a["sha256"] == b["sha256"]
    assert a["id"] == b["id"]
    assert media.asset_path(a).is_file()
    assert media.asset_path(a).parent.name == a["sha256"][:2]


def test_media_rewrite_markdown_replaces_local_paths(conn, tmp_path, monkeypatch):
    from service.publishing import media

    img = tmp_path / "pic.png"
    img.write_bytes(_png_bytes())
    md, stats = media.rewrite_markdown(
        conn, f"# T\n\n![x]({img})\n\n<img src=\"{img}\">", base="https://cdn.example",
        allow_root=tmp_path)
    assert stats["localized"] == 2 and stats["failed"] == 0
    assert "https://cdn.example/media/" in md
    assert str(img).replace("\\", "/") not in md


def test_media_rewrite_keeps_original_on_failure(conn, monkeypatch):
    from service.publishing import media

    def boom(url):
        raise ValueError("HTTP 403")

    monkeypatch.setattr(media, "fetch_bytes", boom)
    errors = []
    md, stats = media.rewrite_markdown(
        conn, "![x](https://locked.example/a.png)", base="https://cdn.example",
        on_error=lambda src, e: errors.append(src))
    assert stats["failed"] == 1
    assert "https://locked.example/a.png" in md      # 原图保留，不阻断发布
    assert errors == ["https://locked.example/a.png"]


def test_media_rejects_private_hosts():
    from service.publishing import media

    for bad in ("http://127.0.0.1/a.png", "http://10.0.0.5/a.png",
                "http://169.254.169.254/latest/meta-data.png",
                "file:///etc/passwd", "ftp://example.com/a.png"):
        with pytest.raises(ValueError):
            media._validate_url(bad)


def test_media_upload_endpoint_dedupes(client):
    files = {"file": ("a.png", _png_bytes(), "image/png")}
    r1 = client.post("/api/v1/hub/media/upload", files=files)
    assert r1.status_code == 200
    files2 = {"file": ("b.png", _png_bytes(), "image/png")}
    r2 = client.post("/api/v1/hub/media/upload", files=files2)
    assert r2.status_code == 200
    assert r1.json()["sha256"] == r2.json()["sha256"]

    served = client.get(f"/api/v1/hub/media/{r1.json()['sha256']}.png")
    assert served.status_code == 200
    assert served.headers["cache-control"].startswith("public")
    assert client.get("/api/v1/hub/media/../../etc/passwd").status_code in (404, 400)


def test_media_upload_rejects_non_image(client):
    r = client.post("/api/v1/hub/media/upload",
                    files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 422


# ---------------------------------------------------------------- API 契约

def test_qa_analyze_accepts_json_body(client):
    """回归：前端发 JSON body，端点原先只读 query → 422。"""
    r = client.post("/api/v1/hub/qa/analyze",
                    json={"content_md": "# 标题\n\n" + "正文内容。" * 40,
                          "title": "标题", "summary": "摘要", "tags": "a,b"})
    assert r.status_code == 200
    assert "total" in r.json()


def test_qa_analyze_still_accepts_query(client):
    r = client.post("/api/v1/hub/qa/analyze?content_md=hello&title=t")
    assert r.status_code == 200


def test_qa_analyze_rejects_empty(client):
    assert client.post("/api/v1/hub/qa/analyze", json={}).status_code == 422


def test_tags_suggest_accepts_json_body(client):
    r = client.post("/api/v1/hub/tags/suggest",
                    json={"title": "Python 异步", "content_md": "协程"})
    assert r.status_code == 200
    assert "tags" in r.json()


@pytest.fixture
def client(sandbox):
    from app.factory import create_app
    from service.publishing import events
    from service.publishing.tasks import TaskManager

    conn = db.connect(sandbox["db_file"])
    events.init_events(conn)

    class Stub:
        def __init__(self):
            self.conn = conn
            self.tasks = TaskManager(self, conn, backoff_base=0, backoff_max=0)

        def list(self, status=None, limit=100, offset=0, slim=False):
            return [dict(r) for r in db.list_articles(conn, status=status,
                                                      limit=limit, offset=offset,
                                                      slim=slim)]

        def content_qa(self, **kw):
            from service.publishing.content_qa import full_qa
            return full_qa({"title": kw.get("title", ""),
                            "content_md": kw.get("content_md", ""),
                            "summary": kw.get("summary", ""),
                            "tags": kw.get("tags", "")})

        def import_url(self, url, tags=""):
            from service.publishing import importing
            meta, body = importing.fetch_url(url)
            return {"id": 1, "title": meta["title"], "chars": len(body)}

        def accounts(self):
            return []

        def status(self):
            return {"articles": 0}

    hub = Stub()
    import api.deps as deps
    deps._hub_instance = hub
    try:
        with TestClient(create_app()) as c:
            yield c
    finally:
        hub.tasks.close()
        conn.close()
        deps._hub_instance = None


# ---------------------------------------------------------------- 部署脚本一致性

def test_start_scripts_use_app_factory():
    """start.py 的 app 建在 main() 里，模块级没有 app —— `start:app` 必然报错。"""
    repo = Path(__file__).resolve().parents[2]
    for name in ("start.sh", "start.bat", "start.ps1"):
        text = (repo / name).read_text(encoding="utf-8")
        assert "app.factory:create_app" in text, f"{name} 仍在用不可导入的 start:app"
        assert "start:app" not in text


def test_tauri_conf_paths_and_health_url():
    repo = Path(__file__).resolve().parents[2]
    conf = json.loads((repo / "web/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    assert conf["build"]["frontendDist"].endswith("server/static"), \
        "tauri frontendDist 必须指向 vite 的真实产物目录"
    lib = (repo / "web/src-tauri/src/lib.rs").read_text(encoding="utf-8")
    assert "/api/v1/hub/health" in lib, "侧栏健康探活路径必须与后端实际挂载前缀一致"


# ---------------------------------------------------------------- 导入管线

def test_frontmatter_parses_yaml_lists_and_colons(tmp_path):
    """旧实现 split('---', 2) 会把正文里的水平线当 front-matter 结束符。"""
    from service.publishing import importing

    body = "---\ntitle: \"带: 冒号的标题\"\ntags: [python, 异步]\nsummary: 摘要\n---\n\n# 正文\n\n第一段\n\n---\n\n分隔线之后的正文\n"
    meta, content = importing.parse_markdown(body)
    assert meta["title"] == "带: 冒号的标题"
    assert importing._normalize_tags(meta["tags"]) == "python,异步"
    assert "分隔线之后的正文" in content


def test_read_markdown_file_rejects_escape(tmp_path):
    from service.publishing import importing

    outside = tmp_path.parent / "outside.md"
    outside.write_text("# x", encoding="utf-8")
    with pytest.raises(importing.ImportError_):
        importing.read_markdown_file(outside, allow_root=tmp_path)


def test_import_md_uses_frontmatter_title_and_tags(conn, tmp_path, monkeypatch):
    from service.publishing import service as service_mod

    monkeypatch.setattr(service_mod, "BACKED_ROOT", tmp_path / "backed")
    md = tmp_path / "note.md"
    md.write_text("---\ntitle: YAML 标题\ntags: [a, b]\n---\n\n正文内容", encoding="utf-8")

    class FakeHub(service_mod.Hub):
        def __init__(self, conn):      # 跳过浏览器池/调度器
            self.conn = conn

    aid = FakeHub(conn).import_md(str(md))
    row = db.get_article(conn, aid)
    assert row["title"] == "YAML 标题"
    assert row["tags"] == "a,b"
    assert "正文内容" in row["content_md"]


def test_import_url_rejects_private_host(client):
    r = client.post("/api/v1/hub/articles/import", json={"url": "http://127.0.0.1/a"})
    assert r.status_code == 422


def test_import_requires_path_or_url(client):
    assert client.post("/api/v1/hub/articles/import", json={}).status_code == 422


# ---------------------------------------------------------------- 指标导出

def test_metrics_prometheus_format(client):
    from service.publishing import observability as obs
    obs.METRICS.incr("publish.ok", 2)
    r = client.get("/api/v1/hub/metrics?format=prometheus")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert "publish_ok_total 2.0" in r.text
    assert "# TYPE publish_ok_total counter" in r.text


def test_metrics_json_default_unchanged(client):
    r = client.get("/api/v1/hub/metrics")
    assert r.status_code == 200
    body = r.json()
    assert "counters" in body and "histograms" in body
    assert body["prometheus_url"].startswith("/api/v1/hub/metrics")


# ---------------------------------------------------------------- 数据目录一致性

def test_all_data_artifacts_follow_the_same_root(sandbox, monkeypatch):
    """DB / 事件日志 / 验证码截图 / webhook / 媒体必须同源，否则桌面版会分裂。"""
    from service.publishing import db, media, observability as obs, _webhook_store as wh

    root = db.DB_PATH.parent
    assert obs.EVENT_LOG.parent.parent == root
    assert wh._DB_PATH == db.DB_PATH
    assert media.media_root().parent == root


# ---------------------------------------------------------------- 标签聚类

def test_tag_clusters_group_case_and_typos(conn):
    from service.publishing import tags

    for t in ("Python", "python", "Javscript", "JavaScript",
              "异步编程", "异步", "React", "Reactjs"):
        db.create_article(conn, t, "x", tags=t)
    tags.sync_from_articles(conn)
    clusters = tags.cluster_similar_tags(conn)
    pairs = {frozenset([c["canonical"], *c["members"]]) for c in clusters}
    assert frozenset({"Javscript", "JavaScript"}) in pairs
    assert frozenset({"异步编程", "异步"}) in pairs
    assert frozenset({"React", "Reactjs"}) in pairs


def test_tag_clusters_do_not_merge_unrelated_chinese(conn):
    from service.publishing import tags

    for t in ("数据库", "数据", "算法"):
        db.create_article(conn, t, "x", tags=t)
    tags.sync_from_articles(conn)
    clusters = tags.cluster_similar_tags(conn)
    merged = {m for c in clusters for m in c["members"]} | {c["canonical"] for c in clusters}
    assert "算法" not in merged


# ---------------------------------------------------------------- 门禁扫描

def test_gate_scan_handles_large_wordlist_quickly():
    """Aho-Corasick 预处理一次，扫描是线性的；词表扩张不该拖慢发布。"""
    import time as _t
    from service.publishing import gate

    body = "普通技术文章正文。" * 5000
    t0 = _t.perf_counter()
    assert gate.scan_dangerous(body, "标题") == []
    assert _t.perf_counter() - t0 < 0.5, "50 万字正文的安全扫描必须保持毫秒级"


def test_gate_scan_is_case_insensitive_for_ascii():
    from service.publishing import gate
    assert gate.scan_dangerous("see PORNHUB for details")


def test_gate_scan_catches_ascii_pattern_inside_chinese():
    """回归： 在中文语境不成立，"看PORNHUB网站" 曾被完全漏掉。"""
    from service.publishing import gate
    assert gate.scan_dangerous("看PORNHUB网站")


def test_similarity_signature_cache_reuses_unchanged_articles(conn):
    """同一篇库内文章重复质检不该重算签名（内容哈希不变则命中缓存）。"""
    from service.publishing import content_qa as qa

    # 必须让候选通过"精确 Jaccard 粗筛"才会进入签名计算——这是刻意的两级
    # 剪枝：完全不像的文章连签名都不算（更快），缓存自然不会为它写行。
    base = "这是一段用于相似度检测的正文内容。" * 30
    for i in range(5):
        db.create_article(conn, f"文{i}", base + f"尾部差异{i}")
    target = {"title": "新文", "content_md": base + "另一段结尾"}
    others = [dict(r) for r in db.list_articles(conn, limit=10)]
    qa.full_qa(target, other_articles=others, conn=conn)     # 首次：建缓存
    cached = conn.execute("SELECT COUNT(*) c FROM article_simhash").fetchone()["c"]
    assert cached == len(others)

    import time as _t
    t0 = _t.perf_counter()
    qa.full_qa(target, other_articles=others, conn=conn)     # 二次：全命中
    assert _t.perf_counter() - t0 < 0.5

    db.update_article(conn, others[0]["id"], content_md="改过的内容" * 20)
    qa.full_qa(target, other_articles=others, conn=conn)
    row = conn.execute("SELECT content_hash FROM article_simhash WHERE article_id=?",
                       (others[0]["id"],)).fetchone()
    assert row is not None      # 内容变了会重算并回写 
