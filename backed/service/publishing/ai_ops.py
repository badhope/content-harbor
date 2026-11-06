# -*- coding: utf-8 -*-
"""AIOpsMixin — AI 能力委托（从 service.Hub 机械抽出，逻辑未改）。

Hub 自身不实现 AI，全部委托 ``self.ai``（AIService）；这里只是薄转发层。
"""


class AIOpsMixin:
    def ai_write(self, topic, style="", words=2000, tags_hint="", publish_to=None,
                 model=None, preferred_provider=None):
        """(委托 AIService)"""
        return self.ai.ai_write(topic, style=style, words=words, tags_hint=tags_hint,
                publish_to=publish_to, model=model, preferred_provider=preferred_provider)


    def ai_rewrite(self, article_id, instruction, publish_to=None, model=None,
                   preferred_provider=None):
        """(委托 AIService)"""
        return self.ai.ai_rewrite(article_id, instruction,
                publish_to=publish_to, model=model, preferred_provider=preferred_provider)


    def ai_polish(self, article_id, model=None, preferred_provider=None):
        """(委托 AIService)"""
        return self.ai.ai_polish(article_id, model=model, preferred_provider=preferred_provider)


    def ai_translate(self, article_id, target_lang="en", model=None,
                     preferred_provider=None):
        """(委托 AIService)"""
        return self.ai.ai_translate(article_id, target_lang=target_lang,
                model=model, preferred_provider=preferred_provider)


    def ai_image_prompts(self, article_id, n=3, model=None, preferred_provider=None):
        """(委托 AIService)"""
        return self.ai.ai_image_prompts(article_id, n=n, model=model,
                preferred_provider=preferred_provider)


    def ai_outline(self, article_id):
        """(委托 AIService)"""
        return self.ai.ai_outline(article_id)


    def ai_seo(self, article_id):
        """(委托 AIService)"""
        return self.ai.ai_seo(article_id)


    def clone(self, article_id):
        """(委托 AIService)"""
        return self.ai.clone(article_id)


    def ai_write_with_template(self, topic, template_key="", **kw):
        """(委托 AIService)"""
        return self.ai.ai_write_with_template(topic, template_key, **kw)


    def list_models(self):
        """(委托 AIService)"""
        return self.ai.list_models()


    def content_qa(self, article_id=None, content_md=None, title=None,
                  summary=None, tags=None):
        """(委托 AIService)"""
        return self.ai.content_qa(article_id=article_id, content_md=content_md,
                title=title, summary=summary, tags=tags)


    def list_ai_templates(self):
        """(委托 AIService)"""
        return self.ai.list_ai_templates()


    def ai_ready(self, probe_local=True):
        """(委托 AIService)"""
        try:
            return self.ai.ai_ready(probe_local=probe_local)
        except TypeError:
            # 测试桩/旧实现只接受无参调用
            return self.ai.ai_ready()

