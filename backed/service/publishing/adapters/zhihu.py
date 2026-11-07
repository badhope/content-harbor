# -*- coding: utf-8 -*-
"""知乎适配器：专栏文章，走 UI（Markdown 文件导入 + 点发布）。

为什么不用 API：知乎的 /api/v4/* 大多要求 x-zse-96 签名（前端 JS 生成），
裸 fetch 会被拒。走页面 UI 让前端自己发请求，签名/风控都由它处理，反而最稳。

选择器参考社区实测（MultiPost-Extension），知乎改版后用 dump_dom 重新抓。

正文注入策略（2026-09-27 真机重测）：
  - 知乎工具栏「导入」→「导入文档」弹 Modal，Modal 里就有 input[type=file]，
    直接 set_input_files 注入 .md，知乎自己解析（代码块/表格都对）
  - **「导入文档」是追加不是替换**：不清空的话正文变成 旧+新
    （实测 5442 → 10784，两次 → 16126），必须先清空
  - 工具栏「导入」这一层 Popover 只认真实鼠标事件：Playwright 的
    locator.click() 返回成功但子菜单不弹，core.human_click.move_and_click 才弹得开
  - 降级方案 document.execCommand('insertText')，会把 Markdown 当纯文本塞进去

打开编辑器（2026-09-27 定位到的真凶）：
  文章页上 button:has-text("编辑") 命中的是页头/搜索区的另一个按钮，点完直接
  跳到 www.zhihu.com/search?...（URL 带 search_preset）。后面注入与回读全在搜索
  页上跑，于是报「页面回读到空内容（编辑器没渲染出来，或页面已跳走）」——
  不是编辑器没渲染，是页面根本不在编辑器上。改成直接 goto /p/<id>/edit。

回读验证（2026-09 补）：
  注入与保存都必须回读。_import_md_file 与 _inject_editor 都会「返回成功但正文
  一个字没写进去」，点完提交按钮再 return True，库里就记成已更新，
  重新打开编辑器正文还是旧的。所以：
    1. 点提交之前 —— 回读编辑器，确认正文与标题就是这一篇；
    2. 点提交之后 —— 重新打开编辑页再回读一次（真往返，不是看当前 DOM）。
  任何一步证不出来都抛 PlatformError。

提交按钮的名字（2026-09-27 实测，别再按「保存并发布」找）：
  写新文章页 = 「发布」；编辑已发布文章 = 「更新」；同一页上还有一个
  「发布设置」，button:has-text("发布") 会命中它——点开的是设置浮层，提交按钮
  根本没被点到，于是"点了但没生效"。所以提交一律走精确文本匹配。
"""

import re
import time

from service.publishing.adapters.base import (
    PlatformAdapter,
    PlatformError,
    content_evidence,
    register,
    title_evidence,
)
from core.human_click import move_and_click

TITLE_SEL = 'textarea.Input[placeholder*="请输入标题"]'  # 知乎标题框是 class="Input" 的 textarea
EDITOR_SEL = 'div[data-contents="true"]'  # Draft.js 编辑器根节点
CE_SEL = "[data-contents=true] [contenteditable=true], [data-contents=true][contenteditable=true]"
EDIT_URL_TMPL = "https://zhuanlan.zhihu.com/p/{post_id}/edit"
IMPORT_BTN_SEL = 'button:has-text("导入")'
IMPORT_DOC_SEL = 'button[aria-label="导入文档"]'
MODAL_FILE_SEL = ".Modal input[type=file]"

# ---- 回答问题（2026-10-14 新增，真机探针实测）----
# 问题页「写回答」按钮：页面顶栏 sticky，Playwright 常规 click 会被 header
# 拦截（"intercepts pointer events"），force=True 也打不开——实测唯一稳定
# 通道是 JS 直接 click()。
WRITE_ANSWER_BTN_SEL = "button.WriteAnswerButton"
ANSWER_PUBLISH_TEXTS = ("发布回答",)
ANSWER_URL_RE = re.compile(r"/answer/(\d+)")

# 编辑器「真的渲染出正文了」的判据：只看 DOM 在不在会被空编辑器骗过去
# （选择器命中但 innerText 是空的，正是上一轮误报的现场）。
# 哨兵 hub:editor-len 让测试假 page 能把「编辑器当前有多长」当一等公民回。
READY_JS = """/* hub:editor-len */ () => {
    const el = document.querySelector('div[data-contents="true"]');
    if (!el) return -1;
    return (el.innerText || '').length;
}"""

# 聚焦并全选编辑器内容。选择用 Range 而不是 execCommand('selectAll')：
# 后者在知乎页面上会连带选中标题框，Ctrl+A 之后 Delete 可能把标题也删掉。
# 哨兵 hub:focus-editor 让测试假 page 知道焦点落在编辑器上（键盘事件该改正文）。
FOCUS_SELECT_ALL_JS = """/* hub:focus-editor */ () => {
    const ed = document.querySelector('[contenteditable="true"]');
    if (!ed) return false;
    ed.focus();
    const r = document.createRange();
    r.selectNodeContents(ed);
    const s = window.getSelection();
    s.removeAllRanges();
    s.addRange(r);
    return true;
}"""


def _click_exact(page, text, timeout=8000):
    """点文本**完全等于** text 的按钮；找不到再退回子串匹配。

    为什么不能只用 button:has-text：编辑页上同时存在「发布」和「发布设置」，
    has-text("发布") 命中的是「发布设置」（点开的是发布设置浮层，不是提交按钮），
    提交按钮其实叫「更新」。子串匹配必须排在精确匹配之后。
    """
    exact = page.locator(f'button:text-is("{text}")')
    n = 0
    # aqg: top-level boundary count 探测失败就当没有精确匹配，退回子串匹配
    try:
        n = exact.count()
    except Exception:
        n = 0
    if n:
        exact.first.click(timeout=timeout)
        return text
    for t in (text,):
        # aqg: top-level boundary 精确匹配没命中才退到子串匹配，点了就算成功
        try:
            page.locator(f'button:has-text("{t}")').first.click(timeout=timeout)
            return t
        except Exception:
            continue
    return ""


def _click_button(page, texts, timeout=8000):
    """点文本匹配的按钮（知乎按钮样式多，按文本找最稳）"""
    for t in texts:
        try:
            page.locator(f'button:has-text("{t}")').first.click(timeout=timeout)
            return t
        except Exception:
            continue
    # 兜底：按 CSS class（发布按钮 class 含 Button--primary）
    try:
        page.locator("button.Button--primary").first.click(timeout=timeout)
        return "Button--primary"
    except Exception:
        pass
    raise PlatformError(f"页面上找不到按钮: {texts}")


@register
class ZhihuAdapter(PlatformAdapter):
    id = "zhihu"
    name = "知乎"
    login_url = "https://www.zhihu.com/signin?next=%2F"

    # 页面结构版本（第三刀加固）：平台改版时更新此版本并同步 key_selectors
    selector_version = "2026-09"
    key_selectors = {
        "editor": ".DraftEditor-root",
        "title_input": "textarea, input[placeholder*='标题']",
    }
    home_url = "https://zhuanlan.zhihu.com/write"
    list_url = "https://www.zhihu.com/creator"  # 创作中心（内容管理在里面）
    new_url = "https://zhuanlan.zhihu.com/write"

    # 发布后等页面跳到文章页的轮询参数（提成类属性，便于回归测试缩短）
    PUBLISH_VERIFY_TIMEOUT = 30
    PUBLISH_VERIFY_POLL = 1.5

    # ---------------- 登录态 ----------------

    DOMAIN = "zhihu.com"
    # 「我」接口地址。提成类属性让 E2E mock 可替换（与 login_url 同理）。
    me_api = "https://www.zhihu.com/api/v4/me"

    def check_auth(self, page) -> bool:
        # 只认「我」接口返回真实用户：URL 判断有假成功（扫码确认页/未登录页
        # 都可能不含 /signin，2026-09-25 实测 z_c0 缺失的根因）
        try:
            data = self.api_get(page, self.me_api)
            return bool(data and (data.get("id") or data.get("url_token")))
        except Exception:
            return False

    # ---------------- 列表 ----------------

    # ---------------- 打开编辑器 ----------------

    def _wait_editor(self, page, want_text=True, timeout=45, poll=1.0):
        """等 Draft.js 编辑器把正文渲染出来。

        返回渲染出的长度；超时返回最后一次观察到的值（-1 = 选择器就没命中）。
        用它而不是 wait_for_selector：知乎的编辑器节点在 DOM 里但
        Playwright 的可行动性检查判它不可见（等 30s 直接超时），
        而 JS 读 innerText 拿得到真实长度。
        """
        deadline = time.time() + timeout
        last = -1
        while time.time() < deadline:
            v = self._safe_eval(page, READY_JS, None, -1)
            last = v if isinstance(v, int) else -1
            if want_text and last > 0:
                return last
            if not want_text and last <= 1:
                return last
            time.sleep(poll)
        return last

    def _editor_candidates(self, pub):
        """按可靠度排的编辑器地址候选。

        edit_url 优先是有前提的：publish 现在按 post_id 拼 edit_url
        （EDIT_URL_TMPL），它是确定性的。曾经把 edit_url 排到 post_url 之后，
        因为老实现存的是「点按钮那一刻」的竞态值（新文章还没分配 id 时就是通用
        /write，打开等于空白编辑器）。post_id / post_url 派生是第二、第三道保险。
        """
        out = []
        edit_url = (pub.get("edit_url") or "").strip()
        post_id = (pub.get("post_id") or "").strip()
        post_url = (pub.get("post_url") or "").strip()
        if edit_url and "/edit" in edit_url:
            out.append(edit_url)
        if post_id.isdigit():
            out.append(EDIT_URL_TMPL.format(post_id=post_id))
        if post_url and "/p/" in post_url and "/edit" not in post_url:
            out.append(post_url.rstrip("/") + "/edit")
        seen, uniq = set(), []
        for u in out:
            if u not in seen:
                seen.add(u)
                uniq.append(u)
        return uniq

    def _open_editor(self, page, pub, action="打开编辑器"):
        """直接 goto 编辑器地址并等正文渲染出来，返回真正生效的地址。

        为什么不再在文章页点「编辑」：2026-09-27 真机实测该点击会跳到
        www.zhihu.com/search?...（URL 带 search_preset），随后所有注入与回读
        都在搜索页上执行，于是报「页面回读到空内容」——那句话里的
        「页面已跳走」才是真相。任何候选地址都没渲染出正文就抛错，绝不返回
        一个「打开成功但停在别的页面」的假结果。
        """
        cands = self._editor_candidates(pub)
        if not cands:
            self.verify_or_raise(action, False,
                                 "publication 里没有 post_id / post_url / edit_url，"
                                 "拼不出知乎编辑器地址")
        tried = []
        for url in cands:
            # aqg: top-level boundary 单个候选打不开不能中断后面的候选
            try:
                page.goto(url, timeout=60000, wait_until="domcontentloaded")
            except Exception:
                tried.append("%s(导航失败)" % url)
                continue
            n = self._wait_editor(page, want_text=True, timeout=25)
            if n > 0:
                return url
            tried.append("%s(编辑器正文 %d 字)" % (url, n))
        self.verify_or_raise(action, False,
                             "候选编辑器地址都没渲染出正文：" + "；".join(tried))

    # ---------------- 正文注入 ----------------

    def _clear_editor(self, page, timeout=20):
        """把编辑器清空，返回回读到的长度（<=1 视为已空）。

        必须走真实键盘事件。2026-09-27 实测：document.execCommand('selectAll' +
        'delete') 只把 DOM 清了，Draft.js 内部的 editorState 原封不动——紧接着
        「导入文档」，编辑器拿旧 state 重新渲染，正文又长回 15977 字（导入后
        变成 21318）。Playwright 的 keyboard 事件经 CDP 打进 Draft 的
        onBeforeInput，模型才真的清掉，而且清完再等 10s 不回弹。
        """
        page.evaluate(FOCUS_SELECT_ALL_JS)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        time.sleep(0.8)
        return self._wait_editor(page, want_text=False, timeout=timeout)

    def _import_md_file(self, page, content):
        """清空编辑器后，走「导入」→「导入文档」→ Modal 里的 input[type=file]。

        2026-09-27 真机实测的三个要点：
          1. 「导入文档」是**追加**不是替换（5442 → 10784，两次 → 16126），
             所以必须先 _clear_editor；
          2. 工具栏「导入」的子菜单只认真实鼠标事件，locator.click() 返回成功
             但子菜单不弹（button[aria-label=导入文档] 计数 0），
             move_and_click 才弹得开；
          3. Modal 打开后里面就有 input[type=file]，直接 set_input_files，
             不用走 expect_file_chooser 的原生文件对话框。

        返回 True 成功，False 失败（调用方降级到 _inject_editor）。
        """
        import os
        import tempfile
        from pathlib import Path

        tmp = None
        try:
            left = self._clear_editor(page)
            if left > 1:
                return False

            data_dir = Path(__file__).resolve().parent.parent.parent / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            tmp = tempfile.NamedTemporaryFile(
                suffix=".md", mode="w", encoding="utf-8",
                delete=False, dir=str(data_dir))
            tmp.write(content)
            tmp.close()

            # 工具栏「导入」：只认真实鼠标事件
            if not move_and_click(page, page.locator(IMPORT_BTN_SEL).first,
                                  timeout=10000):
                return False
            if not self._wait_count(page, IMPORT_DOC_SEL, 12):
                return False
            if not move_and_click(page, page.locator(IMPORT_DOC_SEL).first,
                                  timeout=10000):
                return False
            if not self._wait_count(page, MODAL_FILE_SEL, 15):
                return False
            page.locator(MODAL_FILE_SEL).first.set_input_files(tmp.name)
            return self._wait_editor(page, want_text=True, timeout=30) > 0
        # aqg: top-level boundary 导入这条路任何一步都不认就整体降级，绝不半途留着脏状态
        except Exception:
            return False
        finally:
            if tmp:
                try:
                    os.unlink(tmp.name)
                except Exception:
                    pass

    def _wait_count(self, page, selector, timeout, poll=0.8):
        """等选择器至少命中一个元素（Popover / Modal 是异步挂载的）。"""
        js = "(sel) => document.querySelectorAll(sel).length"
        deadline = time.time() + timeout
        while time.time() < deadline:
            # aqg: top-level boundary evaluate 失败（页面在跳）只当还没出现，重试
            try:
                if (page.evaluate(js, selector) or 0) > 0:
                    return True
            except Exception:
                pass
            time.sleep(poll)
        return False

    def _inject_editor(self, page, content):
        """降级注入：真实键盘清空 + execCommand('insertText') 写入。

        实测发现：
          - PASTE_HTML_JS 的 ClipboardEvent 对知乎新版 Draft.js 草稿编辑器已失效
            （paste 事件被禁用）
          - 走真实键盘（CDP）清空，Draft.js 的 editorState 才真的被清掉
          - execCommand('insertText') 写入能被回读读到，但整篇 Markdown 会当成
            纯文本塞进一个段落里（标题的 ## 会原样显示），所以它只是降级方案，
            能用的时候优先 _import_md_file
        """
        left = self._clear_editor(page)
        if left > 1:
            raise PlatformError(
                f"知乎编辑器清空失败（回读还有 {left} 字），已中止注入，"
                "避免把新正文追加在旧正文后面")
        ok = page.evaluate("""(text) => {
            const ces = document.querySelectorAll('[contenteditable="true"]');
            if (!ces.length) return false;
            const ed = ces[0];
            ed.focus();
            document.execCommand('insertText', false, text);
            return true;
        }""", content)
        if not ok:
            raise PlatformError("知乎编辑器注入失败：找不到 contenteditable")
        time.sleep(1.5)

    # ---------------- 回读验证 ----------------

    def _require_editor(self, page, md_content, title, action):
        """回读编辑器，确认正文与标题就是目标内容。证不出来直接抛。"""
        return self.require_content(
            page, md_content, title, EDITOR_SEL, TITLE_SEL, action)

    def _readback_editor(self, page, pub):
        """重新打开编辑页回读正文与标题（真往返验证，不是看当前 DOM 缓存）。

        走的是「保存后页面已经跳走」之后重新载入的那份数据，平台侧的缓存
        （Draft.js 内存态、input 未失焦的 value）骗不过去。

        地址一律由 _open_editor 决定（edit_url → post_id 派生 → post_url 派生），
        全部是确定性的 /p/<id>/edit。旧实现在这里点文章页的「编辑」按钮，而那个
        点击在 2026-09-27 实测里会跳到搜索页——「保存后回读」于是稳定读到空内容。
        """
        url = self._open_editor(page, pub, "保存后回读·打开编辑页")
        st = self.read_editor_state(page, EDITOR_SEL, TITLE_SEL)
        if not st["body"]:
            self.verify_or_raise(
                "保存后回读", False,
                f"重新打开 {url} 后编辑器是空的（选择器 {EDITOR_SEL} 可能已失效）")
        return st

    # ---------------- 发布 ----------------

    def publish(self, page, article, options=None):
        options = options or {}
        # 回答问题模式：settings.zhihu.question_url 给了问题链接就写回答而非发专栏
        question_url = (options.get("question_url") or "").strip()
        if question_url:
            return self.publish_as_answer(page, article, question_url, options)

        page.goto(self.new_url, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_selector(TITLE_SEL, timeout=30000)
        time.sleep(2)

        # 标题（知乎限 100 字）
        page.fill(TITLE_SEL, article["title"][:100])

        # 正文：优先用 Markdown 文件导入（正确解析代码块/表格），降级到逐字输入
        md_content = article.get("content_md", "")
        import_ok = self._import_md_file(page, md_content)
        if not import_ok:
            self._inject_editor(page, md_content)
        time.sleep(3)  # 等编辑器自动保存

        # 点发布之前先回读：导入/注入两条路都可能「返回成功但什么都没写进去」，
        # 带着旧正文点发布 = 又多一篇错的文章 + 库里记成已发布。
        self._require_editor(page, md_content, article["title"][:100], "正文注入")

        if options.get("draft_only"):
            # 知乎编辑器自动存草稿，但没有公开的草稿 ID 可拿 —— 不点发布，交人工确认
            return {
                "post_id": "",
                "post_url": "",
                "edit_url": self.new_url,
                "draft_only": True,
            }

        # ---- 发布设置：必须在点「发布」**之前**填 ----
        # 知乎的「发布设置」（封面/专栏收录/定时发布）是编辑页常驻区块，不是
        # 点发布后弹出的浮层。旧实现放在点发布之后，那时页面已经在提交/跳转，
        # 再去找控件必然落空——这也是为什么以前"填了但没生效"却看不出来。
        self._fill_publish_settings(page, options)

        if not _click_exact(page, "发布"):
            self.verify_or_raise("发布", False,
                                 "写页上找不到「发布」按钮（不要退到「发布设置」）")
        time.sleep(3)

        # 点发布后可能再弹一层确认（首次发文/开通专栏等），尽力点一次
        # aqg: top-level boundary 确认按钮找不到不算失败（有些情况不弹）
        try:
            _click_button(page, ["确认发布", "确定", "发布"], timeout=8000)
        except Exception:
            pass

        # 发布成功后 URL 会跳到 /p/<id>。
        # 注意：知乎编辑器 URL 本身就是 /p/<id>/edit，只判 "/p/" in url 会把
        # "还停在编辑器里" 误判成已发布（2026-09 实测踩到：库里记了 published，
        # 实际文章页显示「你似乎来到了没有知识存在的荒原」）。这里必须排除 /edit。
        # 跳不过去就是没发出去——抛错，不给成功语义。
        # aqg: top-level boundary 没跳过去先存现场再抛：多半是弹了人工确认窗口，
        # 人工接手时要看的正是这一刻的 DOM
        try:
            self.wait_for_url_change(
                page,
                previous_url=None,
                accept=lambda u: "/p/" in u and "/edit" not in u,
                timeout=self.PUBLISH_VERIFY_TIMEOUT,
                poll=self.PUBLISH_VERIFY_POLL,
            )
        except PlatformError:
            self.save_debug(page, "zhihu_publish_stuck")
            raise
        post_id = (page.url or "").rstrip("/").split("/p/")[-1].split("?")[0]
        settings = getattr(self, "_last_settings", None) or {}
        return {
            "post_id": post_id,
            "post_url": f"https://zhuanlan.zhihu.com/p/{post_id}",
            # 编辑地址按 post_id 拼，不存「点按钮那一刻」的竞态值：那会儿可能还
            # 停在通用 /write（id 还没分配），存进去会让后面的原地更新打开一个
            # 空白编辑器，白点一次「发布」（写页主按钮就是「发布」）。
            "edit_url": f"https://zhuanlan.zhihu.com/p/{post_id}/edit",
            "draft_only": False,
            "settings_filled": settings.get("filled", []),
            "settings_skipped": settings.get("skipped", []),
            "settings_failed": settings.get("failed", []),
            "settings_detail": settings.get("detail", ""),
            **({"warning": "发布设置未填满：" + settings["detail"]}
               if settings.get("failed") else {}),
        }

    # ---------------- 回答问题（2026-10-14 新增） ----------------

    def _open_answer_editor(self, page, question_url):
        """打开问题页并展开「写回答」编辑器。成功返回 True。

        真机探针（2026-10-14）钉死的两条：
          1. 顶栏是 sticky 的，locator.click / force click 都进不去（React 只认
             事件真实路径与 JS click），唯一稳定通道是 JS 直接 click()；
          2. 编辑器出现要自己轮询存在性——_wait_editor(want_text=False) 在
             「编辑器还没出现」时也满足（-1 <= 1）会提前返回，不能用它等开场。
        """
        page.goto(question_url, timeout=60000, wait_until="domcontentloaded")
        time.sleep(4)  # SPA 渲染 + 登录态沉降
        clicked = self._safe_eval(page, """() => {
            const b = document.querySelector('button.WriteAnswerButton');
            if (!b) return false;
            b.scrollIntoView({ block: 'center' });
            b.click();
            return true;
        }""", None, False)
        if not clicked:
            return False
        deadline = time.time() + 25
        while time.time() < deadline:
            v = self._safe_eval(page, READY_JS, None, -1)
            if isinstance(v, int) and v >= 0:
                return True
            time.sleep(1)
        return False

    def _click_answer_publish(self, page):
        """点「发布回答」：先常规 locator，失败再 JS click（同「写回答」的教训）。"""
        # aqg: top-level boundary 首选 locator 失败降级 JS click，两条都失败才算失败
        try:
            page.locator('button:text-is("发布回答")').first.click(timeout=8000)
            return True
        except Exception:
            pass
        return bool(self._safe_eval(page, """() => {
            const norm = s => (s || '').replace(/\\u200b/g, '').trim();
            const b = [...document.querySelectorAll('button')]
                .find(x => norm(x.innerText) === '发布回答');
            if (!b) return false;
            b.click();
            return true;
        }""", None, False))

    def publish_as_answer(self, page, article, question_url, options=None):
        """在指定问题下写作并发布回答（真实上线；draft_only 时只填不发布）。

        复用专栏链路的注入/回读纪律（回答编辑器与专栏编辑器同构，均为
        Draft.js + 「导入→导入文档」，探针实测确认）：
          打开编辑器 → 导入 Markdown → 点发布前回读正文 → 点「发布回答」
          → 轮询验证（URL 出现 /answer/ 或按钮变为「修改回答」）。
        """
        options = options or {}
        if not self._open_answer_editor(page, question_url):
            self.save_debug(page, "zhihu_answer_editor_stuck")
            raise PlatformError("知乎「写回答」编辑器未打开（问题页未登录或结构已改版）")

        md_content = article.get("content_md", "")
        import_ok = self._import_md_file(page, md_content)
        if not import_ok:
            self._inject_editor(page, md_content)
        time.sleep(3)  # 等自动存草稿

        # 点发布前回读：注入返回成功但没写进去的情况真实发生过，证不出就不敢点
        st = self.read_editor_state(page, EDITOR_SEL, "")
        c_ok, c_detail = content_evidence(md_content, st["body"])
        self.verify_or_raise("回答正文注入", c_ok, c_detail)

        if options.get("draft_only"):
            # 知乎回答编辑器自动存草稿——不点发布，留人工确认
            return {"post_id": "", "post_url": "", "edit_url": question_url,
                    "draft_only": True}

        if not self._click_answer_publish(page):
            self.save_debug(page, "zhihu_answer_publish_btn")
            raise PlatformError("知乎问题页找不到「发布回答」按钮")

        deadline = time.time() + self.PUBLISH_VERIFY_TIMEOUT
        done_mark = False
        while time.time() < deadline:
            if ANSWER_URL_RE.search(page.url or ""):
                break
            done_mark = bool(self._safe_eval(page, """() => {
                const norm = s => (s || '').replace(/\\u200b/g, '').trim();
                return [...document.querySelectorAll('button')]
                    .some(b => norm(b.innerText) === '修改回答');
            }""", None, False))
            if done_mark:
                break
            time.sleep(self.PUBLISH_VERIFY_POLL)
        else:
            self.save_debug(page, "zhihu_answer_publish_stuck")
            raise PlatformError(
                "回答点了发布但未观察到发布完成（URL 无 /answer/ 且无「修改回答」）")

        u = page.url or question_url
        m = ANSWER_URL_RE.search(u)
        return {
            "post_id": m.group(1) if m else "",
            "post_url": u,
            "edit_url": question_url,   # 回答的"编辑入口"就是问题页本身
            "draft_only": False,
        }

    # ---------------- 发布设置面板填写（0.7.1 新增） ----------------

    def _fill_publish_settings(self, page, options):
        """填知乎「发布设置」面板——按真实结构逐项填，并逐项回报。

        真实结构（2026-11-08 实测 DOM + 截图，见
        docs/verification/2026-11-08-publish-settings.md）：

            添加封面     label.UploadPicture-wrapper > input[type=file][accept=.jpeg,.jpg,.png]
            投稿至问题   （输入框，问题链接模式用）
            专栏收录     label.css-1is1x4f 是**单选 label**，两种文案：
                         "发布到专栏" / "不发布到专栏"
            定时发布     input.css-1i5t6yb type=checkbox

        旧实现填的"话题/AI 辅助/活动/评论权限"在当前版本知乎页面上**一个都不存在**
        ——那些选择器全是猜的，`except` 一包就静默跳过，用户以为填了其实没填。
        本版按真实 DOM 重写，并且**逐项回报**，填不上就是 failed，不静默。

        返回 {"filled": [...], "skipped": [...], "failed": [...], "detail": str}
        """
        filled, skipped, failed = [], [], []

        def rec(name, ok, why=""):
            if ok is True:
                filled.append(name)
            elif ok == "skip":
                skipped.append(f"{name}({why})" if why else name)
            else:
                failed.append(f"{name}({why})" if why else name)

        # ---- 1) 添加封面（本地图必须真上传）----
        cover = (options.get("cover") or "").strip()
        try:
            if not cover:
                rec("添加封面", "skip", "没有封面")
            elif cover.startswith(("http://", "https://")):
                rec("添加封面", "skip", "外链封面，知乎要求本地上传")
            else:
                inp = page.locator(
                    'label.UploadPicture-wrapper input[type=file]').first
                if not inp.count():
                    rec("添加封面", False, "找不到封面上传控件")
                else:
                    inp.set_input_files(cover)
                    time.sleep(4)
                    # 上传成功后 wrapper 里会出现 img（预览）
                    ok = False
                    for _ in range(10):
                        ok = page.evaluate("""() => {
                            const w = document.querySelector('.UploadPicture-wrapper');
                            if (!w) return false;
                            const img = w.querySelector('img') || w.parentElement.querySelector('img');
                            return !!(img && img.src && !img.src.startsWith('data:,'));
                        }""")
                        if ok:
                            break
                        time.sleep(1)
                    rec("添加封面", True if ok else False,
                        "" if ok else "上传后未出现预览图")
        except Exception as e:
            rec("添加封面", False, str(e)[:60])

        # ---- 2) 投稿至问题（仅在给了 question_url 时）----
        qurl = (options.get("question_url") or "").strip()
        try:
            if not qurl:
                rec("投稿至问题", "skip", "未指定问题")
            else:
                ok = page.evaluate("""(url) => {
                    // 「投稿至问题」旁边的输入框：找带该文案的行，取行内 input
                    const rows = [...document.querySelectorAll('div')];
                    for (const r of rows) {
                        const t = (r.innerText || '').trim();
                        if (!t.startsWith('投稿至问题') || t.length > 200) continue;
                        const inp = r.querySelector('input[type=text], input:not([type])');
                        if (inp) {
                            const s = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value').set;
                            s.call(inp, url);
                            inp.dispatchEvent(new Event('input', {bubbles: true}));
                            return true;
                        }
                    }
                    return false;
                }""", qurl)
                time.sleep(2)
                rec("投稿至问题", True if ok else False,
                    "" if ok else "找不到问题输入框")
        except Exception as e:
            rec("投稿至问题", False, str(e)[:60])

        # ---- 3) 专栏收录 ----
        # 真实控件：<label class="css-1is1x4f"> 里含 radio + 文案
        # 「发布到专栏」（选了会再弹专栏选择）/「不发布到专栏」（默认）
        want_column = bool(options.get("column_publish", True)) and bool(
            (options.get("column") or "").strip())
        target_text = "发布到专栏" if want_column else "不发布到专栏"
        try:
            ok = page.evaluate("""(txt) => {
                const labels = [...document.querySelectorAll('label.css-1is1x4f, '
                                 + 'label[class*=RadioButtonSet], label')];
                for (const l of labels) {
                    const t = (l.innerText || '').trim();
                    // "发布到专栏" 是 "不发布到专栏" 的子串，必须精确匹配开头且排除"不"
                    if (txt === '发布到专栏') {
                        if (t === '发布到专栏') {
                            l.click();
                            const inp = l.querySelector('input');
                            return !!(inp && inp.checked);
                        }
                    } else if (t === '不发布到专栏') {
                        l.click();
                        const inp = l.querySelector('input');
                        return !!(inp && inp.checked);
                    }
                }
                return false;
            }""", target_text)
            time.sleep(1.5)
            if ok and want_column:
                # 选了「发布到专栏」还要挑具体专栏
                time.sleep(2)
                picked = page.evaluate("""(name) => {
                    const items = [...document.querySelectorAll(
                        '[class*=Column] [class*=item], [class*=column] li, li')]
                        .filter(e => e.getBoundingClientRect().height > 10);
                    if (!items.length) return '';
                    const m = items.find(e => (e.innerText||'').trim().includes(name))
                           || items[0];
                    m.click();
                    return (m.innerText||'').trim().slice(0, 30);
                }""", (options.get("column") or "").strip())
                rec("专栏收录", True if picked else False,
                    f"已选专栏 {picked}" if picked else "点到专栏选择但没选中具体专栏")
                if picked:
                    filled[-1] = f"专栏收录={picked}"
            else:
                rec("专栏收录", True if ok else False,
                    "" if ok else f"没选中 {target_text}")
        except Exception as e:
            rec("专栏收录", False, str(e)[:60])

        # ---- 4) 定时发布 ----
        # 真实控件：input.css-1i5t6yb type=checkbox。不开就保持 false；
        # 要求定时才勾上（勾上后知乎会要求选时间，本流程不实现具体时间，故
        # 只在用户明确给了 scheduled_at 时才勾，并如实说明需人工确认时间）。
        sched = (options.get("scheduled_at") or "").strip()
        try:
            if not sched:
                rec("定时发布", True)   # 保持关闭也是一种明确状态，不是跳过
            else:
                ok = page.evaluate("""() => {
                    const cb = document.querySelector('input.css-1i5t6yb');
                    if (!cb) return false;
                    if (!cb.checked) cb.click();
                    return cb.checked;
                }""")
                rec("定时发布", True if ok else False,
                    "" if ok else "找不到定时开关")
        except Exception as e:
            rec("定时发布", False, str(e)[:60])

        detail = f"已填 {len(filled)} 项"
        if skipped:
            detail += f"，跳过 {len(skipped)}：" + "、".join(skipped)
        if failed:
            detail += f"，**失败 {len(failed)}**：" + "、".join(failed)
        self._last_settings = {"filled": filled, "skipped": skipped,
                               "failed": failed, "detail": detail}
        return self._last_settings


    def update(self, page, pub, article):
        self._open_editor(page, pub, "更新·打开编辑器")

        # 标题只在与目标不一致时才重填：知乎标题框能被正确读回，
        # 而「打开编辑页」这一步本身不改标题，没必要为一次原样更新去动它。
        st0 = self.read_editor_state(page, EDITOR_SEL, TITLE_SEL)
        want_title = article["title"][:100]
        t_ok, _t_detail = title_evidence(want_title, st0["title"])
        if not t_ok:
            try:
                page.fill(TITLE_SEL, want_title)
            # aqg: top-level boundary 标题写不进去不直接失败，交给下面的回读判
            except Exception:
                pass

        md_content = article.get("content_md", "")
        import_ok = self._import_md_file(page, md_content)
        if not import_ok:
            self._inject_editor(page, md_content)
        time.sleep(3)

        # 点保存之前先回读。_import_md_file 失败后回退 _inject_editor 这条路
        # 2026-09 实测两条都没真替换正文，照样点「保存并发布」然后 return True ——
        # 这里不拦住，后面那一整套回读就是白做（还白点一次按钮）。
        self._require_editor(page, md_content, want_title, "正文注入")

        # 已发布文章的提交按钮就叫「更新」。这里必须精确匹配文本：
        # 同一页上还有「发布设置」，button:has-text("发布") 命中的是它，
        # 点开是发布设置浮层，提交按钮根本没被点到。
        if not _click_exact(page, "更新"):
            self.verify_or_raise("更新·提交", False,
                                 "编辑页上找不到「更新」按钮（不要退到「发布设置」）")
        time.sleep(2)
        # aqg: top-level boundary 二次确认弹窗是可选的，点不到不算失败
        try:
            _click_button(page, ["确认发布", "确定"], timeout=5000)
        except Exception:
            pass
        time.sleep(3)

        # 回读验证（verify-after-act）：点完按钮不算数，重新打开编辑页把正文读回来。
        # 这是整条链路的最后一道闸——上层 update_single 丢弃适配器返回值、
        # 直接按成功记账，所以这里只能抛错，不存在「返回 False」这条路。
        st = self._readback_editor(page, pub)
        # aqg: top-level boundary 回读不过先存现场再抛（人工接手要看的就是这一刻）
        try:
            c_ok, c_detail = content_evidence(md_content, st["body"])
            self.verify_or_raise("保存后回读·正文", c_ok, c_detail)
            t_ok, t_detail = title_evidence(want_title, st["title"])
            self.verify_or_raise("保存后回读·标题", t_ok, t_detail)
        except PlatformError:
            self.save_debug(page, "zhihu_update_unverified")
            raise
        return True
