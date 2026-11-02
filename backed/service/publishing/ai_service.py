# -*- coding: utf-8 -*-
"""AI 增强操作（写稿/改写/润色/翻译/工具）。

外部依赖通过构造函数注入（get_article_fn / create_article_fn / publish_fn /
update_fn），Hub 自身状态不直接耦合；可安全单测。
"""

import json

from service.publishing import db
from service.publishing.versions import capture as ver_capture
from service.publishing import gate as aigc_gate


class AIService:
    """AI 写稿 + 内容增强工具集。"""

    def __init__(self, conn, demo=False,
                 get_article_fn=None, create_article_fn=None,
                 publish_fn=None, update_fn=None):
        self.conn = conn
        self.demo = demo
        self._get = get_article_fn
        self._create = create_article_fn
        self._publish = publish_fn
        self._update = update_fn

    # ---------- 演示模式 AI 写稿 ----------

    def demo_article(self, topic, style="", words=2000, tags_hint=""):
        """演示模式下的 AI 写稿：本地模板生成，不依赖 LLM。"""
        return {
            "title": topic,
            "summary": f"《{topic}》深度解读：从背景到实践，一文讲透（演示文章）。",
            "content_md": (
                f"# {topic}\n\n"
                f"> 这是一篇由 **AI 内容中台** 在演示模式下生成的文章（DEMO 演示）。\n\n"
                f"## 背景\n\n{topic}正在成为内容创作者关注的焦点。"
                f"本文带你从零了解它的来龙去脉、核心概念与落地方法。\n\n"
                f"## 核心要点\n\n"
                f"1. **统一入口**：一个后台管理所有内容平台。\n"
                f"2. **一次登录**：扫码一次，登录态长期复用。\n"
                f"3. **一键多投**：选中目标平台，一篇文章同时发往知乎、掘金、CSDN 等。\n"
                f"4. **原地更新**：改一处，已发布文章全平台同步更新。\n"
                f"5. **人工兜底**：平台风控或验证码出现时，任务自动转人工。\n\n"
                f"## 写在最后\n\n"
                f"内容中台的价值在于：**把时间还给创作，把重复交给机器**。\n"
            ),
            "tags": (tags_hint or "AI,内容中台,自动化"),
            "ai_model": "demo-local",
            "ext": "{}",
        }

    # ---------- AI 写稿/改写/润色 ----------

    def ai_write(self, topic, style="", words=2000, tags_hint="", publish_to=None,
                 model=None, preferred_provider=None):
        """AI 写一篇并入库。传了 publish_to 就顺手发出去。"""
        if self.demo:
            art = self.demo_article(topic, style, words, tags_hint)
        else:
            from service.publishing import ai as ai_mod
            art = ai_mod.write_article(topic, style, words, tags_hint,
                                       model=model, preferred_provider=preferred_provider)
        # 入库前打 AIGC 标识
        art = aigc_gate.add_aigc_label(art, art.get("ai_model", ""))
        ext = json.loads(art.get("ext", "{}") or {})
        aid = db.create_article(self.conn, art["title"], art["content_md"],
                                summary=art["summary"], tags=art["tags"],
                                source="ai", ai_model=art.get("ai_model", ""),
                                ext=json.dumps(ext, ensure_ascii=False),
                                status="draft")
        out = {"id": aid, "title": art["title"], "summary": art["summary"],
               "tags": art["tags"], "chars": len(art["content_md"]),
               "aigc_labeled": True}
        if publish_to and self._publish:
            # 人审闸门：AI 内容只发草稿
            out["publish"] = self._publish(aid, publish_to, draft_only=True)
            out["note"] = ("AI 内容已按合规闸门以草稿(draft_only)发布；"
                           "确认无误后请调 /articles/{id}/publish(draft_only=false) 正式上线")
        return out

    def ai_rewrite(self, article_id, instruction, publish_to=None, model=None,
                   preferred_provider=None):
        """AI 改写已有文章，改完自动标记待同步。"""
        from service.publishing import ai as ai_mod
        art = self._get(article_id)
        if not art:
            raise ValueError(f"文章 {article_id} 不存在")
        ver_capture(art, change_kind="ai_rewrite",
                    change_note=f"AI 改写: {instruction[:80]}", created_by="ai")
        new_md = ai_mod.rewrite(art["content_md"], instruction,
                                model=model, preferred_provider=preferred_provider)
        db.update_article(self.conn, article_id, content_md=new_md)
        out = {"id": article_id, "chars": len(new_md),
               "pending_sync": len(db.get_pending_updates(self.conn)),
               "model": model or "auto"}
        if publish_to and self._update:
            out["update"] = self._update(article_id, publish_to)
        return out

    def ai_polish(self, article_id, model=None, preferred_provider=None):
        """AI 润色文章。润色前存版本快照（可回滚）。"""
        from service.publishing import ai as ai_mod
        art = self._get(article_id)
        if not art:
            raise ValueError(f"文章 {article_id} 不存在")
        ver_capture(art, change_kind="ai_polish",
                    change_note="AI 润色", created_by="ai")
        new_md = ai_mod.polish(art["content_md"],
                               model=model, preferred_provider=preferred_provider)
        db.update_article(self.conn, article_id, content_md=new_md)
        return {"id": article_id,
                "pending_sync": len(db.get_pending_updates(self.conn))}

    # ---------- AI 增强工具 ----------

    def ai_translate(self, article_id, target_lang="en", model=None,
                     preferred_provider=None):
        """翻译文章到目标语言。"""
        from service.publishing.ai_enhancements import translate
        art = self._get(article_id)
        if not art:
            raise ValueError(f"文章 {article_id} 不存在")
        translated = translate(art["content_md"], target_lang=target_lang,
                              model=model, preferred_provider=preferred_provider)
        return {"id": article_id, "target_lang": target_lang,
                "chars": len(translated), "translated_md": translated[:2000]}

    def ai_image_prompts(self, article_id, n=3, model=None, preferred_provider=None):
        """根据文章内容生成文生图 prompt。"""
        from service.publishing.ai_enhancements import image_prompts
        art = self._get(article_id)
        if not art:
            raise ValueError(f"文章 {article_id} 不存在")
        prompts = image_prompts(art.get("title", ""), art.get("content_md", ""),
                                n=n, model=model, preferred_provider=preferred_provider)
        return {"id": article_id, "prompts": prompts}

    def ai_outline(self, article_id):
        """从已有文章提炼大纲。"""
        from service.publishing.ai_enhancements import extract_outline
        art = self._get(article_id)
        if not art:
            raise ValueError(f"文章 {article_id} 不存在")
        outline = extract_outline(art.get("content_md", ""))
        return {"id": article_id, "outline": outline}

    def ai_seo(self, article_id):
        """生成 SEO 元数据。"""
        from service.publishing.ai_enhancements import seo_fields
        art = self._get(article_id)
        if not art:
            raise ValueError(f"文章 {article_id} 不存在")
        seo = seo_fields(art.get("title", ""), art.get("content_md", ""),
                        art.get("tags", ""))
        return {"id": article_id, **seo}

    def clone(self, article_id):
        """克隆文章：「标题（副本）」+克隆内容。"""
        from service.publishing.ai_enhancements import clone_article
        art = self._get(article_id)
        if not art:
            raise ValueError(f"文章 {article_id} 不存在")
        cloned = clone_article(art)
        new_id = db.create_article(self.conn, **cloned)
        return {"new_id": new_id, "title": cloned["title"]}

    def ai_write_with_template(self, topic, template_key="", **kw):
        """用预置模板 AI 写文章。"""
        from service.publishing.ai_enhancements import ai_write_with_template
        return ai_write_with_template(topic, template_key, **kw)

    # ---------- 只读模型/模板 ----------

    def list_models(self):
        """列出所有可用的 Provider + 模型。"""
        from service.publishing import ai_providers as prov_mod
        return prov_mod.list_providers()

    def list_ai_templates(self):
        """列出所有可用的 AI 写作模板。"""
        from service.publishing.ai_enhancements import list_templates
        return list_templates()

    def ai_ready(self):
        """检查 AI 后端是否可用。"""
        if self.demo:
            return True
        from service.publishing import ai as ai_mod
        return ai_mod.is_ready()

    def content_qa(self, article_id=None, content_md=None, title=None,
                   summary=None, tags=None):
        """内容质检：可读性 + SEO + 重复度。"""
        from service.publishing import content_qa as qa_mod

        if article_id:
            art = self._get(article_id)
            if not art:
                raise ValueError(f"文章 {article_id} 不存在")
            content_md = art.get("content_md", "")
            title = art.get("title", "")
            summary = art.get("summary", "")
            tags = art.get("tags", "")

        other = [dict(r) for r in db.list_articles(self.conn, limit=500)]
        if article_id:
            other = [a for a in other if a["id"] != article_id]

        return qa_mod.full_qa(
            {"title": title or "", "content_md": content_md or "",
             "summary": summary or "", "tags": tags or ""},
            other_articles=other,
            conn=self.conn,        # 启用相似度签名缓存
        )
