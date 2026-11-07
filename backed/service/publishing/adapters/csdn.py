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
        """CSDN 编辑器注入（新建/更新通用）。编辑器是 contenteditable <pre>。

        真机教训（2026-11-08）：更新已有文章时，**文件导入和 clipboard 都会
        静默失败**——`_import_md_file` 返回 True，但编辑器里还是原内容；原因是
        CSDN 的导入只往空编辑器里填，已有内容时它什么也不做（也不报错）。
        clipboard 的 `navigator.clipboard.writeText` 在无用户手势时会拒写。

        唯一在"已有内容"场景下实测有效的是：点进编辑器 → Ctrl+A → Delete →
        `keyboard.insert_text`（真实键盘事件，框架状态同步）。所以这里先走
        这条主路径，成功即返回；只有它抛异常才降级到 MD 导入 / innerHTML。
        """
        import time as _time
        # 编辑器是异步渲染的（更新页比新建页更慢）。不等就查会拿到 None，
        # 直接返回 no_editor——后面点发布自然无效，看起来像"点了没反应"。
        for _ in range(20):
            editor = page.query_selector("pre.editor__inner[contenteditable=true]")
            if editor:
                break
            _time.sleep(1)
        if not editor:
            return "no_editor"

        # 主路径：真实键盘全选删除 + 插入
        try:
            editor.click()
            _time.sleep(0.5)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            _time.sleep(0.8)
            page.keyboard.insert_text(content)
            _time.sleep(1.5)
            got = page.evaluate("""() => {
                const el = document.querySelector('pre.editor__inner[contenteditable=true]');
                return el ? el.innerText : '';
            }""")
            if got and len(got.strip()) > 10:
                return "keyboard"
        except Exception:
            pass

        # 降级 1：Markdown 文件导入（空编辑器场景可靠）
        try:
            if self._import_md_file(page, content):
                return "md_import"
        except Exception:
            pass

        # 降级 2：直接设 innerHTML（不触发 onChange，仅兜底显示）
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

    def _select_tag(self, page, tag_names):
        """在 CSDN 发布弹窗里精确保留指定的多个标签。

        真机摸清的行为（2026-11-08，实锤到线上文章才暴露）：

        1. **打开标签面板时，CSDN 会预选 5 个推荐标签**（上次是
           人工智能/语言模型/Gemini/谷歌/深度学习，这一次又是别的）。
           旧实现"点一个就收工"，结果线上文章的标签是 CSDN 猜的
           `#ide`，而不是我们指定 `Python` —— 用户在文章页一眼就能看出不对。
        2. 标签最多 8 个（面板右下角"还可添加 N 个标签"）。
        3. 面板里有搜索框，placeholder 明写
           「请输入文字搜索，Enter键入可添加自定义标签」——键入后回车即可
           精确添加，不依赖推荐列表里恰好有这个词。

        所以正确顺序是：**先清空所有已选 → 再逐个键入+回车添加**。
        只按名字点推荐项是不行的：推荐列表里没有的词永远加不上，而有预选时
        个数又会超标。

        返回实际生效的标签名列表（供上层回读校验用）。
        """
        import time as _time
        wanted = [t.strip() for t in (tag_names or []) if t and t.strip()]
        if not wanted:
            return []

        def chips():
            return page.evaluate("""() => {
                const box = document.querySelector('.form-tag-box');
                if (!box) return [];
                return [...box.querySelectorAll('.el-tag')]
                    .map(e => (e.innerText || '').trim()).filter(Boolean);
            }""")

        # 打开发布弹窗里的「添加文章标签」面板
        try:
            btns = page.locator('.modal__inner-2 button.tag__btn-tag')
            opened = False
            for i in range(btns.count()):
                if "添加文章标签" in btns.nth(i).inner_text():
                    btns.nth(i).click(timeout=5000)
                    opened = True
                    break
            if not opened:
                return []
            _time.sleep(3)
        except Exception:
            return []

        # 清空预选标签（这是关键：不清就会把平台的推荐当成自己的标签发出去）
        page.evaluate("""() => {
            const box = document.querySelector('.form-tag-box');
            if (!box) return 0;
            let n = 0;
            for (let round = 0; round < 12; round++) {
                const chip = box.querySelector('.el-tag');
                if (!chip) break;
                const close = chip.querySelector('.el-tag__close, .el-icon-close, i');
                if (close) { close.click(); n++; } else { chip.remove(); n++; }
            }
            return n;
        }""")
        _time.sleep(1.5)

        # 逐个添加：键入 → 等下拉 → 回车
        for name in wanted[:8]:
            try:
                inp = page.locator('.mark_selection_box input').first
                if not inp.count():
                    break
                inp.click()
                _time.sleep(0.6)
                page.keyboard.type(name, delay=70)
                _time.sleep(2.0)
                page.keyboard.press("Enter")
                _time.sleep(1.5)
            except Exception:
                continue

        got = chips()

        # 收掉标签面板（不收会遮住下面的单选，截图也看不见）
        try:
            panel_close = page.locator(
                '.mark_selection_box .modal__close-button').first
            if panel_close.count() and panel_close.is_visible():
                panel_close.click(timeout=3000)
                _time.sleep(1)
        except Exception:
            pass
        return got

    def _fill_publish_form(self, page, article, options):
        """把 CSDN 发布弹窗的每一项都填上，并逐项回报结果。

        弹窗真实结构（2026-11-07 用登录态实测 + 截图确认，见
        docs/verification/2026-11-08-publish-settings.md）：

            文章标签 *      —— 必须至少一个，红星
            添加封面        —— 从本地上传
            文章摘要        —— 0/256，不填则自动截正文前 256 字
            分类专栏        —— 新建/选择专栏
            文章类型 *      —— 原创 / 转载 / 翻译
            创作声明        —— 下拉（默认"无声明"）
            文章备份        —— 是否同步到 GitCode（勾选）
            可见范围 *      —— 全部可见 / 仅我可见 / 粉丝可见 / VIP 可见
            文章模板 *      —— 默认 / 简洁 / 墨衍
            多平台发布      —— 否 / 是
            参与活动        —— 下拉
            话题            —— 下拉

        设计原则：**每项都试，逐项记录成功/失败，绝不"填不上就静默跳过"**。
        返回 {"filled": [...], "skipped": [...], "failed": [...]}，由上层写进
        job 消息，用户能看到到底哪项没填上。截图由 page_hook 侧负责。

        全部用 JS 原生 setter / label click：Vue 框架只认原生事件，直接改
        value 属性不会同步状态（探针 61~68 验证）。
        """
        import time as _time
        filled, skipped, failed = [], [], []

        def rec(name, ok, why=""):
            if ok is True:
                filled.append(name)
            elif ok == "skip":
                skipped.append(f"{name}({why})" if why else name)
            else:
                failed.append(f"{name}({why})" if why else name)

        # ---- 1) 文章标签（必填，最多 8 个）----
        # 必须按"清空预选 → 逐个键入回车"来做，并按回读到的**真实标签名**判定。
        # 旧实现只点一个推荐项，线上文章的标签会是平台猜的 `#ide`（2026-11-08
        # 在文章页实锤），而我们的 settings_filled 里却写着"文章标签 ✅"。
        tags = options.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        try:
            got = self._select_tag(page, tags)
            got_set = {g.strip() for g in got}
            missing = [t for t in tags[:8] if t.strip() not in got_set]
            if got and not missing:
                rec("文章标签", True)
                filled[-1] = f"文章标签={','.join(got)}"
            elif got:
                rec("文章标签", False, f"缺少 {','.join(missing)}（实得 {','.join(got)}）")
            else:
                rec("文章标签", False, "一个标签都没选上")
        except Exception as e:   # aqg: top-level boundary（单项失败不阻断其余项）
            rec("文章标签", False, str(e)[:60])

        # ---- 2) 添加封面（把文章 cover 上传）----
        # 页面上有 3 个 file input，顺序不可靠：
        #   #import-markdown-file-input  accept=.md   ← 正文导入，绝不能选错
        #   .cfw-file-input              accept=image ← 客服反馈组件
        #   .el-upload__input            accept=.png,.jpg,.jpeg,.gif ← 封面
        # 旧实现用 querySelector('input[type=file]') 取第一个，结果把封面图塞给了
        # Markdown 导入器——静默无效，截图里封面框还是空的（实测 2026-11-08）。
        cover = (options.get("cover") or "").strip()
        if not cover:
            rec("添加封面", "skip", "文章没有封面")
        else:
            try:
                inp = page.query_selector('.el-upload__input')
                if not inp:
                    rec("添加封面", False, "找不到封面上传控件 (.el-upload__input)")
                elif cover.startswith(("http://", "https://")):
                    rec("添加封面", "skip", "远程 URL 需要先下载，本次跳过")
                else:
                    inp.set_input_files(cover)
                    _time.sleep(3)
                    # CSDN 上传后会弹「图片编辑」对话框，必须点「确认上传」，
                    # 否则封面只在弹窗里预览、不会真正附加到文章（实测截图：
                    # 弹窗一直挡在发布弹窗前，点发布会被拦）。这一步以前完全缺失。
                    #
                    # 真实元素是 `<div class="vicp-operate-btn">确认上传</div>`
                    # ——不是 button。按 tag 找永远找不到（2026-11-08 实测）。
                    # 用 JS 点 div：fixed 浮层里 Playwright 原生 click 会等到超时。
                    confirmed = page.evaluate("""() => {
                        const el = document.querySelector('.vicp-operate-btn');
                        if (el && (el.innerText||'').includes('确认上传')) {
                            el.click();
                            return true;
                        }
                        for (const d of document.querySelectorAll('div,span')) {
                            if ((d.innerText||'').trim() === '确认上传') { d.click(); return true; }
                        }
                        return false;
                    }""")
                    if confirmed:
                        _time.sleep(4)
                    # 保险：等图片编辑浮层消失，最多等 10s
                    for _ in range(10):
                        if not page.evaluate(
                                "() => !!document.querySelector('.vicp-operate-btn')"):
                            break
                        _time.sleep(1)
                    state = page.evaluate("""() => {
                        const box = document.querySelector('.form-tag-box');
                        const scope = box ? box.parentElement : document;
                        const img = scope.querySelector('img');
                        const hint = (document.body.innerText || '').includes('从本地上传');
                        return {img: !!(img && img.src && !img.src.startsWith('data:,')),
                                hint};
                    }""")
                    ok = bool(state.get("img"))
                    rec("添加封面", True if ok else False,
                        "" if ok else f"上传后仍未见预览图（hint={state.get('hint')}）")
            except Exception as e:
                rec("添加封面", False, str(e)[:60])

        # ---- 3) 文章摘要 ----
        try:
            summary = (options.get("summary") or article.get("summary")
                       or article.get("title") or "")
            # 必须限定在发布弹窗内：页面别处也有 .el-textarea__inner（AI 助手输入框、
            # 反馈框），全局取第一个会填错地方——摘要栏始终是 6/256，点发布时被
            # 前端校验拦下，表现为"点了没反应、没有任何网络请求"（2026-11-08 实测
            # 就是这么卡的：弹窗里摘要只有 6 个字，CSDN 要求 ≥ 10 字才允许发）。
            page.evaluate("""(t) => {
                const scope = document.querySelector('.modal__inner-2')
                              || document.querySelector('.modal__publish-article')
                              || document;
                const ta = scope.querySelector('textarea.el-textarea__inner');
                if (!ta) return false;
                const s = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value').set;
                s.call(ta, t);
                ta.dispatchEvent(new Event('input', {bubbles: true}));
                ta.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }""", summary)
            _time.sleep(1.2)
            got = page.evaluate("""() => {
                const scope = document.querySelector('.modal__inner-2')
                              || document.querySelector('.modal__publish-article')
                              || document;
                const ta = scope.querySelector('textarea.el-textarea__inner');
                return ta ? (ta.value || '') : '';
            }""")
            rec("文章摘要", True if got.strip() == summary.strip() else False,
                "" if got.strip() == summary.strip() else f"回读 {got[:20]!r}")
        except Exception as e:
            rec("文章摘要", False, str(e)[:60])

        # ---- 4) 分类专栏 ----
        category = options.get("category") or "后端与架构设计"
        try:
            hit = page.evaluate("""(val) => {
                const inps = document.querySelectorAll('input.tag__option-chk');
                for (const c of inps) { if (c.value === val) {
                    const lbl = c.closest('label');
                    if (lbl) lbl.click(); else c.click();
                    return true;
                } }
                return false;
            }""", category)
            _time.sleep(1)
            # 选了专栏后弹窗里会出现已选专栏名
            picked = page.evaluate("""(val) => document.body.innerText.includes(val)""", category)
            rec("分类专栏", True if (hit and picked) else ("skip" if not hit else False),
                "" if hit else f"没有名为 {category!r} 的专栏")
        except Exception as e:
            rec("分类专栏", False, str(e)[:60])

        # ---- 5) 文章类型（原创/转载/翻译）----
        # 真实结构（2026-11-08 实测 DOM）：<input type=radio id="original" value="original">
        # + <label for="original">原创</label>。label 自己包着 input 是错的假设——
        # CSDN 用 for 关联兄弟节点，所以必须点 label（click 会转发给 input），
        # 再回读 input.checked 才算验证。此前按 label 内含 input 找，永远找不到。
        atype = (options.get("article_type") or "original").strip()
        label_map = {"original": "原创", "reprint": "转载", "translation": "翻译"}
        target = label_map.get(atype, "原创")
        try:
            ok = page.evaluate("""(txt) => {
                const labs = document.querySelectorAll('label[for]');
                for (const l of labs) {
                    if ((l.innerText||'').trim().startsWith(txt)) {
                        l.click();
                        const inp = document.getElementById(l.getAttribute('for'));
                        return !!(inp && inp.checked);
                    }
                }
                return false;
            }""", target)
            _time.sleep(0.5)
            rec("文章类型", True if ok else False, "" if ok else f"没选中 {target}")
        except Exception as e:
            rec("文章类型", False, str(e)[:60])

        # ---- 6) 创作声明 ----
        statement = (options.get("creation_statement") or "").strip()
        try:
            if not statement:
                # 保持默认"无声明"，但确认它是选中状态而不是空
                cur = page.evaluate(
                    "() => (document.querySelector('input[placeholder=\"无声明\"]')||{}).value || ''")
                rec("创作声明", "skip", f"用默认（当前 {cur or '无声明'}）")
            else:
                inp = page.query_selector('input[placeholder="无声明"]')
                if not inp:
                    rec("创作声明", False, "找不到下拉")
                else:
                    inp.click()
                    _time.sleep(1.5)
                    chosen = page.evaluate("""(txt) => {
                        const items = document.querySelectorAll(
                            '.el-select-dropdown__item, [class*="dropdown"] li, [class*="option"]');
                        for (const it of items) {
                            if ((it.innerText||'').trim() === txt) { it.click(); return true; }
                        }
                        return false;
                    }""", statement)
                    _time.sleep(0.8)
                    rec("创作声明", True if chosen else False,
                        "" if chosen else f"下拉里没有 {statement!r}")
        except Exception as e:
            rec("创作声明", False, str(e)[:60])

        # ---- 7) 文章备份到 GitCode（默认不勾，勾上才是"填满"）----
        try:
            want_backup = bool(options.get("backup_gitcode"))
            state = page.evaluate("""(want) => {
                const labels = document.querySelectorAll('label, span');
                for (const l of labels) {
                    if ((l.innerText||'').includes('同时备份到GitCode')) {
                        const cb = l.querySelector('input[type=checkbox]')
                                  || (l.previousElementSibling
                                      && l.previousElementSibling.querySelector
                                      && l.previousElementSibling.querySelector('input[type=checkbox]'));
                        if (cb) { if (cb.checked !== want) l.click(); return cb.checked; }
                    }
                }
                return null;
            }""", want_backup)
            if state is None:
                rec("文章备份", "skip", "账号未开通 GitCode 备份")
            else:
                rec("文章备份", True if state == want_backup else False)
        except Exception as e:
            rec("文章备份", False, str(e)[:60])

        # ---- 8) 可见范围 ----
        vis = (options.get("visibility") or "public").strip()
        vis_map = {"public": "全部可见", "private": "仅我可见",
                   "fans": "粉丝可见", "vip": "VIP可见"}
        vtxt = vis_map.get(vis, "全部可见")
        try:
            ok = page.evaluate("""(txt) => {
                const labs = document.querySelectorAll('label[for]');
                for (const l of labs) {
                    if ((l.innerText||'').trim().startsWith(txt)) {
                        l.click();
                        const inp = document.getElementById(l.getAttribute('for'));
                        return !!(inp && inp.checked);
                    }
                }
                return false;
            }""", vtxt)
            _time.sleep(0.5)
            rec("可见范围", True if ok else False, "" if ok else f"没选中 {vtxt}")
        except Exception as e:
            rec("可见范围", False, str(e)[:60])

        # ---- 9) 文章模板 ----
        # 结构：<input id="article-template-simple-1" type=radio value="4">
        # + <label for="article-template-simple-1">简洁模板</label>。
        # id 带随机后缀，所以按 label 文本找、用 for 反查 input。
        tmpl = (options.get("template") or "").strip()
        tmpl_map = {"default": "默认模板", "simple": "简洁模板", "special": "墨衍模板"}
        ttxt = tmpl_map.get(tmpl, "")
        try:
            if not ttxt:
                rec("文章模板", "skip", "用默认模板")
            else:
                ok = page.evaluate("""(txt) => {
                    const labs = document.querySelectorAll('label[for]');
                    for (const l of labs) {
                        if ((l.innerText||'').trim().startsWith(txt)) {
                            l.click();
                            const inp = document.getElementById(l.getAttribute('for'));
                            return !!(inp && inp.checked);
                        }
                    }
                    return false;
                }""", ttxt)
                _time.sleep(0.5)
                rec("文章模板", True if ok else False, "" if ok else f"没选中 {ttxt}")
        except Exception as e:
            rec("文章模板", False, str(e)[:60])

        # ---- 10) 多平台发布 ----
        # 结构：`.multi-platform-publish__radio` 里两个 input，
        # id 分别是 multiPlatformPublishNo / multiPlatformPublishYes，
        # 靠 id 直接定位，别按 label 文本猜（旧实现要求 label 里同时含"是"和
        # "多平台"，实际 label 只有"是"，永远匹配不到 → 静默跳过）。
        try:
            want_multi = bool(options.get("multiplatform"))
            target_id = ("multiPlatformPublishYes" if want_multi
                         else "multiPlatformPublishNo")
            ok = page.evaluate("""(id) => {
                const inp = document.getElementById(id);
                if (!inp) return false;
                const lab = document.querySelector(`label[for="${id}"]`);
                if (lab) lab.click(); else inp.click();
                return inp.checked;
            }""", target_id)
            _time.sleep(0.5)
            rec("多平台发布", True if ok else False,
                "" if ok else f"没选中 {target_id}")
        except Exception as e:
            rec("多平台发布", False, str(e)[:60])

        # ---- 11) 参与活动 ----
        # 没指定时也去拉下拉：CSDN 的活动列表是平台推荐的，有就用第一个。
        # "留空"和"选一个平台给的活动"对文章曝光是两回事——用户要的是填满。
        event = (options.get("event") or "").strip()
        try:
            inp = page.query_selector('input[placeholder="请选择创作活动"]')
            if not inp:
                rec("参与活动", False, "找不到活动下拉")
            else:
                page.evaluate("""() => {
                    const i = document.querySelector('input[placeholder="请选择创作活动"]');
                    if (i) i.click();
                }""")
                _time.sleep(2)
                result = page.evaluate("""(want) => {
                    // 同话题：只认当前可见的 dropdown，取最靠上的那个（活动在话题上）
                    const dds = [...document.querySelectorAll('.el-select-dropdown')]
                        .filter(d => {
                            const r = d.getBoundingClientRect();
                            const cs = getComputedStyle(d);
                            return r.width > 20 && r.height > 20
                                && cs.display !== 'none' && cs.visibility !== 'hidden';
                        });
                    if (!dds.length) return {ok:false, names:[], picked:'', why:'没有可见下拉'};
                    dds.sort((a,b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
                    const items = [...dds[0].querySelectorAll('.el-select-dropdown__item')];
                    const names = items.map(it => (it.innerText||'').trim()).filter(Boolean);
                    if (!names.length) return {ok:false, names:[], picked:'', why:'下拉为空'};
                    let target = items[0];
                    if (want) {
                        const m = items.find(it => (it.innerText||'').includes(want));
                        if (m) target = m;
                    }
                    const picked = (target.innerText||'').trim();
                    target.click();
                    return {ok:true, names:names.slice(0,8), picked};
                }""", event)
                _time.sleep(1.5)
                rec("参与活动", True if result.get("ok") else False,
                    "" if result.get("ok") else result.get("why", "活动下拉为空"))
                if result.get("ok"):
                    filled[-1] = f"参与活动={result.get('picked')}"
        except Exception as e:
            rec("参与活动", False, str(e)[:60])

        # ---- 12) 话题 ----
        # 关键：CSDN 的 el-select 下拉是全局 teleport 的，页面上会同时存在
        # **活动**和**话题**两个 .el-select-dropdown。只用全局选择器会抓到
        # 上一个（活动）的列表——实测把"IT疑难杂症诊疗室"（活动名）当成了
        # 话题名。必须只认当前可见、且未被隐藏的那个 dropdown。
        topics = (options.get("topics") or "").strip()
        try:
            inp = page.query_selector('input[placeholder="请选择创作话题"]')
            if not inp:
                rec("话题", False, "找不到话题下拉")
            else:
                page.evaluate("""() => {
                    const i = document.querySelector('input[placeholder="请选择创作话题"]');
                    if (i) i.click();
                }""")
                _time.sleep(2.5)
                first = topics.split(",")[0].strip() if topics else ""
                result = page.evaluate("""(want) => {
                    // 只取可见的 dropdown（el-select 隐藏时 display:none 或 width=0）
                    const dds = [...document.querySelectorAll('.el-select-dropdown')]
                        .filter(d => {
                            const r = d.getBoundingClientRect();
                            const cs = getComputedStyle(d);
                            return r.width > 20 && r.height > 20
                                && cs.display !== 'none' && cs.visibility !== 'hidden';
                        });
                    if (!dds.length) return {ok:false, names:[], picked:'', why:'没有可见下拉'};
                    // 取最靠下的那个（话题在活动下面）；同位置时取面积最大的
                    dds.sort((a,b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
                    const items = [...dds[0].querySelectorAll('.el-select-dropdown__item')];
                    const names = items.map(it => (it.innerText||'').trim()).filter(Boolean);
                    if (!names.length) return {ok:false, names:[], picked:'', why:'下拉为空'};
                    let target = items[0];
                    if (want) {
                        const m = items.find(it => (it.innerText||'').includes(want));
                        if (m) target = m;
                    }
                    const picked = (target.innerText||'').trim();
                    target.click();
                    return {ok:true, names:names.slice(0,8), picked};
                }""", first)
                _time.sleep(1.5)
                ok = bool(result.get("ok"))
                rec("话题", True if ok else False,
                    "" if ok else result.get("why", "下拉为空"))
                if ok:
                    filled[-1] = f"话题={result.get('picked')}"
        except Exception as e:
            rec("话题", False, str(e)[:60])

        detail = f"已填 {len(filled)} 项"
        if skipped:
            detail += f"，跳过 {len(skipped)}：" + "、".join(skipped)
        if failed:
            detail += f"，**失败 {len(failed)}**：" + "、".join(failed)
        return {"filled": filled, "skipped": skipped, "failed": failed, "detail": detail}

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

        # 发布设置逐项填满（12 项），并拿回逐项结果。
        # 用户明确要求："设置里的东西都要填好，截图确定没问题才能发"。
        # 所以这里不只是"尽力填"——把结果带出去，且失败项要能让上层看见。
        form = self._fill_publish_form(page, article, options)
        shot = ""
        try:
            shot = self.save_debug(page, "csdn-before-publish")
        except Exception:   # aqg: top-level boundary（截图失败不影响发布）
            shot = ""

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
        # 判定"发布成功"的唯一可靠信号是整页跳到 creation/success/<id>。
        # 旧实现还接受 `articleId=` —— 那是个致命误判：编辑页 URL 本身就是
        # `/md/?articleId=xxx`，所以「弹出没关、什么都没发出去」也会命中，
        # 于是把草稿记成已发布（2026-11-08 真机实锤：文章 170271615 线上
        # 状态是「草稿」，库里却是 status=ok / draft_only=0）。
        # 现在只认 success 页；停在编辑页 = 没发出去。
        while time.time() < deadline:
            time.sleep(1.5)
            url = page.url
            if "creation/success/" in url:
                aid = url.rstrip("/").split("/")[-1]
                break
            # 体检 B2 修复（QA 标质力 2026-09-21）：发布链路加验证码检测，
            # 弹验证码时明确报类型，而不是傻等 40s 后报"发布未完成"
            kind = CaptchaPolicy.detect(page)
            if kind:
                captcha_kind = kind
                break
            # 「弹窗消失」不能当成发布成功：点了取消、或前端校验把弹窗收掉，
            # 都会让弹窗消失，但文章仍然是草稿。这里只在**已经离开编辑器**时
            # 才继续观察，最终判定仍然只认 success 页（或下面按标题对账）。
            # 2026-11-08 真机：正是这一支把"停在编辑页"判成了已发布。
        if not aid:
            # 按标题对账：只在**确认文章已经公开发布**时才认。
            # 旧实现只要列表里标题匹配就当成成功——但 CSDN 的列表里同时包含
            # 草稿，于是"发失败、只剩草稿"也被记成 ok（2026-11-08 实锤）。
            # 这里额外要求该条目的状态不是草稿；拿不到状态时宁可判失败。
            try:
                for a in self.list_articles(page, limit=15):
                    if (a.get("title") or "").strip() != article["title"].strip():
                        continue
                    st = str(a.get("status") or a.get("state") or "").lower()
                    if st in ("draft", "草稿", "0"):
                        continue          # 是草稿，不算发布成功
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
                "draft_only": False,
                # 发布设置逐项结果透出：前端/job 消息能看到哪项没填上
                "settings_filled": form.get("filled", []),
                "settings_skipped": form.get("skipped", []),
                "settings_failed": form.get("failed", []),
                "settings_detail": form.get("detail", ""),
                "settings_screenshot": shot,
                # 有必填项没填上时给警告（不静默）
                **({"warning": "发布设置未填满：" + form["detail"]}
                   if form.get("failed") else {})}

    def read_back_in_page(self, page, pub, article=None):
        """CSDN 专用回读：除了标题正文，还要验**发布状态**和**标签**。

        为什么必须单独写（2026-11-08 在文章页实锤两件事）：

        1. **状态可能是草稿。** 发布链路误把"停在编辑页"当成成功，文章实际
           只是草稿。通用回读只比标题和正文——而草稿页也有标题和正文，
           于是照样判 verified。必须显式读页面上的状态词。
        2. **标签可能是平台猜的。** 打开标签面板时 CSDN 会预选 5 个推荐标签，
           旧实现只点一个就发布，线上标签成了 `#ide`，而回读只看正文自然发现
           不了。这里把正文底部 `.article-footer-category-segment` 里的
           `标签` 组读出来，跟文章 tags 逐一比对。

        仍然遵守基类契约：读不到就是 found=False，绝不为了"好看"返回 True。
        """
        ev = super().read_back_in_page(page, pub, article)
        if not ev.get("found"):
            return ev

        # 状态：公开/私密/审核中/草稿
        try:
            state = page.evaluate("""() => {
                const words = ['公开', '私密', '草稿', '审核中', '仅我可见',
                               '粉丝可见', 'VIP可见', '待审核'];
                for (const el of document.querySelectorAll('span,em,div,a')) {
                    const t = (el.innerText || '').trim();
                    if (words.includes(t) && el.getBoundingClientRect().width > 0) {
                        return t;
                    }
                }
                return '';
            }""")
        except Exception:
            state = ""
        ev["state"] = state
        if state in ("草稿", "审核中", "待审核"):
            ev["found"] = False
            ev["detail"] = f"文章状态是「{state}」，不是公开发布"
            return ev

        # 标签：读底部标签组，跟目标比对
        want = []
        tags_raw = (article or {}).get("tags") or ""
        if isinstance(tags_raw, str):
            want = [t.strip() for t in tags_raw.split(",") if t.strip()]
        if want:
            try:
                got = page.evaluate("""() => {
                    const seg = document.querySelector('.article-footer-category-segment');
                    if (!seg) return [];
                    for (const g of seg.querySelectorAll('.article-footer-category-group')) {
                        const label = (g.querySelector('.article-footer-label') || {}).innerText || '';
                        if (!label.trim().startsWith('标签')) continue;
                        return [...g.querySelectorAll(
                            '.article-footer-pill-list a, .article-footer-pill-list span')]
                            .map(e => (e.innerText || '').trim().replace(/^#/, ''))
                            .filter(Boolean);
                    }
                    return [];
                }""")
            except Exception:
                got = []
            ev["tags"] = got
            got_low = {g.strip().lower() for g in got}
            missing = [t for t in want if t.strip().lower() not in got_low]
            if missing:
                ev["found"] = False
                ev["detail"] = (f"标签不符：期望 {want}，实际 {got}"
                                f"（缺 {missing}）")
                return ev

        # 基类已经写了一句"标题与正文都对得上"。这里补上状态和标签，
        # 让 verified 的证据链完整——否则用户看到的仍是"只验了正文"的旧说法。
        extra = []
        if state:
            extra.append(f"状态={state}")
        if ev.get("tags"):
            extra.append(f"标签={','.join(ev['tags'])}")
        ev["detail"] = "回读通过：标题、正文" + ("、" + "、".join(extra) if extra else "") + "都对得上"
        return ev

    # ---------------- 原地更新 ----------------

    def update(self, page, pub, article, options=None):
        options = options or {}
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

        # 正文替换：优先走 Markdown 文件导入——和发布链路用同一个机制。
        # 更新场景下 clipboard 粘贴不可靠（navigator.clipboard.writeText 在
        # 无用户手势/权限受限时静默失败，返回成功但编辑器里还是旧内容），
        # 2026-11-08 真机实测：注入后立刻检查"含标记"就是 False。
        md = article.get("content_md", "")
        self._set_cs_editor(page, md)
        time.sleep(2)
        # 注入后必须回读确认：编辑器里真的是新正文，否则后面点发布等于把旧稿
        # 又发了一遍（用户看到"更新成功"，线上却毫无变化）。
        # CSDN 没有知乎那样的 _require_editor，用正文首段做包含判定即可：
        # 编辑器会把标记渲染成文本，取目标里第一段够长的非空行去 has-text。
        probe = ""
        for line in md.splitlines():
            s_line = line.strip().lstrip("#").strip()
            if len(s_line) >= 12:
                probe = s_line[:40]
                break
        if probe:
            actual = page.evaluate("""() => {
                const el = document.querySelector('pre.editor__inner[contenteditable=true]');
                return el ? el.innerText : '';
            }""")
            if probe not in (actual or ""):
                raise PlatformError(
                    f"更新正文注入未生效：编辑器里找不到 {probe!r}"
                    f"（编辑器 {len(actual or '')} 字），已中止以避免把旧稿重发一遍")

        # 点底栏「发布文章」打开发布弹窗。2026-11-04 实测：弹窗打开后底栏按钮仍在
        # DOM 里，此前二次 _click_text(["发布文章",...]) 会再次命中底栏按钮，把刚
        # 打开的弹窗点掉，30s 等关闭必然超时误报"更新未成功"。
        # 现改为：只点一次底栏 → 等弹窗出现 → 在弹窗容器内定位确认按钮。
        # 点底栏的 `.btn-publish`（就是「发布文章」）。不能用 _click_text 按文案：
        # 页面上还有一个 0x0 的 `.simulation-button` 文案相同，先命中它点了没反应。
        opened = page.evaluate("""() => {
            const b = document.querySelector('button.btn-publish');
            if (b) { b.scrollIntoView({block: 'center'}); b.click(); return true; }
            return false;
        }""")
        if not opened:
            _click_text(page, ["发布文章"])
        try:
            page.wait_for_selector(".modal__inner-2, .modal__publish-article",
                                   timeout=20000)
        except Exception:
            # 再试一次：编辑器内容多时首次点击可能被懒加载拦掉
            page.evaluate("""() => {
                const b = document.querySelector('button.btn-publish');
                if (b) { b.click(); }
            }""")
            try:
                page.wait_for_selector(".modal__inner-2", timeout=15000)
            except Exception:
                raise PlatformError("更新未确认：打不开发布弹窗（.btn-publish 点了没出弹窗）")

        # 更新也要把发布设置填满——否则改完正文会把原有的标签/专栏/封面/摘要
        # 一起清掉（CSDN 的更新弹窗是空的，等于重新填一遍）。2026-11-08 实测：
        # 旧实现完全跳过这一步，更新后的文章会丢掉所有设置。
        self._fill_publish_form(page, article, options or {})

        # 确认按钮实测是 `button.btn-b-red.ml16`（弹窗右下红色按钮）。
        # 旧实现的 `button:has-text("发布文章")` 会同时命中弹窗内的两个按钮，
        # 加上 force=True 反而点错；改成 JS click 命中最靠后的那个红按钮。
        # 必须按**文案精确匹配**。弹窗底栏有三个按钮：取消 / 保存为草稿 /
        # 定时发布 / 发布文章，其中「定时发布」和「发布文章」都带 btn-b-red
        # ——取"最后一个 btn-b-red"实测点到了「定时发布」（2026-11-08 真机：
        # clicked='定时发布'，弹窗不关、线上内容不变，却返回了 ok=true）。
        clicked = page.evaluate("""() => {
            const bar = document.querySelector('.modal__button-bar');
            const scope = bar || document;
            for (const b of scope.querySelectorAll('button')) {
                if ((b.innerText || '').trim() === '发布文章') {
                    b.click();
                    return '发布文章';
                }
            }
            return '';
        }""")
        if not clicked:
            raise PlatformError("更新未确认：弹窗里找不到「发布文章」确认按钮")

        # 体检 B8 修复（QA 标质力 2026-09-21）：不再盲发——轮询等发布弹窗关闭，
        # 30s 还挂着就报错（让上层记 failed），而不是 sleep 完直接 return True
        # 发布成功时 CSDN 会整页跳转到 creation/success/<id>，此时 evaluate 会因
        # "Execution context was destroyed" 抛错——那不是失败，是成功的表现。
        # 所以判定要同时接受两种情况：跳转到 success 页，或弹窗消失。
        deadline = time.time() + 30
        closed = False
        while time.time() < deadline:
            time.sleep(1.5)
            try:
                if "creation/success" in (page.url or ""):
                    closed = True
                    break
                if not page.evaluate("() => !!document.querySelector('.modal__button-bar')"):
                    closed = True
                    break
            except Exception:
                # 上下文被销毁 = 正在导航，等同"弹窗没了"
                if "creation/success" in (page.url or ""):
                    closed = True
                    break
                time.sleep(1)
        if not closed:
            raise PlatformError("更新未确认：30s 内发布弹窗未关闭，更新可能没生效")

        # 弹窗关了不等于线上改了。2026-11-08 真机：弹窗正常关闭、函数返回 True，
        # 但打开文章页发现还是旧内容——body 里连注入的独特标记都没有。
        # 所以必须再回读一次**线上文章页**，确认目标正文真的在里面。
        # 拿不到 post_url 时退回用 post_id 拼，仍然是同一个页面。
        post_url = (pub or {}).get("post_url") or ""
        if not post_url:
            pid = (pub or {}).get("post_id") or ""
            if pid:
                try:
                    user = self._username(page)
                except Exception:
                    user = ""
                post_url = (f"https://blog.csdn.net/{user}/article/details/{pid}"
                            if user else f"https://blog.csdn.net/article/details/{pid}")
        if not post_url:
            return True   # 没有可回读的地址，只能按弹窗关闭判定

        # 用足够长的独特片段做判定：短片段在导航/推荐位里可能偶然出现。
        # 探针必须挑**渲染后仍原样存在**的文本：Markdown 的 **粗体**、`代码`、
        # [链接](url) 渲染完只剩内容，拿原文去比必然找不到（第一版就是这么
        # 误报的）。所以先用 _flatten 去掉标记符号，再取一段够长的纯文本。
        def _plain(t):
            return (t.replace("**", "").replace("`", "").replace("__", "")
                     .replace("*", "").strip())

        probe = ""
        for line in (article.get("content_md") or "").splitlines():
            cand = _plain(line.lstrip("#-*> ").strip())
            # 跳过表格/链接行这类渲染后结构会变的
            if not cand or "](http" in line or "|" in cand:
                continue
            if len(cand) >= 16:
                probe = cand[:48]
                break
        if not probe:
            return True

        # 页面有缓存，给几轮重试；CSDN 的详情页本身也要几秒渲染正文
        for attempt in range(4):
            try:
                page.goto(post_url, timeout=45000, wait_until="domcontentloaded")
                time.sleep(3)
                body = self.body_text(page)
                if probe in (body or ""):
                    return True
            except Exception:
                pass
            time.sleep(3)
        raise PlatformError(
            f"更新未生效：文章页里找不到 {probe!r}（已重试 4 次）。"
            f"弹窗关闭被当成了成功，实际线上仍是旧内容")
