# -*- coding: utf-8 -*-
"""CSDN 适配器。

CSDN 的发布接口走阿里云网关签名（X-Ca-Signature），逆向成本高还容易失效，
所以这里统一走**编辑器 UI 操作**——比签名稳，也和真人操作等价。
代价是慢一点，但自动化本来也不赶这几秒。
"""

import time
from pathlib import Path

from service.publishing.adapters.base import PlatformAdapter, PlatformError, register
from service.publishing.browser import CaptchaPolicy

HOME_API = "https://blog.csdn.net/community/home-api/v1/get-business-list"


def _click_text(page, texts, timeout=8000):
    for t in texts:
        try:
            page.click(f"text={t}", timeout=timeout)
            return t
        except Exception:
            continue
    raise PlatformError(f"页面上找不到这些按钮: {texts}")


@register
class CSDNAdapter(PlatformAdapter):
    id = "csdn"
    name = "CSDN"
    login_url = "https://passport.csdn.net/login"

    # 页面结构版本（第三刀加固）：平台改版时更新此版本并同步 key_selectors
    selector_version = "2026-09"
    key_selectors = {'editor': '.editor, .CodeMirror', 'title_input': "input[placeholder*='标题']"}
    home_url = "https://mp.csdn.net/mp_blog/manage/article"
    list_url = "https://mp.csdn.net/mp_blog/manage/article"
    new_url = "https://editor.csdn.net/md/"

    # ---------------- 登录态 ----------------

    def check_auth(self, page) -> bool:
        # 注意：CSDN 未登录时访问 mp 后台**不跳转**（返回空壳 SPA），
        # 光看 URL 会误判成已登录。必须再验一次能不能读到用户名。
        # 2026-09-25 修复：**不再 goto home_url**——登录流程里 check_auth
        # 会每轮轮询，导航会把登录页（二维码）带走，永远等不到扫码。
        # 纯 API 校验（page 在 csdn 域即可带 cookie 调业务接口）。
        try:
            if "passport.csdn.net" in page.url or "/login" in page.url:
                return False
            # 快路径：cookie 里有用户名（UserToken/UserName 是真正登录标志）
            try:
                if self._username(page):
                    return True
            except PlatformError:
                pass
            # 兜底：业务接口的列表**非空**才算登录（游客/未登录返回空列表，
            # 只有登录用户的博客列表才有文章行——2026-09-25 实测假阳性修复）
            try:
                data = self.api_get(
                    page,
                    f"{HOME_API}?page=1&size=10&businessType=blog&noMore=false")
                lst = (data.get("data") or {}).get("list")
                return (data.get("code") == 200
                        and isinstance(lst, list) and len(lst) > 0)
            except Exception:
                return False
        except Exception:
            return False

    def _username(self, page):
        for c in page.context.cookies():
            if c["name"] in ("UserName", "username") and c["value"]:
                return c["value"]
        raise PlatformError("读不到 CSDN 用户名，登录态可能失效")

    # ---------------- 列表 ----------------

    def list_articles(self, page, limit=50):
        user = self._username(page)
        data = self.api_get(
            page, f"{HOME_API}?page=1&size={min(limit, 100)}&businessType=blog"
                  f"&username={user}&noMore=false")
        rows = ((data.get("data") or {}).get("list")) or []
        out = []
        for a in rows:
            aid = str(a.get("articleId") or a.get("id") or "")
            out.append({
                "post_id": aid,
                "title": a.get("title", ""),
                "url": a.get("url") or (f"https://blog.csdn.net/{user}/article/details/{aid}"),
                "edit_url": f"https://editor.csdn.net/md/?articleId={aid}",
                "status": "published",
                "stats": {"view": a.get("viewCount", 0),
                          "digg": a.get("diggCount", 0),
                          "comment": a.get("commentCount", 0)},
            })
        return out

    # ---------------- 发布 ----------------

    def _set_cs_editor(self, page, content):
        """CSDN 编辑器注入。编辑器是 contenteditable <pre.editor__inner>，不是 CodeMirror。
        三级降级：clipboard paste → 逐字 type → 直接设 innerHTML 兜底。
        """
        import time as _time
        # 1) clipboard paste（最稳，能触发 CSDN 编辑器的 onChange）
        try:
            editor = page.query_selector("pre.editor__inner[contenteditable=true]")
            if editor:
                page.evaluate("(t) => navigator.clipboard.writeText(t)", content)
                editor.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                page.keyboard.press("Control+V")
                _time.sleep(1)
                return "clipboard"
        except Exception:
            pass
        # 2) 逐字 type（慢但触发事件最完整）
        try:
            editor = page.query_selector("pre.editor__inner[contenteditable=true]")
            if editor:
                editor.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                page.keyboard.insert_text(content)
                return "typing"
        except Exception:
            pass
        # 3) 兜底：直接设 innerHTML（不触发 onChange，但内容已填入）
        page.evaluate("""([sel, text]) => {
            const el = document.querySelector(sel);
            if (el) { el.innerHTML = text; }
        }""", ["pre.editor__inner[contenteditable=true]", content])
        return "html_fallback"

    def _import_md_file(self, page, content):
        """通过 CSDN 编辑器的 Markdown 文件导入注入正文（比 typing 稳）。

        CSDN 编辑器有隐藏的 input[type=file][accept=".md"]，
        点击"导入"按钮后文件对话框弹出，Playwright 直接 set_input_files 注入。
        返回 True 成功，False 失败（调用方降级到 typing）。
        """
        import tempfile
        import os
        tmp = None
        try:
            # 体检 B18 修复（QA 标质力 2026-09-21）：临时文件写项目 data 目录的
            # 绝对路径——原来 dir="data" 是相对 CWD，从别的目录启动 uvicorn 会炸
            data_dir = Path(__file__).resolve().parent.parent.parent / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            # 写临时 .md 文件
            tmp = tempfile.NamedTemporaryFile(
                suffix=".md", mode="w", encoding="utf-8",
                delete=False, dir=str(data_dir))
            tmp.write(content)
            tmp.close()
            # 找到隐藏的 md 文件输入框
            file_input = page.query_selector('input[type=file][accept*=".md"]')
            if not file_input:
                # 兜底：找任意 .md 输入框
                file_input = page.query_selector('input[type=file]')
            if not file_input:
                return False
            file_input.set_input_files(tmp.name)
            time.sleep(4)  # 等 CSDN 解析 Markdown 并填入编辑器
            return True
        except Exception:
            return False
        finally:
            if tmp:
                try:
                    os.unlink(tmp.name)
                except Exception:
                    pass

    def _select_tag(self, page, tag_name):
        """在 CSDN 发布弹窗里选择文章标签。

        血泪教训（2026-09-20，探针 61~68 验证）：
          选完标签**绝对不要点任何 .modal__close-button**——DOM 顺序上第一个
          close-button 是整个发布弹窗的关闭按钮，点了=把弹窗关了，
          后面点"发布文章"全是空操作（按钮已经不存在了）。
          标签面板保持打开没关系，JS 直接点发布按钮不受浮层遮挡影响。

        步骤：
        1. 在弹窗范围内找"添加文章标签"按钮（弹窗里有两个 tag__btn-tag，
           另一个是"新建分类专栏"，必须按文字区分，不能 .first 了事）
        2. 点面板里的 span.el-tag（优先精确匹配 tag_name，否则点第一个推荐标签）
        3. 不关面板，直接返回

        返回 True 成功，False 失败（调用方兜底不选标签直接发）。
        """
        import time as _time
        try:
            btns = page.locator('.modal__inner-2 button.tag__btn-tag')
            target_btn = None
            for i in range(btns.count()):
                if "添加文章标签" in btns.nth(i).inner_text():
                    target_btn = i
                    break
            if target_btn is None:
                return False
            btns.nth(target_btn).click(timeout=5000)
            _time.sleep(4)
        except Exception:
            return False

        # 点 el-tag：优先精确匹配，匹配不到点第一个推荐标签（保证"至少一个标签"）
        try:
            tags = page.locator('.modal__inner-2 span.el-tag')
            n = tags.count()
            if n == 0:
                return False
            clicked = False
            if tag_name:
                for i in range(n):
                    txt = tags.nth(i).inner_text().strip()
                    if txt and txt.lower() == tag_name.lower():
                        tags.nth(i).click(timeout=5000)
                        clicked = True
                        break
            if not clicked:
                tags.nth(0).click(timeout=5000)
            _time.sleep(2)
            return True
        except Exception:
            return False

    def _fill_publish_form(self, page, article, options):
        """填发布弹窗三件套：分类专栏 + 摘要 + 标签。

        全部用 JS 原生 setter/label click（探针 61~68 验证可写入框架状态）：
          - 分类：JS click 到 label 上（checkbox 本体 display:none 点不了），
            会同步写进隐藏 input[name=categories]，CSDN 认这个值
          - 摘要：nativeInputValueSetter + input 事件（字数计数器会同步变化，
            说明 Vue 状态已更新）
        """
        import time as _time
        # 分类专栏（必填，默认后端与架构设计；可经 options.category 指定）
        category = options.get("category") or "后端与架构设计"
        page.evaluate("""(val) => {
            const inps = document.querySelectorAll('input.tag__option-chk');
            for (const c of inps) { if (c.value === val) {
                const lbl = c.closest('label');
                if (lbl) lbl.click(); else c.click();
            } }
        }""", category)
        _time.sleep(1)

        # 摘要（可选但强烈建议，不填 CSDN 会截正文前 256 字）
        summary = article.get("summary") or article["title"]
        page.evaluate("""(t) => {
            const ta = document.querySelector('textarea.el-textarea__inner');
            if (ta) {
                const s = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value').set;
                s.call(ta, t);
                ta.dispatchEvent(new Event('input', {bubbles: true}));
            }
        }""", summary)
        _time.sleep(1)

        # 标签（文章标签面板，选完不关面板）：优先文章第一个标签，没有就选推荐
        tag_name = options.get("tag_category") or ""
        self._select_tag(page, tag_name)
        _time.sleep(1)

    def _set_title(self, page, title):
        """CSDN 标题注入。

        .article-bar__title--input 是 display:none 的隐藏 input。
        JS value setter **不能用**：值能写进 DOM，但 CSDN 框架状态不同步，
        发布出来的文章标题会变成草稿临时名（tmpXXXXXXXX）——端到端测试 69 实锤。
        唯一可靠路径：强制显示 + Playwright 原生 click + keyboard.type 真实击键
        （真实击键走框架的 key/input 监听，状态才会同步；测试 56-58 验证）。
        """
        # 强制显示（挪到左上角避免被编辑器内容压住）
        orig = page.evaluate("""() => {
            const input = document.querySelector('.article-bar__title--input');
            if (!input) return '';
            const o = input.getAttribute('style') || '';
            input.style.cssText += '; display:block !important; position:fixed;'
                + ' top:8px; left:8px; z-index:99999; width:420px; height:32px;'
                + ' opacity:1; background:#fff; color:#000;';
            return o;
        }""")
        time.sleep(0.3)
        try:
            inp = page.locator('.article-bar__title--input').first
            inp.click(timeout=5000)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.keyboard.type(title, delay=25)
            time.sleep(0.5)
        except Exception:
            pass
        # 恢复原样式
        page.evaluate("""(o) => {
            const input = document.querySelector('.article-bar__title--input');
            if (input) input.setAttribute('style', o);
        }""", orig or "")
        # 校验：display 区域是框架状态的镜像，不一致说明没同步成功
        shown = ""
        try:
            shown = (page.evaluate(
                "() => (document.querySelector('.article-bar__title-display')||{}).innerText || ''"
            ) or "").strip()
        except Exception:
            pass
        # 标题校验做空格不敏感比较（CSDN 标题输入会吞空格："Python 协程"→"Python协程"）
        def _norm(s):
            return (s or "").replace(" ", "").strip()
        return _norm(shown) == _norm(title)

    def publish(self, page, article, options=None):
        options = options or {}
        page.goto(self.new_url, timeout=60000, wait_until="domcontentloaded")
        # CSDN 编辑器是 contenteditable <pre.editor__inner markdown-highlighting>，不是 CodeMirror。
        # 等它渲染完成（5s 兜底）
        try:
            page.wait_for_selector("pre.editor__inner[contenteditable=true], .CodeMirror",
                                   timeout=30000)
        except Exception:
            pass
        time.sleep(3)

        # 正文：优先用 Markdown 文件导入（最稳，CSDN 能正确渲染代码块/表格）。
        # 注意顺序：必须**先导正文再设标题**——导入会重置标题为草稿临时名
        # （端到端测试 70 实锤：先标题后导入 → 发布出来标题=tmpXXXXXX）。
        md_content = article.get("content_md", "")
        import_ok = self._import_md_file(page, md_content)
        if not import_ok:
            # 降级到 typing
            self._set_cs_editor(page, md_content)
        time.sleep(1)

        # 标题：强制显示 + 真实击键（JS setter 不同步框架状态，发布标题会变草稿名）
        title_ok = self._set_title(page, article["title"])
        if not title_ok:
            # 体检 B7 修复（QA 标质力 2026-09-21）：标题没同步就发出去，
            # 文章标题会变成草稿临时名（tmpXXXXXX）且无法回溯是哪篇——直接中止
            shown = page.evaluate(
                "() => (document.querySelector('.article-bar__title-display')||{}).innerText || ''")
            raise PlatformError(
                f"标题注入未通过 display 校验（display={shown!r}），"
                "已中止发布以避免产生 tmp 标题文章。可重试或检查编辑器结构变更。")

        if options.get("draft_only"):
            try:
                btn = page.query_selector('.btn-save')
                if btn and btn.is_visible():
                    btn.click()
                else:
                    _click_text(page, ["保存草稿", "存草稿"])
            except Exception:
                pass
            time.sleep(2)
            return {"post_id": "", "post_url": "", "edit_url": page.url, "draft_only": True}

        # 点"发布文章"按钮（编辑器工具栏），等弹窗完全渲染
        page.query_selector('.btn-publish').click()
        time.sleep(8)

        # 体检 B7 修复（QA 标质力 2026-09-21）：额度预检。弹窗里自带
        # "今日发文额度还有 N 篇"（.publish-quota-tip__count），额度 0 直接报错，
        # 否则只会掉进"40s 无跳转"的模糊失败，还浪费一次发布尝试
        quota = page.evaluate(
            "() => (document.querySelector('.publish-quota-tip__count')||{}).innerText || ''")
        if quota.strip() == "0":
            raise PlatformError(
                "CSDN 今日发文额度已用完（0 篇），请明天再发或前往创作中心提升额度")

        # 弹窗三件套：分类专栏 + 摘要 + 标签（详见 _fill_publish_form 注释）
        self._fill_publish_form(page, article, options)

        # 点弹窗里的"发布文章"红色按钮。
        # 必须用 JS click：Playwright 原生 click 在这个 fixed 布局弹窗里会被
        # 各浮层/拦截器卡超时（探针 57/58/61 反复验证），JS click 直接触发 handler。
        # 点击后 CSDN 先做 userstatus + risk/check 两个前置检查（各约 0.2s），
        # 全过才发提交请求，成功后整页跳转到 mp_blog/creation/success/{articleId}。
        page.evaluate("() => document.querySelector('button.btn-b-red.ml16')?.click()")

        # 轮询等跳转（最长 40s）
        aid = ""
        captcha_kind = None
        deadline = time.time() + 40
        while time.time() < deadline:
            time.sleep(1.5)
            url = page.url
            if "creation/success/" in url:
                aid = url.rstrip("/").split("/")[-1]
                break
            if "articleId=" in url:
                aid = url.split("articleId=")[-1].split("&")[0]
                break
            # 体检 B2 修复（QA 标质力 2026-09-21）：发布链路加验证码检测，
            # 弹验证码时明确报类型，而不是傻等 40s 后报"发布未完成"
            kind = CaptchaPolicy.detect(page)
            if kind:
                captcha_kind = kind
                break
            # 弹窗还在=没发出去；再给一次点击（偶发首次点击被前置检查卡住）
            modal_open = page.evaluate(
                "() => !!document.querySelector('.modal__button-bar')")
            if not modal_open and "editor.csdn.net" not in url:
                aid = url.rstrip("/").split("/")[-1]
                break
        if not aid:
            # 体检 B6 对账修复（2026-09-21 实战验证）：失败前先按标题对一次账——
            # 文章可能实际已发出（比如验证码检测恰好误判在成功跳转前后），
            # 直接按标题查列表，命中就当成功，避免重复发布浪费每日额度
            try:
                for a in self.list_articles(page, limit=10):
                    if (a.get("title") or "").strip() == article["title"].strip():
                        aid = a["post_id"]
                        break
            except Exception:
                pass
        if not aid:
            # 兜底：回到编辑器页说明失败
            msg = (f"发布被验证码拦截（{captcha_kind}），"
                   "请用 --headed 跑一次人工过验证" if captcha_kind
                   else "发布未完成：40s 内未跳转到 success 页")
            return {"post_id": "", "post_url": "", "edit_url": page.url,
                    "draft_only": False,
                    "error": msg}

        try:
            user = self._username(page)
        except Exception:
            user = ""
        return {"post_id": aid,
                "post_url": (f"https://blog.csdn.net/{user}/article/details/{aid}"
                             if user else f"https://blog.csdn.net/article/details/{aid}"),
                "edit_url": f"https://editor.csdn.net/md/?articleId={aid}",
                "draft_only": False}

    # ---------------- 原地更新 ----------------

    def update(self, page, pub, article):
        edit_url = pub.get("edit_url")
        if not edit_url and pub.get("post_id"):
            edit_url = f"https://editor.csdn.net/md/?articleId={pub['post_id']}"
        if not edit_url:
            raise PlatformError("没有 edit_url 也没有 post_id，没法原地更新")

        page.goto(edit_url, timeout=60000, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("pre.editor__inner[contenteditable=true], .CodeMirror",
                                   timeout=30000)
        except Exception:
            pass
        time.sleep(3)

        self._set_cs_editor(page, article.get("content_md", ""))
        time.sleep(1)

        # 点底栏「发布文章」打开发布弹窗。2026-11-04 实测：弹窗打开后底栏按钮仍在
        # DOM 里，此前二次 _click_text(["发布文章",...]) 会再次命中底栏按钮，把刚
        # 打开的弹窗点掉，30s 等关闭必然超时误报"更新未成功"。
        # 现改为：只点一次底栏 → 等弹窗出现 → 在弹窗容器内定位确认按钮。
        _click_text(page, ["发布文章", "发布"])
        try:
            page.wait_for_selector(".modal__publish-article", timeout=15000)
        except Exception:
            pass  # 弹窗没出现时，下面的确认点击会自然失败并给出明确错误
        try:
            page.locator('.modal__publish-article button:has-text("发布文章")') \
                .first.click(timeout=8000, force=True)
        except Exception:
            _click_text(page, ["确定", "确认发布"], timeout=6000)

        # 体检 B8 修复（QA 标质力 2026-09-21）：不再盲发——轮询等发布弹窗关闭，
        # 30s 还挂着就报错（让上层记 failed），而不是 sleep 完直接 return True
        deadline = time.time() + 30
        while time.time() < deadline:
            time.sleep(1.5)
            if not page.evaluate("() => !!document.querySelector('.modal__button-bar')"):
                return True
        raise PlatformError("更新未确认：30s 内发布弹窗未关闭，更新可能没生效")
