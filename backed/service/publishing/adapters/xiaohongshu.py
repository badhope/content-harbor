# -*- coding: utf-8 -*-
"""小红书适配器（浏览器自动化，Patchright 驱动系统 Edge）。

小红书无公开 API，走浏览器自动化——与掘金/CSDN 同款基建。
⚠️ 选择器未经真机验证（无账号凭据），发布前需 dump-dom 采集并更新
   key_selectors。探针会追踪选择器版本。

功能范围：
  - 图文笔记发布（标题 + 正文 Markdown 转 HTML + 可选封面图）
  - 仅支持草稿（小红书网页版无"定时发布"）
  - 原地更新：小红书不支持编辑已发布笔记（平台限制），update 抛"不支持"

登录方式：Edge 弹窗 → 小红书 App 扫码 → 登录态存 profile
"""

import time

from service.publishing.adapters.base import (
    PlatformAdapter, PlatformError, register,
)

HOST = "https://www.xiaohongshu.com"
CREATOR_HOST = "https://creator.xiaohongshu.com"

TITLE_SEL = 'input[placeholder*="填写标题"]'
EDITOR_SEL = '#post-textarea, .ql-editor, [contenteditable="true"]'
PUBLISH_BTN_SEL = 'button:has-text("发布"), .publishBtn'
LOGIN_CHECK_SEL = '.user-avatar, .side-bar-component .user-info'


@register
class XiaohongshuAdapter(PlatformAdapter):
    id = "xiaohongshu"
    name = "小红书"
    needs_browser = True

    selector_version = "2026-10-30 (未验证)"      # 探针据此追踪是否需要更新
    key_selectors = {
        "title_input": TITLE_SEL,
        "editor": EDITOR_SEL,
        "publish_btn": PUBLISH_BTN_SEL,
    }

    login_url = f"{HOST}/login"
    home_url = HOST
    new_url = f"{CREATOR_HOST}/publish/publish?source=official"

    def check_auth(self, page) -> bool:
        try:
            page.goto(self.home_url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(2)
            return bool(page.query_selector(LOGIN_CHECK_SEL))
        except Exception:
            return False

    def publish(self, page, article, options=None):
        options = options or {}
        page.goto(self.new_url, timeout=60000, wait_until="domcontentloaded")
        time.sleep(3)

        # 标题（小红书限 20 字）
        title = (article.get("title") or "")[:20]
        try:
            page.fill(TITLE_SEL, title, timeout=8000)
        except Exception:
            raise self.sel_fail("title_input")

        # 正文（小红书笔记编辑器接受纯文本，Markdown 语法会原样显示）
        content = article.get("content_md", "")
        try:
            page.click(EDITOR_SEL, timeout=8000)
            page.keyboard.insert_text(content)
        except Exception:
            raise self.sel_fail("editor")

        # 等编辑器自动保存
        time.sleep(2)

        if options.get("draft_only"):
            # 小红书网页版无草稿箱 API，直接点发布但不等结果 → 交人工确认
            return {"post_id": "", "post_url": self.new_url,
                    "edit_url": self.new_url, "draft_only": True}

        # 点发布
        try:
            page.click(PUBLISH_BTN_SEL, timeout=8000)
        except Exception:
            raise self.sel_fail("publish_btn")

        # 等跳转到笔记详情（发布成功的信号）
        time.sleep(4)
        current = page.url or ""
        if "publish" in current:
            # 还在发布页 = 可能没成功，回读确认
            raise PlatformError("[小红书] 点发布后仍在发布页，可能内容不合规或接口变更")

        return {"post_id": "", "post_url": current, "edit_url": self.new_url,
                "draft_only": False}

    def update(self, page, pub, article):
        raise PlatformError("小红书不支持原地更新已发布笔记（平台限制），请删除后重发")
