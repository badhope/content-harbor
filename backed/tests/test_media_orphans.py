# -*- coding: utf-8 -*-
"""媒体孤儿清理：这个模块此前只有"入库"没有"出库"。

删掉文章后配图永久占盘，用户既看不到也删不掉——媒体目录只增不减。
"""
import io

from PIL import Image


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buf, "PNG")
    return buf.getvalue()


def test_orphan_detection_follows_references(conn):
    from service.publishing import media

    asset = media.store_bytes(conn, _png(), "image/png", source="test")
    sha = asset["sha256"]

    # 没有文章引用 → 是孤儿
    assert [o["sha256"] for o in media.find_orphans(conn)] == [sha]

    # 正文引用完整 sha → 不再是孤儿
    conn.execute("INSERT INTO articles (title, content_md) VALUES (?, ?)",
                 ("引用", f"![](/media/{sha}.png)"))
    conn.commit()
    assert media.find_orphans(conn) == []


def test_cover_reference_counts_as_usage(conn):
    """封面字段也是引用，只扫正文会误删封面图。"""
    from service.publishing import media

    asset = media.store_bytes(conn, _png(), "image/png", source="cover")
    sha = asset["sha256"]
    conn.execute("INSERT INTO articles (title, content_md, cover) VALUES (?, ?, ?)",
                 ("封面", "无图正文", f"/media/{sha}.png"))
    conn.commit()
    assert media.find_orphans(conn) == []


def test_dry_run_does_not_delete(conn):
    from service.publishing import media

    asset = media.store_bytes(conn, _png(), "image/png", source="dry")
    result = media.purge_orphans(conn, dry_run=True)
    assert result["dry_run"] is True
    assert result["deleted"] == 0
    assert media.asset_path(asset).is_file(), "dry_run 不得删文件"


def test_purge_removes_row_and_file(conn):
    from service.publishing import media

    asset = media.store_bytes(conn, _png(), "image/png", source="purge")
    path = media.asset_path(asset)
    assert path.is_file()

    result = media.purge_orphans(conn, dry_run=False)
    assert result["deleted"] == 1
    assert not path.exists(), "文件应被删除"
    left = conn.execute("SELECT COUNT(*) n FROM media_assets").fetchone()["n"]
    assert left == 0


def test_purge_keeps_referenced_assets(conn):
    from service.publishing import media

    kept = media.store_bytes(conn, _png(), "image/png", source="kept")
    conn.execute("INSERT INTO articles (title, content_md) VALUES (?, ?)",
                 ("用图", f"![](/media/{kept['sha256']}.png)"))
    conn.commit()
    media.purge_orphans(conn, dry_run=False)
    assert media.asset_path(kept).is_file(), "被引用的图不能删"
