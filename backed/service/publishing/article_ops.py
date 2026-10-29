# -*- coding: utf-8 -*-
"""ArticleOpsMixin — 文章 CRUD 与导入（从 service.Hub 机械抽出，逻辑未改）。

拆分动机：service.py 曾近千行、混了文章 / AI / 发布 / 账号 / 浏览器五类职责。
这里按职责搬出，Hub 用多继承组合，对外签名与行为完全不变。
"""

from pathlib import Path

from service.publishing import db
from service.publishing.versions import capture as ver_capture

# 与 service.py 同义：backed/ 目录。导入根用它的父目录（仓库根）。
BACKED_ROOT = Path(__file__).resolve().parents[2]


class ArticleOpsMixin:
    def create(self, title, content_md="", **kw):
        return db.create_article(self.conn, title, content_md, **kw)


    def edit(self, article_id, **kw):
        """改文章。内容一改，已发布实例自动标成 pending，等 sync_pending 去同步。
        编辑前先 snapshot 一份旧版本到 version_history（误改可回滚）。"""
        art = db.get_article(self.conn, article_id)
        if art and ("content_md" in kw or "title" in kw):
            # 改内容前存版本快照（只在真正改动内容时触发）
            ver_capture(self.conn, dict(art), change_kind="edit",
                        change_note="手工编辑", created_by="human")
        ok = db.update_article(self.conn, article_id, **kw)
        pending = db.get_pending_updates(self.conn)
        return ok, len(pending)


    def get(self, article_id):
        r = db.get_article(self.conn, article_id)
        return dict(r) if r else None


    def list(self, status=None, limit=100, offset=0, slim=False):
        """列出文章。slim=True 走列表投影（不带正文），列表页用。"""
        return [dict(r) for r in db.list_articles(self.conn, status, limit, offset,
                                                  slim=slim)]


    def count_articles(self, status=None):
        return db.count_articles(self.conn, status)


    def search(self, keyword):
        return [dict(r) for r in db.search_articles(self.conn, keyword)]


    def import_md(self, path, title=None, tags=""):
        """导入本地 Markdown（front-matter 用 python-frontmatter 解析）。

        旧实现手写 split("---", 2)：正文里出现水平分隔线就切错正文起点，
        也不支持 YAML 列表形式的 tags。现在交给标准库处理。
        """
        from pathlib import Path
        from service.publishing import importing

        try:
            # allow_root 用仓库根：旧实现把 ROOT 算成 backed/service，
            # 连 docs/*.md 都判越权——"仅允许项目目录内"写成了"仅允许 service/ 内"。
            meta, body = importing.read_markdown_file(
                Path(path), allow_root=BACKED_ROOT.parent)
        except importing.ImportError_ as e:
            raise ValueError(str(e))
        title = title or meta.get("title") or Path(path).stem
        tags = tags or meta.get("tags") or ""
        aid = db.create_article(self.conn, title, body, tags=tags, source="import",
                                summary=meta.get("summary", ""),
                                origin_url=meta.get("origin_url", ""))
        return aid


    def import_url(self, url, tags=""):
        """从网页导入：trafilatura 正文提取 → Markdown → 入库。

        返回 {id, title, chars, sitename, author}。抓不到正文时抛 ValueError
        （带原因），不做"导入了一篇空文章"的假成功。
        """
        from service.publishing import importing
        try:
            meta, body = importing.fetch_url(url)
        except importing.ImportError_ as e:
            raise ValueError(str(e))
        aid = db.create_article(self.conn, meta["title"], body,
                                tags=tags or meta.get("tags", ""),
                                summary=meta.get("summary", ""),
                                source="import", origin_url=url)
        return {"id": aid, "title": meta["title"], "chars": len(body),
                "sitename": meta.get("sitename", ""), "author": meta.get("author", "")}

